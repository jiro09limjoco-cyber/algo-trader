# Algo Trader (Paper Trading)

A **paper-trading-only** bot that scans US large-cap stocks for breakout setups,
sends Telegram alerts with YES/NO buttons, and executes approved trades via Alpaca's
paper trading API. Runs free on GitHub Actions.

> ⚠️ **This is a learning project, not an income generator.** SPIVA data shows
> 79% of professional active fund managers underperform the S&P 500 over a single
> year and 92% underperform over 20 years. Expect to underperform too. The point
> is to learn how markets, risk, and your own psychology work — not to make money.

---

## What it does

- **Scans** a watchlist of 23 liquid US large-caps every 30 minutes during US
  market hours
- **Detects setups** when a stock is in a confirmed uptrend, breaking out to a
  new 20-day high, with above-average volume, in a healthy momentum range
- **Alerts you** via Telegram with all relevant numbers
- **Executes** your decision via Alpaca paper trading
- **Enforces risk** through hard-coded circuit breakers (daily/total drawdown
  limits, consecutive-loss pause, position-count cap)

## What it does NOT do

- No live trading (paper only — codebase enforces this at the URL level)
- No leveraged positions, no shorting, no options
- No trailing stops in v1 (fixed -3% only)
- No earnings-event filter (you'll occasionally take a position right before
  earnings — this is a known v1 limitation)

---

## Deployment checklist

Tick each item off:

- [ ] Alpaca paper account created, paper API keys saved, balance reset to your target
- [ ] Telegram bot created via @BotFather, token saved
- [ ] Telegram chat ID known (from @userinfobot)
- [ ] You've sent at least one message to your bot (so it can reply later)
- [ ] GitHub account
- [ ] This repo created on GitHub (public)
- [ ] All 6 files uploaded to the repo
- [ ] GitHub Secrets configured (see below)
- [ ] Actions enabled in repo settings
- [ ] First manual workflow run completed successfully

---

## Setup

### 1. Create the repo

1. Go to https://github.com/new
2. Repository name: `algo-trader` (or anything you like)
3. Visibility: **Public** (for free unlimited Actions minutes)
4. Initialize: don't tick "Add README" — we have our own
5. Create repository

### 2. Upload the files

Easiest path with no command line:

1. On the empty repo page, click "uploading an existing file"
2. Drag-and-drop the whole `algo-trader` folder
3. Wait for upload to complete
4. Scroll down, type a commit message like "Initial commit", click "Commit changes"

You should end up with a repo structure like:

```
algo-trader/
├── README.md
├── requirements.txt
├── .gitignore
├── .github/workflows/scanner.yml
├── .github/workflows/responder.yml
├── .github/workflows/heartbeat.yml
└── bot/
    ├── __init__.py
    ├── config.py
    ├── strategy.py
    ├── alpaca_client.py
    ├── telegram_client.py
    ├── state.py
    ├── scanner.py
    ├── responder.py
    └── heartbeat.py
```

### 3. Add GitHub Secrets

This is where your API keys live. They are encrypted by GitHub and never
appear in code or logs.

In your repo: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these five secrets (case-sensitive names matter):

| Name | Value |
|---|---|
| `ALPACA_API_KEY` | Your Alpaca paper API Key ID (starts with `PK...`) |
| `ALPACA_SECRET_KEY` | Your Alpaca paper Secret Key |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` |
| `TELEGRAM_BOT_TOKEN` | The token from @BotFather (the long `123:ABC...` string) |
| `TELEGRAM_CHAT_ID` | Your numeric chat ID from @userinfobot |

**Important:** `ALPACA_BASE_URL` *must* point to `paper-api.alpaca.markets`.
If you ever change this to the live endpoint, the bot will trade real money.
Don't.

### 4. Enable Actions

Some repos require Actions to be explicitly enabled:

1. Go to the **Actions** tab in your repo
2. If you see "Workflows aren't being run on this forked repository", click
   the button to enable
3. Otherwise you should see three workflows listed: Scanner, Responder, Heartbeat

### 5. First test run

Don't wait for the cron schedule. Trigger manually:

1. **Actions** tab → **Heartbeat** workflow → **Run workflow** → green button
2. Wait ~30 seconds, the run should turn green ✅
3. Check your Telegram — you should get a daily heartbeat message

If you got a Telegram message: **everything works.** You're done.

If you got nothing or an error in the Actions log:

- **`TELEGRAM_BOT_TOKEN env var not set`** → secrets aren't configured correctly
- **`unauthorized`** → wrong API key or wrong base URL
- **`chat not found`** → wrong chat ID, or you haven't messaged the bot first
- **anything else** → copy the error and bring it back to your Claude chat

### 6. Test the scanner

After heartbeat works:

1. **Actions** → **Scanner** workflow → **Run workflow**
2. If US market is closed (most likely from Sydney), the run will just print
   "Market closed. Exiting." — that's correct behaviour
3. To test scanner logic when market is closed, you can temporarily comment
   out the `if not alpaca.is_market_open(): return` lines in `scanner.py`,
   re-run, then put it back

### 7. Test the responder

1. Send `/status` to your bot from Telegram
2. **Actions** → **Responder** workflow → **Run workflow**
3. Within ~30 seconds you should get a status message back

If all three workflows work, the system is fully operational and the cron
schedule will take over.

---

## Usage

### Daily flow

- **Sydney morning (~8am):** heartbeat arrives in Telegram with overnight status
- **Sydney evening / overnight:** scanner fires alerts when setups appear
- **You:** tap YES or NO on each alert within 60 minutes
- **Bot:** executes paper trade on YES, logs result, places stop at Alpaca

### Commands you can send

- `/status` — fresh account snapshot
- `/pause` — stop new scans (open positions are unaffected; Alpaca still manages stops)
- `/resume` — resume scanning and reset consecutive-loss counter
- `/help` — list commands

### When the bot pauses itself

It auto-pauses on any of these conditions. You'll get a Telegram message
explaining why:

- Daily loss exceeds 3% of equity (resets next trading day)
- Total drawdown exceeds 10% from peak equity (stops bot until you /resume)
- 3 consecutive losses (requires /resume to continue)

These are deliberate. Do not raise the limits to "give the strategy room to
work." That is the most common mistake.

---

## Adjusting things

### Safe to change anytime

- The watchlist in `bot/config.py`
- Operational parameters: `TRADE_APPROVAL_TIMEOUT_MINUTES`, `MIN_EQUITY_TO_TRADE`

### Don't change for at least 3 months

- All `STRATEGY PARAMETERS` (SMAs, RSI bounds, breakout lookback)
- All `RISK PARAMETERS` (risk per trade, stop loss %, drawdown limits)

The strategy is conservative on purpose. Premature tweaking based on a
handful of trades is the #1 way to destroy a system that would otherwise
work.

### Never change

- `ALPACA_BASE_URL` secret. Keep it pointing at paper.

---

## Files

| File | Purpose |
|---|---|
| `bot/config.py` | Watchlist, risk parameters, strategy parameters |
| `bot/strategy.py` | Entry rule evaluation + position sizing (pure logic) |
| `bot/alpaca_client.py` | Alpaca API wrapper |
| `bot/telegram_client.py` | Telegram bot API wrapper |
| `bot/state.py` | SQLite state store |
| `bot/scanner.py` | Main scan-and-alert loop |
| `bot/responder.py` | Polls Telegram and executes approved trades |
| `bot/heartbeat.py` | Daily status message |
| `.github/workflows/*.yml` | GitHub Actions cron schedules |
| `state.db` | Auto-created SQLite file (committed by the workflows) |

---

## Cost

- GitHub Actions on public repo: **free, unlimited** for our usage
- Alpaca paper trading: **free**
- Telegram bot: **free**
- Alpaca free-tier IEX market data (delayed): **free**

Total monthly cost: **$0**.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Heartbeat workflow fails with "unauthorized" | Wrong Alpaca keys, or `ALPACA_BASE_URL` typo |
| Heartbeat works but you get no Telegram message | Wrong chat ID, or you haven't messaged the bot first |
| Buttons appear but tapping does nothing within ~5 min | Responder workflow not running — check Actions tab is enabled |
| Bot reports "Market closed" when you expect it open | Correct — US market is closed when most of Sydney is awake |
| State commits failing in workflow logs | A previous run is still mid-push — next run self-heals |
| Pandas/numpy install slow on first run | Normal, pip caches it for subsequent runs |

---

## Honest expectations

After 50–100 trades (probably 4–8 months of running), you should be able to
answer:

1. Did the entry rules actually fire as designed?
2. What was the win rate? Average R-multiple?
3. In what market conditions did it win? Lose?
4. What's the max drawdown you experienced?
5. Did you follow the system, or did you override it?

That data is the actual product of this project. Profit, if any, is a side
effect.

If your honest answer to question 5 is "I overrode it a lot," the system
isn't broken — your discipline is the bottleneck. That is also extremely
useful information about yourself.
