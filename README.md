# binance-futures-bot

Automated Binance USDT-M Futures trading bot (2 books: CORE and HIGH), scans every hour.

- `bn.py`: signed Binance API helper (env `BINANCE_API_KEY`, `BINANCE_API_SECRET`).
- `tg_trade.py`: sends Telegram notifications (env `TELEGRAM_BOT_TOKEN`, chat id in a separate file, not committed).
- `trade/run_hourly.py`: scans setups (BREAKOUT / RETEST / MOMENTUM), computes risk, places the order + SL + 2 TPs. Use `--dry-run` for a trial run.
- `trade/risk_tier.py`: risk per trade is 25% of the wallet (CORE and HIGH, approved 2026-11-20); the drawdown factor can only reduce it, never exceed 25%. Daily stop is -50% of the start-of-day wallet. At most 2 concurrent trades.
- `trade/pos_manage.py`: moves SL to breakeven after TP1.
- `trade/pos_watch.py`: 60s watcher, reports TP1 / BE / closed trades and writes the journal.
- `trade/lessons.json`: lessons learned from closed trades (rules can only be tightened).
- `trade/trade_journal.jsonl`: journal of closed trades.

No keys are stored in the repo; everything comes from environment variables.
