"""
Pure strategy logic. Computes signals and position sizes from price data.

This module has no side effects - no API calls, no database writes.
That makes it testable in isolation and easy to reason about.

Strategy rules (all must be true to enter):
1. Regime filter:   50-day SMA > 200-day SMA
2. Momentum filter: 50 <= RSI(14) <= 70
3. Trigger:         Close > prior 20-day high
4. Confirmation:    Today's volume >= 20-day average volume
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np

from . import config


@dataclass
class Signal:
    """Result of evaluating the strategy on one ticker's data."""
    ticker: str
    is_buy: bool
    reason: str           # Human-readable explanation
    close: float          # Latest close price
    sma_fast: float
    sma_slow: float
    rsi: float
    prior_20d_high: float
    volume: float
    volume_avg: float


@dataclass
class PositionSize:
    """Calculated trade size."""
    shares: float          # Fractional shares supported
    notional_usd: float    # shares * entry_price
    stop_price: float
    risk_usd: float        # Max loss if stop hits


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI. Standard formulation.

    NOTE: there are several RSI formulations. This uses Wilder's smoothing
    (an exponentially weighted moving average with alpha = 1/period).
    Different platforms may show slightly different RSI values for the
    same data. That's normal.
    """
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    # Wilder's smoothing
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)  # Default to neutral when undefined


def evaluate(ticker: str, bars: pd.DataFrame) -> Optional[Signal]:
    """
    Evaluate strategy on a single ticker's historical bars.

    Args:
        ticker: e.g. 'AAPL'
        bars: DataFrame with columns ['open','high','low','close','volume'],
              indexed by timestamp ascending. Must have at least
              SMA_SLOW_PERIOD + 1 rows.

    Returns:
        Signal object, or None if not enough data.
    """
    needed = max(config.SMA_SLOW_PERIOD, config.BREAKOUT_LOOKBACK + 1) + 5
    if len(bars) < needed:
        return None

    closes = bars["close"]
    volumes = bars["volume"]
    highs = bars["high"]

    # --- Compute indicators ---
    sma_fast = closes.rolling(config.SMA_FAST_PERIOD).mean()
    sma_slow = closes.rolling(config.SMA_SLOW_PERIOD).mean()
    rsi = compute_rsi(closes, config.RSI_PERIOD)
    # Prior N-day high = max of the previous N days, EXCLUDING today
    prior_high = highs.shift(1).rolling(config.BREAKOUT_LOOKBACK).max()
    vol_avg = volumes.rolling(config.VOLUME_AVG_PERIOD).mean()

    # --- Latest values ---
    last_close = float(closes.iloc[-1])
    last_sma_fast = float(sma_fast.iloc[-1])
    last_sma_slow = float(sma_slow.iloc[-1])
    last_rsi = float(rsi.iloc[-1])
    last_prior_high = float(prior_high.iloc[-1])
    last_volume = float(volumes.iloc[-1])
    last_vol_avg = float(vol_avg.iloc[-1])

    # --- Evaluate each rule ---
    rule_regime = last_sma_fast > last_sma_slow
    rule_momentum = config.RSI_MIN <= last_rsi <= config.RSI_MAX
    rule_breakout = last_close > last_prior_high
    rule_volume = last_volume >= last_vol_avg * config.VOLUME_MULTIPLIER

    is_buy = all([rule_regime, rule_momentum, rule_breakout, rule_volume])

    if is_buy:
        reason = (
            f"BUY: regime ok (SMA50>{last_sma_slow:.2f}), "
            f"RSI={last_rsi:.1f}, "
            f"close {last_close:.2f}>20d-high {last_prior_high:.2f}, "
            f"vol {last_volume/1e6:.1f}M>{last_vol_avg/1e6:.1f}M avg"
        )
    else:
        # Build a useful diagnostic even when no signal fired
        failed = []
        if not rule_regime: failed.append("regime")
        if not rule_momentum: failed.append(f"RSI={last_rsi:.1f}")
        if not rule_breakout: failed.append("no-breakout")
        if not rule_volume: failed.append("low-vol")
        reason = f"no setup ({', '.join(failed)})"

    return Signal(
        ticker=ticker,
        is_buy=is_buy,
        reason=reason,
        close=last_close,
        sma_fast=last_sma_fast,
        sma_slow=last_sma_slow,
        rsi=last_rsi,
        prior_20d_high=last_prior_high,
        volume=last_volume,
        volume_avg=last_vol_avg,
    )


def size_position(entry_price: float, equity: float) -> PositionSize:
    """
    Calculate position size given entry price and current account equity.

    Sizing logic:
      risk_per_trade_usd = equity * RISK_PER_TRADE
      stop_price         = entry * (1 - STOP_LOSS_PCT)
      shares_by_risk     = risk_per_trade_usd / (entry - stop_price)
      shares_by_max_pos  = (equity * MAX_POSITION_PCT) / entry
      shares             = min(shares_by_risk, shares_by_max_pos)

    The position is sized so that if the stop hits, the loss is at most
    RISK_PER_TRADE of equity. The MAX_POSITION_PCT cap prevents
    over-concentration regardless of how tight the stop is.

    Fractional shares are supported (Alpaca allows them on US stocks).
    """
    risk_usd = equity * config.RISK_PER_TRADE
    stop_price = entry_price * (1.0 - config.STOP_LOSS_PCT)
    stop_distance = entry_price - stop_price

    if stop_distance <= 0:
        # Defensive - shouldn't happen but never divide by zero
        return PositionSize(0.0, 0.0, stop_price, 0.0)

    shares_by_risk = risk_usd / stop_distance
    shares_by_max_pos = (equity * config.MAX_POSITION_PCT) / entry_price
    shares = min(shares_by_risk, shares_by_max_pos)

    # Round to 4 decimal places (Alpaca's typical precision for fractional shares)
    shares = round(shares, 4)

    notional = shares * entry_price
    actual_risk = shares * stop_distance

    return PositionSize(
        shares=shares,
        notional_usd=notional,
        stop_price=stop_price,
        risk_usd=actual_risk,
    )
