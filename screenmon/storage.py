from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List

from .config import StorageConfig
from .utils import ensure_directory


class StorageManager:
    def __init__(self, cfg: StorageConfig):
        self.cfg = cfg
        self.logger = logging.getLogger("StorageManager")
        ensure_directory(Path(self.cfg.database_path).parent)
        self.conn = sqlite3.connect(self.cfg.database_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._init_tables()

    def _init_tables(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                screenshot_path TEXT NOT NULL,
                summary TEXT,
                detail TEXT,
                confidence REAL,
                raw_response TEXT,
                error TEXT
            )
            """
        )
        self.conn.commit()

    def insert_snapshot(
        self,
        captured_at: datetime,
        screenshot_path: Path,
        summary: str,
        detail: str,
        confidence: float,
        raw_response: Dict[str, Any],
        error: str | None = None,
    ) -> int:
        with self._lock:
            cursor = self.conn.execute(
                """
                INSERT INTO snapshots (captured_at, screenshot_path, summary, detail, confidence, raw_response, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at.isoformat(),
                    str(screenshot_path),
                    summary,
                    detail,
                    confidence,
                    json.dumps(raw_response, ensure_ascii=False),
                    error,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid

    def cleanup(self, retention_days: int, now: datetime | None = None):
        anchor = now or datetime.now()
        cutoff = (anchor - timedelta(days=retention_days)).isoformat()
        with self._lock:
            cur = self.conn.execute("DELETE FROM snapshots WHERE captured_at < ?", (cutoff,))
            deleted = cur.rowcount
            if deleted:
                self.logger.info("已清理 %s 条过期日志", deleted)
            self.conn.commit()

    def fetch_day(self, when: datetime) -> List[sqlite3.Row]:
        tz = when.tzinfo
        base_date = when.astimezone(tz).date() if tz else when.date()
        start = datetime.combine(base_date, time.min)
        end = datetime.combine(base_date, time.max)
        if tz:
            start = start.replace(tzinfo=tz)
            end = end.replace(tzinfo=tz)
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM snapshots WHERE captured_at BETWEEN ? AND ? ORDER BY captured_at ASC",
                (start.isoformat(), end.isoformat()),
            )
            rows = cur.fetchall()
        return rows

    def aggregate_day(self, when: datetime) -> Dict[str, Any]:
        rows = self.fetch_day(when)
        total = len(rows)
        if not rows:
            return {"total": 0, "items": []}
        summary = {
            "total": total,
            "first": rows[0]["captured_at"],
            "last": rows[-1]["captured_at"],
            "items": [
                {
                    "captured_at": row["captured_at"],
                    "summary": row["summary"],
                    "confidence": row["confidence"],
                    "screenshot_path": row["screenshot_path"],
                }
                for row in rows
            ],
        }
        return summary

    def close(self):
        self.conn.close()

