# Binance Futures Bot

Automated trading bot for **Binance USDT-M Futures**. It scans 19 coins every hour, sizes each trade by risk, and places the entry together with a stop loss and two take profits. A 60-second watcher then manages the open position.

> **Real money.** `run_hourly.py` places live orders unless you pass `--dry-run`. Risk per trade is set to 25% of the wallet, and the backtest in [`quant/REPORT.md`](quant/REPORT.md) found no proven edge in the earlier rule set. Start with `--dry-run`.

## How it works

```
every hour                          every 60 s
run_hourly.py                       pos_watch.py
  scan 19 coins (CORE + HIGH)         TP1 filled?  ->  pos_manage.py
  pick a BREAKOUT candidate                              move SL to breakeven
  size by risk, set leverage                             cancel stale maker entries
  entry + SL + TP1 + TP2                                 journal closed trades
  Telegram report                                        update lessons.json
```

### Books and setups

| | CORE | HIGH |
|---|---|---|
| Coins | BTC, ETH, SOL, BNB, XRP, LINK | DOGE, SUI, AVAX, 1000PEPE, WIF, NEAR, APT, ... (13 coins, see `HIGH` in `run_hourly.py`) |
| Minimum score | 3.5 | 2.5 |
| Max concurrent trades | 2 | 2 (2 total across both books) |

- **BREAKOUT** is the only setup that can open a trade. RETEST and MOMENTUM are blocked by `trade/lessons_locked.json` after the backtest.
- The break candle must go at least **1.0 ATR** past the 20h level, and the SL must be at least **1.0 ATR** from entry.
- Entry is a post-only (GTX) **maker limit** at the breakout candle close. It is cancelled with its SL/TP after 60 minutes if unfilled.
- TP1 closes 50% and moves the SL to breakeven. TP2 is the structure target, or a 4R fallback. Required R:R to TP2 is at least 2.
- Close-back rule: if a 1h candle closes back through the breakout level, the position is flattened and any unfilled entry is cancelled.

### Risk

- Risk per trade: **25% of wallet** for both books. The drawdown factor can only reduce it, never exceed 25%.
- Daily stop: **-50%** of the start-of-day wallet (Saigon time, UTC+7).
- Isolated margin. Leverage is capped so liquidation sits at least 1.5x the SL distance away.
- No new trades while the Binance IP ban is active (`trade/ip_ban.py`).

## Setup

Requires Python 3 (developed on 3.15) and Binance API keys with futures trading enabled. No third-party packages are needed for the live bot.

```bash
touch .env   # then add the three variables below
```

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
```

For Telegram, put your chat id in `business/.telegram_chat_id` (git-ignored). The bot does not read `.env` by itself, so export it first:

```bash
set -a && . ./.env && set +a
```

## Usage

```bash
python3 bn.py                          # check keys, balance and open positions
python3 trade/run_hourly.py --dry-run  # full scan, no orders, no Telegram -> trade/_dry_run.json
python3 trade/run_hourly.py            # live: scan and place orders
```

Run `run_hourly.py` from cron (or any scheduler) once per hour. It starts `pos_watch.py` by itself when a position or pending entry exists, and the watcher exits when everything is flat.

### Tests

```bash
for t in trade/test_*.py; do python3 $t; done
```

## Project layout

| Path | Purpose |
|---|---|
| `bn.py` | Signed Binance API helper. Routes through a local tunnel when one is available |
| `tg_trade.py` | Telegram notifier (reads text from stdin) |
| `trade/run_hourly.py` | Hourly scan, risk sizing, order placement |
| `trade/risk_tier.py` | Risk per trade, daily stop, leverage and liquidation safety |
| `trade/breakout_rules.py` | Shallow-break filter and level-invalidation rule |
| `trade/slots.py` | Concurrent-trade slot accounting |
| `trade/ip_ban.py` | Detects and honors Binance IP bans |
| `trade/pos_manage.py` | Breakeven move, maker-entry expiry, journal, lessons |
| `trade/pos_watch.py` | 60 s watcher around `pos_manage` |
| `trade/lessons.json` | Lessons learned from closed trades (rules can only tighten) |
| `trade/lessons_locked.json` | Rules locked in by the backtest |
| `trade/trade_journal.jsonl` | Journal of closed trades |
| `quant/` | Read-only research: backtest, walk-forward, correlation. See [`quant/README.md`](quant/README.md) |

State files (`risk_state.json`, `pos_state.json`, `last_run.json`, logs) are written to `trade/` and are git-ignored.

## Research

The [`quant/`](quant/) folder holds a 2-year backtest of the live rules on 19 coins. Headline results from [`quant/REPORT.md`](quant/REPORT.md):

- The original rules (with RETEST) averaged **-0.19R per trade**, so RETEST and MOMENTUM were blocked.
- BREAKOUT with SL >= 1 ATR is about **+0.05R per trade**, but the confidence interval still contains 0.
- Maker entries beat market entries by about +0.02R per trade out-of-sample.

`quant/` never imports or edits live files and never places orders.

## Security

No keys are stored in the repo. Everything comes from environment variables, and `.env` is git-ignored.
