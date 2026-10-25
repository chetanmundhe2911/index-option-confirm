"""Telegram alerts. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID come from .env;
without them messages are not sent (they still print in the terminal)."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request


def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
    except Exception as e:  # never let Telegram stop the watcher
        print(f"  ! telegram failed: {e}")
        return False
    return True
