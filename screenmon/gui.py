from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
from typing import Any, Dict

import yaml
import pystray
from PIL import Image, ImageDraw, ImageTk

from .app import ScreenMonApp
from .config import AppConfig, dump_config_for_storage, load_config
from .llm import LLMAnalyzer
from .screenshot import CameraCapturer, ScreenCapturer
from .storage import StorageManager
from .utils import configure_logging, parse_timezone

BG_COLOR = "#EBF6EE"  # 浅绿背景
CARD_COLOR = "#C8E8D6"
BTN_COLOR = "#4BA575"
BTN_TEXT = "#F6FFFB"
BORDER_COLOR = "#30785A"
CONFIG_TEXT_COLOR = "#0A0A0A"
TITLE_FONT = ("Segoe UI", 14, "bold")
BUTTON_FONT = ("Segoe UI", 12, "bold")
TEXT_FONT = ("Microsoft YaHei", 11)
MAIN_WIDTH = 920
MAIN_HEIGHT = 640
PREVIEW_MAX_WIDTH = 640
PREVIEW_MAX_HEIGHT = 360
PREVIEW_CARD_EXTRA_HEIGHT = 72
PREVIEW_CARD_MAX_HEIGHT = PREVIEW_MAX_HEIGHT + PREVIEW_CARD_EXTRA_HEIGHT
EXPLAIN_MIN_LINES = 6
WINDOW_SCREEN_PADDING = 80
MONITOR_START_COUNTDOWN_SECONDS = 2
MONITOR_HIDE_DELAY_MS = 200
MONITOR_IDLE_MINIMIZE_SECONDS = 10
TRAY_STATE_POLL_MS = 1000
ERROR_ICON_MIN_SECONDS = 3
TRAY_DOUBLE_CLICK_INTERVAL_SECONDS = 0.4
RUNTIME_STATE_FILE = "monitor_state.json"
RES_DIR_NAME = "res"
TRAY_STATE_LOGO = {
    "idle": "logo.png",
    "monitor_idle": "logo_pink.png",
    "capture": "logo_red.png",
    "llm": "logo_blue.png",
    "email": "logo_yellow.png",
    "error": "logo_gray.png",
}

FIELD_SPECS = [
    {"section": "模型配置", "key": "llm.provider", "label": "Provider", "type": "str"},
    {"section": "模型配置", "key": "llm.api_key", "label": "API Key", "type": "str", "secret": True},
    {"section": "模型配置", "key": "llm.api_base", "label": "API Base", "type": "str"},
    {"section": "模型配置", "key": "llm.model", "label": "模型", "type": "str"},
    {"section": "模型配置", "key": "llm.max_tokens", "label": "最大输出Token", "type": "int"},
    {"section": "模型配置", "key": "llm.max_retries", "label": "重试次数", "type": "int"},
    {"section": "模型配置", "key": "llm.timeout_seconds", "label": "超时时间(秒)", "type": "int"},
    {"section": "Prompt", "key": "llm.screenshot_prompt1", "label": "截图解读Prompt1", "type": "text"},
    {"section": "Prompt", "key": "llm.photo_prompt2", "label": "照片解读Prompt2", "type": "text"},
    {"section": "Prompt", "key": "llm.log_analysis_prompt3", "label": "日志分析Prompt3", "type": "text"},
    {"section": "邮箱配置", "key": "email.enabled", "label": "启用邮件", "type": "bool"},
    {"section": "邮箱配置", "key": "email.smtp_host", "label": "SMTP Host", "type": "str"},
    {"section": "邮箱配置", "key": "email.smtp_port", "label": "SMTP Port", "type": "int"},
    {"section": "邮箱配置", "key": "email.use_tls", "label": "STARTTLS", "type": "bool"},
    {"section": "邮箱配置", "key": "email.use_ssl", "label": "SSL", "type": "bool"},
    {"section": "邮箱配置", "key": "email.username", "label": "用户名", "type": "str"},
    {"section": "邮箱配置", "key": "email.password", "label": "密码", "type": "str", "secret": True},
    {"section": "邮箱配置", "key": "email.from_addr", "label": "发件邮箱", "type": "str"},
    {"section": "邮箱配置", "key": "email.to_addrs", "label": "收件人(逗号)", "type": "list"},
    {"section": "邮箱配置", "key": "email.send_time", "label": "发送时间(HH:MM)", "type": "str"},
    {"section": "邮箱配置", "key": "email.subject", "label": "主题", "type": "str"},
    {"section": "邮箱配置", "key": "email.attach_top_screenshots", "label": "附图数量", "type": "int"},
    {"section": "基础配置", "key": "project_name", "label": "项目名称", "type": "str"},
    {"section": "基础配置", "key": "log_level", "label": "日志级别", "type": "str"},
    {"section": "监控配置", "key": "capture.interval_seconds", "label": "监控间隔时间(秒)", "type": "int"},
    {"section": "监控配置", "key": "capture.monitor_start_time", "label": "监控工作区间开始(HH:MM)", "type": "str"},
    {"section": "监控配置", "key": "capture.monitor_end_time", "label": "监控工作区间结束(HH:MM)", "type": "str"},
    {"section": "截图配置", "key": "capture.screenshot_dir", "label": "截图目录", "type": "str"},
    {"section": "截图配置", "key": "capture.image_format", "label": "图片格式", "type": "str"},
    {"section": "截图配置", "key": "capture.image_quality", "label": "压缩质量", "type": "int"},
    {"section": "截图配置", "key": "capture.max_width", "label": "最大宽度", "type": "int"},
    {"section": "截图配置", "key": "capture.monitors", "label": "显示器", "type": "str"},
    {"section": "截图配置", "key": "capture.idle_skip_enabled", "label": "空闲跳过LLM", "type": "bool"},
    {"section": "截图配置", "key": "capture.idle_similarity_percent", "label": "空闲相似度阈值(%)", "type": "int"},
    {"section": "截图配置", "key": "capture.idle_changed_percent", "label": "变化像素阈值(%)", "type": "int"},
    {"section": "截图配置", "key": "capture.idle_consecutive_frames", "label": "连续空闲帧数", "type": "int"},
    {"section": "截图配置", "key": "capture.idle_diff_pixel_threshold", "label": "像素差阈值(0-255)", "type": "int"},
    {"section": "截图配置", "key": "capture.idle_compare_width", "label": "空闲判定宽度", "type": "int"},
    {"section": "截图配置", "key": "capture.idle_compare_height", "label": "空闲判定高度", "type": "int"},
    {"section": "截图配置", "key": "storage.screenshot_retention_days", "label": "截图保留天数", "type": "int"},
    {"section": "照片配置", "key": "capture.default_camera", "label": "默认摄像头", "type": "choice", "choices": "camera"},
    {"section": "照片配置", "key": "capture.photo_dir", "label": "照片目录", "type": "str"},
    {"section": "照片配置", "key": "capture.photo_idle_skip_enabled", "label": "照片空闲跳过LLM", "type": "bool"},
    {"section": "照片配置", "key": "capture.photo_idle_similarity_percent", "label": "照片相似度阈值(%)", "type": "int"},
    {"section": "照片配置", "key": "capture.photo_idle_changed_percent", "label": "照片变化像素阈值(%)", "type": "int"},
    {"section": "照片配置", "key": "capture.photo_idle_consecutive_frames", "label": "照片连续空闲帧数", "type": "int"},
    {"section": "照片配置", "key": "capture.photo_idle_diff_pixel_threshold", "label": "照片像素差阈值(0-255)", "type": "int"},
    {"section": "照片配置", "key": "capture.photo_idle_compare_width", "label": "照片判定宽度", "type": "int"},
    {"section": "照片配置", "key": "capture.photo_idle_compare_height", "label": "照片判定高度", "type": "int"},
    {"section": "存储配置", "key": "storage.database_path", "label": "数据库文件", "type": "str"},
    {"section": "存储配置", "key": "storage.log_dir", "label": "日志目录", "type": "str"},
    {"section": "存储配置", "key": "storage.log_retention_days", "label": "日志保留天数", "type": "int"},
    {"section": "行为与报告", "key": "retry.max_attempts", "label": "最大重试", "type": "int"},
    {"section": "行为与报告", "key": "retry.backoff_seconds", "label": "退避秒数", "type": "int"},
    {"section": "行为与报告", "key": "report.timezone", "label": "时区", "type": "str"},
    {"section": "行为与报告", "key": "report.summary_limit", "label": "摘要条数", "type": "int"},
]


class ScreenMonGUI:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        log_path = self._setup_logging()
        self.logger = logging.getLogger("ScreenMonGUI")
        self.logger.info("日志输出: %s", log_path)
        self.root = tk.Tk()
        self.root.title("ScreenLog")
        self.root.configure(bg=BG_COLOR)
        self._place_center(MAIN_WIDTH, MAIN_HEIGHT)
        self.root.resizable(False, True)
        self.root.minsize(MAIN_WIDTH, MAIN_HEIGHT)
        self.logo_path = self._ensure_logo_image()
        runtime_cfg = self._load_runtime_config()
        self.monitor_state_file = self._runtime_state_file(runtime_cfg)
        self.tray_logo_paths = self._load_tray_logo_paths(self.logo_path)
        self.current_tray_state = "idle"
        self.tray_state_override: str | None = None
        self.error_icon_until = 0.0
        self.last_tray_click_at = 0.0
        self.tray_poll_after_id: str | None = None
        self.current_tray_image = None
        self.tk_logo = ImageTk.PhotoImage(Image.open(self.logo_path).resize((48, 48)))
        self.root.iconphoto(False, self.tk_logo)
        self.tray_icon: pystray.Icon | None = None
        self.tray_thread: threading.Thread | None = None
        self._show_tray_icon()

        self.capture_time_var = tk.StringVar(value="截图时间：--")
        self.status_var = tk.StringVar(value="就绪")
        self.explain_title_var = tk.StringVar(value="图片解释（长度：0）：")

        self.vars: Dict[str, tk.Variable] = {}
        self.text_values: Dict[str, str] = {}
        self.text_widgets: Dict[str, tk.Text] = {}
        self.camera_choice_label_to_value: Dict[str, str] = {}
        self.camera_choice_value_to_label: Dict[str, str] = {}
        self.camera_test_window: tk.Toplevel | None = None
        self.camera_test_after_id: str | None = None
        self.camera_test_capture = None
        self.camera_test_image_label: tk.Label | None = None
        self.camera_test_frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self.camera_test_stop_event: threading.Event | None = None
        self.camera_test_worker_thread: threading.Thread | None = None
        self.camera_test_started_at: float | None = None
        self.camera_test_has_frame = False
        self.camera_test_timeout_logged = False

        self.monitor_proc: subprocess.Popen | None = None
        self.monitor_stopping = False  # 标记是否为用户主动停止
        self.monitor_start_after_id: str | None = None
        self.monitor_idle_after_id: str | None = None
        self.pending_monitor_cmd: list[str] | None = None
        self.latest_image = None
        self.preview_card: tk.Frame | None = None
        self.preview_image_label: tk.Label | None = None
        self.config_window: tk.Toplevel | None = None
        self.root.bind("<Unmap>", self._handle_unmap)
        self._bind_activity_events(self.root)

        self._init_form_vars()
        self._load_config_into_vars()
        self._build_main_view()
        self._schedule_tray_state_poll()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _init_form_vars(self):
        for spec in FIELD_SPECS:
            if spec["type"] == "bool":
                self.vars[spec["key"]] = tk.BooleanVar(value=False)
            elif spec["type"] in {"int", "str", "list", "choice"}:
                self.vars[spec["key"]] = tk.StringVar(value="")
            elif spec["type"] == "text":
                self.text_values[spec["key"]] = ""

    def _load_config_into_vars(self):
        cfg_dict = self._load_config_dict()
        for spec in FIELD_SPECS:
            key = spec["key"]
            value = self._dig_value(cfg_dict, key)
            if spec["type"] == "text":
                self.text_values[key] = value or ""
            elif spec["type"] == "bool":
                self.vars[key].set(bool(value))
            elif spec["type"] == "list":
                joined = ",".join(value or []) if isinstance(value, list) else value or ""
                self.vars[key].set(joined)
            else:
                self.vars[key].set("" if value is None else str(value))

    def _load_config_dict(self) -> Dict[str, Any]:
        cfg_path = self.config_path
        if not cfg_path.exists():
            return AppConfig().model_dump(exclude={"base_dir"})
        with cfg_path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp) or {}
        return AppConfig().model_validate(raw).model_dump(exclude={"base_dir"})

    def _setup_logging(self) -> Path:
        cfg = self._load_runtime_config()
        return configure_logging(cfg.storage.log_dir, cfg.log_level)

    def _load_runtime_config(self) -> AppConfig:
        try:
            cfg = load_config(self.config_path, runtime_dir=self._runtime_base_dir())
        except Exception:
            cfg = AppConfig()
            cfg.resolve_paths(self._runtime_base_dir())
        return cfg

    def _build_main_view(self):
        header = tk.Frame(self.root, bg=BG_COLOR)
        header.pack(fill=tk.X, padx=20, pady=(15, 5))
        tk.Label(header, text="ScreenLog", font=TITLE_FONT, bg=BG_COLOR, fg="#0A0A0A").pack(side=tk.LEFT)
        tk.Label(header, textvariable=self.status_var, bg=BG_COLOR, fg="#0A0A0A", font=("Segoe UI", 10, "bold")).pack(
            side=tk.RIGHT
        )

        btn_row = tk.Frame(self.root, bg=BG_COLOR)
        btn_row.pack(fill=tk.X, padx=40, pady=(10, 5))
        self.start_btn = self._primary_button(btn_row, "开始监控", self.start_monitoring)
        self.stop_btn = self._primary_button(btn_row, "停止监控", self.stop_monitoring, disabled=True)
        self.test_btn = self._primary_button(btn_row, "单次测试", self.run_test)
        self.config_btn = self._primary_button(btn_row, "配置", self.open_config_window)
        for idx, btn in enumerate((self.start_btn, self.stop_btn, self.test_btn, self.config_btn)):
            btn.grid(row=0, column=idx, padx=10)

        preview_card = tk.Frame(self.root, bg=CARD_COLOR, bd=2, relief="ridge", height=PREVIEW_CARD_MAX_HEIGHT)
        preview_card.pack(fill=tk.X, expand=False, padx=40, pady=(5, 10))
        preview_card.pack_propagate(False)
        preview_card.grid_rowconfigure(1, weight=1)
        preview_card.grid_columnconfigure(0, weight=1)
        self.preview_card = preview_card

        tk.Label(
            preview_card,
            textvariable=self.capture_time_var,
            bg=CARD_COLOR,
            fg="#0A0A0A",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))

        self.preview_image_label = tk.Label(
            preview_card, text="最新截图", bg=CARD_COLOR, fg="#0A0A0A", font=("Segoe UI", 16, "bold")
        )
        self.preview_image_label.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)

        explain_card = tk.Frame(self.root, bg=CARD_COLOR, bd=2, relief="ridge")
        explain_card.pack(fill=tk.BOTH, expand=True, padx=40, pady=(0, 20))
        tk.Label(
            explain_card,
            textvariable=self.explain_title_var,
            bg=CARD_COLOR,
            fg="#0A0A0A",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(
            fill=tk.X, padx=15, pady=(8, 0)
        )
        explain_text_wrap = tk.Frame(explain_card, bg=CARD_COLOR)
        explain_text_wrap.pack(fill=tk.BOTH, expand=True, padx=15, pady=(4, 10))
        self.explain_text = tk.Text(
            explain_text_wrap,
            height=EXPLAIN_MIN_LINES,
            bg=CARD_COLOR,
            fg="#0A0A0A",
            wrap="word",
            relief="flat",
            font=TEXT_FONT,
        )
        explain_scroll = tk.Scrollbar(explain_text_wrap, orient="vertical", command=self.explain_text.yview)
        self.explain_text.configure(yscrollcommand=explain_scroll.set)
        self.explain_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        explain_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        initial_text = "最新解释会显示在这里。"
        self.explain_text.insert("1.0", initial_text)
        self.explain_text.configure(state=tk.DISABLED)
        self._update_explain_title(initial_text)

    def _primary_button(self, parent, text, command, disabled=False):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=BTN_COLOR,
            fg=BTN_TEXT,
            font=BUTTON_FONT,
            relief="flat",
            width=10,
            bd=2,
            highlightbackground=BORDER_COLOR,
            activebackground="#1285B5",
        )
        if disabled:
            btn.configure(state=tk.DISABLED, disabledforeground="#BBD7E5")
        return btn

    def open_config_window(self):
        if self.monitor_start_after_id is not None:
            self._cancel_pending_monitor_start()
            self._reset_to_idle_status("已停止监控，打开配置")
            if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
                self.config_window.focus_set()
            else:
                self._open_config_window_ui()
            return
        proc = self.monitor_proc
        if proc and proc.poll() is None:
            self.status_var.set("正在停止监控并打开配置...")
            self.config_btn.configure(state=tk.DISABLED)
            self.monitor_stopping = True

            def _stop_then_open():
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                finally:
                    def _finish():
                        if self.monitor_proc is proc:
                            self.monitor_proc = None
                        self.monitor_stopping = False
                        self._reset_to_idle_status("监控已停止")
                        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
                            self.config_window.focus_set()
                        else:
                            self._open_config_window_ui()

                    self.root.after(0, _finish)

            threading.Thread(target=_stop_then_open, daemon=True).start()
            return
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self.config_window.focus_set()
            return
        self._open_config_window_ui()

    def _open_config_window_ui(self):
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self.config_window.focus_set()
            return
        self._refresh_camera_options()
        self.config_window = tk.Toplevel(self.root)
        self._bind_activity_events(self.config_window)
        self.config_window.title("ScreenLog 配置")
        self.config_window.configure(bg=BG_COLOR)
        self.config_window.geometry("760x620")
        self.config_window.protocol("WM_DELETE_WINDOW", self.close_config_window)

        header = tk.Frame(self.config_window, bg=BG_COLOR)
        header.pack(fill=tk.X, padx=20, pady=(15, 5))
        tk.Label(header, text="版本 V0.1", font=("Segoe UI", 11, "bold"), bg=BG_COLOR).pack(side=tk.LEFT)

        btn_row = tk.Frame(header, bg=BG_COLOR)
        btn_row.pack(side=tk.RIGHT)
        self._primary_button(btn_row, "读取配置", self.reload_from_file).pack(side=tk.LEFT, padx=6)
        self._primary_button(btn_row, "保存配置", self.save_config).pack(side=tk.LEFT, padx=6)
        self._primary_button(btn_row, "返回", self.close_config_window).pack(side=tk.LEFT, padx=6)

        scroll_area = tk.Frame(self.config_window, bg=BG_COLOR)
        scroll_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        canvas = tk.Canvas(scroll_area, bg=BG_COLOR, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = tk.Scrollbar(scroll_area, orient="vertical", command=canvas.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scroll.set)

        container = tk.Frame(canvas, bg=BG_COLOR)
        window_id = canvas.create_window((0, 0), window=container, anchor="nw")
        self.config_window.update_idletasks()
        canvas.itemconfigure(window_id, width=canvas.winfo_width())

        def _update_scrollregion(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(event):
            canvas.itemconfigure(window_id, width=event.width)

        container.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _on_canvas_resize)

        def _on_mousewheel(event):
            direction = -1 if event.delta > 0 else 1
            canvas.yview_scroll(direction, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        sections: Dict[str, tk.Frame] = {}
        for spec in FIELD_SPECS:
            section = spec["section"]
            if section not in sections:
                card = tk.Frame(container, bg=CARD_COLOR, bd=2, relief="ridge")
                card.pack(fill=tk.X, expand=True, pady=8)
                tk.Label(
                    card,
                    text=section + "：",
                    bg=CARD_COLOR,
                    fg=CONFIG_TEXT_COLOR,
                    font=("Segoe UI", 12, "bold"),
                    anchor="w",
                ).pack(fill=tk.X, padx=15, pady=(10, 4))
                body = tk.Frame(card, bg=CARD_COLOR)
                body.pack(fill=tk.X, expand=True, padx=15, pady=(0, 10))
                body.grid_columnconfigure(1, weight=1)
                sections[section] = body
            self._add_config_field(sections[section], spec)

    def _add_config_field(self, parent: tk.Frame, spec: Dict[str, Any]):
        key = spec["key"]
        field_type = spec["type"]
        row = parent.grid_size()[1]
        if field_type == "text":
            text = tk.Text(parent, height=5, bg="#A5DCC0", fg=CONFIG_TEXT_COLOR, wrap="word", relief="flat")
            text.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
            text.insert("1.0", self.text_values.get(key, ""))
            self.text_widgets[key] = text
            return
        if field_type == "bool":
            var = self.vars[key]
            chk = tk.Checkbutton(
                parent,
                text=spec["label"],
                variable=var,
                onvalue=True,
                offvalue=False,
                bg=CARD_COLOR,
            fg=CONFIG_TEXT_COLOR,
            activebackground=CARD_COLOR,
                selectcolor=CARD_COLOR,
            )
            chk.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
            return
        if field_type == "choice":
            tk.Label(parent, text=spec["label"] + "：", bg=CARD_COLOR, fg=CONFIG_TEXT_COLOR).grid(
                row=row, column=0, sticky="w", pady=3
            )
            values = self._choice_values(spec)
            current = str(self.vars[key].get() or "").strip()
            if key == "capture.default_camera":
                current_value = self.camera_choice_label_to_value.get(current, self._camera_choice_to_value(current))
                preferred = self.camera_choice_value_to_label.get(current_value)
                if preferred:
                    self.vars[key].set(preferred)
                elif current and current not in values:
                    values = [*values, current]
            else:
                if current and current not in values:
                    values = [*values, current]
                if values and not current:
                    self.vars[key].set(values[0])
            combo = ttk.Combobox(parent, textvariable=self.vars[key], values=values, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=3)
            if key == "capture.default_camera":
                test_btn = tk.Button(
                    parent,
                    text="测试摄像头",
                    command=self._open_camera_test_from_selection,
                    bg=BTN_COLOR,
                    fg=BTN_TEXT,
                    relief="flat",
                    bd=1,
                    padx=8,
                )
                test_btn.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=3)
            return
        tk.Label(parent, text=spec["label"] + "：", bg=CARD_COLOR, fg=CONFIG_TEXT_COLOR).grid(
            row=row, column=0, sticky="w", pady=3
        )
        entry = tk.Entry(parent, textvariable=self.vars[key], relief="flat", bg="#A5DCC0", fg=CONFIG_TEXT_COLOR)
        if spec.get("secret"):
            entry.configure(show="*")
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if self._is_directory_picker_field(key):
            pick_btn = tk.Button(
                parent,
                text="配置目录",
                command=lambda k=key: self._pick_directory_for_field(k),
                bg=BTN_COLOR,
                fg=BTN_TEXT,
                relief="flat",
                bd=1,
                padx=8,
            )
            pick_btn.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=3)

    @staticmethod
    def _is_directory_picker_field(key: str) -> bool:
        return key.endswith("_dir") or key in {"storage.database_path"}

    def _choice_values(self, spec: Dict[str, Any]) -> list[str]:
        choice_key = str(spec.get("choices", "")).strip().lower()
        if choice_key == "camera":
            return list(self.camera_choice_label_to_value.keys())
        return []

    def _refresh_camera_options(self):
        names = self._enumerate_camera_device_names()
        labels: list[str] = []
        label_to_value: Dict[str, str] = {}
        value_to_label: Dict[str, str] = {}

        auto_label = "自动选择（系统默认）"
        labels.append(auto_label)
        label_to_value[auto_label] = "auto"
        value_to_label["auto"] = auto_label

        if not names:
            for idx in range(4):
                value = str(idx)
                label = f"摄像头 {value} (#{value})"
                labels.append(label)
                label_to_value[label] = value
                value_to_label[value] = label
        else:
            for i, name in enumerate(names):
                value = str(i)
                label = f"{name} (#{value})"
                labels.append(label)
                label_to_value[label] = value
                value_to_label[value] = label

        self.camera_choice_label_to_value = label_to_value
        self.camera_choice_value_to_label = value_to_label

    def _enumerate_camera_device_names(self) -> list[str]:
        if not sys.platform.startswith("win"):
            return []
        commands = [
            "$ErrorActionPreference='SilentlyContinue';"
            "Get-PnpDevice -Class Camera | Where-Object {$_.Status -eq 'OK'} | "
            "Select-Object -ExpandProperty FriendlyName",
            "$ErrorActionPreference='SilentlyContinue';"
            "Get-CimInstance Win32_PnPEntity | "
            "Where-Object { $_.PNPClass -in @('Camera','Image') -or $_.Service -eq 'usbvideo' } | "
            "Select-Object -ExpandProperty Name",
        ]
        for command in commands:
            try:
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                continue
            lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if lines:
                return self._dedupe_preserve_order(lines)
        return []

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        seen = set()
        result: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    @staticmethod
    def _camera_choice_to_value(raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return "auto"
        lower = text.lower()
        if lower == "auto" or lower.startswith("自动"):
            return "auto"
        if text.isdigit():
            return str(int(text))
        match = re.search(r"#\s*(\d+)\)?\s*$", text)
        if match:
            return str(int(match.group(1)))
        return "auto"

    def _open_camera_test_from_selection(self):
        var = self.vars.get("capture.default_camera")
        if not var:
            return
        selected = str(var.get() or "").strip()
        camera_value = self.camera_choice_label_to_value.get(selected, self._camera_choice_to_value(selected))
        if camera_value == "auto":
            camera_indices = list(range(0, 6))
            camera_label = selected or "自动选择（系统默认）"
        else:
            camera_index = int(camera_value)
            camera_indices = [camera_index]
            camera_label = selected or f"摄像头 #{camera_index}"
        self.logger.info("配置页测试摄像头开始: 选择=%s, 解析=%s", camera_label, camera_indices)
        self._open_camera_test_window(camera_indices, camera_label)

    def _open_camera_test_window(self, camera_indices: list[int], camera_label: str):
        try:
            import cv2  # type: ignore
        except Exception:
            messagebox.showerror("摄像头测试", "未安装 opencv-python，无法测试摄像头。")
            return
        self._close_camera_test_window()
        self.camera_test_stop_event = threading.Event()
        self.camera_test_frame_queue = queue.Queue(maxsize=1)
        self.camera_test_started_at = time.monotonic()
        self.camera_test_has_frame = False
        self.camera_test_timeout_logged = False
        self.camera_test_window = tk.Toplevel(self.config_window or self.root)
        self.camera_test_window.title("摄像头测试")
        self.camera_test_window.geometry("860x620")
        self.camera_test_window.configure(bg=BG_COLOR)
        tk.Label(
            self.camera_test_window,
            text=f"当前设备：{camera_label}",
            bg=BG_COLOR,
            fg=CONFIG_TEXT_COLOR,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill=tk.X, padx=12, pady=(10, 6))
        self.camera_test_image_label = tk.Label(
            self.camera_test_window,
            text="摄像头画面加载中...",
            bg=CARD_COLOR,
            fg=CONFIG_TEXT_COLOR,
            font=("Segoe UI", 12),
        )
        self.camera_test_image_label.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        tk.Button(
            self.camera_test_window,
            text="关闭测试",
            command=self._close_camera_test_window,
            bg=BTN_COLOR,
            fg=BTN_TEXT,
            relief="flat",
            bd=1,
            padx=10,
            pady=4,
        ).pack(pady=(0, 10))
        self.camera_test_window.protocol("WM_DELETE_WINDOW", self._close_camera_test_window)
        self.camera_test_worker_thread = None
        self.camera_test_after_id = None
        self.camera_test_window.update()
        self._run_simple_camera_test(camera_indices, camera_label)

    def _camera_test_worker(self, camera_indices: list[int], camera_label: str):
        stop_event = self.camera_test_stop_event
        if stop_event is None:
            return
        try:
            import cv2  # type: ignore
        except Exception:
            self._offer_camera_test_payload(("error", "未安装 opencv-python，无法测试摄像头。"))
            return
        try:
            cap = None
            active_index = None
            for camera_index in camera_indices:
                if stop_event.is_set():
                    return
                probe_cap = cv2.VideoCapture(camera_index)
                if not probe_cap or not probe_cap.isOpened():
                    if probe_cap:
                        probe_cap.release()
                    continue
                ok, frame = probe_cap.read()
                if not ok or frame is None:
                    probe_cap.release()
                    continue
                cap = probe_cap
                active_index = camera_index
                break
            if not cap or not cap.isOpened():
                self._offer_camera_test_payload(("error", f"无法打开摄像头：{camera_label}"))
                return
            self.camera_test_capture = cap
            if active_index is not None:
                self._offer_camera_test_payload(("opened", f"已连接摄像头 #{active_index}"))
            self.camera_test_has_frame = True
            self._offer_camera_test_payload(("status", "预览已在独立窗口显示"))
            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.03)
                    continue
                cv2.imshow("Camera Test", frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
        except Exception:
            self._offer_camera_test_payload(("status", "摄像头预览异常"))
        finally:
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
            try:
                cv2.destroyWindow("Camera Test")
            except Exception:
                pass
            self.camera_test_capture = None

    def _offer_camera_test_payload(self, payload):
        q = self.camera_test_frame_queue
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(payload)
        except Exception:
            pass

    def _run_simple_camera_test(self, camera_indices: list[int], camera_label: str) -> None:
        if self.camera_test_image_label:
            label = self.camera_test_image_label
            label.configure(text="正在启动最简摄像头测试窗口（ESC 退出）", image="")
            label.image = None
        if self.camera_test_window and tk.Toplevel.winfo_exists(self.camera_test_window):
            self.camera_test_window.update()
        indices_literal = "[" + ",".join(str(i) for i in camera_indices) + "]"
        script = (
            "import cv2,sys,time\n"
            f"indices={indices_literal}\n"
            "cap=None\n"
            "frame=None\n"
            "for idx in indices:\n"
            "    cap=cv2.VideoCapture(idx)\n"
            "    if not cap or not cap.isOpened():\n"
            "        if cap:\n"
            "            cap.release()\n"
            "        cap=None\n"
            "        continue\n"
            "    ok,frame=cap.read()\n"
            "    if ok and frame is not None:\n"
            "        break\n"
            "    cap.release()\n"
            "    cap=None\n"
            "if not cap or not cap.isOpened() or frame is None:\n"
            "    sys.exit(2)\n"
            "cv2.imshow('Camera Test', frame)\n"
            "start=time.time()\n"
            "while True:\n"
            "    ok,frame=cap.read()\n"
            "    if ok and frame is not None:\n"
            "        cv2.imshow('Camera Test', frame)\n"
            "    if cv2.waitKey(1)&0xFF==27:\n"
            "        break\n"
            "    if time.time()-start>3600:\n"
            "        break\n"
            "cap.release()\n"
            "cv2.destroyAllWindows()\n"
        )
        try:
            subprocess.Popen([sys.executable, "-c", script])
        except Exception:
            messagebox.showerror("摄像头测试", "无法启动最简摄像头测试进程")
            return
        self.camera_test_has_frame = True

    def _pump_camera_test_frame(self):
        window = self.camera_test_window
        label = self.camera_test_image_label
        if not window or not tk.Toplevel.winfo_exists(window) or label is None:
            self._close_camera_test_window()
            return
        try:
            latest = None
            while True:
                latest = self.camera_test_frame_queue.get_nowait()
        except queue.Empty:
            latest = None
        except Exception:
            latest = ("status", "摄像头预览异常")
        if latest is not None:
            kind, payload = latest
            if kind == "error":
                self._close_camera_test_window()
                messagebox.showerror("摄像头测试", str(payload))
                return
            if kind == "frame":
                self.camera_test_has_frame = True
                image = Image.fromarray(payload)
                image.thumbnail((820, 540))
                tk_image = ImageTk.PhotoImage(image=image)
                label.configure(image=tk_image, text="")
                label.image = tk_image
            elif kind == "status":
                if not self.camera_test_has_frame:
                    label.configure(text=str(payload), image="")
                    label.image = None
            elif kind == "opened":
                if not self.camera_test_has_frame:
                    label.configure(text=str(payload), image="")
                    label.image = None
        if not self.camera_test_has_frame and self.camera_test_started_at is not None:
            elapsed = time.monotonic() - self.camera_test_started_at
            if elapsed >= 6:
                if not self.camera_test_timeout_logged:
                    self.logger.warning("配置页摄像头测试超时未出画面: elapsed=%.2fs", elapsed)
                    self.camera_test_timeout_logged = True
                label.configure(text="摄像头已连接但未返回画面，请切换默认摄像头索引后重试", image="")
                label.image = None
        if window and tk.Toplevel.winfo_exists(window):
            self.camera_test_after_id = window.after(50, self._pump_camera_test_frame)

    def _close_camera_test_window(self):
        if self.camera_test_after_id is not None and self.camera_test_window and tk.Toplevel.winfo_exists(self.camera_test_window):
            try:
                self.camera_test_window.after_cancel(self.camera_test_after_id)
            except Exception:
                pass
        self.camera_test_after_id = None
        if self.camera_test_stop_event is not None:
            try:
                self.camera_test_stop_event.set()
            except Exception:
                pass
        self.camera_test_stop_event = None
        self.camera_test_worker_thread = None
        self.camera_test_started_at = None
        self.camera_test_has_frame = False
        self.camera_test_timeout_logged = False
        if self.camera_test_capture is not None:
            try:
                self.camera_test_capture.release()
            except Exception:
                pass
        self.camera_test_capture = None
        if self.camera_test_window and tk.Toplevel.winfo_exists(self.camera_test_window):
            self.camera_test_window.destroy()
        self.camera_test_window = None
        self.camera_test_image_label = None

    def _pick_directory_for_field(self, key: str):
        var = self.vars.get(key)
        if not var:
            return
        current = str(var.get() or "").strip()
        runtime_dir = self._runtime_base_dir()
        candidate = runtime_dir
        if current:
            current_path = Path(current)
            if not current_path.is_absolute():
                current_path = runtime_dir / current_path
            if key == "storage.database_path":
                candidate = current_path.parent
            else:
                candidate = current_path
        if not candidate.exists():
            candidate = runtime_dir
        selected = filedialog.askdirectory(
            parent=self.config_window or self.root,
            initialdir=str(candidate),
            title="选择目录",
        )
        if not selected:
            return
        selected_path = Path(selected)
        if key == "storage.database_path":
            db_name = Path(current).name if current else "monitor.db"
            target_path = selected_path / db_name
        else:
            target_path = selected_path
        var.set(self._path_to_config_str(target_path))

    def _path_to_config_str(self, target: Path) -> str:
        runtime_dir = self._runtime_base_dir().resolve()
        try:
            rel = target.resolve().relative_to(runtime_dir)
            return rel.as_posix()
        except Exception:
            return str(target)

    def close_config_window(self):
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self._close_camera_test_window()
            self._sync_text_values()
            self.text_widgets.clear()
            self.config_window.destroy()
            self.config_window = None

    def reload_from_file(self):
        self._load_config_into_vars()
        stale_keys: list[str] = []
        for key, widget in list(self.text_widgets.items()):
            try:
                widget.delete("1.0", tk.END)
                widget.insert("1.0", self.text_values.get(key, ""))
            except tk.TclError:
                stale_keys.append(key)
        for key in stale_keys:
            self.text_widgets.pop(key, None)
        self.status_var.set("配置已重新加载")

    def save_config(self):
        try:
            data = self._collect_form_data()
            cfg = AppConfig.model_validate(data)
        except Exception as exc:
            messagebox.showerror("校验失败", str(exc))
            return
        self._write_config(cfg)
        self.status_var.set("配置已保存")

    def _collect_form_data(self) -> Dict[str, Any]:
        self._sync_text_values()
        data: Dict[str, Any] = {}
        for spec in FIELD_SPECS:
            key = spec["key"]
            parts = key.split(".")
            target = data
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            if spec["type"] == "text":
                value = self.text_values.get(key, "")
            elif spec["type"] == "bool":
                value = bool(self.vars[key].get())
            elif spec["type"] == "int":
                raw = self.vars[key].get()
                value = int(raw) if str(raw).strip() else 0
            elif spec["type"] == "list":
                raw = self.vars[key].get().strip()
                value = [item.strip() for item in raw.split(",") if item.strip()]
            elif spec["type"] == "choice":
                raw = self.vars[key].get().strip()
                if key == "capture.default_camera":
                    value = self.camera_choice_label_to_value.get(raw, self._camera_choice_to_value(raw))
                else:
                    value = raw
            else:
                value = self.vars[key].get().strip()
            target[parts[-1]] = value
        self._validate_email_block(data.get("email", {}))
        return data

    def _sync_text_values(self):
        stale_keys: list[str] = []
        for key, widget in list(self.text_widgets.items()):
            try:
                self.text_values[key] = widget.get("1.0", tk.END).strip()
            except tk.TclError:
                stale_keys.append(key)
        for key in stale_keys:
            self.text_widgets.pop(key, None)

    def _dig_value(self, data: Dict[str, Any], dotted: str):
        cur = data
        for part in dotted.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur

    def _validate_email_block(self, email_cfg: Dict[str, Any]):
        if not email_cfg.get("enabled"):
            return
        warnings = []
        required_fields = [
            ("smtp_host", "SMTP Host"),
            ("smtp_port", "SMTP 端口"),
            ("username", "用户名"),
            ("password", "密码"),
            ("from_addr", "发件邮箱"),
        ]
        missing = [label for key, label in required_fields if not str(email_cfg.get(key, "")).strip()]
        if missing:
            warnings.append("缺少字段：" + "、".join(missing))
        to_addrs = email_cfg.get("to_addrs") or []
        if not to_addrs:
            warnings.append("收件人列表为空")
        if email_cfg.get("from_addr") and not self._is_valid_email(email_cfg["from_addr"]):
            warnings.append("发件邮箱格式不正确")
        invalid_receivers = [addr for addr in to_addrs if not self._is_valid_email(addr)]
        if invalid_receivers:
            warnings.append("以下收件人格式不正确：" + "、".join(invalid_receivers))
        if warnings:
            messagebox.showinfo("邮箱配置提示", "\n".join(warnings) + "\n已自动关闭邮件功能。")
            email_cfg["enabled"] = False
            var = self.vars.get("email.enabled")
            if var:
                var.set(False)

    @staticmethod
    def _is_valid_email(value: str) -> bool:
        if not value or "@" not in value:
            return False
        name, _, domain = value.partition("@")
        return bool(name.strip() and "." in domain)

    def run_test(self):
        try:
            cfg = AppConfig.model_validate(self._collect_form_data())
            cfg.resolve_paths(self._runtime_base_dir())
        except Exception as exc:
            messagebox.showerror("校验失败", str(exc))
            return
        self._set_tray_state_override("capture")
        self._hide_windows_for_capture()
        self.status_var.set("测试中...")
        self._set_controls_state(tk.DISABLED)
        self.root.after(
            500,
            lambda: threading.Thread(target=self._do_test, args=(cfg,), daemon=True).start(),
        )

    def start_monitoring(self):
        if self.monitor_proc and self.monitor_proc.poll() is None:
            messagebox.showinfo("提示", "监控已在运行")
            return
        if self.monitor_start_after_id is not None:
            messagebox.showinfo("提示", "监控正在倒计时启动")
            return
        try:
            cfg = AppConfig.model_validate(self._collect_form_data())
            cfg.resolve_paths(self._runtime_base_dir())
            self.monitor_state_file = self._runtime_state_file(cfg)
            self._write_config(cfg)
        except Exception as exc:
            messagebox.showerror("校验失败", str(exc))
            return
        self._set_tray_state_override("monitor_idle")
        self.pending_monitor_cmd = [sys.executable, "-m", "screenmon", "--config", str(self.config_path)]
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.test_btn.configure(state=tk.DISABLED)
        self.config_btn.configure(state=tk.DISABLED)
        self._start_monitoring_countdown(MONITOR_START_COUNTDOWN_SECONDS)

    def _start_monitoring_countdown(self, seconds_left: int):
        if seconds_left > 0:
            self.status_var.set(f"{seconds_left}秒后开始监控...")
            self.monitor_start_after_id = self.root.after(1000, self._start_monitoring_countdown, seconds_left - 1)
            return
        self.status_var.set("监控启动中...")
        self._hide_windows_for_capture()
        self.monitor_start_after_id = self.root.after(MONITOR_HIDE_DELAY_MS, self._launch_monitor_process)

    def _launch_monitor_process(self):
        self.monitor_start_after_id = None
        cmd = self.pending_monitor_cmd
        self.pending_monitor_cmd = None
        if not cmd:
            self._reset_to_idle_status("监控未运行")
            return
        self.monitor_stopping = False  # 重置停止标志
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            self.monitor_proc = subprocess.Popen(cmd, creationflags=creationflags)
        except Exception as exc:
            self.monitor_proc = None
            self._reset_to_idle_status("监控未运行")
            messagebox.showerror("启动失败", str(exc))
            return
        self.status_var.set("监控运行中")
        self._clear_tray_state_override()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.test_btn.configure(state=tk.NORMAL)
        self.config_btn.configure(state=tk.NORMAL)
        threading.Thread(target=self._watch_monitor_process, args=(self.monitor_proc,), daemon=True).start()

    def stop_monitoring(self):
        self._cancel_monitor_idle_countdown(restore_status=False)
        if self.monitor_start_after_id is not None:
            self._cancel_pending_monitor_start()
            self._reset_to_idle_status("已取消监控启动")
            return
        proc = self.monitor_proc
        if not proc or proc.poll() is not None:
            self.monitor_proc = None
            self._reset_to_idle_status("监控未运行")
            return
        self.monitor_stopping = True  # 标记为主动停止
        self.status_var.set("正在停止...")

        def _stop():
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            finally:
                self.root.after(0, lambda: self._reset_to_idle_status("监控已停止"))

        threading.Thread(target=_stop, daemon=True).start()

    def _watch_monitor_process(self, proc: subprocess.Popen):
        code = proc.wait()
        if self.monitor_proc is proc:
            self.monitor_proc = None
            if self.monitor_stopping:
                self.monitor_stopping = False
                self.root.after(0, lambda: self._reset_to_idle_status("监控已停止"))
            else:
                self.root.after(0, lambda: self._on_monitor_process_error(code))

    def _on_monitor_process_error(self, code: int):
        self._set_tray_state("error")
        self._reset_to_idle_status(f"监控异常结束，退出码 {code}")
        # 自动显示主窗口通知用户
        self._show_main_window()
        messagebox.showerror("监控异常", f"后台监控进程异常退出，退出码: {code}\n\n请检查日志了解详细原因。")

    def _cancel_pending_monitor_start(self):
        if self.monitor_start_after_id is not None:
            try:
                self.root.after_cancel(self.monitor_start_after_id)
            except Exception:
                pass
        self.monitor_start_after_id = None
        self.pending_monitor_cmd = None

    def _reset_to_idle_status(self, status_text: str):
        self._cancel_monitor_idle_countdown(restore_status=False)
        self._clear_tray_state_override()
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.test_btn.configure(state=tk.NORMAL)
        self.config_btn.configure(state=tk.NORMAL)
        self.status_var.set(status_text)
        self._set_tray_state("idle")

    def _is_monitor_running(self) -> bool:
        return bool(self.monitor_proc and self.monitor_proc.poll() is None)

    def _is_main_window_visible(self) -> bool:
        try:
            return self.root.state() in {"normal", "zoomed"}
        except Exception:
            return False

    def _bind_activity_events(self, widget):
        for sequence in ("<ButtonPress>", "<KeyPress>", "<MouseWheel>", "<FocusIn>"):
            widget.bind(sequence, self._on_user_activity, add="+")

    def _on_user_activity(self, _event=None):
        if self.monitor_start_after_id is not None:
            return
        self._restart_monitor_idle_countdown()

    def _restart_monitor_idle_countdown(self):
        if not self._is_monitor_running() or not self._is_main_window_visible():
            return
        self._cancel_monitor_idle_countdown(restore_status=False)
        self._monitor_idle_tick(MONITOR_IDLE_MINIMIZE_SECONDS)

    def _monitor_idle_tick(self, seconds_left: int):
        if not self._is_monitor_running() or not self._is_main_window_visible():
            self._cancel_monitor_idle_countdown(restore_status=False)
            return
        if seconds_left > 0:
            self.status_var.set(f"监控运行中，{seconds_left}秒后自动最小化")
            self.monitor_idle_after_id = self.root.after(1000, self._monitor_idle_tick, seconds_left - 1)
            return
        self._cancel_monitor_idle_countdown(restore_status=True)
        self._hide_windows_for_capture()

    def _cancel_monitor_idle_countdown(self, restore_status: bool):
        if self.monitor_idle_after_id is not None:
            try:
                self.root.after_cancel(self.monitor_idle_after_id)
            except Exception:
                pass
        self.monitor_idle_after_id = None
        if restore_status and self._is_monitor_running():
            self.status_var.set("监控运行中")

    def _do_test(self, cfg: AppConfig):
        storage: StorageManager | None = None
        capturer: ScreenCapturer | None = None
        photo_capturer: CameraCapturer | None = None
        screenshot_path: Path | None = None
        photo_path: Path | None = None
        captured_at: datetime | None = None
        test_failed = False
        nonfatal_errors: list[str] = []
        try:
            tz = parse_timezone(cfg.report.timezone)
            captured_at = datetime.now(tz)
            capturer = ScreenCapturer(cfg.capture)
            self.root.after(0, lambda: self._set_tray_state_override("capture"))
            try:
                screenshot_started_at = datetime.now(tz)
                screenshot_path = capturer.grab()
                self._assert_capture_file_fresh(screenshot_path, screenshot_started_at, "截图")
            except Exception as exc:
                self.root.after(0, lambda msg=f"截图失败: {exc}": self.status_var.set(msg))
                raise
            timestamp = screenshot_path.stem
            self.root.after(1000, self._show_main_window)
            self.root.after(1000, lambda: self._update_preview(screenshot_path, timestamp))

            async def _run():
                analyzer = LLMAnalyzer(cfg.llm, cfg.retry)
                try:
                    self.root.after(0, lambda: self._set_tray_state_override("llm"))
                    return await analyzer.analyze(screenshot_path)
                finally:
                    await analyzer.close()

            try:
                result = asyncio.run(_run())
            except Exception as exc:
                self.root.after(0, lambda msg=f"截图解读失败: {exc}": self.status_var.set(msg))
                raise
            storage = StorageManager(cfg.storage)
            storage.insert_snapshot(
                captured_at=captured_at,
                screenshot_path=screenshot_path,
                summary=result.summary,
                detail=result.detail,
                confidence=result.confidence,
                raw_response=result.raw_response,
            )
            self.root.after(0, lambda: self._update_explain(result.detail))
            self._append_valid_analysis_log(
                cfg=cfg,
                screenshot_path=screenshot_path,
                summary=result.summary,
                detail=result.detail,
            )
            self.root.after(0, lambda: self._set_tray_state_override("email"))
            storage.cleanup(cfg.storage.log_retention_days, now=captured_at)
            if capturer is not None:
                capturer.cleanup(cfg.storage.screenshot_retention_days)
            photo_capturer = CameraCapturer(cfg.capture)
            try:
                photo_started_at = datetime.now(tz)
                photo_path = photo_capturer.grab()
                if photo_path is not None:
                    self._assert_capture_file_fresh(photo_path, photo_started_at, "照片")
            except Exception as exc:
                msg = f"拍照失败: {exc}"
                nonfatal_errors.append(msg)
                self.root.after(0, lambda m=msg: self.status_var.set(m))
                photo_path = None
            if photo_path is not None:
                async def _run_photo():
                    analyzer = LLMAnalyzer(cfg.llm, cfg.retry)
                    try:
                        return await analyzer.analyze_photo(photo_path)
                    finally:
                        await analyzer.close()
                try:
                    photo_result = asyncio.run(_run_photo())
                except Exception as exc:
                    msg = f"照片解读失败: {exc}"
                    nonfatal_errors.append(msg)
                    self.root.after(0, lambda m=msg: self.status_var.set(m))
                else:
                    photo_timestamp = photo_path.stem
                    storage.insert_snapshot(
                        captured_at=captured_at,
                        screenshot_path=photo_path,
                        summary=photo_result.summary,
                        detail=photo_result.detail,
                        confidence=photo_result.confidence,
                        raw_response=photo_result.raw_response,
                    )
                    self.root.after(0, lambda: self._update_preview(photo_path, photo_timestamp))
                    self.root.after(0, lambda: self._update_explain(photo_result.detail))
                    self._append_photo_valid_analysis_log(
                        cfg=cfg,
                        photo_path=photo_path,
                        summary=photo_result.summary,
                        detail=photo_result.detail,
                    )
                    self.logger.info("GUI 单次测试照片 LLM 返回全文（长度=%s）:\n%s", len(photo_result.detail or ""), photo_result.detail or "")
            else:
                msg = "摄像头不存在，已跳过拍照"
                nonfatal_errors.append(msg)
                self.root.after(0, lambda m=msg: self.status_var.set(m))
            if photo_capturer is not None:
                photo_capturer.cleanup(cfg.storage.screenshot_retention_days)

            async def _run_daily_pipeline():
                app = ScreenMonApp(cfg)
                try:
                    await app.send_daily_email()
                finally:
                    await app.shutdown()

            asyncio.run(_run_daily_pipeline())
            self.logger.info("GUI 单次测试 LLM 返回全文（长度=%s）:\n%s", len(result.detail or ""), result.detail or "")
            if nonfatal_errors:
                self.root.after(0, lambda msg="；".join(nonfatal_errors): self.status_var.set(msg))
            else:
                self.root.after(0, lambda: self.status_var.set("全链路测试成功"))
        except Exception as exc:
            test_failed = True
            err_msg = f"测试失败: {exc}"
            self.logger.exception("GUI 单次测试失败: %s", exc)
            self.root.after(0, lambda: self._set_tray_state_override("error"))
            if screenshot_path is not None and captured_at is not None:
                try:
                    if storage is None:
                        storage = StorageManager(cfg.storage)
                    storage.insert_snapshot(
                        captured_at=captured_at,
                        screenshot_path=screenshot_path,
                        summary="分析失败",
                        detail=str(exc),
                        confidence=0.0,
                        raw_response={},
                        error=str(exc),
                    )
                except Exception as db_exc:
                    self.logger.warning("GUI 单次测试写入数据库失败: %s", db_exc)
            self.root.after(0, lambda msg=err_msg: self.status_var.set(msg))
        finally:
            if storage is not None:
                storage.close()
            if test_failed:
                self.root.after(2000, self._clear_tray_state_override)
            else:
                self.root.after(0, self._clear_tray_state_override)
            self.root.after(0, lambda: self._set_tray_state("idle"))
            self.root.after(0, lambda: self._set_controls_state(tk.NORMAL))
    
    def _update_preview(self, image_path: Path, timestamp: str):
        self.capture_time_var.set(f"截图时间：{timestamp}")
        try:
            img = Image.open(image_path)
            img.thumbnail((PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT))
            rendered_height = img.height
            self.latest_image = ImageTk.PhotoImage(img)
            if self.preview_image_label:
                self.preview_image_label.configure(image=self.latest_image, text="")
            self._adjust_preview_card_height(rendered_height)
        except Exception as exc:
            if self.preview_image_label:
                self.preview_image_label.configure(text=f"无法加载截图: {exc}", image="")
            self._adjust_preview_card_height(PREVIEW_MAX_HEIGHT)

    def _update_explain(self, text: str):
        text = text or ""
        self.explain_text.configure(state=tk.NORMAL)
        self.explain_text.delete("1.0", tk.END)
        self.explain_text.insert("1.0", text)
        self.explain_text.configure(state=tk.DISABLED)
        self._update_explain_title(text)
        self._auto_expand_explain_area()

    def _update_explain_title(self, text: str):
        self.explain_title_var.set(f"图片解释（长度：{len(text)}）：")

    def _auto_expand_explain_area(self):
        self.root.update_idletasks()
        try:
            display_lines = int(self.explain_text.count("1.0", "end-1c", "displaylines")[0])
        except Exception:
            display_lines = int(self.explain_text.index("end-1c").split(".")[0])
        target_lines = max(EXPLAIN_MIN_LINES, display_lines + 1)
        current_lines = int(self.explain_text.cget("height"))
        if target_lines <= current_lines:
            return
        font_value = self.explain_text.cget("font")
        try:
            line_height = tkfont.nametofont(font_value).metrics("linespace")
        except tk.TclError:
            line_height = tkfont.Font(root=self.root, font=font_value).metrics("linespace")
        self.explain_text.configure(height=target_lines)
        extra_height = (target_lines - current_lines) * line_height + 12
        current_w = self.root.winfo_width()
        current_h = self.root.winfo_height()
        max_h = max(MAIN_HEIGHT, self.root.winfo_screenheight() - WINDOW_SCREEN_PADDING)
        desired_h = min(max_h, current_h + extra_height)
        if desired_h > current_h:
            self.root.geometry(f"{current_w}x{int(desired_h)}")

    def _adjust_preview_card_height(self, rendered_image_height: int):
        if not self.preview_card:
            return
        image_h = max(1, min(PREVIEW_MAX_HEIGHT, int(rendered_image_height)))
        target_h = PREVIEW_CARD_EXTRA_HEIGHT + image_h
        target_h = min(target_h, PREVIEW_CARD_MAX_HEIGHT)
        self.preview_card.configure(height=target_h)

    def _assert_capture_file_fresh(self, image_path: Path, capture_started_at: datetime, label: str):
        path = Path(image_path)
        if not path.exists():
            raise RuntimeError(f"{label}文件不存在: {path}")
        mtime = path.stat().st_mtime
        min_allowed = capture_started_at.timestamp() - 2.0
        max_allowed = datetime.now().timestamp() + 2.0
        if mtime < min_allowed:
            raise RuntimeError(f"{label}文件时间戳过旧，疑似复用历史文件: {path.name}")
        if mtime > max_allowed:
            raise RuntimeError(f"{label}文件时间戳异常: {path.name}")

    def _append_valid_analysis_log(
        self,
        cfg: AppConfig,
        screenshot_path: Path,
        summary: str,
        detail: str,
    ) -> None:
        if not (summary or "").strip() or not (detail or "").strip():
            return
        try:
            tz = parse_timezone(cfg.report.timezone)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now().astimezone()
        log_path = Path(cfg.storage.log_dir) / f"valid_screenshot_{now.strftime('%Y%m%d')}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "captured_at": now.isoformat(),
            "screenshot_path": str(screenshot_path),
            "summary": summary,
            "detail": detail,
        }
        try:
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("GUI 写入有效解读日志失败 %s: %s", log_path, exc)

    def _append_photo_valid_analysis_log(
        self,
        cfg: AppConfig,
        photo_path: Path,
        summary: str,
        detail: str,
    ) -> None:
        if not (summary or "").strip() or not (detail or "").strip():
            return
        try:
            tz = parse_timezone(cfg.report.timezone)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now().astimezone()
        log_path = Path(cfg.storage.log_dir) / f"valid_photo_{now.strftime('%Y%m%d')}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "captured_at": now.isoformat(),
            "photo_path": str(photo_path),
            "summary": summary,
            "detail": detail,
        }
        try:
            with log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("GUI 写入照片有效解读日志失败 %s: %s", log_path, exc)

    def _set_controls_state(self, state):
        if state == tk.DISABLED:
            self.start_btn.configure(state=state)
            self.stop_btn.configure(state=tk.DISABLED if not self.monitor_proc or self.monitor_proc.poll() is not None else tk.NORMAL)
            self.test_btn.configure(state=state)
        else:
            self.start_btn.configure(state=tk.NORMAL if not self.monitor_proc or self.monitor_proc.poll() is not None else tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL if self.monitor_proc and self.monitor_proc.poll() is None else tk.DISABLED)
            self.test_btn.configure(state=tk.NORMAL)

    def _write_config(self, cfg: AppConfig):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as fp:
            yaml.safe_dump(
                dump_config_for_storage(cfg, runtime_dir=self._runtime_base_dir()),
                fp,
                allow_unicode=True,
                sort_keys=False,
            )

    def _place_center(self, width: int, height: int):
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = int((screen_w - width) / 2)
        y = int((screen_h - height) / 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _runtime_base_dir() -> Path:
        return Path.cwd()


    def run(self):
        self.root.mainloop()

    def on_close(self):
        self._close_camera_test_window()
        self._cancel_pending_monitor_start()
        self._cancel_monitor_idle_countdown(restore_status=False)
        self._cancel_tray_state_poll()
        self._hide_tray_icon()
        if self.monitor_proc and self.monitor_proc.poll() is None:
            if not messagebox.askyesno("确认退出", "监控仍在运行，确定要退出吗？"):
                return
            self.stop_monitoring()
        self.root.destroy()

    def _hide_windows_for_capture(self):
        self._cancel_monitor_idle_countdown(restore_status=False)
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self.config_window.withdraw()
        self.root.update_idletasks()
        self.root.iconify()

    def _show_main_window(self):
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self.config_window.deiconify()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        if self._is_monitor_running():
            self._restart_monitor_idle_countdown()
        else:
            self._cancel_monitor_idle_countdown(restore_status=False)

    def _handle_unmap(self, _event):
        if self.root.state() == "iconic":
            self._cancel_monitor_idle_countdown(restore_status=False)
            self.root.withdraw()
            self._show_tray_icon()

    def _schedule_tray_state_poll(self):
        desired_state = self._desired_tray_state()
        self._set_tray_state(desired_state)
        self._sync_main_status_from_runtime()
        self.tray_poll_after_id = self.root.after(TRAY_STATE_POLL_MS, self._schedule_tray_state_poll)

    def _cancel_tray_state_poll(self):
        if self.tray_poll_after_id is not None:
            try:
                self.root.after_cancel(self.tray_poll_after_id)
            except Exception:
                pass
        self.tray_poll_after_id = None

    def _desired_tray_state(self) -> str:
        if time.monotonic() < self.error_icon_until:
            return "error"
        if self.tray_state_override:
            return self.tray_state_override
        if not self._is_monitor_running():
            return "idle"
        state, _ = self._read_monitor_runtime_payload()
        if not state or state == "idle":
            return "monitor_idle"
        return state

    def _sync_main_status_from_runtime(self):
        if self.tray_state_override:
            return
        if not self._is_monitor_running():
            return
        state, detail = self._read_monitor_runtime_payload()
        if state == "error":
            self.status_var.set(detail or "监控异常")
            return
        if state == "capture":
            self.status_var.set("截图中...")
            return
        if state == "llm":
            self.status_var.set("截图解读中...")
            return
        if state == "email":
            self.status_var.set("日报发送中...")
            return
        self.status_var.set("监控运行中")

    def _set_tray_state_override(self, state: str):
        normalized = state if state in TRAY_STATE_LOGO else "idle"
        self.tray_state_override = normalized
        self._set_tray_state(normalized)

    def _clear_tray_state_override(self):
        self.tray_state_override = None

    @staticmethod
    def _runtime_state_file(cfg: AppConfig) -> Path:
        return Path(cfg.storage.log_dir) / RUNTIME_STATE_FILE

    def _read_monitor_runtime_state(self) -> str | None:
        state, _ = self._read_monitor_runtime_payload()
        return state

    def _read_monitor_runtime_payload(self) -> tuple[str | None, str | None]:
        state_file = self.monitor_state_file
        if not state_file or not state_file.exists():
            return None, None
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return None, None
        state = str(raw.get("state", "")).strip().lower()
        detail = str(raw.get("detail", "")).strip() or None
        if state not in TRAY_STATE_LOGO:
            return None, detail
        return state, detail

    def _set_tray_state(self, state: str):
        normalized = state if state in TRAY_STATE_LOGO else "idle"
        now = time.monotonic()
        if normalized == "error":
            self.error_icon_until = now + ERROR_ICON_MIN_SECONDS
        elif now < self.error_icon_until:
            normalized = "error"
        if normalized == self.current_tray_state and self.tray_icon:
            return
        self.current_tray_state = normalized
        logo_path = self.tray_logo_paths.get(normalized, self.logo_path)
        self.current_tray_image = self._create_tray_image(logo_path)
        if self.tray_icon:
            self.tray_icon.icon = self.current_tray_image

    def _show_tray_icon(self):
        if self.tray_icon:
            return
        self.current_tray_image = self._create_tray_image(
            self.tray_logo_paths.get(self.current_tray_state, self.logo_path)
        )
        menu = pystray.Menu(
            pystray.MenuItem("切换窗口", self._on_tray_default_action, default=True, visible=False),
            pystray.MenuItem("开始监控", lambda: self.root.after(0, self.start_monitoring)),
            pystray.MenuItem("停止监控", lambda: self.root.after(0, self.stop_monitoring)),
            pystray.MenuItem("单次测试", lambda: self.root.after(0, self.run_test)),
            pystray.MenuItem("配置", lambda: self.root.after(0, self._open_config_from_tray)),
            pystray.MenuItem("显示窗口", lambda: self.root.after(0, self._restore_from_tray)),
            pystray.MenuItem("退出程序", lambda: self.root.after(0, self._quit_from_tray)),
        )
        self.tray_icon = pystray.Icon("ScreenLog", self.current_tray_image, "ScreenLog", menu)

        def _run_icon():
            self.tray_icon.run()

        self.tray_thread = threading.Thread(target=_run_icon, daemon=True)
        self.tray_thread.start()

    def _hide_tray_icon(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
            self.tray_thread = None

    def _restore_from_tray(self):
        self.root.after(0, self._show_main_window)

    def _on_tray_default_action(self, *_args):
        now = time.monotonic()
        if now - self.last_tray_click_at <= TRAY_DOUBLE_CLICK_INTERVAL_SECONDS:
            self.last_tray_click_at = 0.0
            self.root.after(0, self._toggle_main_window_from_tray)
            return
        self.last_tray_click_at = now

    def _toggle_main_window_from_tray(self):
        if self._is_main_window_visible():
            self._hide_main_window_from_tray()
        else:
            self._show_main_window()

    def _hide_main_window_from_tray(self):
        self._cancel_monitor_idle_countdown(restore_status=False)
        if self.config_window and tk.Toplevel.winfo_exists(self.config_window):
            self.config_window.withdraw()
        self.root.withdraw()
        self._show_tray_icon()

    def _open_config_from_tray(self):
        self._show_main_window()
        self.open_config_window()

    def _quit_from_tray(self):
        self.root.after(0, self.on_close)

    def _create_tray_image(self, logo_path: Path | None = None):
        img = Image.open(logo_path or self.logo_path).copy()
        img.thumbnail((64, 64))
        return img

    def _load_tray_logo_paths(self, default_logo: Path) -> Dict[str, Path]:
        mapping: Dict[str, Path] = {}
        for state, file_name in TRAY_STATE_LOGO.items():
            found = self._find_named_logo(file_name)
            mapping[state] = found or default_logo
        return mapping

    def _ensure_logo_image(self) -> Path:
        external_logo = self._find_named_logo("logo.png") or self._find_external_logo()
        if external_logo:
            self.logger.info("使用外部 LOGO: %s", external_logo)
            return external_logo
        asset_dir = Path(__file__).resolve().parent / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        logo_path = asset_dir / "screenlog_icon.png"
        if not logo_path.exists():
            base = Image.new("RGBA", (256, 256), (75, 165, 117, 255))
            draw = ImageDraw.Draw(base)
            draw.ellipse((20, 20, 236, 236), fill=(255, 255, 255, 235))
            draw.text((96, 110), "SL", fill=(52, 118, 86, 255))
            base.save(logo_path)
        return logo_path

    def _find_named_logo(self, file_name: str) -> Path | None:
        search_dirs = self._logo_search_dirs()
        seen: set[Path] = set()
        for directory in search_dirs:
            directory = Path(directory).resolve()
            if directory in seen or not directory.exists():
                continue
            seen.add(directory)
            candidate = directory / file_name
            if candidate.is_file():
                return candidate
        return None

    def _logo_search_dirs(self) -> list[Path]:
        runtime_dir = self._runtime_base_dir()
        config_dir = self.config_path.parent
        return [
            runtime_dir / RES_DIR_NAME,
            config_dir / RES_DIR_NAME,
            runtime_dir,
            config_dir,
        ]

    def _find_external_logo(self) -> Path | None:
        search_dirs = self._logo_search_dirs()
        seen: set[Path] = set()
        preferred = ["LOGO.PNG", "logo.png", "Logo.png"]
        for directory in search_dirs:
            project_dir = Path(directory).resolve()
            if project_dir in seen or not project_dir.exists():
                continue
            seen.add(project_dir)
            for name in preferred:
                candidate = project_dir / name
                if candidate.is_file():
                    return candidate
            for candidate in project_dir.iterdir():
                if candidate.is_file() and candidate.suffix.lower() == ".png" and candidate.stem.lower() == "logo":
                    return candidate
        return None


def launch_gui(config_path: Path):
    gui = ScreenMonGUI(config_path)
    gui.run()
