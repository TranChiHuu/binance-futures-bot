# 2026-11-19 loosening study (user approved "ha xuong run di")
Data: 19 symbols, 1h + 15m + funding, last 730 days to 2026-11-19 10:00 (UTC+7).
- gen.py: raw fresh 20h BREAKOUT candidates (RETEST precedence as live) -> raw.pkl (not committed)
- bt.py: live filter chain + maker GTX entry at breakout close (60 min), TP1 1.5R 50% -> BE, TP2 (cap 4R),
  flatten on 1h close back through level before TP1, maker 0.02% / taker 0.05%, funding, 2 CORE + 2 HIGH slots,
  corr groups, US-data blackout. grid.txt = requested grid; explore.txt = target-fallback sweep; chosen.txt = detail.
Applied: MIN_BREAKOUT_PAST_ATR 0.75 -> 1.0, BREAKOUT with no structure target uses TP2 = 4R (was skipped).
