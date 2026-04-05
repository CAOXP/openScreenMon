from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

from tzlocal import get_localzone


def ensure_directory(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def configure_logging(log_dir: str | Path, level: str) -> Path:
    ensure_directory(log_dir)
    timestamp = datetime.now().strftime("%Y%m%d")
    log_path = Path(log_dir) / f"app_{timestamp}.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return log_path


def parse_timezone(tz_name: str):
    if tz_name.lower() == "local":
        return get_localzone()
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"无法加载时区 {tz_name}: {exc}")


def seconds_until(target_hhmm: str, tz) -> int:
    hour, minute = [int(part) for part in target_hhmm.split(":", maxsplit=1)]
    now = datetime.now(tz)
    target_today = datetime.combine(now.date(), time(hour=hour, minute=minute, tzinfo=tz))
    if target_today <= now:
        target_today += timedelta(days=1)
    return int((target_today - now).total_seconds())

