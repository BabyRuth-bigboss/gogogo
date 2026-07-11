# Shuishui Stock Strategy Reference v2

This reference encodes the reusable parts of the “水水” trading framework as actionable A-share screening and trade-planning rules.

## 1. Philosophy

Core sequence:

```text
主线 -> 核心票 -> 标准图 -> 盈亏比 -> 仓位 -> 止损执行
```

Reusable principles:

1. Trade only inside a market or sector mainline.
2. Prefer leaders, second leaders, core constituents, ETF weights, and recognizable names.
3. Require a standard chart structure.
4. Calculate entry, stop, target, and risk/reward before buying.
5. Size by account risk, not conviction.
6. Stop failed structures; do not average down after invalidation.

Do not reuse:

1. Leverage by default.
2. Full-position or “all-in” sizing.
3. -30% stop loss as a routine setting.
4. Macro story as a substitute for entry/stop rules.
5. Winner-only examples as proof.

## 2. Five Gates

| Gate | Pass Criteria | Fail Action |
| --- | --- | --- |
| Market | Index trend supports long exposure | No new long trade |
| Theme | Sector is a current/emerging capital mainline | C pool or delete |
| Status | Stock is leader, second leader, core weight, or recognized target | Downgrade |
| Pattern | Fits A/B/C/D model below | Wait |
| Trade | Entry/stop/target/RR/position are valid | No trade |

## 3. Stock Pool Fields

Preferred schema:

| Field | Meaning |
| --- | --- |
| date | screening date |
| code | stock code |
| name | stock name |
| board | main, chinext, star, bse, etf |
| sector | industry/theme |
| market_regime | strong, neutral, weak, panic |
| sector_rank_20d | sector strength rank |
| sector_volume_ratio | sector 5d amount / 20d amount |
| stock_amount_20d | stock 20d average amount |
| rs_20d | stock 20d return minus benchmark 20d return |
| rs_60d | stock 60d return minus benchmark 60d return |
| above_ma20 | close above MA20 |
| above_ma60 | close above MA60 |
| ma20_slope | MA20 slope |
| base_days | consolidation/base days |
| breakout_level | horizontal resistance or box upper bound |
| close | close price |
| volume_ratio | daily volume / 20d average volume |
| pattern_type | breakout, commodity_map, box, pullback, risk |
| entry_price | planned entry |
| stop_price | planned stop |
| target_price | first target |
| rr | risk/reward |
| score | total score |
| pool | A, B, C, D |
| invalidation | failure condition |

Minimum useful schema:

```text
date, code, name, sector, close, sector_rank_20d,
stock_amount_20d, pattern_type, entry_price,
stop_price, target_price, rr, score, pool
```

## 4. Score

100-point score:

| Module | Points | Notes |
| --- | --- | --- |
| Market | 10 | index trend, liquidity, breadth |
| Sector | 20 | sector strength, persistence, volume, diffusion |
| Stock status | 15 | leader, second leader, core weight, liquidity |
| Pattern | 25 | clear resistance, box, pullback, or wave structure |
| Volume | 10 | breakout volume, pullback contraction |
| Risk/reward | 10 | 1:2 = 6, 1:3 = 8, 1:5 = 10 |
| Execution | 10 | distance to entry/stop, liquidity, T+1 risk |
| Risk deduction | -30 | failed breakout, bad liquidity, late chase, major risk |

Pool mapping:

| Score | Pool |
| --- | --- |
| 90-100 | A, tradeable if plan complete |
| 80-89 | A/B, precise trigger or small size |
| 70-79 | B/C, observe only |
| <70 | no trade |
| Risk pattern | D/delete unless repaired |

## 5. Pattern Models

### A_breakout: Mainline Breakout

Source pattern: horizontal resistance + descending pressure line + volume breakout.

Screening:

| Condition | Standard |
| --- | --- |
| Sector | 20d strength in top 20% |
| Liquidity | 20d average amount > 200M RMB, unless user has small-account exception |
| Base | at least 20 trading days; prefer 40+ |
| Resistance | clear horizontal resistance |
| Contraction | lower highs or descending pressure line |
| Breakout | close above resistance and pressure |
| Volume | volume > 1.5x 20d average |
| Entry distance | within 3%-5% above breakout level |

Rules:

```text
entry = breakout_level * 1.00 to 1.03
or entry = pullback to breakout_level and hold
stop = min(breakout_candle_low, breakout_level * 0.97-0.98)
target_1 = entry + 2R
target_2 = entry + 3R
trend_exit = close below MA20 or prior swing low
```

Invalidation:

1. Falls back into the base within 3 trading days.
2. Breakout candle has long upper shadow and no next-day repair.
3. Sector index breaks MA20.
4. Sector leader breaks before the candidate confirms.

### B_commodity: Commodity Mapping Catch-up

Source pattern: futures/spot commodity leads; related stock or ETF catches up.

Screening:

| Condition | Standard |
| --- | --- |
| Commodity | futures/spot trend confirmed |
| Sector | related equity sector starts expanding volume |
| Stock | core chain stock, ETF weight, leader, or low-position catch-up |
| Chart | breaks MA20/MA60, box upper bound, or prior high |
| Correlation | commodity, sector, stock: at least two confirm |
| Timing | not late-stage consecutive surge |

Rules:

```text
entry = stock/ETF breaks platform or confirms pullback
stop = platform lower bound or commodity trendline failure
target_1 = prior high
target_2 = related ETF prior high or 3R
time_stop = reduce/exit if no catch-up in 3-5 sessions
```

Invalidation:

1. Commodity falls below MA20.
2. Policy pressure hits commodity price.
3. Sector has one-day pulse without diffusion.
4. Candidate underperforms both ETF and leader.

### C_box: Long Base / Box Breakout

Source pattern: large prior decline, long base, box upper break.

Screening:

| Condition | Standard |
| --- | --- |
| Location | prior decline > 40% |
| Box | at least 60 trading days |
| Resistance | box upper bound touched multiple times |
| MA | MA20/MA60 flattening or rising |
| Breakout | volume close above box upper bound |
| Pullback | best entry is retest that holds |

Rules:

```text
entry = break box upper bound or retest and hold
stop = box_upper * 0.97-0.98
hard_stop = box midpoint
target = box_upper + box_height
```

Invalidation:

1. Breakout has no volume.
2. Quickly falls back into box.
3. Pullback to box upper bound has heavy selling.
4. Sector does not cooperate.

### D_pullback: 3/5-Wave Trend Continuation

Source pattern: “发财就 3.5 浪”; constructive pullback inside a mainline trend.

Screening:

| Condition | Standard |
| --- | --- |
| Trend | higher highs and higher lows |
| First wave | clear volume-backed advance |
| Pullback | holds prior high, MA20, or platform |
| Restart | volume expands after pullback |
| Sector | still mainline |

Rules:

```text
entry = pullback holds and reclaims short MA
stop = below recent swing low
target_1 = entry + 2R
target_2 = equal measured move of prior leg
trend_exit = close below MA20 or swing low
```

Invalidation:

1. Fails to make new high.
2. Low breaks below prior low.
3. Bounce cannot reclaim prior high.
4. High-volume bearish candle appears near top.

### R_risk: Risk / Delete Pattern

Do not buy; downgrade or delete.

| Pattern | Meaning | Action |
| --- | --- | --- |
| failed prior high | breakout failed; support becomes resistance | delete or wait repair |
| trendline break | original uptrend damaged | reduce/stop |
| right shoulder | high-level rebound failure | do not buy bounce |
| lower highs | demand weakening | downgrade |
| lower lows | downtrend forming | delete |
| volume without progress | distribution or churn | reduce |
| long upper shadow after acceleration | emotional late stage | no chase |

## 6. Risk/Reward

For long trades:

```text
E = entry price
S = stop price
T = target price
R = E - S
RR = (T - E) / (E - S)
```

Minimum:

| RR | Action |
| --- | --- |
| <2 | no trade |
| 2-3 | small trade |
| 3-5 | preferred |
| >5 | high quality if target realistic |

Break-even win rate without costs:

```text
required_win_rate = 1 / (1 + RR)
```

Always account for transaction costs, slippage, T+1, and limit-down risk.

## 7. Position Sizing

Base formula:

```text
account_size = A
risk_per_trade = r
allowed_loss = A * r
planned_share_risk = entry - stop + estimated_slippage
shares = allowed_loss / planned_share_risk
```

Recommended `r`:

| Stage | risk_per_trade |
| --- | --- |
| simulation | 1.0%-2.0% |
| small live test | 0.3%-0.8% |
| stable live | 0.5%-1.0% |
| high-volatility theme | 0.3%-0.5% |

Nominal position cap by score:

| Condition | Initial | Max |
| --- | --- | --- |
| 70-79 | 0 | observe only |
| 80-84, RR 2 | 3%-5% | 8% |
| 85-89, RR 3 | 5%-8% | 12%-15% |
| 90+, RR >3 | 8%-10% | 20% |
| 90+ and backtested | 10%-15% | 20%-30% |

## 8. A-Share T+1 / Limit Risk

Because many A-share stocks cannot be sold on the buy day and may hit limit down, use an extreme-risk cap:

```text
planned_risk_rate = (entry - stop) / entry
extreme_risk_rate = limit_down_rate * stress_factor
effective_risk_rate = max(planned_risk_rate, extreme_risk_rate)
max_position_pct = risk_per_trade / effective_risk_rate
```

Default assumptions:

| Instrument | Limit assumption | stress_factor |
| --- | --- | --- |
| main board | 10% | 0.5-1.0 |
| STAR/ChiNext | 20% | 0.5-1.0 |
| ETF | 5%-10% | 0.5 |

Example:

```text
risk_per_trade = 1%
ChiNext extreme = 20% * 0.5 = 10%
max_position = 1% / 10% = 10%
```

With full limit-down stress:

```text
max_position = 1% / 20% = 5%
```

## 9. Trade Execution

Allowed buys:

1. Breakout buy: close above key resistance with volume.
2. Pullback buy: breakout retest holds and turns up.
3. Catch-up buy: commodity/mainline strong, related core stock just breaks out.

Forbidden buys:

1. Entry too far above stop.
2. RR below 2.
3. Target unknown.
4. Risk pattern active.
5. No sector support.
6. Hot chat but no chart confirmation.

Stop triggers:

| Trigger | Action |
| --- | --- |
| close back below breakout line | sell if not repaired next session |
| below breakout candle low | sell |
| below recent swing low | sell |
| below MA20 for two sessions | sell trend position |
| no follow-through in 3-5 sessions | reduce or exit |

Take profit:

| Trigger | Action |
| --- | --- |
| reaches 2R | sell 1/3, move stop near cost |
| reaches 3R | sell 1/3, trail by MA10 or swing low |
| trend continues | hold 1/3 until MA20/swing exit |
| volume long upper shadow | reduce |
| closes below MA20 | exit trend remainder |

## 10. Pool Assignment

| Pool | Meaning | Action |
| --- | --- | --- |
| A | passes five gates and has complete plan | trade only by plan |
| B | good theme/status but trigger not ready | wait |
| C | has logic but chart not standard | observe |
| D | risk pattern or invalidated logic | delete |

A-pool plan must include:

```text
entry:
stop:
target_1:
target_2:
RR:
initial_position:
add_rules:
stop_rules:
take_profit_rules:
invalidation:
```

## 11. Backtest Spec

Example hypothesis:

```text
Strong-sector core stocks that consolidate at least 20 days and then break 60-day high on >1.5x volume have positive expectancy over the next 20-40 sessions.
```

Model A test:

Entry:

```text
close > highest close of last 60 sessions
volume > 20d average volume * 1.5
20d average amount > 200M RMB
sector 20d rank in top 20%
buy next open or breakout close
```

Exit:

```text
stop below breakout candle low
sell 1/3 at 2R
sell 1/3 at 3R
sell remainder below MA20
max holding 40 sessions
```

Parameter tests:

| Parameter | Values |
| --- | --- |
| base days | 20, 40, 60 |
| breakout period | 60, 120, 250 |
| volume multiple | 1.2, 1.5, 2.0 |
| stop | breakout candle low, 5%, 8%, 10%, 12% |
| take profit | 2R, 3R, 5R, MA20 trailing |
| holding period | 10, 20, 40, 60 |
| market regime | strong, neutral, weak |

Validation rules:

1. Test all matching cases, not only winners.
2. Add costs and slippage.
3. Vary parameters and seek stable plateaus.
4. Fewer than 30 trades is not enough.
5. 100+ trades gives initial confidence.
6. 200+ trades before raising size.

## 12. Output Templates

Pool output:

```text
code | name | sector | model | score | pool | entry | stop | target | RR | max_position | invalidation | note
```

Single-stock output:

```text
Gate result:
Model:
Score:
Entry:
Stop:
Target:
RR:
Max position:
Invalidation:
Action:
```

Trade log:

```text
date:
code:
name:
sector:
model:
pool:
market_regime:
sector_rank_20d:
score:
entry_price:
stop_price:
target_price:
rr:
planned_position:
actual_position:
entry_reason:
invalidation:
exit_plan_2R:
exit_plan_3R:
actual_exit:
result_R:
mistake:
lesson:
```

## 13. Execution Tightening From Live Review

These rules were added after reviewing a 2026-06-30 stock-pool trade plan against 2026-07-03 closing prices. Treat them as execution discipline, not curve-fitted signal rules.

### Plan Freshness

Breakout and short-term tracking plans expire quickly:

```text
For A_breakout and D_pullback plans:
plan_age_days <= 1 is preferred.
plan_age_days > 2 means the entry, stop, and RR must be rebuilt from current data.
```

Every plan should include:

```text
plan_date
quote_date
plan_age_days
current_price
distance_to_entry_pct
current_rr_to_T1
trigger_status
paper_trade_status
actual_R after exit or review
```

### Current RR Recalculation

Do not keep using the original RR after price has moved.

```text
If the stock was not filled near entry and current_price is more than 5% above entry,
recalculate RR from current_price.
If current_rr_to_T1 < 2, do not buy.
```

This rule applies even if the original plan had RR >= 3.

### Breakout Tranching

For A_breakout, prefer confirmation over full-size first entry:

```text
First trigger: buy at most 1/2 of planned position.
Add: only if the next 1-2 sessions hold above entry/breakout level and the sector remains strong.
Delete: if price falls back into the base or touches stop within 1-3 sessions.
```

### Repair Confirmation

A failed or weak breakout cannot be revived by an intraday spike alone:

```text
Intraday reclaim of entry/resistance: watch only, no trade.
Close back above entry/resistance: eligible for observation, but rebuild the plan.
Two consecutive closes back above entry/resistance with volume support: eligible for a new trade plan.
Intraday reclaim followed by a close below stop: failed repair, delete the old plan.
```

Once a plan has been stopped or deleted, do not reuse the old entry/stop/RR. Rebuild the chart and risk/reward from current data.

### Review Accounting

Separate watchlist quality from simulated trading performance:

```text
Watchlist review: evaluate every candidate.
Paper-trade PnL: count only stocks that actually triggered under the plan.
Missed stocks: do not count as wins or losses.
Deleted/waiting stocks: count as process feedback, not PnL.
```

### Failure Throttle

Reduce exposure after repeated failed breakouts:

```text
Two consecutive -1R trades: halve risk per new trade.
Three failed triggers or watchlist breakage > 50%: pause new entries and review market/sector gates.
```
