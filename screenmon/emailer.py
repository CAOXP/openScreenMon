from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List

from .config import EmailConfig


class EmailClient:
    def __init__(self, cfg: EmailConfig):
        self.cfg = cfg
        self.logger = logging.getLogger("EmailClient")

    def build_body(self, report: Dict, summary_limit: int) -> str:
        if report.get("total", 0) == 0:
            return "今日尚未记录任何活动。"
        lines = [
            f"总计 {report['total']} 条记录，首条 {report['first']}，末条 {report['last']}。",
            "\n重点活动：",
        ]
        for item in report["items"][:summary_limit]:
            lines.append(
                f"- {item['captured_at']}: {item['summary']} (置信度 {item['confidence']:.2f})"
            )
        return "\n".join(lines)

    def send(self, report: Dict, summary_limit: int, attachments: List[Path]):
        if not self.cfg.enabled:
            self.logger.debug("邮件功能未开启，跳过发送")
            return
        body = self.build_body(report, summary_limit)
        msg = self._build_message(self.cfg.subject, body)
        for attach in attachments[: self.cfg.attach_top_screenshots]:
            if not attach.exists():
                continue
            msg.add_attachment(
                attach.read_bytes(),
                maintype="image",
                subtype=attach.suffix.lstrip("."),
                filename=attach.name,
            )
        self._send_message(msg, success_msg="日报邮件发送成功")

    def send_single_test(
        self,
        captured_at: datetime,
        summary: str,
        detail: str,
    ):
        if not self.cfg.enabled:
            self.logger.debug("邮件功能未开启，跳过发送")
            return
        body = "\n".join(
            [
                f"单次测试时间: {captured_at.isoformat()}",
                f"摘要: {summary}",
                "",
                "解读全文:",
                detail or "",
            ]
        )
        subject = f"{self.cfg.subject} [单次测试]"
        msg = self._build_message(subject, body)
        self._send_message(msg, success_msg="单次测试邮件发送成功")

    def send_daily_summary(
        self,
        report_date: datetime,
        body: str,
        summary_file: Path,
    ):
        if not self.cfg.enabled:
            self.logger.debug("邮件功能未开启，跳过发送")
            return
        day_text = report_date.strftime("%Y-%m-%d")
        subject = f"{self.cfg.subject} [{day_text}]"
        msg = self._build_message(subject, body)
        if summary_file.exists():
            msg.add_attachment(
                summary_file.read_bytes(),
                maintype="text",
                subtype="markdown",
                filename="summary.md",
            )
        self._send_message(msg, success_msg="日报汇总邮件发送成功")

    def _build_message(self, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.from_addr
        msg["To"] = ", ".join(self.cfg.to_addrs)
        msg.set_content(body)
        return msg

    def _send_message(self, msg: EmailMessage, success_msg: str):
        try:
            if self.cfg.use_ssl:
                server = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port)
            else:
                server = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port)
            with server as smtp:
                smtp.ehlo()
                if self.cfg.use_tls and not self.cfg.use_ssl:
                    smtp.starttls()
                if self.cfg.username and self.cfg.password:
                    smtp.login(self.cfg.username, self.cfg.password)
                smtp.send_message(msg)
            self.logger.info(success_msg)
        except smtplib.SMTPException as exc:
            self.logger.error("SMTP 发送失败: %s", exc)
            raise
        except OSError as exc:
            self.logger.error("邮件网络异常: %s", exc)
            raise

