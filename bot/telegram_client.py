"""
Telegram bot wrapper.

Direct REST calls to the Bot API. Two main operations:
  1. Send messages (with optional inline keyboard buttons)
  2. Poll for updates (button taps, text messages)

Bot token and chat ID come from env vars. The chat ID restricts the bot
to messaging only one user (you).
"""
from __future__ import annotations

import os
from typing import Any

import requests


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set.")
    return t


def _chat_id() -> str:
    c = os.environ.get("TELEGRAM_CHAT_ID")
    if not c:
        raise RuntimeError("TELEGRAM_CHAT_ID env var not set.")
    return c


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


class TelegramError(RuntimeError):
    pass


def _call(method: str, **kwargs) -> Any:
    try:
        resp = requests.post(_api(method), json=kwargs, timeout=30)
    except requests.RequestException as e:
        raise TelegramError(f"Telegram network error: {e}") from e

    if resp.status_code != 200:
        raise TelegramError(f"Telegram {method} {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(f"Telegram {method} not ok: {data}")
    return data["result"]


def send_message(text: str, reply_markup: dict | None = None) -> dict:
    """Send a plain or button-containing message to the configured chat."""
    payload = {
        "chat_id": _chat_id(),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", **payload)


def edit_message(message_id: int, text: str, reply_markup: dict | None = None) -> dict:
    """Edit an existing message (used to update trade alerts after action)."""
    payload = {
        "chat_id": _chat_id(),
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call("editMessageText", **payload)


def answer_callback(callback_id: str, text: str = "") -> None:
    """Acknowledge a button tap so the loading spinner disappears."""
    _call("answerCallbackQuery", callback_query_id=callback_id, text=text)


def get_updates(offset: int | None = None, timeout: int = 0) -> list[dict]:
    """
    Poll for new updates (button taps, messages).

    offset: only fetch updates with update_id > offset. Use this to mark
            processed messages so they don't repeat.
    timeout: long-poll seconds. 0 = immediate return (suits cron model).
    """
    params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["callback_query", "message"]}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(_api("getUpdates"), params=params, timeout=timeout + 10)
    except requests.RequestException as e:
        raise TelegramError(f"Telegram network error: {e}") from e

    if resp.status_code != 200:
        raise TelegramError(f"Telegram getUpdates {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(f"Telegram getUpdates not ok: {data}")
    return data["result"]


# -----------------------------------------------------------------------------
# Convenience: inline keyboard for YES/NO approval
# -----------------------------------------------------------------------------
def approval_keyboard(trade_id: str) -> dict:
    """Returns reply_markup dict with [YES] [NO] buttons for a pending trade."""
    return {
        "inline_keyboard": [[
            {"text": "✅ YES (BUY)", "callback_data": f"approve:{trade_id}"},
            {"text": "❌ NO (skip)", "callback_data": f"reject:{trade_id}"},
        ]]
    }
