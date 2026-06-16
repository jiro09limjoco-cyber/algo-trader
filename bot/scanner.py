"""
Scanner - the main "look for setups" loop.

Run by the scanner.yml GitHub Actions workflow every 30 minutes during US
market hours. Steps:

1. Bail early if market closed, bot paused, or circuit breaker tripped.
2. For each ticker in watchlist:
   a. Skip if we already have a pending alert for it (avoid duplicate spam).
   b. Skip if we already have an open position in it.
   c. Fetch daily bars from Alpaca.
   d. Evaluate strategy.
   e. If buy signal: calculate size, send Telegram alert, record pending trade.
3. Update peak equity tracker.

Idempotent on retry: rerunning the same minute doesn't re-alert the same
setup because of the de-dup checks.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone

from . import alpaca_client as alpaca
from . import config
from . import state
from . import strategy
from . import telegram_client as tg


def _format_alert(ticker: str, sig, ps, equity: float, expires_at: datetime) -> str:
    """Build the human-readable Telegram alert text."""
    pct_of_equity = (ps.notional_usd / equity * 100.0) if equity > 0 else 0.0
    pct_risk = (ps.risk_usd / equity * 100.0) if equity > 0 else 0.0
    return (
        f"<b>📈 SETUP: {ticker}</b>\n"
        f"\n"
        f"Entry:     <b>${sig.close:.2f}</b>\n"
        f"Stop:      ${ps.stop_price:.2f}  (-{config.STOP_LOSS_PCT*100:.1f}%)\n"
        f"Shares:    {ps.shares:.4f}\n"
        f"Position:  ${ps.notional_usd:.2f}  ({pct_of_equity:.1f}% of equity)\n"
        f"Risk:      ${ps.risk_usd:.2f}  ({pct_risk:.2f}% of equity)\n"
        f"\n"
        f"<i>{sig.reason}</i>\n"
        f"\n"
        f"Expires:   {expires_at.strftime('%H:%M UTC')}\n"
        f"\n"
        f"Tap a button to decide:"
    )


def _check_circuit_breakers(equity: float) -> str | None:
    """Returns a string reason if any breaker is tripped, else None."""
    if state.get_bool("paused", False):
        return "Bot is paused (use /resume to unpause)."

    # Total drawdown check
    peak = state.get_float("peak_equity", equity)
    if equity > peak:
        state.set_float("peak_equity", equity)
        peak = equity
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    if drawdown >= config.TOTAL_DRAWDOWN_LIMIT_PCT:
        state.set_bool("paused", True)
        return (
            f"Total drawdown {drawdown*100:.1f}% exceeds limit "
            f"{config.TOTAL_DRAWDOWN_LIMIT_PCT*100:.0f}%. Bot stopped."
        )

    # Daily loss check
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_day = state.get_state("daily_loss_day")
    if last_day != today_utc:
        state.set_state("daily_loss_day", today_utc)
        state.set_float("daily_loss_baseline_equity", equity)
    baseline = state.get_float("daily_loss_baseline_equity", equity)
    daily_change = (equity - baseline) / baseline if baseline > 0 else 0.0
    if daily_change <= -config.DAILY_LOSS_LIMIT_PCT:
        return (
            f"Daily loss {daily_change*100:.1f}% exceeds limit "
            f"{config.DAILY_LOSS_LIMIT_PCT*100:.0f}%. Pausing for the day."
        )

    # Consecutive losses
    cl = state.get_int("consecutive_losses", 0)
    if cl >= config.MAX_CONSECUTIVE_LOSSES:
        return (
            f"{cl} consecutive losses. Bot paused. "
            f"Reply /resume from Telegram to continue."
        )

    return None


def run_scan() -> None:
    """Main entry point. Called by scanner.yml."""
    try:
        # 1. Market check
        if not alpaca.is_market_open():
            print("Market closed. Exiting.")
            return

        # 2. Account & breakers
        account = alpaca.get_account()
        equity = float(account.get("equity", 0))

        block_reason = _check_circuit_breakers(equity)
        if block_reason:
            print(f"Scan blocked: {block_reason}")
            return

        if equity < config.MIN_EQUITY_TO_TRADE:
            print(f"Equity ${equity:.2f} below MIN_EQUITY_TO_TRADE. Skipping.")
            return

        # 3. Check open positions limit
        open_positions = alpaca.get_positions()
        held_symbols = {p["symbol"] for p in open_positions}

        if len(open_positions) >= config.MAX_CONCURRENT_POSITIONS:
            print(f"Already at max positions ({len(open_positions)}). Skipping.")
            return

        # 4. Check pending alerts to avoid re-alerting same symbol
        pending_symbols = {p["ticker"] for p in state.get_all_pending()}

        slots_left = config.MAX_CONCURRENT_POSITIONS - len(open_positions)
        alerts_sent = 0

        for ticker in config.WATCHLIST:
            if alerts_sent >= slots_left:
                break  # don't queue more than we can fill
            if ticker in held_symbols:
                continue
            if ticker in pending_symbols:
                continue

            try:
                bars = alpaca.get_daily_bars(ticker, days_back=config.BARS_LOOKBACK_DAYS)
                if bars.empty:
                    continue
                sig = strategy.evaluate(ticker, bars)
            except Exception as e:
                print(f"[{ticker}] data/eval error: {e}")
                continue

            if not sig or not sig.is_buy:
                continue

            ps = strategy.size_position(sig.close, equity)
            if ps.shares <= 0 or ps.notional_usd < 1.0:
                print(f"[{ticker}] position size too small ({ps.shares}). Skipping.")
                continue

            # 5. Record + send alert
            trade_id = state.new_trade_id()
            expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=config.TRADE_APPROVAL_TIMEOUT_MINUTES
            )
            state.insert_pending_trade(
                trade_id=trade_id,
                ticker=ticker,
                entry_price=sig.close,
                stop_price=ps.stop_price,
                shares=ps.shares,
                notional_usd=ps.notional_usd,
                risk_usd=ps.risk_usd,
                expires_at_utc=expires_at.isoformat(),
            )

            text = _format_alert(ticker, sig, ps, equity, expires_at)
            try:
                msg = tg.send_message(text, reply_markup=tg.approval_keyboard(trade_id))
                state.attach_message_id(trade_id, msg["message_id"])
                alerts_sent += 1
                print(f"[{ticker}] alert sent. trade_id={trade_id}")
            except Exception as e:
                # If Telegram fails, mark trade as failed so it doesn't sit
                state.set_pending_status(trade_id, "failed")
                print(f"[{ticker}] Telegram send failed: {e}")

        print(f"Scan complete. {alerts_sent} alerts sent.")

    except Exception:
        # Top-level catch so the workflow doesn't silently fail
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            tg.send_message(f"⚠️ <b>Scanner error</b>\n<pre>{err[:1500]}</pre>")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run_scan()
