# 双引擎 ETF 回测对比

- T0: `/Users/yansenz/Documents/marketing/a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2`
- T1: `/Users/yansenz/Desktop/Vibe-Trading/agent/runs/etf_v2_ablation/runs/T1_tencent_vibe_execution`

## 指标

| 指标 | T0 | T1 |
|---|---:|---:|
| total_return | 3.5666482900440553 | 3.1323924163801955 |
| cagr | 0.35567221730142795 | 0.24315851538975086 |
| max_drawdown | -0.2170934156199278 | -0.23917892857579104 |
| sharpe | 1.2138010835100692 | 1.0148716134835738 |
| final_value | 4463173.167197284 | 4132392.416380196 |

## 快照

- T0 标的数：50
- T1 标的数：52
- 共同标的：50
- T0 独有：无
- T1 独有：159960, 510500

## 信号与成交

- 信号：{'available': True, 'a_rows': 1210, 'b_rows': 1579, 'same_keys': 1210, 'a_only': 0, 'b_only': 369, 'first_a_only': [], 'first_b_only': [('2020-01-02',)]}
- 成交：{'available': True, 'a_rows': 94, 'b_rows': 78, 'same_keys': 0, 'a_only': 94, 'b_only': 78, 'first_a_only': [('2021-07-12', '159995', 'BUY', '0.7999')], 'first_b_only': [('2021-07-13', '159995', 'BUY', '0.8007')]}

结论必须结合快照、信号和成交差异判断，不能只依据最终收益。
