from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class AlertConfig:
    channel: str  # disabled | discord | telegram | email
    webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""


class AlertDispatcher:
    def __init__(self, config: AlertConfig) -> None:
        self.config = config

    def send(self, title: str, message: str) -> bool:
        channel = self.config.channel.lower().strip()
        if channel in {"", "disabled", "none"}:
            return False
        if channel == "discord":
            return self._send_discord(title, message)
        if channel == "telegram":
            return self._send_telegram(title, message)
        if channel == "email":
            return self._send_email(title, message)
        return False

    def _send_discord(self, title: str, message: str) -> bool:
        if not self.config.webhook_url:
            return False
        body = json.dumps({"content": f"**{title}**\n{message}"}).encode("utf-8")
        req = Request(self.config.webhook_url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5):
            return True

    def _send_telegram(self, title: str, message: str) -> bool:
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            return False
        text = f"{title}\n{message}"
        url = (
            f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
            f"?chat_id={self.config.telegram_chat_id}&text={text}"
        )
        req = Request(url)
        with urlopen(req, timeout=5):
            return True

    def _send_email(self, title: str, message: str) -> bool:
        if not self.config.smtp_host or not self.config.email_to:
            return False

        mail = MIMEText(message)
        mail["Subject"] = title
        mail["From"] = self.config.email_from or self.config.smtp_user
        mail["To"] = self.config.email_to

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=5) as server:
            server.starttls()
            if self.config.smtp_user and self.config.smtp_password:
                server.login(self.config.smtp_user, self.config.smtp_password)
            server.send_message(mail)
        return True
