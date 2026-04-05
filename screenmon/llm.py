from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import io

import httpx
from PIL import Image

from .config import LLMConfig, RetryConfig


@dataclass
class AnalysisResult:
    summary: str
    confidence: float
    detail: str
    raw_response: Dict[str, Any]


class LLMAnalyzer:
    OPENAI_COMPATIBLE_PROVIDERS = {
        "openai",
        "openai-compatible",
        "qwen",
        "dashscope",
        "aliyun",
        "aliyun-dashscope",
    }

    def __init__(self, cfg: LLMConfig, retry: RetryConfig):
        self.cfg = cfg
        self.retry = retry
        self.logger = logging.getLogger("LLMAnalyzer")
        self._client: httpx.AsyncClient | None = None

    async def analyze(self, image_path: Path) -> AnalysisResult:
        return await self._analyze_image(image_path, use_photo_prompt=False)

    async def analyze_photo(self, image_path: Path) -> AnalysisResult:
        return await self._analyze_image(image_path, use_photo_prompt=True)

    async def _analyze_image(self, image_path: Path, use_photo_prompt: bool) -> AnalysisResult:
        attempt_limit = self.cfg.max_retries or self.retry.max_attempts
        provider = self.cfg.provider.lower().strip()
        for attempt in range(1, attempt_limit + 1):
            try:
                if provider == "mock":
                    return await asyncio.to_thread(self._mock_analysis, image_path)
                if provider in self.OPENAI_COMPATIBLE_PROVIDERS:
                    return await self._openai_analysis(image_path, use_photo_prompt=use_photo_prompt)
                raise ValueError(f"暂不支持的 provider: {self.cfg.provider}")
            except Exception as exc:
                self.logger.warning("第 %s 次大模型分析失败: %s", attempt, exc)
                if attempt >= attempt_limit:
                    raise
                await asyncio.sleep(self.retry.backoff_seconds * attempt)
        raise RuntimeError("分析失败")

    async def summarize_valid_log(self, log_path: Path, photo_log_path: Path | None = None) -> str:
        attempt_limit = self.cfg.max_retries or self.retry.max_attempts
        provider = self.cfg.provider.lower().strip()
        log_text = await asyncio.to_thread(self._read_valid_log_text, log_path)
        photo_log_text = ""
        if photo_log_path is not None:
            photo_log_text = await asyncio.to_thread(self._read_valid_log_text, photo_log_path)
        merged_text = self._merge_daily_log_text(log_text, photo_log_text)
        if not merged_text.strip():
            return "今日没有可汇总的有效解读记录。"
        for attempt in range(1, attempt_limit + 1):
            try:
                if provider == "mock":
                    return await asyncio.to_thread(self._mock_daily_summary, merged_text)
                if provider in self.OPENAI_COMPATIBLE_PROVIDERS:
                    return await self._openai_text_summary(merged_text)
                raise ValueError(f"暂不支持的 provider: {self.cfg.provider}")
            except Exception as exc:
                self.logger.warning("第 %s 次日报汇总失败: %s", attempt, exc)
                if attempt >= attempt_limit:
                    raise
                await asyncio.sleep(self.retry.backoff_seconds * attempt)
        raise RuntimeError("日报汇总失败")

    def _merge_daily_log_text(self, screenshot_log_text: str, photo_log_text: str) -> str:
        lines: list[str] = []
        for source, text in (("screenshot", screenshot_log_text), ("photo", photo_log_text)):
            for line in text.splitlines():
                row = line.strip()
                if not row:
                    continue
                try:
                    payload = json.loads(row)
                except json.JSONDecodeError:
                    continue
                compact_item = {
                    "source": source,
                    "captured_at": payload.get("captured_at"),
                    "summary": str(payload.get("summary", "")).strip(),
                    "detail_excerpt": str(payload.get("detail", "")).strip()[:180],
                }
                lines.append(json.dumps(compact_item, ensure_ascii=False))
        return "\n".join(lines)

    async def _openai_analysis(self, image_path: Path, use_photo_prompt: bool) -> AnalysisResult:
        if not self.cfg.api_key:
            raise RuntimeError("必须提供 API Key 才能调用 OpenAI")
        client = self._client or httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
        self._client = client
        image_b64 = self._encode_image_as_jpeg(image_path)
        model = self._normalize_model_name(self.cfg.model)
        payload = {
            "model": model,
            "max_tokens": self.cfg.max_tokens,
            "messages": self._build_messages(image_b64, use_photo_prompt=use_photo_prompt),
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        url = self._build_chat_completions_url()
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "").strip()
            if exc.response.status_code == 404:
                raise RuntimeError(
                    "DashScope/OpenAI 兼容接口返回 404。请检查："
                    "1) api_base 建议为 https://dashscope.aliyuncs.com/compatible-mode/v1；"
                    "2) 视觉模型名应为 qwen-vl-plus（不是 gqwen-vl-plus）；"
                    "3) 若使用国际站账号，尝试 https://dashscope-intl.aliyuncs.com/compatible-mode/v1。"
                    f" 当前请求 URL: {url}；响应: {detail[:300]}"
                ) from exc
            raise RuntimeError(
                f"LLM 请求失败，HTTP {exc.response.status_code}，URL: {url}，响应: {detail[:300]}"
            ) from exc
        data = response.json()
        content = self._extract_chat_content(data)
        return AnalysisResult(
            summary=content.splitlines()[0],
            confidence=0.8,
            detail=content,
            raw_response=data,
        )

    async def _openai_text_summary(self, log_text: str) -> str:
        if not self.cfg.api_key:
            raise RuntimeError("必须提供 API Key 才能调用 OpenAI")
        client = self._client or httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
        self._client = client
        model = self._normalize_model_name(self.cfg.model)
        payload = {
            "model": model,
            "max_tokens": self.cfg.max_tokens,
            "messages": self._build_summary_messages(log_text),
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        url = self._build_chat_completions_url()
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "").strip()
            raise RuntimeError(
                f"LLM 汇总请求失败，HTTP {exc.response.status_code}，URL: {url}，响应: {detail[:300]}"
            ) from exc
        data = response.json()
        return self._extract_chat_content(data)

    def _mock_analysis(self, image_path: Path) -> AnalysisResult:
        digest = hashlib.sha1(image_path.read_bytes()).hexdigest()
        hints = [
            "浏览器研究资料",
            "撰写文档",
            "会议沟通",
            "编程调试",
            "阅读报告",
        ]
        summary = f"推测当前在{hints[int(digest, 16) % len(hints)]}"
        return AnalysisResult(
            summary=summary,
            confidence=0.42,
            detail=f"Mock 模式：根据文件哈希 {digest[:8]} 给出的随机描述。",
            raw_response={"mode": "mock", "digest": digest},
        )

    def _mock_daily_summary(self, log_text: str) -> str:
        items = []
        for line in log_text.splitlines():
            row = line.strip()
            if not row:
                continue
            try:
                items.append(json.loads(row))
            except json.JSONDecodeError:
                continue
        if not items:
            return "今日没有可汇总的有效解读记录。"
        summaries: Dict[str, int] = {}
        source_counter: Dict[str, int] = {"screenshot": 0, "photo": 0}
        for item in items:
            summary = str(item.get("summary", "")).strip()
            if summary:
                summaries[summary] = summaries.get(summary, 0) + 1
            source = str(item.get("source", "")).strip()
            if source in source_counter:
                source_counter[source] += 1
        top_topics = sorted(summaries.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [
            "### 活动摘要（Mock）",
            f"- 总记录 {len(items)} 条（截图 {source_counter['screenshot']}，照片 {source_counter['photo']}）",
            f"- 时间范围：{items[0].get('captured_at', '-')} ~ {items[-1].get('captured_at', '-')}",
            "",
            "### 关键活动",
        ]
        if top_topics:
            for topic, count in top_topics[:3]:
                lines.append(f"- {topic}（{count} 次）")
        else:
            lines.append("- 无可统计主题")
        return "\n".join(lines)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _encode_image_as_jpeg(self, image_path: Path) -> str:
        with Image.open(image_path) as raw_img:
            img = raw_img.convert("RGB")
            try:
                with io.BytesIO() as buffer:
                    img.save(buffer, format="JPEG", quality=80, optimize=True)
                    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
            finally:
                img.close()
        return encoded

    def _extract_chat_content(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 返回内容为空: 缺少 choices")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            text = content.strip()
            return text or "未返回文本内容"
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    value = str(part.get("text", "")).strip()
                    if value:
                        text_parts.append(value)
            if text_parts:
                return "\n".join(text_parts)
        raise RuntimeError("LLM 返回结构无法解析为文本")

    def _build_chat_completions_url(self) -> str:
        base = self.cfg.api_base.rstrip("/")
        provider = self.cfg.provider.lower().strip()
        if provider in {"qwen", "dashscope", "aliyun", "aliyun-dashscope"}:
            if "/compatible-mode" in base and not base.endswith("/v1"):
                base = f"{base}/v1"
        return f"{base}/chat/completions"

    def _normalize_model_name(self, model: str) -> str:
        normalized = model.strip()
        if normalized.startswith("gqwen-"):
            fixed = normalized[1:]
            self.logger.warning("检测到模型名可能拼写错误，已自动改为: %s", fixed)
            return fixed
        return normalized

    def _build_messages(self, image_b64: str, use_photo_prompt: bool) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        prompt = (
            (self.cfg.photo_prompt2 if use_photo_prompt else self.cfg.screenshot_prompt1)
            or ""
        ).strip()
        if prompt:
            messages.append(
                {
                    "role": "system",
                    "content": prompt,
                }
            )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                ],
            }
        )
        return messages

    def _build_summary_messages(self, log_text: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        prompt = (self.cfg.log_analysis_prompt3 or "").strip()
        if prompt:
            messages.append({"role": "system", "content": prompt})
        messages.append(
            {
                "role": "system",
                "content": (
                    "请输出凝练中文摘要，控制在 6 行内。"
                    "必须包含：1) 今日主要工作主题；2) 关键进展；3) 风险与下一步。"
                    "日志中的 source=screenshot 代表屏幕解读，source=photo 代表照片解读。"
                ),
            }
        )
        messages.append({"role": "user", "content": f"以下为当日活动日志（已合并截图与照片）：\n{log_text}"})
        return messages

    def _read_valid_log_text(self, log_path: Path) -> str:
        if not log_path.exists():
            return ""
        content = log_path.read_text(encoding="utf-8").strip()
        max_chars = 120000
        if len(content) > max_chars:
            self.logger.warning("有效解读日志过长(%s)，已截断后汇总", len(content))
            content = content[-max_chars:]
        return content
