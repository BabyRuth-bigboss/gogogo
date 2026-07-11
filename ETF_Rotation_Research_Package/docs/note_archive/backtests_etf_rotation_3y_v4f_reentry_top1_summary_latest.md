---
tags:
  - etf
  - quant/backtest
  - quant/strategy
---

# ETF动量轮动3年回测 V4f 再入只买第一名

规则：保持V4e，但提前再入场只买final_score第一名，不买第二第三名

| 项目 | 策略 | 沪深300 | 创业板指 | 科创50 |
|---|---:|---:|---:|---:|
| 总收益 | +3.37% | +26.02% | +81.26% | +118.28% |
| 年化 | +1.11% | +8.03% | +21.97% | +29.77% |
| 最大回撤 | -17.74% | -21.42% | -32.38% | -35.79% |
| Sharpe | 0.15 | 0.54 | 0.80 | 0.95 |

## 年度收益
- 2023: +0.00%
- 2024: -9.43%
- 2025: +23.87%
- 2026: -7.85%

## 交易
- 调仓日：45，买卖笔数：79，平均仓位：+19.55%。
- 主要原因：rebalance=32；daily_forced_clear:跌破MA20且10日动量转负=19；weekly_reentry_after_clear=9；reduce_or_exit=6；market_weak_force_cash=2；daily_forced_clear:20日回撤12.2%>12%=2；daily_forced_clear:20日回撤12.7%>12%=1；daily_forced_clear:20日回撤12.6%>12%=1
