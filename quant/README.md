# quant/ - research layer for the live bot

Read-only research: never imports or edits live files, never places/cancels orders.

```
python3 fetch_data.py 1h funding 15m   # public klines + funding -> data/*.parquet (resumable, rate-limit aware)
python3 fetch_account.py               # READ-ONLY income/userTrades/allOrders since 2026-11-02
python3 run_backtest.py                # baseline of current live rules -> results/baseline.json + equity PNGs
python3 walkforward.py                 # 972-combo walk-forward (6m train / 2m test) -> results/walkforward.json
python3 correlation.py                 # rolling 30d correlation -> results/correlation.json + PNGs
python3 run_portfolio_tests.py         # hypotheses (a)(b)(c), corr rules, WF portfolio replay
python3 live_history.py                # real fills vs plan, port fidelity, per-trade analysis
python3 extra_charts.py                # comparison charts + OOS breakdown
```
Files: `strategy.py` (faithful port of run_hourly.scan_sym), `engine.py` (exit sim + portfolio rules),
`metrics.py`, `common.py` (signal cache). Report: `REPORT.md`; proposals: `proposed_changes.json`.
