# ETF Rotation Research Package

面向中国场内 ETF 的动量轮动研究归档，包含策略代码、截至 2026-07-11 的回测快照、研究笔记、PDF 报告和可复用的方法论技能。

## 先读这里

- [策略目录](docs/STRATEGY_CATALOG.md)：策略规则、结果和证据强度。
- [回测结果总览](docs/BACKTEST_RESULTS.md)：可横向比较的结果与口径。
- [数据与复现](docs/DATA_AND_REPRODUCTION.md)：数据来源、成本、运行命令和前视偏差控制。
- [限制与待修复项](docs/KNOWN_LIMITATIONS.md)：发布前必须理解的风险。
- [结果目录说明](results/README.md)：CSV、历史总结和报告的位置。

## 快速开始

```bash
cd ETF_Rotation_Research_Package
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v2.py
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v21.py
```

策略脚本仅依赖 Python 标准库；生成 PDF 需要 `reportlab`。独立的 `modules/quant_factor_lab` 模块依赖 `pandas` 和 `numpy`，可按其 README 安装。

## 当前最值得继续验证的三条线

1. **V2.1 双袖轮动**：5 年总收益 +390.45%，年化 +37.50%，最大回撤 -19.93%，Sharpe 1.33。收益与回撤平衡较好，但仍有样本内选择和行业集中风险。
2. **V2 双袖轮动**：5 年总收益 +442.86%，年化 +40.32%，最大回撤 -20.98%，Sharpe 1.37。是收益冠军，适合作为进攻候选，不应直接当作实盘定稿。
3. **RS612 Top2 + MA120 市场闸门**：5 年总收益 +176.37%，年化 +22.59%，最大回撤 -18.49%，Sharpe 1.02。规则短、便于审计，是更干净的对照基线。

这些数字为历史模拟，不构成投资建议或收益承诺。详见 [限制与待修复项](docs/KNOWN_LIMITATIONS.md)。

## 目录

```text
docs/       发布说明、策略目录、可复现说明和原始研究文档
scripts/    ETF 轮动、市场闸门、因子与报告生成脚本
results/    当前回测快照、CSV 和全部历史总结
reports/    已生成 PDF
skills/     回测方法论与 A 股数据能力文档
modules/    可独立运行的恒生科技因子实验室
```

## 发布说明

发布前请至少阅读 `docs/KNOWN_LIMITATIONS.md`，并在自己的环境中重新拉取数据、重跑策略。`scripts/etf_rotation_v2_live.py` 仅为实验性信号脚本，尚未与 V2 回测逐日逐笔完成一致性验证。
