
import asyncio
import json
import logging
import os
import shutil
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from screenmon.app import ScreenMonApp
from screenmon.config import AppConfig, CaptureConfig, LLMConfig, StorageConfig
from screenmon.llm import AnalysisResult

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        # Reset logging
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
            
        self.test_dir = Path.cwd() / "data" / "test_integration" / uuid4().hex
        self.test_dir.mkdir(parents=True, exist_ok=True)
        
        self.config = AppConfig(
            capture=CaptureConfig(
                screenshot_dir=str(self.test_dir / "screenshots"),
                interval_seconds=10,
                monitor_start_time="00:00",
                monitor_end_time="23:59",
                idle_skip_enabled=False  # Disable idle detection to force LLM call
            ),
            storage=StorageConfig(
                database_path=str(self.test_dir / "monitor.db"),
                log_dir=str(self.test_dir / "logs")
            ),
            llm=LLMConfig(
                provider="mock",
                api_key="sk-test"
            )
        )
        # Ensure paths are absolute
        self.config.resolve_paths(self.test_dir)

    def tearDown(self):
        # Close logging handlers to release file locks
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
            
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def _run_app_once(self):
        app = ScreenMonApp(self.config)
        
        # Mock dependencies to avoid external calls and UI interactions
        app.capturer.grab = MagicMock(return_value=self.test_dir / "screenshots" / "test.png")
        # Ensure screenshot file exists
        (self.test_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "screenshots" / "test.png").touch()
        (self.test_dir / "photo").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "photo" / "test_cam0.png").touch()
        
        # Mock idle detector to always return "active"
        mock_idle_decision = MagicMock()
        mock_idle_decision.idle = False
        mock_idle_decision.compared = True
        mock_idle_decision.similarity = 0.5
        mock_idle_decision.changed_ratio = 0.5
        mock_idle_decision.streak = 0
        app.idle_detector.evaluate = MagicMock(return_value=mock_idle_decision)

        # Mock LLM to avoid actual API calls (even mock provider might be slow or depend on file hash)
        mock_analysis = AnalysisResult(
            summary="Integration Test Summary",
            confidence=0.9,
            detail="Integration Test Detail",
            raw_response={"test": "data"}
        )
        app.llm.analyze = AsyncMock(return_value=mock_analysis)
        app.camera_capturer.grab = MagicMock(return_value=self.test_dir / "photo" / "test_cam0.png")
        app.llm.analyze_photo = AsyncMock(
            return_value=AnalysisResult(
                summary="Photo Integration Summary",
                confidence=0.88,
                detail="Photo Integration Detail",
                raw_response={"photo": "data"},
            )
        )

        try:
            await app.run(run_once=True)
        finally:
            # Manually close resources since app.run doesn't do it fully yet
            await app.llm.close()
            app.storage.close()
            app._executor.shutdown(wait=False)

    def test_full_cycle(self):
        asyncio.run(self._run_app_once())
        
        # Verify Database
        import sqlite3
        conn = sqlite3.connect(self.config.storage.database_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM snapshots")
        rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 2, "Should have screenshot and photo snapshots in database")
        summaries = {row[3] for row in rows}
        self.assertIn("Integration Test Summary", summaries)
        self.assertIn("Photo Integration Summary", summaries)

        # Verify Log File
        log_dir = Path(self.config.storage.log_dir)
        log_files = list(log_dir.glob("valid_screenshot_*.log"))
        self.assertTrue(len(log_files) > 0, "Should have created a log file")
        
        content = log_files[0].read_text(encoding="utf-8")
        self.assertIn("Integration Test Summary", content)
        photo_log_files = list(log_dir.glob("valid_photo_*.log"))
        self.assertTrue(len(photo_log_files) > 0, "Should have created a photo log file")
        photo_content = photo_log_files[0].read_text(encoding="utf-8")
        self.assertIn("Photo Integration Summary", photo_content)

    def test_capture_once_skips_photo_when_camera_missing(self):
        app = ScreenMonApp(self.config)
        app.capturer.grab = MagicMock(return_value=self.test_dir / "screenshots" / "test.png")
        (self.test_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "screenshots" / "test.png").touch()
        mock_idle = MagicMock()
        mock_idle.idle = False
        mock_idle.compared = True
        mock_idle.similarity = 0.5
        mock_idle.changed_ratio = 0.5
        mock_idle.streak = 0
        app.idle_detector.evaluate = MagicMock(return_value=mock_idle)
        app.llm.analyze = AsyncMock(
            return_value=AnalysisResult(
                summary="Only Screenshot",
                confidence=0.9,
                detail="Only Screenshot Detail",
                raw_response={},
            )
        )
        app.camera_capturer.grab = MagicMock(return_value=None)
        app.llm.analyze_photo = AsyncMock()

        async def run_case():
            try:
                result = await app.capture_once()
                self.assertTrue(result)
            finally:
                await app.shutdown()

        asyncio.run(run_case())
        app.llm.analyze_photo.assert_not_called()

    def test_capture_once_skips_photo_llm_when_photo_is_highly_similar(self):
        app = ScreenMonApp(self.config)
        screenshot_path = self.test_dir / "screenshots" / "test.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.touch()
        photo_path = self.test_dir / "photo" / "test_cam0.png"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.touch()

        app.capturer.grab = MagicMock(return_value=screenshot_path)
        screen_idle = MagicMock()
        screen_idle.idle = False
        screen_idle.compared = True
        screen_idle.similarity = 0.6
        screen_idle.changed_ratio = 0.4
        screen_idle.streak = 0
        app.idle_detector.evaluate = MagicMock(return_value=screen_idle)
        app.llm.analyze = AsyncMock(
            return_value=AnalysisResult(
                summary="Screenshot Active",
                confidence=0.92,
                detail="Screenshot Active Detail",
                raw_response={},
            )
        )
        app.camera_capturer.grab = MagicMock(return_value=photo_path)
        photo_idle = MagicMock()
        photo_idle.idle = True
        photo_idle.compared = True
        photo_idle.similarity = 0.995
        photo_idle.changed_ratio = 0.002
        photo_idle.streak = 1
        app.photo_idle_detector.evaluate = MagicMock(return_value=photo_idle)
        app.llm.analyze_photo = AsyncMock()

        async def run_case():
            try:
                result = await app.capture_once()
                self.assertTrue(result)
            finally:
                await app.shutdown()

        asyncio.run(run_case())
        app.photo_idle_detector.evaluate.assert_called_once()
        app.llm.analyze_photo.assert_not_called()

    def test_capture_once_rejects_stale_screenshot_file(self):
        app = ScreenMonApp(self.config)
        stale_screenshot = self.test_dir / "screenshots" / "stale.png"
        stale_screenshot.parent.mkdir(parents=True, exist_ok=True)
        stale_screenshot.touch()
        stale_time = datetime.now().timestamp() - 180
        os.utime(stale_screenshot, (stale_time, stale_time))
        app.capturer.grab = MagicMock(return_value=stale_screenshot)
        app.idle_detector.evaluate = MagicMock()
        app.llm.analyze = AsyncMock()
        app.camera_capturer.grab = MagicMock(return_value=None)
        app.llm.analyze_photo = AsyncMock()

        async def run_case():
            try:
                result = await app.capture_once()
                self.assertFalse(result)
            finally:
                await app.shutdown()

        asyncio.run(run_case())
        app.llm.analyze.assert_not_called()

    def test_capture_once_rejects_stale_photo_file(self):
        app = ScreenMonApp(self.config)
        screenshot_path = self.test_dir / "screenshots" / "fresh.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.touch()
        photo_path = self.test_dir / "photo" / "stale_cam0.png"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.touch()
        stale_time = datetime.now().timestamp() - 180
        os.utime(photo_path, (stale_time, stale_time))

        app.capturer.grab = MagicMock(return_value=screenshot_path)
        mock_idle = MagicMock()
        mock_idle.idle = False
        mock_idle.compared = True
        mock_idle.similarity = 0.5
        mock_idle.changed_ratio = 0.5
        mock_idle.streak = 0
        app.idle_detector.evaluate = MagicMock(return_value=mock_idle)
        app.llm.analyze = AsyncMock(
            return_value=AnalysisResult(
                summary="Fresh Screenshot",
                confidence=0.9,
                detail="Fresh Screenshot Detail",
                raw_response={},
            )
        )
        app.camera_capturer.grab = MagicMock(return_value=photo_path)
        app.llm.analyze_photo = AsyncMock()

        async def run_case():
            try:
                result = await app.capture_once()
                self.assertTrue(result)
            finally:
                await app.shutdown()

        asyncio.run(run_case())
        app.llm.analyze.assert_called_once()
        app.llm.analyze_photo.assert_not_called()

    def test_loop_resilience(self):
        """Test that the capture loop continues even if non-critical components fail"""
        # Create a new app instance for this test
        app = ScreenMonApp(self.config)
        
        # Mock dependencies
        app.capturer.grab = MagicMock(return_value=self.test_dir / "screenshots" / "test.png")
        (self.test_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "screenshots" / "test.png").touch()
        
        # Mock idle detector
        mock_idle = MagicMock()
        mock_idle.idle = False
        mock_idle.compared = True
        mock_idle.similarity = 0.5
        mock_idle.changed_ratio = 0.5
        mock_idle.streak = 0
        app.idle_detector.evaluate = MagicMock(return_value=mock_idle)

        # Mock LLM
        mock_analysis = AnalysisResult(
            summary="Resilience Test",
            confidence=0.9,
            detail="Resilience Test Detail",
            raw_response={}
        )
        app.llm.analyze = AsyncMock(return_value=mock_analysis)

        # Mock storage.cleanup to raise exception
        app.storage.cleanup = MagicMock(side_effect=Exception("Storage cleanup failed"))

        # We can't easily test the loop continuing without modifying the loop condition or using a timeout
        # So we'll run it with run_once=True and verify it handles the exception gracefully (doesn't crash)
        # In run_once mode, my fix breaks the loop on exception, which is correct for run_once.
        # But in normal mode it should continue.
        
        # To test resilience, we need to ensure run() doesn't raise the exception
        async def run_safe():
            try:
                await app.run(run_once=True)
            finally:
                await app.shutdown()

        asyncio.run(run_safe())
        
        # Verify that the error was logged
        log_dir = Path(self.config.storage.log_dir)
        log_files = list(log_dir.glob("app_*.log"))
        # Exclude valid log
        app_logs = [f for f in log_files if "valid" not in f.name]
        self.assertTrue(len(app_logs) > 0)
        
        content = app_logs[0].read_text(encoding="utf-8")
        self.assertIn("截图循环发生未知异常", content)
        self.assertIn("Storage cleanup failed", content)

if __name__ == "__main__":
    unittest.main()
