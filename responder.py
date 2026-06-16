"""
Responder - polls Telegram for button taps and acts on them.

Run by responder.yml every 5 minutes during market hours.

Handles:
  - YES/NO button taps on pending trade alerts
  - /pause and /resume text commands
  - /status text command (on-demand snapshot)
  - Expired pending trades (auto-skip after timeout)
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone

from . import alpaca_client as alpaca
from . import config
from . import state
from . import telegram_client as tg


def _is_authorized(user_id: int) -> bool:
    """Only the configured chat owner can interact with the bot."""
    return str(user_id) == os.environ.get("TELEGRAM_CHAT_ID", "")


def _handle_approval(trade_id: str, callback_id: str) -> None:
    """User tapped YES. Execute the paper trade."""
    trade = state.get_pending(trade_id)
    if not trade:
        tg.answer_callback(callback_id, "Trade not found.")
        return
    if trade["status"] != "pending":
        tg.answer_callback(callback_id, f"Already {trade['status']}.")
        return

    # Has it expired?
    if datetime.now(timezone.utc).isoformat() > trade["expires_at_utc"]:
        state.set_pending_status(trade_id, "expired")
        tg.answer_callback(callback_id, "Expired.")
        if trade.get("telegram_message_id"):
            tg.edit_message(
                trade["telegram_message_id"],
                f"⌛ <b>{trade['ticker']} EXPIRED</b> (no response in time)",
            )
        return

    ticker = trade["ticker"]
    shares = float(trade["shares"])
    stop_price = float(trade["stop_price"])

    # Final safety check: re-verify we don't already hold it and we're under limit
    try:
        if alpaca.position_exists(ticker):
            state.set_pending_status(trade_id, "rejected")
            tg.answer_callback(callback_id, "Already holding.")
            tg.edit_message(
                trade["telegram_message_id"],
                f"⚠️ <b>{ticker} SKIPPED</b> - already have a position.",
            )
            return
        if len(alpaca.get_positions()) >= config.MAX_CONCURRENT_POSITIONS:
            state.set_pending_status(trade_id, "rejected")
            tg.answer_callback(callback_id, "Max positions reached.")
            tg.edit_message(
                trade["telegram_message_id"],
                f"⚠️ <b>{ticker} SKIPPED</b> - at max position limit.",
            )
            return
    except Exception as e:
        tg.answer_callback(callback_id, f"Pre-check failed: {e}")
        return

    # Place the order
    try:
        order = alpaca.place_bracket_buy(ticker, shares, stop_price)
    except alpaca.AlpacaError as e:
        # Try fractional fallback if bracket failed (e.g. qty < 1 share)
        try:
            order = alpaca.place_fractional_buy_with_pending_stop(
                ticker, shares, stop_price
            )
        except alpaca.AlpacaError as e2:
            state.set_pending_status(trade_id, "failed")
            tg.answer_callback(callback_id, "Order failed.")
            tg.edit_message(
                trade["telegram_message_id"],
                f"❌ <b>{ticker} ORDER FAILED</b>\n<pre>{str(e2)[:500]}</pre>",
            )
            return

    state.set_pending_status(trade_id, "approved")
    tg.answer_callback(callback_id, "Order placed.")
    tg.edit_message(
        trade["telegram_message_id"],
        (
            f"✅ <b>{ticker} APPROVED</b>\n"
            f"Shares: {shares:.4f}\n"
            f"Entry: ~${trade['entry_price']:.2f}\n"
            f"Stop:  ${stop_price:.2f}\n"
            f"Order ID: <code>{order.get('id', 'unknown')}</code>"
        ),
    )


def _handle_rejection(trade_id: str, callback_id: str) -> None:
    """User tapped NO."""
    trade = state.get_pending(trade_id)
    if not trade:
        tg.answer_callback(callback_id, "Trade not found.")
        return
    state.set_pending_status(trade_id, "rejected")
    tg.answer_callback(callback_id, "Skipped.")
    if trade.get("telegram_message_id"):
        tg.edit_message(
            trade["telegram_message_id"],
            f"⏭️ <b>{trade['ticker']} SKIPPED</b> by user.",
        )


def _handle_command(text: str) -> None:
    """Process text commands sent in the chat."""
    cmd = text.strip().lower().split()[0] if text else ""
    if cmd == "/pause":
        state.set_bool("paused", True)
        tg.send_message("⏸️ <b>Bot paused.</b> No new scans until /resume.")
    elif cmd == "/resume":
        state.set_bool("paused", False)
        state.set_int("consecutive_losses", 0)  # reset on manual resume
        tg.send_message("▶️ <b>Bot resumed.</b>")
    elif cmd == "/status":
        try:
            account = alpaca.get_account()
            equity = float(account.get("equity", 0))
            positions = alpaca.get_positions()
            peak = state.get_float("peak_equity", equity)
            paused = state.get_bool("paused", False)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            pos_txt = "\n".join(
                f"  {p['symbol']}: {p['qty']} @ ${float(p['avg_entry_price']):.2f} "
                f"(P&L: ${float(p.get('unrealized_pl', 0)):.2f})"
                for p in positions
            ) or "  (none)"
            tg.send_message(
                f"<b>📊 Status</b>\n"
                f"Equity: ${equity:.2f}\n"
                f"Peak:   ${peak:.2f}\n"
                f"DD:     {dd:.1f}%\n"
                f"Paused: {paused}\n"
                f"Positions:\n{pos_txt}"
            )
        except Exception as e:
            tg.send_message(f"⚠️ Status error: {e}")
    elif cmd in ("/help", "/start"):
        tg.send_message(
            "<b>Commands</b>\n"
            "/status - account snapshot\n"
            "/pause  - stop new scans\n"
            "/resume - resume scans\n"
            "/help   - this message"
        )


def run_responder() -> None:
    """Main entry point. Called by responder.yml."""
    try:
        last_update_id = state.get_int("last_update_id", 0)
        offset = last_update_id + 1 if last_update_id else None

        updates = tg.get_updates(offset=offset, timeout=0)

        for upd in updates:
            state.set_int("last_update_id", upd["update_id"])

            # Callback queries (button taps)
            cb = upd.get("callback_query")
            if cb:
                from_id = cb.get("from", {}).get("id")
                if not _is_authorized(from_id):
                    tg.answer_callback(cb["id"], "Unauthorized.")
                    continue
                data = cb.get("data", "")
                if data.startswith("approve:"):
                    _handle_approval(data.split(":", 1)[1], cb["id"])
                elif data.startswith("reject:"):
                    _handle_rejection(data.split(":", 1)[1], cb["id"])
                else:
                    tg.answer_callback(cb["id"], "Unknown action.")
                continue

            # Text messages (commands)
            msg = upd.get("message")
            if msg:
                from_id = msg.get("from", {}).get("id")
                if not _is_authorized(from_id):
                    continue
                text = msg.get("text", "")
                if text.startswith("/"):
                    _handle_command(text)

        # Expire stale pending trades and notify
        expired = state.expire_old_pending(datetime.now(timezone.utc).isoformat())
        for ex in expired:
            if ex.get("telegram_message_id"):
                try:
                    tg.edit_message(
                        ex["telegram_message_id"],
                        f"⌛ <b>{ex['ticker']} EXPIRED</b> (no response in time)",
                    )
                except Exception:
                    pass

        print(f"Processed {len(updates)} updates, expired {len(expired)} trades.")

    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            tg.send_message(f"⚠️ <b>Responder error</b>\n<pre>{err[:1500]}</pre>")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run_responder()
