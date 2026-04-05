from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .config import CaptureConfig


@dataclass(frozen=True)
class ActivityDecision:
    idle: bool
    compared: bool
    similarity: float
    changed_ratio: float
    streak: int


@dataclass(frozen=True)
class _DiffMetrics:
    similarity: float
    changed_ratio: float


class ScreenIdleDetector:
    # Exclude clock/notification area at the bottom-right by default.
    _DEFAULT_MASK_REGIONS = (
        (0.78, 0.88, 1.0, 1.0),
    )

    def __init__(self, cfg: CaptureConfig, profile: str = "idle"):
        prefix = "photo_idle_" if profile == "photo_idle" else "idle_"
        self.enabled = bool(getattr(cfg, f"{prefix}skip_enabled"))
        self.similarity_threshold = getattr(cfg, f"{prefix}similarity_percent") / 100.0
        self.changed_ratio_threshold = getattr(cfg, f"{prefix}changed_percent") / 100.0
        self.consecutive_frames = getattr(cfg, f"{prefix}consecutive_frames")
        self.diff_pixel_threshold = getattr(cfg, f"{prefix}diff_pixel_threshold")
        self.sample_size = (
            getattr(cfg, f"{prefix}compare_width"),
            getattr(cfg, f"{prefix}compare_height"),
        )
        self._last_path: Path | None = None
        self._similar_streak = 0
        self._mask_cache: dict[tuple[int, int], Image.Image] = {}

    def evaluate(self, current_path: Path) -> ActivityDecision:
        previous_path = self._last_path
        self._last_path = current_path

        if not self.enabled:
            self._similar_streak = 0
            return ActivityDecision(idle=False, compared=False, similarity=0.0, changed_ratio=1.0, streak=0)
        if previous_path is None or not previous_path.exists() or not current_path.exists():
            self._similar_streak = 0
            return ActivityDecision(idle=False, compared=False, similarity=0.0, changed_ratio=1.0, streak=0)

        try:
            metrics = self._diff(previous_path, current_path)
        except Exception:
            self._similar_streak = 0
            return ActivityDecision(idle=False, compared=False, similarity=0.0, changed_ratio=1.0, streak=0)

        similar = (
            metrics.similarity >= self.similarity_threshold
            and metrics.changed_ratio <= self.changed_ratio_threshold
        )
        if similar:
            self._similar_streak += 1
        else:
            self._similar_streak = 0
        idle = similar and self._similar_streak >= self.consecutive_frames
        return ActivityDecision(
            idle=idle,
            compared=True,
            similarity=metrics.similarity,
            changed_ratio=metrics.changed_ratio,
            streak=self._similar_streak,
        )

    def _diff(self, previous_path: Path, current_path: Path) -> _DiffMetrics:
        previous = self._load_gray(previous_path)
        current = self._load_gray(current_path)
        try:
            diff = ImageChops.difference(previous, current)
            mask = self._build_mask(diff.size)
            histogram = diff.histogram(mask=mask)
            valid_pixels = sum(histogram)
            if valid_pixels <= 0:
                return _DiffMetrics(similarity=0.0, changed_ratio=1.0)
            diff_sum = sum(level * count for level, count in enumerate(histogram))
            changed_pixels = sum(histogram[self.diff_pixel_threshold :])
            similarity = 1.0 - (diff_sum / (255.0 * valid_pixels))
            if similarity < 0.0:
                similarity = 0.0
            changed_ratio = changed_pixels / valid_pixels
            return _DiffMetrics(similarity=similarity, changed_ratio=changed_ratio)
        finally:
            previous.close()
            current.close()

    def _load_gray(self, image_path: Path) -> Image.Image:
        with Image.open(image_path) as raw:
            converted = raw.convert("L")
        return converted.resize(self.sample_size, Image.Resampling.BILINEAR)

    def _build_mask(self, size: tuple[int, int]) -> Image.Image:
        cached = self._mask_cache.get(size)
        if cached is not None:
            return cached
        width, height = size
        mask = Image.new("L", size, 255)
        drawer = ImageDraw.Draw(mask)
        for x1_r, y1_r, x2_r, y2_r in self._DEFAULT_MASK_REGIONS:
            x1 = max(0, min(width - 1, int(width * x1_r)))
            y1 = max(0, min(height - 1, int(height * y1_r)))
            x2 = max(0, min(width - 1, int(width * x2_r)))
            y2 = max(0, min(height - 1, int(height * y2_r)))
            if x2 <= x1 or y2 <= y1:
                continue
            drawer.rectangle((x1, y1, x2, y2), fill=0)
        self._mask_cache[size] = mask
        return mask
