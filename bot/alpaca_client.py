"""
Thin wrapper around Alpaca's API.

Encapsulates all Alpaca-specific code so the rest of the bot is broker-agnostic.
Uses direct REST calls via the `requests` library to avoid heavyweight SDK
dependencies in GitHub Actions (faster cold start, fewer breaking changes).

Two base URLs:
  - https://paper-api.alpaca.markets  (paper trading)
  - https://data.alpaca.markets       (market data)

Paper trading data is the same as live data (Alpaca uses the same feed).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests


# Paper trading base URL. Override via env var if you ever switch to live
# (which we are explicitly NOT doing in this project).
TRADING_BASE = os.environ.get(
    "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
)
DATA_BASE = "https://data.alpaca.markets"


def _headers() -> dict[str, str]:
    """Build auth headers from env vars. Raises if not configured."""
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as env vars."
        )
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }


class AlpacaError(RuntimeError):
    """Raised when an Alpaca call fails."""


def _request(method: str, url: str, **kwargs) -> Any:
    """Wrapped request with timeout and error reporting."""
    try:
        resp = requests.request(
            method, url, headers=_headers(), timeout=30, **kwargs
        )
    except requests.RequestException as e:
        raise AlpacaError(f"Network error calling {url}: {e}") from e

    if resp.status_code >= 400:
        raise AlpacaError(
            f"Alpaca {method} {url} failed {resp.status_code}: {resp.text[:500]}"
        )
    if resp.text:
        return resp.json()
    return None


# -----------------------------------------------------------------------------
# Account & clock
# -----------------------------------------------------------------------------
def get_account() -> dict:
    """Returns account dict. Key fields: equity, cash, buying_power, status."""
    return _request("GET", f"{TRADING_BASE}/v2/account")


def get_clock() -> dict:
    """Returns market clock. Key field: is_open (bool)."""
    return _request("GET", f"{TRADING_BASE}/v2/clock")


def is_market_open() -> bool:
    """True if US market currently open."""
    return bool(get_clock().get("is_open", False))


# -----------------------------------------------------------------------------
# Positions
# -----------------------------------------------------------------------------
def get_positions() -> list[dict]:
    """List of open positions with symbol, qty, avg_entry_price, market_value, unrealized_pl."""
    return _request("GET", f"{TRADING_BASE}/v2/positions") or []


def position_exists(symbol: str) -> bool:
    """Check if we already have an open position in this symbol."""
    return any(p["symbol"] == symbol for p in get_positions())


# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------
def place_bracket_buy(
    symbol: str, qty: float, stop_price: float, limit_take_profit: float | None = None
) -> dict:
    """
    Place a market buy with attached stop-loss. Alpaca's bracket orders combine
    entry + stop (+ optional take-profit) so the stop becomes a live order
    immediately on fill.

    NOTE: Alpaca bracket orders historically require whole-share quantities.
    Fractional bracket orders may or may not be supported depending on
    account type and may have changed since this code was written.
    If a bracket fails, we fall back to a simple market order + separate
    stop order placed after fill.
    """
    qty_int = int(qty)  # bracket orders require whole shares
    if qty_int < 1:
        # Too small for bracket - fall back to fractional market with manual stop later
        return place_fractional_buy_with_pending_stop(symbol, qty, stop_price)

    body = {
        "symbol": symbol,
        "qty": str(qty_int),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "stop_loss": {"stop_price": f"{stop_price:.2f}"},
    }
    if limit_take_profit:
        body["take_profit"] = {"limit_price": f"{limit_take_profit:.2f}"}

    return _request("POST", f"{TRADING_BASE}/v2/orders", json=body)


def place_fractional_buy_with_pending_stop(
    symbol: str, qty: float, stop_price: float
) -> dict:
    """
    Fallback when shares < 1: place a notional/fractional market buy.
    The stop has to be placed separately after the fill (handled by responder
    after order confirmation).
    """
    body = {
        "symbol": symbol,
        "qty": f"{qty:.4f}",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    return _request("POST", f"{TRADING_BASE}/v2/orders", json=body)


def place_stop_loss(symbol: str, qty: float, stop_price: float) -> dict:
    """Standalone stop-loss order, for use after a fractional fill."""
    body = {
        "symbol": symbol,
        "qty": f"{qty:.4f}",
        "side": "sell",
        "type": "stop",
        "stop_price": f"{stop_price:.2f}",
        "time_in_force": "gtc",
    }
    return _request("POST", f"{TRADING_BASE}/v2/orders", json=body)


def get_order(order_id: str) -> dict:
    """Fetch a specific order by ID."""
    return _request("GET", f"{TRADING_BASE}/v2/orders/{order_id}")


# -----------------------------------------------------------------------------
# Market data
# -----------------------------------------------------------------------------
def get_daily_bars(symbol: str, days_back: int = 260) -> pd.DataFrame:
    """
    Fetch daily bars (OHLCV) for the symbol.

    Returns DataFrame indexed by date, columns ['open','high','low','close','volume'].
    Returns empty DataFrame if no data.

    Free Alpaca data tier provides IEX feed with 15-min delay. For daily
    bars this delay is irrelevant - we're looking at completed days.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back + 60)  # buffer for weekends/holidays

    params = {
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 1000,
        "adjustment": "raw",
        "feed": "iex",  # Free tier
    }
    url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
    data = _request("GET", url, params=params)

    bars = (data or {}).get("bars", [])
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(bars)
    df["timestamp"] = pd.to_datetime(df["t"])
    df = df.rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    df = df.sort_index()
    return df.tail(days_back)
