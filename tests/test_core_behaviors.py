from __future__ import annotations

import unittest
from datetime import datetime, time as dtime
from pathlib import Path
import shutil
from contextlib import contextmanager
from uuid import uuid4

import yaml

from screenmon.app import ScreenMonApp
from screenmon.config import AppConfig, CaptureConfig, dump_config_for_storage, load_config


@contextmanager
def workspace_tempdir():
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    created = root / f"cfg_{uuid4().hex}"
    created.mkdir(parents=True, exist_ok=False)
    probe = created / ".write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


class ConfigPathBehaviorTests(unittest.TestCase):
    def test_dump_config_prefers_relative_paths(self):
        with workspace_tempdir() as run_dir:
            cfg = AppConfig()
            cfg.resolve_paths(run_dir)
            raw = dump_config_for_storage(cfg, runtime_dir=run_dir)
            self.assertEqual(raw["capture"]["screenshot_dir"], "data/screenshots")
            self.assertEqual(raw["capture"]["photo_dir"], "data/photo")
            self.assertEqual(raw["storage"]["log_dir"], "data/logs")
            self.assertEqual(raw["storage"]["database_path"], "data/monitor.db")

    def test_load_config_autofixes_invalid_output_paths(self):
        with workspace_tempdir() as run_dir:
            cfg_path = run_dir / "config.yaml"
            raw = {
                "capture": {
                    "screenshot_dir": "C:/<>bad/screenshots",
                    "photo_dir": "C:/<>bad/photo",
                },
                "storage": {
                    "log_dir": "C:/<>bad/logs",
                    "database_path": "C:/<>bad/db/monitor.db",
                },
            }
            cfg_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

            cfg = load_config(cfg_path, runtime_dir=run_dir)

            self.assertEqual(Path(cfg.capture.screenshot_dir), run_dir / "data/screenshots")
            self.assertEqual(Path(cfg.capture.photo_dir), run_dir / "data/photo")
            self.assertEqual(Path(cfg.storage.log_dir), run_dir / "data/logs")
            self.assertEqual(Path(cfg.storage.database_path), run_dir / "data/monitor.db")

            saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            self.assertEqual(saved["capture"]["screenshot_dir"], "data/screenshots")
            self.assertEqual(saved["capture"]["photo_dir"], "data/photo")
            self.assertEqual(saved["storage"]["log_dir"], "data/logs")
            self.assertEqual(saved["storage"]["database_path"], "data/monitor.db")


class CaptureWindowTests(unittest.TestCase):
    def test_capture_window_normal_range(self):
        app = ScreenMonApp.__new__(ScreenMonApp)
        app.capture_start = dtime(9, 0)
        app.capture_end = dtime(18, 0)
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 1, 9, 0)))
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 1, 12, 30)))
        self.assertFalse(app._is_within_capture_window(datetime(2026, 3, 1, 8, 59)))
        self.assertFalse(app._is_within_capture_window(datetime(2026, 3, 1, 18, 1)))

    def test_capture_window_cross_day(self):
        app = ScreenMonApp.__new__(ScreenMonApp)
        app.capture_start = dtime(22, 0)
        app.capture_end = dtime(6, 0)
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 1, 23, 0)))
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 2, 5, 30)))
        self.assertFalse(app._is_within_capture_window(datetime(2026, 3, 1, 12, 0)))

    def test_capture_window_full_day_when_equal(self):
        app = ScreenMonApp.__new__(ScreenMonApp)
        app.capture_start = dtime(0, 0)
        app.capture_end = dtime(0, 0)
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 1, 0, 0)))
        self.assertTrue(app._is_within_capture_window(datetime(2026, 3, 1, 13, 45)))

    def test_capture_config_time_validation(self):
        cfg = CaptureConfig(monitor_start_time="9:7", monitor_end_time="23:59")
        self.assertEqual(cfg.monitor_start_time, "09:07")
        with self.assertRaises(ValueError):
            CaptureConfig(monitor_start_time="24:00", monitor_end_time="23:59")

    def test_capture_config_default_camera_validation(self):
        self.assertEqual(CaptureConfig(default_camera="").default_camera, "auto")
        self.assertEqual(CaptureConfig(default_camera="AUTO").default_camera, "auto")
        self.assertEqual(CaptureConfig(default_camera="03").default_camera, "3")
        with self.assertRaises(ValueError):
            CaptureConfig(default_camera="camera-a")

    def test_capture_config_photo_idle_migrates_from_screenshot_idle(self):
        cfg = CaptureConfig(
            idle_skip_enabled=False,
            idle_similarity_percent=77,
            idle_changed_percent=3,
            idle_consecutive_frames=2,
            idle_diff_pixel_threshold=15,
            idle_compare_width=480,
            idle_compare_height=270,
        )
        self.assertFalse(cfg.photo_idle_skip_enabled)
        self.assertEqual(cfg.photo_idle_similarity_percent, 77)
        self.assertEqual(cfg.photo_idle_changed_percent, 3)
        self.assertEqual(cfg.photo_idle_consecutive_frames, 2)
        self.assertEqual(cfg.photo_idle_diff_pixel_threshold, 15)
        self.assertEqual(cfg.photo_idle_compare_width, 480)
        self.assertEqual(cfg.photo_idle_compare_height, 270)

    def test_capture_config_photo_idle_can_be_set_independently(self):
        cfg = CaptureConfig(
            idle_similarity_percent=80,
            photo_idle_similarity_percent=92,
            photo_idle_changed_percent=4,
        )
        self.assertEqual(cfg.idle_similarity_percent, 80)
        self.assertEqual(cfg.photo_idle_similarity_percent, 92)
        self.assertEqual(cfg.photo_idle_changed_percent, 4)

    def test_capture_config_photo_dir_defaults_to_screenshot_sibling_for_legacy(self):
        cfg = CaptureConfig.model_validate({"screenshot_dir": "records/screens"})
        self.assertEqual(Path(cfg.photo_dir), Path("records/photo"))


if __name__ == "__main__":
    unittest.main()
