"""
Configuration for the trading bot.

All tunable parameters in one place. Edit these to adjust behaviour without
touching the strategy logic itself.

THIS FILE CONTAINS NO SECRETS. API keys come from environment variables
(GitHub Secrets at deploy time). Never put keys in this file.
"""
from __future__ import annotations

# -----------------------------------------------------------------------------
# WATCHLIST
# -----------------------------------------------------------------------------
# 3 broad ETFs + 20 liquid large-caps. Equally weighted scanning - the bot
# checks each ticker independently.
WATCHLIST: list[str] = [
    # Broad ETFs
    "SPY", "QQQ", "IWM",
    # Mega-caps
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "JPM", "V",
    # Other large-caps
    "UNH", "XOM", "JNJ", "WMT", "MA", "HD", "PG", "COST", "ABBV", "LLY",
]


# -----------------------------------------------------------------------------
# STRATEGY PARAMETERS
# -----------------------------------------------------------------------------
# Regime filter: only buy in confirmed uptrends.
SMA_FAST_PERIOD = 50
SMA_SLOW_PERIOD = 200

# Momentum filter: RSI must be in this range. Avoids overbought entries
# (>70) and weak-momentum entries (<50).
RSI_PERIOD = 14
RSI_MIN = 50.0
RSI_MAX = 70.0

# Entry trigger: today's close must exceed the prior N-day high (a breakout).
BREAKOUT_LOOKBACK = 20

# Volume confirmation: today's volume must exceed the N-day average by this
# multiplier.
VOLUME_AVG_PERIOD = 20
VOLUME_MULTIPLIER = 1.0  # 1.0 means today's volume >= 20-day average

# Bars to fetch for analysis. We need at least SMA_SLOW_PERIOD bars plus
# some buffer for the calculations to be stable.
BARS_LOOKBACK_DAYS = 260


# -----------------------------------------------------------------------------
# RISK PARAMETERS
# -----------------------------------------------------------------------------
# All percentages, applied to current equity. Dollar values shown in
# comments assume $1,000 starting equity but the code uses live equity.

# Risk per trade as a fraction of equity. 0.01 = 1%.
RISK_PER_TRADE = 0.01  # $10 on $1,000 account

# Initial stop loss as fraction below entry. Used for position sizing AND
# placed as an actual order at Alpaca.
STOP_LOSS_PCT = 0.03  # 3% below entry

# Maximum single position size as fraction of equity.
MAX_POSITION_PCT = 0.25  # $250 on $1,000 account

# Maximum number of open positions at any time.
MAX_CONCURRENT_POSITIONS = 4

# Circuit breakers - bot stops trading when any of these hit.
DAILY_LOSS_LIMIT_PCT = 0.03   # 3% daily drawdown pauses for the day
TOTAL_DRAWDOWN_LIMIT_PCT = 0.10  # 10% from peak equity stops the bot entirely
MAX_CONSECUTIVE_LOSSES = 3    # After this many in a row, require explicit resume


# -----------------------------------------------------------------------------
# OPERATIONAL PARAMETERS
# -----------------------------------------------------------------------------
# How long a pending trade alert stays valid before auto-skipping (in minutes).
TRADE_APPROVAL_TIMEOUT_MINUTES = 60

# Minimum equity required to take new trades. Below this, the bot just holds.
MIN_EQUITY_TO_TRADE = 100.0  # USD


# -----------------------------------------------------------------------------
# NOTES (these are reminders for you, the human, not enforced by code)
# -----------------------------------------------------------------------------
# 1. Do not edit STRATEGY PARAMETERS for at least 3 months after starting.
#    Changing rules after losses is the most common way to ruin a trading
#    system. Let it run. Trust the data.
#
# 2. The strategy is intentionally conservative. It will skip many days
#    with no setups. That is correct behaviour, not a bug.
#
# 3. If you find yourself wanting to add features ("make it scan more
#    tickers", "lower the stop", "increase risk"), STOP and journal the
#    feeling instead. That impulse is what blows up retail traders.
