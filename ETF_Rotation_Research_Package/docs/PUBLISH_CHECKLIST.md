# 发布检查清单

## 已完成

- ETF 回测脚本已复制为相对项目路径版本，并通过 `python3 -m py_compile`。
- 当前回测总结、V2/V2.1 明细、PDF 报告和研究笔记已归档。
- 恒生科技因子实验室以独立模块保留。
- 已写明数据、交易成本、回测口径和已知偏差。

## 发布前建议执行

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install pytest
python3 -m pytest -q modules/quant_factor_lab/tests
```

随后任选一条策略重新运行，确认当前数据源与本包快照的差异：

```bash
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v21.py
```

## 许可证与免责声明

本包未附带许可证文件。公开发布前，项目维护者应自行选择并添加许可证（例如 MIT、Apache-2.0 或仅限研究使用的自定义条款），并确认其中包含的第三方技能文档及数据接口的再发布条件。回测研究不构成投资建议。
