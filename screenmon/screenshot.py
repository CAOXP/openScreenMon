from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import mss
from PIL import Image

from .config import CaptureConfig
from .utils import ensure_directory
import threading


class ScreenCapturer:
    def __init__(self, capture_cfg: CaptureConfig):
        self.cfg = capture_cfg
        self.logger = logging.getLogger("ScreenCapturer")
        self.output_dir = ensure_directory(self.cfg.screenshot_dir)
        self._mss_local = threading.local()
        self._monitor_indices = self._parse_monitor_config(self.cfg.monitors)

    def grab(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        extension = self.cfg.image_format.lower()
        suffix = ""
        client = self._get_mss()
        monitor_images = self._capture_monitors(client)
        composed_image: Optional[Image.Image] = None
        img: Optional[Image.Image] = None
        try:
            if len(monitor_images) > 1:
                suffix = f"_m{len(monitor_images)}"
                composed_image = self._combine_images(monitor_images)
                img = composed_image
            else:
                img = monitor_images[0]
            if img.width > self.cfg.max_width:
                ratio = self.cfg.max_width / img.width
                old_img = img
                resized = img.resize(
                    (int(img.width * ratio), int(img.height * ratio))
                )
                if monitor_images:
                    monitor_images = [
                        resized if current is old_img else current
                        for current in monitor_images
                    ]
                img = resized
                old_img.close()
            save_params = {}
            if self.cfg.image_format.upper() in {"JPEG", "JPG"}:
                save_params["quality"] = self.cfg.image_quality
                save_params["optimize"] = True
            filename = f"{timestamp}{suffix}.{extension}"
            path = self.output_dir / filename
            img.save(path, self.cfg.image_format.upper(), **save_params)
            self.logger.debug("截图已保存: %s", path)
            return path
        finally:
            if img is not None:
                img.close()
            if composed_image is not None and composed_image is not img:
                composed_image.close()
            for monitor_img in monitor_images:
                if monitor_img is not img:
                    monitor_img.close()

    def cleanup(self, retention_days: int):
        cutoff = datetime.now() - timedelta(days=retention_days)
        pattern = "*." + self.cfg.image_format.lower()
        for file in self.output_dir.glob(pattern):
            if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                try:
                    file.unlink()
                    self.logger.info("已删除过期截图: %s", file)
                except OSError as exc:  # pragma: no cover
                    self.logger.warning("删除截图失败 %s: %s", file, exc)

    def _get_mss(self) -> mss.mss:
        client = getattr(self._mss_local, "client", None)
        if client is None:
            self._mss_local.client = mss.mss()
            client = self._mss_local.client
        return client

    def _parse_monitor_config(self, monitors: str) -> Optional[List[int]]:
        if str(monitors).strip().lower() == "all" or monitors in {None, ""}:
            return None
        indices: List[int] = []
        for part in str(monitors).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                indices.append(int(part))
            except ValueError:
                self.logger.warning("无法解析显示器编号 %s，已忽略", part)
        return indices or None

    def _capture_monitors(self, client: mss.mss) -> List[Image.Image]:
        monitors = client.monitors
        if self._monitor_indices is None:
            return [self._capture_single(client, monitors[0])]
        images: List[Image.Image] = []
        for idx in self._monitor_indices:
            if idx <= 0:
                images.append(self._capture_single(client, monitors[0]))
                break
            if idx >= len(monitors):
                self.logger.warning("显示器索引 %s 超出范围，将跳过", idx)
                continue
            images.append(self._capture_single(client, monitors[idx]))
        return images or [self._capture_single(client, monitors[0])]

    def _capture_single(self, client: mss.mss, monitor_conf) -> Image.Image:
        raw = client.grab(monitor_conf)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def _combine_images(self, images: List[Image.Image]) -> Image.Image:
        total_width = sum(img.width for img in images)
        max_height = max(img.height for img in images)
        canvas = Image.new("RGB", (total_width, max_height), color="#000000")
        offset = 0
        for img in images:
            canvas.paste(img, (offset, 0))
            offset += img.width
        return canvas


class CameraCapturer:
    def __init__(self, capture_cfg: CaptureConfig):
        self.cfg = capture_cfg
        self.logger = logging.getLogger("CameraCapturer")
        self.output_dir = ensure_directory(self.cfg.photo_dir)
        self._default_camera_index = self._parse_default_camera(self.cfg.default_camera)

    def grab(self) -> Path | None:
        camera_indices = self._camera_probe_order()
        frame: Image.Image | None = None
        used_index: int | None = None
        for index in camera_indices:
            frame = self._read_frame(index)
            if frame is not None:
                used_index = index
                break
        if frame is None:
            self.logger.info("未检测到可用摄像头，本次跳过照片采集")
            return None
        try:
            if frame.width > self.cfg.max_width:
                ratio = self.cfg.max_width / frame.width
                resized = frame.resize((int(frame.width * ratio), int(frame.height * ratio)))
                frame.close()
                frame = resized
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            extension = self.cfg.image_format.lower()
            suffix = f"_cam{used_index}" if used_index is not None else "_cam"
            file_path = self.output_dir / f"{timestamp}{suffix}.{extension}"
            save_params = {}
            if self.cfg.image_format.upper() in {"JPEG", "JPG"}:
                save_params["quality"] = self.cfg.image_quality
                save_params["optimize"] = True
            frame.save(file_path, self.cfg.image_format.upper(), **save_params)
            self.logger.debug("摄像头照片已保存: %s", file_path)
            return file_path
        finally:
            frame.close()

    def cleanup(self, retention_days: int):
        cutoff = datetime.now() - timedelta(days=retention_days)
        pattern = "*." + self.cfg.image_format.lower()
        for file in self.output_dir.glob(pattern):
            if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                try:
                    file.unlink()
                    self.logger.info("已删除过期照片: %s", file)
                except OSError as exc:
                    self.logger.warning("删除照片失败 %s: %s", file, exc)

    def _read_frame(self, camera_index: int) -> Image.Image | None:
        try:
            import cv2  # type: ignore
        except Exception:
            self.logger.info("未安装 OpenCV，跳过照片采集")
            return None
        backend = getattr(cv2, "CAP_DSHOW", None) if hasattr(cv2, "CAP_DSHOW") else None
        cap = cv2.VideoCapture(camera_index, backend) if backend is not None else cv2.VideoCapture(camera_index)
        try:
            if not cap or not cap.isOpened():
                return None
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            frame = None
            for _ in range(4):
                ok, current = cap.read()
                if ok and current is not None:
                    frame = current
            if frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        except Exception as exc:
            self.logger.warning("读取摄像头画面失败(index=%s): %s", camera_index, exc)
            return None
        finally:
            if cap:
                cap.release()

    def _camera_probe_order(self) -> list[int]:
        if self._default_camera_index is not None:
            return [self._default_camera_index]
        return list(range(0, 6))

    @staticmethod
    def _parse_default_camera(raw: str) -> int | None:
        value = (raw or "").strip().lower()
        if value == "auto" or not value:
            return None
        if value.isdigit():
            return int(value)
        return None
