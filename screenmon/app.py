from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, Any
import os
from functools import partial

from .activity import ScreenIdleDetector
from .config import AppConfig
from .emailer import EmailClient
from .llm import LLMAnalyzer
from .screenshot import CameraCapturer, ScreenCapturer
from .storage import StorageManager
from .utils import configure_logging, parse_timezone, seconds_until

RUNTIME_STATE_FILE = "monitor_state.json"


class ScreenMonApp:
    def __init__(self, config: AppConfig):
        self.config = config
        log_path = configure_logging(config.storage.log_dir, config.log_level)
        self.logger = logging.getLogger("ScreenMonApp")
        self.logger.info("日志输出: %s", log_path)
        self.tz = parse_timezone(config.report.timezone)
        self.capturer = ScreenCapturer(config.capture)
        self.camera_capturer = CameraCapturer(config.capture)
        self.idle_detector = ScreenIdleDetector(config.capture, profile="idle")
        self.photo_idle_detector = ScreenIdleDetector(config.capture, profile="photo_idle")
        self.storage = StorageManager(config.storage)
        self.llm = LLMAnalyzer(config.llm, config.retry)
        self.emailer = EmailClient(config.email)
        self._stop = asyncio.Event()
        self._email_in_progress = False
        self.capture_start = self._parse_hhmm(config.capture.monitor_start_time)
        self.capture_end = self._parse_hhmm(config.capture.monitor_end_time)
        self._executor = ThreadPoolExecutor(
            max_workers=self._recommend_workers()
        )
        today = datetime.now(self.tz)
        self._ensure_valid_log_file(today)
        self._ensure_photo_valid_log_file(today)
        self.logger.info("截图有效解读日志: %s", self._daily_valid_log_path(today))
        self.logger.info("照片有效解读日志: %s", self._daily_photo_valid_log_path(today))
        self._write_runtime_state("idle")

    async def run(self, run_once: bool = False):
        tasks = [asyncio.create_task(self._capture_loop(run_once), name="capture_loop")]
        if not run_once and self.config.email.enabled:
            tasks.append(asyncio.create_task(self._email_loop(), name="email_loop"))
        stop_waiter = asyncio.create_task(self._stop.wait(), name="stop_waiter")
        try:
            done, _ = await asyncio.wait([*tasks, stop_waiter], return_when=asyncio.FIRST_COMPLETED)
            if stop_waiter in done:
                return
            for task in done:
                exc = task.exception()
                if exc is not None:
                    self.logger.error("后台任务异常退出: %s", task.get_name(), exc_info=exc)
                    raise RuntimeError(f"后台任务异常退出: {task.get_name()}") from exc
                if not run_once:
                    self.logger.error("后台任务意外结束: %s", task.get_name())
                    raise RuntimeError(f"后台任务意外结束: {task.get_name()}")
        finally:
            self._stop.set()
            stop_waiter.cancel()
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            with suppress(asyncio.CancelledError):
                await stop_waiter

    async def _capture_loop(self, run_once: bool):
        while not self._stop.is_set():
            try:
                now = datetime.now(self.tz)
                if self._is_within_capture_window(now):
                    success = await self.capture_once(captured_at=now)
                else:
                    success = False
                    self._write_runtime_state("idle")
                self.storage.cleanup(self.config.storage.log_retention_days, now=now)
                self.capturer.cleanup(self.config.storage.screenshot_retention_days)
                self.camera_capturer.cleanup(self.config.storage.screenshot_retention_days)
                if success:
                    self._write_runtime_state("idle")
                if run_once:
                    self._stop.set()
                    break
            except Exception as exc:
                self.logger.error("截图循环发生未知异常: %s", exc, exc_info=True)
                self._write_runtime_state("error", detail=str(exc))
                if run_once:
                    break
                # 避免死循环快速重试
                await asyncio.sleep(5)
            
            await asyncio.sleep(self.config.capture.interval_seconds)

    async def capture_once(self, captured_at: datetime | None = None) -> bool:
        captured_at = captured_at or datetime.now(self.tz)
        self._write_runtime_state("capture")
        screenshot_success = False
        photo_success = False
        try:
            try:
                screenshot_started_at = datetime.now(self.tz)
                screenshot_path = await self._run_in_pool(self.capturer.grab)
                self._assert_capture_file_fresh(screenshot_path, screenshot_started_at, "截图")
            except Exception as exc:
                self.logger.error("截图失败: %s", exc)
                self._write_runtime_state("error", detail=str(exc))
            else:
                idle_decision = await self._run_in_pool(self.idle_detector.evaluate, screenshot_path)
                if idle_decision.idle:
                    self._write_runtime_state(
                        "monitor_idle",
                        detail=(
                            f"skip_llm similarity={idle_decision.similarity:.4f} "
                            f"changed_ratio={idle_decision.changed_ratio:.4f} "
                            f"streak={idle_decision.streak}"
                        ),
                    )
                    self.logger.info(
                        "判定屏幕空闲，跳过 LLM: 相似度 %.2f%%, 变化像素 %.2f%%, 连续帧 %s/%s",
                        idle_decision.similarity * 100.0,
                        idle_decision.changed_ratio * 100.0,
                        idle_decision.streak,
                        self.config.capture.idle_consecutive_frames,
                    )
                else:
                    if idle_decision.compared:
                        self.logger.debug(
                            "屏幕变化判定为活跃: 相似度 %.2f%%, 变化像素 %.2f%%, 连续相似帧 %s",
                            idle_decision.similarity * 100.0,
                            idle_decision.changed_ratio * 100.0,
                            idle_decision.streak,
                        )
                    try:
                        self._write_runtime_state("llm")
                        analysis = await self.llm.analyze(screenshot_path)
                        self.storage.insert_snapshot(
                            captured_at=captured_at,
                            screenshot_path=screenshot_path,
                            summary=analysis.summary,
                            detail=analysis.detail,
                            confidence=analysis.confidence,
                            raw_response=analysis.raw_response,
                        )
                        self._append_valid_analysis_log(captured_at, screenshot_path, analysis.summary, analysis.detail)
                        self.logger.info("完成分析: %s", analysis.summary)
                        self.logger.info("LLM 返回全文（长度=%s）:\n%s", len(analysis.detail or ""), analysis.detail or "")
                        screenshot_success = True
                    except Exception as exc:
                        self.logger.exception("分析失败: %s", exc)
                        self.storage.insert_snapshot(
                            captured_at=captured_at,
                            screenshot_path=screenshot_path,
                            summary="分析失败",
                            detail=str(exc),
                            confidence=0.0,
                            raw_response={},
                            error=str(exc),
                        )
                        self._write_runtime_state("error", detail=str(exc))
            # 捕获照片（摄像头不可用不会导致整体失败）
            try:
                photo_success = await self._capture_photo_once(captured_at, compare_with_previous=True)
            except Exception as exc:
                self.logger.warning("照片采集过程异常，已跳过: %s", exc)
                photo_success = False
        except Exception as exc:
            self.logger.exception("capture_once 发生未预期异常: %s", exc)
            self._write_runtime_state("error", detail=f"未预期异常: {exc}")
        return screenshot_success or photo_success

    async def _capture_photo_once(self, captured_at: datetime, compare_with_previous: bool = True) -> bool:
        try:
            photo_started_at = datetime.now(self.tz)
            photo_path = await self._run_in_pool(self.camera_capturer.grab)
            if photo_path is not None:
                self._assert_capture_file_fresh(photo_path, photo_started_at, "照片")
        except Exception as exc:
            self.logger.warning("摄像头拍照失败，已跳过: %s", exc)
            self._write_runtime_state("error", detail=f"拍照失败: {exc}")
            return False
        if photo_path is None:
            self.logger.info("未检测到可用摄像头，本次跳过照片采集")
            self._write_runtime_state("idle", detail="摄像头不存在，已跳过拍照")
            return False
        if compare_with_previous:
            idle_decision = await self._run_in_pool(self.photo_idle_detector.evaluate, photo_path)
            if idle_decision.idle:
                self._write_runtime_state(
                    "monitor_idle",
                    detail=(
                        f"skip_photo_llm similarity={idle_decision.similarity:.4f} "
                        f"changed_ratio={idle_decision.changed_ratio:.4f} "
                        f"streak={idle_decision.streak}"
                    ),
                )
                self.logger.info(
                    "判定照片重复，跳过照片 LLM: 相似度 %.2f%%, 变化像素 %.2f%%, 连续帧 %s/%s",
                    idle_decision.similarity * 100.0,
                    idle_decision.changed_ratio * 100.0,
                    idle_decision.streak,
                    self.config.capture.photo_idle_consecutive_frames,
                )
                return False
            if idle_decision.compared:
                self.logger.debug(
                    "照片变化判定为活跃: 相似度 %.2f%%, 变化像素 %.2f%%, 连续相似帧 %s",
                    idle_decision.similarity * 100.0,
                    idle_decision.changed_ratio * 100.0,
                    idle_decision.streak,
                )
        try:
            self._write_runtime_state("llm")
            analysis = await self.llm.analyze_photo(photo_path)
            self.storage.insert_snapshot(
                captured_at=captured_at,
                screenshot_path=photo_path,
                summary=analysis.summary,
                detail=analysis.detail,
                confidence=analysis.confidence,
                raw_response=analysis.raw_response,
            )
            self._append_photo_valid_analysis_log(captured_at, photo_path, analysis.summary, analysis.detail)
            self.logger.info("完成照片分析: %s", analysis.summary)
            self.logger.info("照片 LLM 返回全文（长度=%s）:\n%s", len(analysis.detail or ""), analysis.detail or "")
            return True
        except Exception as exc:
            self.storage.insert_snapshot(
                captured_at=captured_at,
                screenshot_path=photo_path,
                summary="照片分析失败",
                detail=str(exc),
                confidence=0.0,
                raw_response={},
                error=str(exc),
            )
            self.logger.warning("照片分析失败，已跳过: %s", exc)
            self._write_runtime_state("error", detail=f"照片解读失败: {exc}")
            return False

    async def _email_loop(self):
        while not self._stop.is_set():
            wait_seconds = seconds_until(self.config.email.send_time, self.tz)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.send_daily_email()
            except Exception as exc:
                self.logger.error("邮件任务失败，已跳过本轮: %s", exc, exc_info=True)
                self._write_runtime_state("error", detail=f"邮件任务失败: {exc}", force=True)
                if self._stop.is_set():
                    break
                await asyncio.sleep(5)

    async def send_daily_email(self):
        today = datetime.now(self.tz)
        self._email_in_progress = True
        self._write_runtime_state("email", force=True)
        has_error = False
        self._ensure_valid_log_file(today)
        self._ensure_photo_valid_log_file(today)
        valid_log_path = self._daily_valid_log_path(today)
        photo_valid_log_path = self._daily_photo_valid_log_path(today)
        summary_path = self._daily_summary_md_path(today)
        try:
            summary_content = await self.llm.summarize_valid_log(valid_log_path, photo_valid_log_path)
        except Exception as exc:
            self.logger.exception("日报汇总失败: %s", exc)
            summary_content = f"日报汇总失败：{exc}"
            has_error = True
            self._write_runtime_state("error", detail=str(exc))
        await self._run_in_pool(self._write_text_file, summary_path, summary_content)
        try:
            await self._run_in_pool(
                self.emailer.send_daily_summary,
                today,
                summary_content,
                summary_path,
            )
        except Exception as exc:
            has_error = True
            self._write_runtime_state("error", detail=str(exc))
            raise
        finally:
            self._email_in_progress = False
        if not has_error:
            self._write_runtime_state("idle", force=True)

    async def shutdown(self):
        self._stop.set()
        self._write_runtime_state("idle", force=True)
        await self.close()

    async def close(self):
        """释放资源"""
        await self.llm.close()
        self.storage.close()
        self._executor.shutdown(wait=False)

    async def _run_in_pool(self, func: Callable[..., Any], *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, partial(func, *args, **kwargs)
        )

    @staticmethod
    def _recommend_workers() -> int:
        cpu_count = os.cpu_count() or 2
        return max(2, min(8, cpu_count))

    def _daily_valid_log_path(self, when: datetime) -> Path:
        stamp = when.strftime("%Y%m%d")
        return Path(self.config.storage.log_dir) / f"valid_screenshot_{stamp}.log"

    def _daily_summary_md_path(self, when: datetime) -> Path:
        stamp = when.strftime("%Y%m%d")
        return Path(self.config.storage.log_dir) / f"summary_{stamp}.md"

    def _daily_photo_valid_log_path(self, when: datetime) -> Path:
        stamp = when.strftime("%Y%m%d")
        return Path(self.config.storage.log_dir) / f"valid_photo_{stamp}.log"

    @staticmethod
    def _parse_hhmm(value: str) -> dtime:
        hour, minute = [int(part) for part in value.split(":", maxsplit=1)]
        return dtime(hour=hour, minute=minute)

    def _is_within_capture_window(self, now: datetime) -> bool:
        now_t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
        start = self.capture_start
        end = self.capture_end
        if start == end:
            return True
        if start < end:
            return start <= now_t <= end
        return now_t >= start or now_t <= end

    def _runtime_state_path(self) -> Path:
        return Path(self.config.storage.log_dir) / RUNTIME_STATE_FILE

    def _assert_capture_file_fresh(self, image_path: Path, capture_started_at: datetime, label: str) -> None:
        path = Path(image_path)
        if not path.exists():
            raise RuntimeError(f"{label}文件不存在: {path}")
        mtime = path.stat().st_mtime
        min_allowed = capture_started_at.timestamp() - 2.0
        max_allowed = datetime.now(self.tz).timestamp() + 2.0
        if mtime < min_allowed:
            raise RuntimeError(f"{label}文件时间戳过旧，疑似复用历史文件: {path.name}")
        if mtime > max_allowed:
            raise RuntimeError(f"{label}文件时间戳异常: {path.name}")

    def _write_runtime_state(self, state: str, detail: str | None = None, force: bool = False) -> None:
        if self._email_in_progress and state in {"capture", "llm", "idle"} and not force:
            return
        path = self._runtime_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": state,
            "updated_at": datetime.now(self.tz).isoformat(),
        }
        if detail:
            payload["detail"] = detail
        try:
            with path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False)
        except Exception as exc:
            self.logger.warning("写入运行状态失败 %s: %s", path, exc)

    def _append_valid_analysis_log(
        self,
        captured_at: datetime,
        screenshot_path: Path,
        summary: str,
        detail: str,
    ) -> None:
        if not self._is_valid_analysis(summary, detail):
            return
        log_path = self._daily_valid_log_path(captured_at)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "captured_at": captured_at.isoformat(),
            "screenshot_path": str(screenshot_path),
            "summary": summary,
            "detail": detail,
        }
        try:
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("写入有效解读日志失败 %s: %s", log_path, exc)

    def _append_photo_valid_analysis_log(
        self,
        captured_at: datetime,
        photo_path: Path,
        summary: str,
        detail: str,
    ) -> None:
        if not self._is_valid_analysis(summary, detail):
            return
        log_path = self._daily_photo_valid_log_path(captured_at)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "captured_at": captured_at.isoformat(),
            "photo_path": str(photo_path),
            "summary": summary,
            "detail": detail,
        }
        try:
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("写入照片有效解读日志失败 %s: %s", log_path, exc)

    @staticmethod
    def _is_valid_analysis(summary: str, detail: str) -> bool:
        return bool((summary or "").strip() and (detail or "").strip())

    def _ensure_valid_log_file(self, when: datetime) -> None:
        log_path = self._daily_valid_log_path(when)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

    def _ensure_photo_valid_log_file(self, when: datetime) -> None:
        log_path = self._daily_photo_valid_log_path(when)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

    @staticmethod
    def _write_text_file(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")
