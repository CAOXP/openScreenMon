from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from screenmon.activity import ScreenIdleDetector
from screenmon.config import CaptureConfig


class IdleDetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path.cwd() / "data" / "test_tmp" / f"idle_{uuid4().hex}"
        self.tmp_dir.mkdir(parents=True, exist_ok=False)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _save_image(self, name: str, draw_fn=None) -> Path:
        img = Image.new("RGB", (1280, 720), color="#F0F0F0")
        draw = ImageDraw.Draw(img)
        draw.rectangle((80, 80, 420, 260), fill="#333333")
        draw.rectangle((500, 120, 1000, 620), fill="#5A7DB2")
        if draw_fn:
            draw_fn(draw)
        path = self.tmp_dir / name
        img.save(path, "PNG")
        img.close()
        return path

    def test_masked_clock_region_can_be_treated_as_idle(self):
        cfg = CaptureConfig(
            idle_skip_enabled=True,
            idle_similarity_percent=98,
            idle_changed_percent=1,
            idle_consecutive_frames=1,
            idle_diff_pixel_threshold=20,
            idle_compare_width=320,
            idle_compare_height=180,
        )
        detector = ScreenIdleDetector(cfg)
        first = self._save_image("first.png")
        second = self._save_image(
            "second.png",
            draw_fn=lambda draw: draw.rectangle((1140, 670, 1275, 715), fill="#AA1111"),
        )
        cold_start = detector.evaluate(first)
        result = detector.evaluate(second)
        self.assertFalse(cold_start.compared)
        self.assertTrue(result.compared)
        self.assertTrue(result.idle)
        self.assertGreaterEqual(result.similarity, 0.98)
        self.assertLessEqual(result.changed_ratio, 0.01)

    def test_large_screen_change_should_not_be_idle(self):
        cfg = CaptureConfig(
            idle_skip_enabled=True,
            idle_similarity_percent=98,
            idle_changed_percent=1,
            idle_consecutive_frames=1,
            idle_diff_pixel_threshold=20,
            idle_compare_width=320,
            idle_compare_height=180,
        )
        detector = ScreenIdleDetector(cfg)
        first = self._save_image("first.png")
        changed = self._save_image(
            "changed.png",
            draw_fn=lambda draw: draw.rectangle((0, 0, 900, 540), fill="#11AA55"),
        )
        detector.evaluate(first)
        result = detector.evaluate(changed)
        self.assertTrue(result.compared)
        self.assertFalse(result.idle)
        self.assertLess(result.similarity, 0.98)
        self.assertGreater(result.changed_ratio, 0.01)

    def test_requires_configured_consecutive_frames(self):
        cfg = CaptureConfig(
            idle_skip_enabled=True,
            idle_similarity_percent=98,
            idle_changed_percent=1,
            idle_consecutive_frames=2,
            idle_diff_pixel_threshold=20,
            idle_compare_width=320,
            idle_compare_height=180,
        )
        detector = ScreenIdleDetector(cfg)
        img1 = self._save_image("img1.png")
        img2 = self._save_image("img2.png")
        img3 = self._save_image("img3.png")
        first = detector.evaluate(img1)
        second = detector.evaluate(img2)
        third = detector.evaluate(img3)
        self.assertFalse(first.idle)
        self.assertFalse(second.idle)
        self.assertEqual(second.streak, 1)
        self.assertTrue(third.idle)
        self.assertEqual(third.streak, 2)

    def test_photo_profile_uses_photo_idle_fields(self):
        cfg = CaptureConfig(
            idle_skip_enabled=False,
            idle_similarity_percent=99,
            idle_changed_percent=1,
            idle_consecutive_frames=3,
            idle_diff_pixel_threshold=20,
            idle_compare_width=320,
            idle_compare_height=180,
            photo_idle_skip_enabled=True,
            photo_idle_similarity_percent=98,
            photo_idle_changed_percent=1,
            photo_idle_consecutive_frames=1,
            photo_idle_diff_pixel_threshold=20,
            photo_idle_compare_width=320,
            photo_idle_compare_height=180,
        )
        detector = ScreenIdleDetector(cfg, profile="photo_idle")
        first = self._save_image("photo_first.png")
        second = self._save_image("photo_second.png")
        detector.evaluate(first)
        result = detector.evaluate(second)
        self.assertTrue(result.compared)
        self.assertTrue(result.idle)


if __name__ == "__main__":
    unittest.main()
