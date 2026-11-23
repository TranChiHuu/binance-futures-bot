# Quant report: backtest of the live rules and lessons from real trades

Run date: 2026-11-11 (Saigon time, UTC+7). Data: 2024-11-11 to 2026-11-11, 19 coins, 1h and 15m candles, real funding history.
All code lives in `quant/`. It does not touch live files and does not place or cancel any order. Only public APIs and read-only APIs (income, userTrades, allOrders) are used.

## 1. Short conclusion

**The current live rules have no edge, and they clearly lose money.**
- Per coin, independently: 11,837 trades over 2 years, average **−0.19R/trade** (95% confidence interval: −0.21 to −0.17), PF 0.70. 0 of 25 months were positive.
- Simulating the full portfolio rules (1 trade per book, 2 trades total, daily stop, US data-release hours, correlation groups) at the current size (CORE 2 USDT, HIGH 6.75 USDT risk per trade): **−1,515 USDT over 2 years** (1,975 trades, −0.24R/trade).
- Compounded simulation starting from 45 USDT: **the wallet falls below 5 USDT on 2025-01-15**, that is after 2 months and 173 trades.

**The main culprit is the RETEST setup.** RETEST is 83% of all trades, averaging −0.23R. It is negative in every group tried: every score level, every break age (0–7h), long and short, CORE and HIGH, every coin, every quarter. Even before fees RETEST is still negative (−0.08R). Adding fees and slippage, with a tight SL (average 0.81%, i.e. 0.67×ATR), costs about another 0.15R per trade.

**The part that can survive is BREAKOUT with SL ≥ 1×ATR:** +0.05R/trade (n=2,128). Year 1 +0.051, year 2 +0.054. A fixed-size portfolio simulation gives +140 USDT/2 years, maxDD 21R. But the confidence interval still contains 0 (−0.00 to +0.11). **This is not evidence of an edge, only the least bad version.**

Walk-forward gives a similar result. Optimizing on 6 months and testing on the next 2 months, repeated 9 times: in-sample reaches +0.071R but out-of-sample is only **−0.001R** (n=1,597). The live rules on the same test windows give −0.21R. Parameter optimization helps avoid losses but does not create profit. The IS→OOS drop of about 0.07R is a sign of overfitting.

## 2. Method (summary)

- `fetch_data.py`: downloads 1h and 15m klines and funding, respecting the weight header and backing off on 418/429 errors.
- `fetch_account.py`: downloads income, userTrades, allOrders since 02/11 (read-only).
- `strategy.py`: a faithful copy of `scan_sym()` from `run_hourly.py`. It cannot be imported directly because the live script runs itself, takes the lock and places orders on import. Differences from live:
  - Uses the last price instead of the mark price. Live SL/TP trigger on MARK_PRICE.
  - Scan time: the "mark" price is the close of the first 15m candle of the hour (about :15). The running candle volume is taken as 1/3 of that 15m candle volume, to simulate a scan at about :05 like live.
  - The 4h trend is aggregated from 1h candles (4h UTC boundaries).
  - tickSize/stepSize are not simulated. minNotional is 5 USDT.
  - lessons.json is held fixed in its current state: RETEST −0.5, all −0.25, TP1 ≤ 1.5R, fee 0.055%. Auto-updating from win/loss streaks is not simulated.
  - Fidelity check against 55 real scan rows in `hourly_log.jsonl`: 71% have the same setup and direction. Of those matches, 72% have an SL that differs by no more than 0.1% (median 0%). The mismatch is mostly from the port scanning at :15 while live scans at :55–:05.
- `engine.py`:
  - Market entry on the next 15m candle, 0.05% adverse slippage. A limit order fills when price touches it (+0.02%) and is cancelled after 2h.
  - SL/TP are simulated on 15m candles pessimistically: if one candle touches both SL and TP, SL counts first. This case is only 0.15% of trades so it does not distort results.
  - TP1 closes 50%, then SL moves to entry ±0.10% from the next candle (exactly like pos_manage). The remainder runs to TP2.
  - Fee 0.055% per side. Funding comes from real history. SL exits slip 0.02%.
  - Portfolio: daily stop −7.5 USDT by Saigon day, the HIGH "room" rule, US data-release hours, static correlation groups, sizing and automatic leverage reduction like live.
- `walkforward.py`, `run_portfolio_tests.py`, `correlation.py`, `live_history.py`, `extra_charts.py`: produce the tables below. Raw results are in `results/*.json`.

Note on metrics: R is profit/loss divided by expected risk, including 2 fees as live computes size, and net of fees, slippage and funding. "Sharpe" is computed from daily R, annualized.

## 3. Live rule results (in-sample, full 2 years)

### 3a. Per coin, independent (no slot limit, so the sample is large enough)

| Group | Trades | Win | Avg R | PF | Sharpe |
|---|---|---|---|---|---|
| All | 11,837 | 41% | −0.187 | 0.70 | −7.1 |
| CORE | 3,224 | 42% | −0.263 | 0.59 | −7.3 |
| CORE / RETEST | 3,070 | 42% | −0.277 | 0.57 | −7.5 |
| CORE / BREAKOUT | 154 | 44% | +0.008 | 1.01 | 0.0 |
| HIGH | 8,613 | 41% | −0.159 | 0.74 | −5.6 |
| HIGH / RETEST | 6,806 | 40% | −0.209 | 0.67 | −7.4 |
| HIGH / BREAKOUT | 1,801 | 44% | +0.032 | 1.06 | 0.5 |
| HIGH / MOMENTUM | 6 | 33% | −0.451 | 0.34 | – |

Cost by setup:
- RETEST: before fees −0.083R, fees 0.146R, average SL 0.81% (0.67×ATR).
- BREAKOUT: before fees +0.077R, fees 0.044R, SL 3.2% (1.86×ATR).

Sensitivity:
- Zero slippage: −0.125R.
- 0.1% slippage: −0.258R.
- Real fee 0.05%: −0.178R.

Dropping all lessons (the original rules from 10/11) is no better: −0.169R.

### 3b. Full portfolio, fixed size (CORE 2 USDT, HIGH 6.75 USDT per R)

| Group | Trades | Win | Avg R | PF | Notes |
|---|---|---|---|---|---|
| All | 1,975 | 41% | −0.236 | 0.63 | −1,515 USDT, maxDD 467R, 3/25 months positive |
| CORE | 1,174 | 41% | −0.291 | 0.56 | |
| HIGH | 801 | 41% | −0.154 | 0.75 | |

Compounded simulation from 45 USDT: falls to 4.7 USDT on 2025-01-15. Charts: `results/equity_portfolio_usdt.png`, `results/equity_portfolio_fixed_usdt.png`.

## 4. Walk-forward / out-of-sample

The optimized parameter set has 972 combinations:
- Minimum score: +0, +0.5 or +1.0.
- SL ATR multiplier: 0.3, 0.4 or 0.6.
- TP1: 1.0, 1.5 or 2.0R.
- Retest distance: 0.4% or 0.8%.
- Break age: 3, 5 or 8h.
- Block re-entry at the same level: yes or no.
- Minimum SL: 0, 0.75 or 1.0×ATR.

Optimize on 6 months (objective is total R, at least 40 trades), test on the next 2 months, repeated 9 times.

| | Trades | Avg R | 95% confidence interval |
|---|---|---|---|
| In-sample (train windows) | 5,104 | +0.071 | +0.036..+0.106 |
| **Out-of-sample (9 test windows combined)** | 1,597 | **−0.001** | −0.062..+0.061 |
| OOS: CORE / BREAKOUT | 62 | +0.383 | +0.06..+0.71 (small sample, may be luck) |
| OOS: CORE / RETEST | 27 | −0.175 | |
| OOS: HIGH / BREAKOUT | 1,334 | +0.019 | −0.05..+0.09 |
| OOS: HIGH / RETEST | 174 | −0.261 | −0.43..−0.09 |
| Live rules on the same test windows | 8,961 | −0.212 | −0.235..−0.188 |

- 5/9 test windows are positive.
- The chosen parameters are fairly stable across windows:
  - Minimum SL 1.0×ATR: chosen 9/9 times.
  - Break age 3h: 9/9.
  - TP1 2.0R: 8/9.
  - Score +0.5 or +1.0: 9/9.
- Both the minimum SL and the break age are chosen at the edge of the grid. This means "the more you filter, the less you lose", not that a profitable optimum was found.
- The fixed-size OOS portfolio gives +0.004R (381 trades, −25 USDT). The compounded version gives 45 → 96.5 USDT, but with only 93 trades and a confidence interval containing 0, it is not reliable.
- **Overfit flag:** in-sample +0.07R but OOS ≈ 0. The top 10 combinations over the full period (in-sample) only reach +0.048R.

Chart: `results/equity_walkforward_oos_usdt.png`.

## 5. Hypotheses drawn from real losing trades (per coin, independent, 2 years)

| Rule tried | Trades | Avg R | Year 1 | Year 2 | Fixed portfolio (USDT/2 years) |
|---|---|---|---|---|---|
| Live rules | 11,837 | −0.187 | −0.162 | −0.213 | −1,515 |
| (a) No re-retest at the same level | 10,304 | −0.183 | −0.158 | −0.208 | −1,512 |
| (b) Break no older than 0h / 1h / 2h / 3h | 6,453 / 8,489 / 9,617 / 10,336 | −0.153 / −0.170 / −0.175 / −0.179 | | | −792 / −1,088 / −1,359 / −1,382 |
| (c) SL ≥ 0.75×ATR | 5,445 | −0.118 | −0.078 | −0.158 | −795 |
| (c) SL ≥ 1.0×ATR | 3,413 | −0.054 | −0.011 | −0.100 | −247 |
| (c) SL ≥ 1.25×ATR | 2,455 | +0.010 | +0.031 | −0.017 | +35 |
| Drop RETEST (BREAKOUT only) | 2,196 | +0.031 | +0.032 | +0.030 | +73 |
| **BREAKOUT only + SL ≥ 1×ATR** | 2,128 | **+0.052** | +0.051 | +0.054 | **+140** (maxDD 21R, 14/25 months positive) |
| BREAKOUT only + SL 1–2×ATR (chosen after seeing the data) | 1,498 | +0.098 | +0.132 | +0.051 | +236 |

Conclusion for each hypothesis:
- **(a)** and **(b)** barely change the per-trade expectancy. They only remove some losing trades and do not turn RETEST profitable.
- **(c)** is the most correct hypothesis: an SL that is too tight relative to noise is the problem. But RETEST with SL ≥ 1.25×ATR is still −0.15R.

Charts: `results/equity_variants_R.png`, `results/equity_portfolio_fixed_compare_usdt.png`.

## 6. Correlation

- The average 30-day correlation across the 19 coins is **0.72**. Even the lowest pair is 0.60 (BNB–WLD). 31% of pair-days exceed 0.8.
- The highest pairs: ETH–LINK 0.84, ARB–OP 0.84, BTC–ETH 0.84, LINK–AVAX 0.82, DOGE–PEPE 0.82.
- In the simulated portfolio there are 951 times when CORE and HIGH had overlapping open trades:
  - 748 times in the same direction (average correlation 0.68).
  - 200 times both lost when in the same direction, versus 48 times when in opposite directions.
- The rule "block same-direction trades if correlation > 0.8" **does not improve results**. The live rules go from −0.236 to −0.243R. BREAKOUT + SL 1×ATR goes from +0.041 to +0.027R. The current static groups also change nothing.
- In practice 2 same-direction trades are almost always one double-size bet. The right control is to reduce the risk per trade, not a correlation threshold.

Charts: `results/correlation_heatmap.png`, `results/correlation_over_time.png`.

## 7. Lessons from real history (since 02/11)

- **Account:** realized −2.36 USDT, fees −1.96, funding −0.02. Total trading **−4.34 USDT**. Fees are about 83% of the realized loss.
- **Real fees:** taker 0.050%, maker 0.020%. The 0.055% in lessons is conservative and stays.
- **Market entry slippage:** orders fill 2–3 minutes after the scan. The deviation from the planned price ranges from −0.18% (favorable) to +0.07% (adverse), on average 0.07% favorable. But the ±0.13% variability equals 0.2–0.3R when the SL is only 0.4–0.6%.
- **SL slippage:** close to 0 (−0.02% to +0.04%). One limit order (BNB) filled after 157 minutes, so live leaves pending orders up to about 3h rather than 2h.
- **Table of 7 trades:**

| Trade | Setup | SL/ATR | MFE | Result | (a) | (b ≤ 3h) | (c ≥ 1 ATR) | Drop RETEST |
|---|---|---|---|---|---|---|---|---|
| ARB long 06/11 | BREAKOUT | 0.41 | 1.69R | −8.46 | | | blocked | |
| XRP short 07/11 | old pullback | 0.70 | 0.52R | −1.72 | | | blocked | |
| SOL short 10/11 | RETEST | ~0.49 | 3.3% | +3.96 | | | blocked | blocked |
| BTC short 10/11 | RETEST | 0.70 | 1.23R | +0.76 | | | blocked | blocked |
| BTC short 11/11 | RETEST (re-entry at level 73368) | 0.69 | 0.63R | −1.49 | blocked | | blocked | blocked |
| BNB short 11/11 | RETEST (break 5h) | 0.47 | 0.54R | −1.63 | | blocked | blocked | blocked |
| ARB short 10/11 (open) | BREAKOUT | 2.57 | 2.10R | +4.29 taken, remainder SL at breakeven | | | | |

- **Reading the table:**
  - All 4 losing trades had SL < 0.75×ATR. The biggest winner (ARB short) had SL 2.57×ATR.
  - Applying rule (c) SL ≥ 1×ATR: avoids −13.30 USDT of losses but loses +4.72 of gains, net +8.58 USDT.
  - Dropping RETEST on these 7 trades gives net −1.60, because SOL was a nice win.
  - 7 trades are far too few to conclude. The number to trust is the 2-year backtest, and it says RETEST is −0.23R.
- **Real trades vs simulation:** running the engine on the exact plan of each real trade gives the same result in all 5 trades with a clear SL (SL / SL / TP1→BE / SL / SL). For ARB long 06/11 only, the simulated MFE is lower than the journal, because the simulation counts from the 15m candle after entry. This shows the engine reflects how real trades play out.

## 8. Recommendations (details in `proposed_changes.json`)

### A. Tighten-only rules (can be auto-applied under your policy; I have NOT applied them)

| | Change | Evidence | Expected impact |
|---|---|---|---|
| A1 | Block RETEST in both books (`score_penalty.RETEST = 99`, no code change needed) | −0.23R over 9,876 trades, negative in 25/25 months, OOS still negative | 2-year portfolio from −1,515 to +73 USDT. About 5 trades/week remain |
| A2 | Block MOMENTUM (`score_penalty.MOMENTUM = 99`) | small n, all negative | Small, reduces risk |
| A3 | Skip trades with SL < 1×ATR(1h,14) (needs one more filter in the script) | All 4 real losing trades had a tight SL. BREAKOUT +0.031 to +0.052R, positive in both years | From +73 to +140 USDT/2 years, maxDD 29R down to 21R |
| A4 | Reduce risk to ≤1% of wallet per trade (about 0.45 USDT) for both books, instead of 2 USDT / 15% of wallet | Even the best variant has maxDD of about 21R. At the current level 21R is larger than the whole wallet. 13/18 variants run the wallet below 5 USDT | No change in R/trade, cuts the maximum loss by 4–15x |
| A5 | Do not re-enter the same level a second time | Harmless but almost no effect at scale | Small |
| A6 (optional, low confidence) | SL cap ≤ 2×ATR for BREAKOUT | Threshold chosen after seeing the data, weak in year 2 | Could go from +140 to +236 USDT, but likely luck |
| A7 (optional) | Lower the daily stop to about −3× risk per trade | Proportional to A4 | – |

### B. Loosen or change behavior (needs your approval)

| | Change | Opinion |
|---|---|---|
| B1 | Pause live trading or run `--dry-run` until the A1+A3 set has at least 50 positive trades | Recommend considering it, since no edge has been proven |
| B2 | Raise the TP1 cap from 1.5R to 2.0R | Not worth it: walk-forward picks 2.0 but the effect is about 0 |
| B3 | Change scan parameters per walk-forward | Not advised: OOS ≈ 0, mostly affects RETEST |
| B4 | Dynamic correlation rule > 0.8 | Not advised: tried, no improvement |
| B5 | Research entering BREAKOUT with limit (maker 0.02%) | Worth pursuing, since fees are the biggest lever left |

## 9. Honestly

- The current bot loses because of the structure of the rules, not bad luck. The current "learning" mechanism (deducting score after a losing streak) cannot fix a setup with negative expectancy.
- The best version found (BREAKOUT only, SL ≥ 1×ATR) is only around breakeven to slightly positive, not enough to call an edge. If live trading continues, it should run with very small risk (A4) and be treated as a data-collection phase.

## 10. B5 research: entering with limit (maker) instead of market (2026-11-11)

**Configuration tested:** exactly the configuration running live, i.e. BREAKOUT only, SL ≥ 1×ATR, TP1 1.5R closing 50% then moving SL to breakeven, the remainder running to TP2. Real fees: taker 0.050%, maker 0.020%.

**How it is simulated:**
- Market entry: 0.05% slippage.
- Limit entry: counts as filled only if a later 15m candle **trades through** the limit price by at least 0.01%. If it is still unfilled after N 15m candles it is cancelled and the trade is lost.
- Limit placement:
  - "level": placed exactly at the level that was just broken.
  - "pbX": placed X% away from the breakout candle close, with X = 0, 0.1, 0.2, 0.3%.
  - Never placed on the wrong side of the current price (post-only).
- SL and TP are recomputed from the real entry price and still have to pass A3 plus the R:R ≥ 2 condition.
- TP has two modes: TAKE_PROFIT_MARKET (taker), or a reduce-only limit order (maker, which must also trade through the price by 0.01%).
- SL and BE are always taker orders, with an extra 0.02% slippage.

### 10a. Per coin, independent, 2 years (in-sample)

| Variant | Fill rate | Trades | Avg R | 95% confidence interval | PF | Market R on the missed trades |
|---|---|---|---|---|---|---|
| Market (running) | 100% | 2,128 | +0.056 | +0.001..+0.110 | 1.10 | – |
| level, N=4 | 74% | 1,786 | +0.073 | +0.013..+0.134 | 1.13 | +0.24 |
| level, N=8, maker TP | 81% | 1,858 | +0.081 | +0.021..+0.140 | 1.14 | +0.38 |
| **pb0, N=4 (1h), maker TP** | **91%** | 2,004 | **+0.081** | +0.024..+0.137 | 1.15 | +0.40 |
| pb0.1, N=8, maker TP | 92% | 2,010 | +0.079 | +0.023..+0.136 | 1.14 | +0.58 |
| pb0.2, N=8, maker TP | 88% | 1,955 | +0.073 | +0.016..+0.131 | 1.13 | +0.52 |
| pb0.3, N=4, maker TP | 79% | 1,848 | +0.085 | +0.025..+0.145 | 1.15 | +0.28 |

- All 30 maker variants lie in +0.066..+0.085R, all higher than market (+0.056R) by about +0.01..+0.03R. The improvement comes from cheaper fees and a better entry price.
- **There is clear adverse selection:** the limit orders that do not fill are winners that ran away. Had they entered at market, those trades would have averaged +0.24 to +0.67R. Conversely, the limit orders that do fill would have been around 0R at market.
- Fees and the better entry price make up for the missed part, but only leave a little extra.
- Maker TP adds about +0.003R per trade. Very small.

### 10b. Walk-forward

Using exactly the same 9 windows as section 4: in each window choose the best maker variant on the 6-month train, then test on the following 2 months.

| | Trades | Avg R | 95% confidence interval |
|---|---|---|---|
| Maker, in-sample | 4,409 | +0.114 | +0.076..+0.153 |
| **Maker, out-of-sample** | 1,333 | **+0.039** | −0.028..+0.106 |
| Market, in-sample (same windows) | 4,947 | +0.065 | +0.029..+0.100 |
| Market, out-of-sample (same windows) | 1,497 | +0.017 | −0.046..+0.080 |

- The chosen variant changes from window to window (pb0.3/N4 chosen 3 times, level 2 times, other types 1 time each), meaning no single parameter really stands out.
- Out-of-sample, maker beats market by about **+0.02R per trade**. Both confidence intervals still contain 0.

### 10c. Compounded portfolio from 45 USDT

The simulation includes the A7 daily stop and applies the exchange's real minQty/MIN_NOTIONAL. Maker here is "pb0, N4, maker TP": limit placed at the breakout candle close, cancelled after 1h.

| Variant / risk | Final wallet | Max DD | Trades/week | MC: median final wallet (5%..95%) | MC: % ending < 20 | MC: % ever touching < 20 |
|---|---|---|---|---|---|---|
| Market 1% | 49.6 | 12.0 USDT (19.7%) | 5.7 | 51.6 (30.8..86.5) | 0% | 0% |
| Market 2% | 49.4 | 26.4 (35.6%) | 5.8 | 52.0 (19.1..140) | 5.6% | 11.4% |
| Market 3% | 37.0 | 43.3 (55.2%) | 5.8 | 37.0 (8.9..174) | 23.4% | 43.3% |
| **Maker 1%** | **63.2** | **9.5 (14.6%)** | 5.4 | 64.7 (39.6..107) | 0% | 0% |
| Maker 2% | 76.0 | 22.1 (28.9%) | 5.5 | 78.0 (30.5..226) | 0.6% | 3.7% |
| Maker 3% | 84.4 | 39.9 (43.2%) | 5.5 | 85.7 (19.9..386) | 5.1% | 14.5% |

- MC is a 1,000-path bootstrap, resampling with replacement from the backtest's own trades. It therefore **assumes the backtest edge is real**.
- A harsher check: use the same volatility but set the expectancy to the out-of-sample level (+0.039R), or to 0.

| Expectancy | Risk | % ending < 20 USDT | % ever touching < 20 USDT | Median final wallet |
|---|---|---|---|---|
| OOS +0.039R | 1% | 0% | 0.1% | 52.8 |
| OOS +0.039R | 2% | 4% | 10% | 56.5 |
| OOS +0.039R | 3% | 14% | 30% | 55.0 |
| 0R | 1% | 0.7% | 0.9% | 42.2 |
| 0R | 2% | 16% | 29% | 36.1 |
| 0R | 3% | 37% | 55% | 28.1 |

**Skipped for insufficient min-notional** (2 years, maker):
- Risk 1%: AVAX 35 times (stepSize 1, hard to split the TP in half), BTC 11, ETH 8, LINK 5, NEAR 4, other coins 1–2.
- Risk 2%: AVAX 20, BTC 8, ETH 1.
- Risk 3%: AVAX 11, BTC 7.
- The market version is similar.
- Currently BTC needs a risk of at least about 0.51 USDT per trade with SL 1×ATR, i.e. a wallet of about 51 USDT at 1%.

### 10d. B5 conclusion

- **Entering with a limit maker order is a real but small improvement:** about +0.02R per trade, stable across all variants and still present out-of-sample. It does not turn the strategy into one with a solid edge, because OOS +0.04R has a confidence interval that still contains 0.
- If applied, choose the simple option with many fills: a post-only limit at the breakout candle close, cancelled after 1h, with TP placed as a reduce-only limit. This loses about 9% of trades, mostly winners that ran away.
- **Risk level: keep 1%.**
  - At 1%, the probability that the wallet ever falls below 20 USDT is almost 0, even if the strategy has no edge.
  - At 2%, if the real edge is only the OOS level then there is about a 10% chance the wallet ever touches 20 USDT. With no edge it is about 29%.
  - At 3%, this risk rises to 30–55%.
- **Should this rule be applied with a larger wallet?** A larger wallet helps in two ways:
  - It removes the min-notional limit, so BTC, AVAX, ETH/LINK can be traded when the SL is wide.
  - Trades are easier to split in half for TP.

  But a larger wallet **does not make the edge bigger**, since R per trade in percentage terms is the same. Increase capital only after about 100 real trades give results like the backtest, and keep risk as a percentage (1%), not growing with the amount.
