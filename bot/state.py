"""
SQLite state.

This file (state.db) is committed back to the repo at the end of each
workflow run, so state persists across runs. It is intentionally small.

Tables:
  pending_trades   - trade alerts awaiting YES/NO from Telegram
  closed_trades    - historical trade log
  bot_state        - singleton key/value store (paused, peak_equity, last_update_id, etc.)

Race conditions: GitHub Actions workflows can overlap. Each workflow
pulls the latest state.db, modifies it, and commits. Concurrent commits
cause one push to fail. The next run reads the committed state and continues.
For our low frequency this is acceptable - occasional missed commits self-heal.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).resolve().parent.parent / "state.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_trades (
    trade_id        TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    stop_price      REAL NOT NULL,
    shares          REAL NOT NULL,
    notional_usd    REAL NOT NULL,
    risk_usd        REAL NOT NULL,
    telegram_message_id INTEGER,
    created_at_utc  TEXT NOT NULL,
    expires_at_utc  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'  -- pending | approved | rejected | expired | failed
);

CREATE TABLE IF NOT EXISTS closed_trades (
    trade_id        TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    qty             REAL NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    pnl_usd         REAL,
    opened_at_utc   TEXT,
    closed_at_utc   TEXT,
    close_reason    TEXT  -- 'stop' | 'manual' | 'unknown'
);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    """Context-managed sqlite connection. Commits on exit, closes always."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# bot_state helpers (singleton key/value store)
# -----------------------------------------------------------------------------
def get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_bool(key: str, default: bool = False) -> bool:
    v = get_state(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes")


def set_bool(key: str, value: bool) -> None:
    set_state(key, "true" if value else "false")


def get_float(key: str, default: float = 0.0) -> float:
    v = get_state(key)
    return float(v) if v is not None else default


def set_float(key: str, value: float) -> None:
    set_state(key, str(value))


def get_int(key: str, default: int = 0) -> int:
    v = get_state(key)
    return int(v) if v is not None else default


def set_int(key: str, value: int) -> None:
    set_state(key, str(value))


# -----------------------------------------------------------------------------
# pending_trades helpers
# -----------------------------------------------------------------------------
def new_trade_id() -> str:
    """Short unique ID for use in callback_data (max 64 bytes per Telegram)."""
    return uuid.uuid4().hex[:12]


def insert_pending_trade(
    trade_id: str,
    ticker: str,
    entry_price: float,
    stop_price: float,
    shares: float,
    notional_usd: float,
    risk_usd: float,
    expires_at_utc: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO pending_trades
               (trade_id, ticker, entry_price, stop_price, shares,
                notional_usd, risk_usd, created_at_utc, expires_at_utc, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                trade_id, ticker, entry_price, stop_price, shares,
                notional_usd, risk_usd, _now_utc(), expires_at_utc,
            ),
        )


def attach_message_id(trade_id: str, message_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE pending_trades SET telegram_message_id = ? WHERE trade_id = ?",
            (message_id, trade_id),
        )


def get_pending(trade_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None


def set_pending_status(trade_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE pending_trades SET status = ? WHERE trade_id = ?",
            (status, trade_id),
        )


def get_all_pending() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_trades WHERE status = 'pending'"
        ).fetchall()
        return [dict(r) for r in rows]


def expire_old_pending(now_iso: str) -> list[dict]:
    """Mark expired pending trades and return them for notification."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_trades WHERE status = 'pending' AND expires_at_utc <= ?",
            (now_iso,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE pending_trades SET status = 'expired' WHERE trade_id = ?",
                (r["trade_id"],),
            )
        return [dict(r) for r in rows]
