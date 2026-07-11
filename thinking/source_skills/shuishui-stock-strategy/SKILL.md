---
name: shuishui-stock-strategy
description: A股股票池筛选、个股交易评分、图形策略分析和交易计划生成技能，基于“水水”交易体系的主线、标准图形、盈亏比、止损和仓位规则。Use when the user asks to 用水水策略/水水体系 筛股票池、分析A股个股、识别突破/箱体/回踩/商品映射图形、计算盈亏比、止损、仓位、生成交易计划、复盘交易或把股票分入A/B/C/D池。
---

# Shuishui Stock Strategy

## Core Rule

Apply this sequence every time:

```text
主线 -> 核心票 -> 标准图 -> 盈亏比 -> 仓位 -> 止损执行
```

Never turn a macro story, chat-room opinion, or “looks strong” feeling into a buy plan unless the trade passes the gate checks, risk/reward calculation, and stop-loss plan.

## Safety Frame

Treat all outputs as research and process support, not personalized investment advice. When the user asks about current prices, current rankings, or today’s market state, verify with current market data or ask for the user’s exported data. If no current data is available, clearly mark assumptions.

Do not recommend leverage, full-position buying, or averaging down after stop failure. The reusable part of the source strategy is structure recognition and risk control, not high-risk sizing.

## Load Reference

Read `references/strategy-v2.md` when doing any substantive task:

- scoring a stock or stock pool
- creating a trading plan
- calculating risk/reward, stop loss, or position size
- classifying patterns into A/B/C/D models
- designing or validating a screener/backtest

For a very short conceptual answer, you may use only the core rule above.

## Workflow

### 1. Identify Task Type

Classify the user request:

- **Pool screening**: user provides a stock list, CSV, sector list, or asks to build A/B/C/D pools.
- **Single-stock evaluation**: user asks whether a stock fits the strategy.
- **Trade plan**: user gives or asks for entry/stop/target/position sizing.
- **Review/backtest**: user wants to validate, refine, or stress-test the rules.
- **Education**: user asks to explain the strategy.

### 2. Require Inputs

For stock-pool screening, prefer these fields:

```text
date, code, name, sector, close, sector_rank_20d, stock_amount_20d,
rs_20d, rs_60d, above_ma20, above_ma60, base_days,
breakout_level, volume_ratio, pattern_type
```

For a trade plan, require:

```text
entry_price, stop_price, target_price, account_size, risk_per_trade
```

If missing, either compute from available data or state that the result is provisional.

### 3. Apply Five Gates

Every candidate must pass:

1. **Market gate**: index environment allows long exposure.
2. **Theme gate**: sector is a current or emerging capital mainline.
3. **Status gate**: stock is a leader, second leader, core constituent, ETF weight, or highly recognized name.
4. **Pattern gate**: chart fits one model from the reference.
5. **Trade gate**: entry, stop, target, risk/reward, and position size are valid.

If any gate fails, downgrade the stock instead of forcing a trade.

### 4. Classify Pattern

Use these model codes:

- `A_breakout`: mainline breakout; horizontal resistance + descending pressure + volume breakout.
- `B_commodity`: commodity/futures lead, stock or ETF follows as mapped catch-up.
- `C_box`: long base/box breakout after large decline.
- `D_pullback`: 3/5-wave trend continuation after constructive pullback.
- `R_risk`: risk pattern, such as failed breakout, broken trendline, right shoulder, lower highs/lows, distribution.

### 5. Score

Use a 100-point score:

```text
market 10 + sector 20 + stock_status 15 + pattern 25
+ volume 10 + risk_reward 10 + execution 10 - risk_deductions up to 30
```

Pool assignment:

```text
90-100: A pool, tradeable if plan is complete
80-89: A/B pool, wait for precise trigger or use small size
70-79: B/C pool, observe only
<70: do not trade
Risk pattern: D/delete unless repaired
```

### 6. Calculate Risk

For long trades:

```text
R = entry_price - stop_price
risk_reward = (target_price - entry_price) / R
```

Minimum:

```text
risk_reward >= 2.0
```

Position size:

```text
allowed_loss = account_size * risk_per_trade
shares = allowed_loss / (entry_price - stop_price + estimated_slippage)
```

For A-shares, adjust for T+1 and limit-down risk:

```text
planned_risk_rate = (entry_price - stop_price) / entry_price
extreme_risk_rate = limit_down_rate * stress_factor
effective_risk_rate = max(planned_risk_rate, extreme_risk_rate)
max_position_pct = risk_per_trade / effective_risk_rate
```

Use this to cap the nominal position.

### 7. Output Format

For a stock pool, output a table:

```text
code | name | sector | model | score | pool | entry | stop | target | RR | max_position | invalidation | note
```

For a single stock, output:

1. Gate result
2. Pattern classification
3. Score
4. Entry/stop/target/risk-reward
5. Position sizing if account/risk inputs exist
6. Invalidation conditions
7. Final action: A pool / B wait / C observe / D delete

For a trade plan, include:

```text
Entry:
Stop:
Target 1:
Target 2:
RR:
Initial position:
Add rules:
Stop rules:
Take-profit rules:
Invalidation:
```

### 8. Backtest Discipline

When asked to validate the strategy, insist on:

- explicit hypothesis
- no subjective discretionary fields in the test
- all matching cases, not cherry-picked winners
- slippage and costs
- parameter sensitivity
- at least 30 trades for rough signal, 100+ for initial confidence, 200+ before increasing size

Prefer strategies that break the least under stress, not those that look best in one parameter setting.
