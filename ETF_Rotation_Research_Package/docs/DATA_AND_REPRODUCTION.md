# 数据、口径与复现

## 数据来源

- ETF 和指数日线：腾讯财经复权 K 线接口，由脚本即时拉取。
- ETF 池：定义在 `scripts/etf_momentum_rotation.py`，主研究约 41 只行业、主题、宽基与海外 ETF；动态池要求上市至少 252 个交易日。
- 复权处理：`split_adjust_prices` 修复明显的拆分/反向拆分跳变，不替代完整分红再投资复权。

## 共同回测口径

- 信号：仅使用上一交易日收盘前可得的数据。
- 成交：下一交易日开盘；佣金 0.005%（万 0.5）+ 单边滑点 0.10%，单边合计 0.105%。
- 输出：写入 `a_stock_daily_workflow/etf_rotation/backtests/`。发布包已将运行时绝对路径改为相对项目根目录。
- 网络：重跑需要能访问腾讯行情接口；不使用历史缓存时，数据供应商修订可能造成轻微差异。

## 常用命令

```bash
# 审计 2020--2025 的 ETF 长期收益，筛掉年化过差标的
python3 scripts/audit_etf_long_term_returns_2020_2025.py

# 5 年动态池基线 / 市场闸门 / 因子覆盖
python3 scripts/backtest_etf_dynamic_pool_5y.py
python3 scripts/backtest_etf_dynamic_pool_5y_market_gate.py
python3 scripts/backtest_etf_multifactor_overlay_5y.py

# 双袖策略
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v2.py
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v21.py

# 生成已有策略的 PDF 报告（需 reportlab）
python3 scripts/generate_etf_v2_backtest_pdf.py
```

## 独立因子实验室

`modules/quant_factor_lab` 是一个单独的恒生科技因子研究模块：

```bash
cd modules/quant_factor_lab
python3 -m pip install -e .
python3 scripts/run_hstech_research.py
```

它的依赖、数据与测试说明见 `modules/quant_factor_lab/README.md`。
