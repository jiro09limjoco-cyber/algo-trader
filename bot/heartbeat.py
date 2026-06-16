"""
Heartbeat - daily "I'm alive" message.

Sent once per day in your morning (Sydney time). Provides a daily checkpoint:
  - Account equity
  - Open positions and P&L
  - Total drawdown from peak
  - Yesterday's outcome
  - Pause status
"""
from __future__ import annotations

import sys
import traceback

from . import alpaca_client as alpaca
from . import state
from . import telegram_client as tg


def run_heartbeat() -> None:
    try:
        account = alpaca.get_account()
        equity = float(account.get("equity", 0))
        cash = float(account.get("cash", 0))
        positions = alpaca.get_positions()

        peak = state.get_float("peak_equity", equity)
        if equity > peak:
            state.set_float("peak_equity", equity)
            peak = equity
        dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0

        paused = state.get_bool("paused", False)
        status_emoji = "⏸️" if paused else "✅"

        positions_text = "  <i>(none open)</i>"
        total_unrealized = 0.0
        if positions:
            lines = []
            for p in positions:
                upl = float(p.get("unrealized_pl", 0))
                total_unrealized += upl
                lines.append(
                    f"  {p['symbol']}: {float(p['qty']):.4f} @ "
                    f"${float(p['avg_entry_price']):.2f} → "
                    f"${float(p['current_price']):.2f}  "
                    f"({'+' if upl >= 0 else ''}{upl:.2f})"
                )
            positions_text = "\n".join(lines)

        msg = (
            f"{status_emoji} <b>Daily heartbeat</b>\n"
            f"\n"
            f"Equity:   ${equity:.2f}\n"
            f"Cash:     ${cash:.2f}\n"
            f"Peak:     ${peak:.2f}\n"
            f"Drawdown: {dd_pct:.1f}%\n"
            f"Open P&L: ${total_unrealized:+.2f}\n"
            f"Paused:   {paused}\n"
            f"\n"
            f"<b>Positions ({len(positions)}):</b>\n"
            f"{positions_text}\n"
            f"\n"
            f"<i>Reply /status anytime for a fresh snapshot.</i>"
        )
        tg.send_message(msg)
        print("Heartbeat sent.")
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            tg.send_message(f"⚠️ <b>Heartbeat error</b>\n<pre>{err[:1500]}</pre>")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run_heartbeat()
