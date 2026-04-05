from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class CaptureConfig(BaseModel):
    interval_seconds: int = Field(60, ge=10)
    monitor_start_time: str = Field(default="00:00")
    monitor_end_time: str = Field(default="23:59")
    screenshot_dir: str = Field(default="data/screenshots")
    photo_dir: str = Field(default="data/photo")
    image_format: str = Field(default="PNG")
    image_quality: int = Field(85, ge=10, le=100)
    max_width: int = Field(1600, ge=320)
    monitors: str = Field("all")
    default_camera: str = Field(default="auto")
    idle_skip_enabled: bool = Field(default=True)
    idle_similarity_percent: int = Field(default=98, ge=50, le=100)
    idle_changed_percent: int = Field(default=1, ge=0, le=20)
    idle_consecutive_frames: int = Field(default=1, ge=1, le=10)
    idle_diff_pixel_threshold: int = Field(default=20, ge=1, le=255)
    idle_compare_width: int = Field(default=320, ge=64, le=1920)
    idle_compare_height: int = Field(default=180, ge=64, le=1080)
    photo_idle_skip_enabled: bool = Field(default=True)
    photo_idle_similarity_percent: int = Field(default=98, ge=50, le=100)
    photo_idle_changed_percent: int = Field(default=1, ge=0, le=20)
    photo_idle_consecutive_frames: int = Field(default=1, ge=1, le=10)
    photo_idle_diff_pixel_threshold: int = Field(default=20, ge=1, le=255)
    photo_idle_compare_width: int = Field(default=320, ge=64, le=1920)
    photo_idle_compare_height: int = Field(default=180, ge=64, le=1080)

    @model_validator(mode="before")
    @classmethod
    def _migrate_photo_idle_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if "photo_dir" not in data and "screenshot_dir" in data:
            screenshot_dir = Path(str(data.get("screenshot_dir", "data/screenshots")))
            data["photo_dir"] = str(screenshot_dir.parent / "photo")
        mapping = (
            ("photo_idle_skip_enabled", "idle_skip_enabled"),
            ("photo_idle_similarity_percent", "idle_similarity_percent"),
            ("photo_idle_changed_percent", "idle_changed_percent"),
            ("photo_idle_consecutive_frames", "idle_consecutive_frames"),
            ("photo_idle_diff_pixel_threshold", "idle_diff_pixel_threshold"),
            ("photo_idle_compare_width", "idle_compare_width"),
            ("photo_idle_compare_height", "idle_compare_height"),
        )
        for new_key, fallback_key in mapping:
            if new_key not in data and fallback_key in data:
                data[new_key] = data.get(fallback_key)
        return data

    @field_validator("monitor_start_time", "monitor_end_time")
    @classmethod
    def _validate_hhmm(cls, v: str):
        value = (v or "").strip()
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("时间格式必须为 HH:MM")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError("时间格式必须为 HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("时间必须在 00:00-23:59 范围内")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("default_camera")
    @classmethod
    def _validate_default_camera(cls, v: str):
        value = (v or "").strip().lower()
        if not value:
            return "auto"
        if value == "auto":
            return value
        if value.isdigit():
            return str(int(value))
        raise ValueError("默认摄像头必须为 auto 或数字索引")


class LLMConfig(BaseModel):
    provider: str = Field(default="mock")
    api_key: str | None = Field(default=None)
    api_base: str = Field(default="https://api.openai.com/v1")
    model: str = Field(default="gpt-4o-mini")
    screenshot_prompt1: str = Field(default="")
    photo_prompt2: str = Field(default="")
    log_analysis_prompt3: str = Field(default="")
    max_tokens: int = Field(default=1200, ge=64, le=8192)
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=45, ge=5)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_prompts(cls, data: Any):
        if not isinstance(data, dict):
            return data
        if "screenshot_prompt1" not in data and "system_prompt" in data:
            data["screenshot_prompt1"] = data.get("system_prompt")
        if "log_analysis_prompt3" not in data and "daily_summary_prompt" in data:
            data["log_analysis_prompt3"] = data.get("daily_summary_prompt")
        if "photo_prompt2" not in data:
            data["photo_prompt2"] = ""
        return data


class StorageConfig(BaseModel):
    database_path: str = Field(default="data/monitor.db")
    log_dir: str = Field(default="data/logs")
    log_retention_days: int = Field(default=30, ge=1)
    screenshot_retention_days: int = Field(default=7, ge=1)


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(default=587)
    use_tls: bool = True
    use_ssl: bool = False
    username: str | None = None
    password: str | None = None
    from_addr: str | None = None
    to_addrs: List[str] = Field(default_factory=list)
    send_time: str = Field(default="23:00")
    subject: str = Field(default="[ScreenMon] 每日活动摘要")
    attach_top_screenshots: int = Field(default=3, ge=0, le=10)

    @field_validator("smtp_host", "username", "password", "from_addr")
    @classmethod
    def _strip(cls, v: Optional[str]):
        return v.strip() if isinstance(v, str) else v

    @field_validator("to_addrs", mode="before")
    @classmethod
    def _normalize_to_addrs(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.replace(";", ",").split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _ensure_required(self):
        if self.enabled:
            missing = [
                name
                for name, value in {
                    "smtp_host": self.smtp_host,
                    "username": self.username,
                    "password": self.password,
                    "from_addr": self.from_addr,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"邮件配置缺少字段: {', '.join(missing)}")
            if not self.to_addrs:
                raise ValueError("邮件收件人列表不能为空")
        return self


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    backoff_seconds: int = Field(default=5, ge=1)


class ReportConfig(BaseModel):
    timezone: str = Field(default="local")
    summary_limit: int = Field(default=10, ge=3)


class AppConfig(BaseModel):
    project_name: str = Field(default="ScreenMon")
    log_level: str = Field(default="INFO")
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    base_dir: Path | None = None

    def resolve_paths(self, base_dir: Path):
        def _resolve(path_str: str) -> Path:
            p = Path(path_str)
            if not p.is_absolute():
                p = base_dir / p
            return p

        self.base_dir = base_dir
        self.capture.screenshot_dir = str(_resolve(self.capture.screenshot_dir))
        self.capture.photo_dir = str(_resolve(self.capture.photo_dir))
        self.storage.database_path = str(_resolve(self.storage.database_path))
        self.storage.log_dir = str(_resolve(self.storage.log_dir))


def _set_nested_value(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    cur = target
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        next_value = cur.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cur[part] = next_value
        cur = next_value
    cur[parts[-1]] = value


def _get_nested_value(target: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = target
    for part in dotted_key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _to_relative_if_possible(path_str: str, runtime_dir: Path) -> str:
    p = Path(path_str)
    if not p.is_absolute():
        return p.as_posix()
    try:
        rel = p.resolve().relative_to(runtime_dir.resolve())
        return rel.as_posix()
    except Exception:
        return str(p)


def dump_config_for_storage(cfg: AppConfig, runtime_dir: Path | None = None) -> dict[str, Any]:
    run_dir = (runtime_dir or Path.cwd()).resolve()
    raw = cfg.model_dump(exclude={"base_dir"})
    for dotted_key in ("capture.screenshot_dir", "capture.photo_dir", "storage.database_path", "storage.log_dir"):
        current = _get_nested_value(raw, dotted_key)
        if isinstance(current, str) and current.strip():
            _set_nested_value(raw, dotted_key, _to_relative_if_possible(current, run_dir))
    return raw


def _is_dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".screenmon_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _is_file_parent_writable(file_path: Path) -> bool:
    parent = file_path.parent
    return _is_dir_writable(parent)


def _write_config_file(cfg_path: Path, cfg: AppConfig) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(
            dump_config_for_storage(cfg),
            fp,
            allow_unicode=True,
            sort_keys=False,
        )


def _autofix_output_paths(raw: dict[str, Any], cfg: AppConfig, runtime_dir: Path) -> tuple[dict[str, Any], bool]:
    changed = False
    logger = logging.getLogger("ConfigLoader")
    dir_checks = [
        ("capture.screenshot_dir", Path(cfg.capture.screenshot_dir), Path("data/screenshots")),
        ("capture.photo_dir", Path(cfg.capture.photo_dir), Path("data/photo")),
        ("storage.log_dir", Path(cfg.storage.log_dir), Path("data/logs")),
    ]
    for dotted_key, configured_path, fallback_rel in dir_checks:
        if _is_dir_writable(configured_path):
            continue
        fallback_abs = runtime_dir / fallback_rel
        if not _is_dir_writable(fallback_abs):
            raise RuntimeError(f"路径不可用且回退失败: {configured_path} -> {fallback_abs}")
        _set_nested_value(raw, dotted_key, fallback_rel.as_posix())
        logger.warning("检测到无效路径，已回退到运行目录相对路径: %s -> %s", configured_path, fallback_rel.as_posix())
        changed = True

    db_path = Path(cfg.storage.database_path)
    db_fallback_rel = Path("data/monitor.db")
    if not _is_file_parent_writable(db_path):
        db_fallback_abs = runtime_dir / db_fallback_rel
        if not _is_file_parent_writable(db_fallback_abs):
            raise RuntimeError(f"数据库路径不可用且回退失败: {db_path} -> {db_fallback_abs}")
        _set_nested_value(raw, "storage.database_path", db_fallback_rel.as_posix())
        logger.warning("检测到无效数据库路径，已回退到运行目录相对路径: %s -> %s", db_path, db_fallback_rel.as_posix())
        changed = True
    return raw, changed


def load_config(path: str | Path, runtime_dir: Path | None = None) -> AppConfig:
    cfg_path = Path(path)
    run_dir = (runtime_dir or Path.cwd()).resolve()
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp) or {}
    else:
        raw = {}
    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"配置文件校验失败: {exc}") from exc
    cfg.resolve_paths(run_dir)
    raw, changed = _autofix_output_paths(raw, cfg, run_dir)
    if changed or not cfg_path.exists():
        try:
            cfg = AppConfig.model_validate(raw)
            _write_config_file(cfg_path, cfg)
        except ValidationError as exc:
            raise RuntimeError(f"自动修复后配置校验失败: {exc}") from exc
    cfg.resolve_paths(run_dir)
    return cfg
