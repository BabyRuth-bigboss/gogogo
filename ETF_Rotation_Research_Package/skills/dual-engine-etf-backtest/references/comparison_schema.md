# 双引擎比较器口径

## 输入目录

每个运行目录可以包含：

- `signals.csv` 或文件名包含 `signals` 的 CSV
- `trades.csv` 或文件名包含 `trades` 的 CSV
- `equity.csv`、文件名包含 `equity` 的 CSV，或带 `date/timestamp` 与 `equity` 列的 CSV
- `ohlcv_*.csv`：行情快照；日期列可以是 `date` 或 `trade_date`
- `manifest.json`：可选，记录数据源、区间和哈希

比较器会自动识别常见列名：日期 `date/signal_date/trade_date/timestamp`，代码 `code/symbol`，方向 `side`，价格 `price/fill_price`。

## 输出

`dual_comparison.md` 包含指标、快照标的集合、第一处分歧日期、信号向量差异、交易记录差异和归因提示；`dual_comparison.json` 保存机器可读结果。

## 解释规则

- 快照完全一致但目标向量不同：优先归因于指标实现、状态机、资产池、调仓日或预热长度。
- 目标向量一致但成交不同：优先归因于信号偏移、价格字段、滑点、佣金、数量取整或执行顺序。
- OHLCV 不同：先停止收益归因，改做数据一致性审计。
- 只有最终收益相同而信号/成交不同：不能判定两个模型等价。
