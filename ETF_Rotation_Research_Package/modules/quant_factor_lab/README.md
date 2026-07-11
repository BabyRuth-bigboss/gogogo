# Quant Factor Lab（A 股 / ETF）

一个小而可审计的日频因子研究 MVP。它计算动量、反转、低波和量价因子，输出 Rank IC、分组收益、组合净值、换手与成本。信号在 T 日收盘形成，T+1 收盘成交，持仓从随后一个周期开始贡献收益。

## 快速开始

```bash
cd /Users/yansenz/Documents/marketing/quant_factor_lab
../.venv/bin/python -m pip install -e .

quant-factor fetch --symbols 510300 510500 159915 588000 512100 \
  --start 20160101 --end 20260710 --asset etf --output data/etf.csv

quant-factor research --input data/etf.csv --factor momentum_20 \
  --top-n 2 --frequency weekly --asset etf --output-dir output/momentum20
```

本地 CSV 必须包含 `date,symbol,open,high,low,close,volume`。可研究的内置因子为 `momentum_20`、`momentum_60`、`reversal_5`、`low_vol_20`、`volume_ratio_20`。

数据建议顺序：付费 Point-in-Time 数据（全市场个股） > Qlib 自备数据管线 > Tushare Pro > AKShare/腾讯/通达信公开接口。公开接口可能受代理、限流或上游字段变化影响，因此下载后应冻结原始快照并记录校验值；不要在每次回测时在线重拉。

## 上实盘前必须补齐

- 使用含退市证券、历史指数成分、真实披露时间的 Point-in-Time 数据。
- 个股撮合加入 T+1 可卖、100 股整数手、最低佣金、停牌、ST/主板/科创板涨跌停与不可成交队列。
- 做滚动样本外、参数平台、逐年/牛熊状态、成本 1.5–2 倍压力测试。
- 通过 PaperBroker 连续模拟至少四周，再单独接 QMT/VeighNa；真实交易开关、单笔/单日限额和 kill switch 必须在券商适配层。

本项目仅用于研究，不构成投资建议，也不保证未来收益。

## 恒生科技周频状态策略

```bash
cd /Users/yansenz/Documents/marketing/quant_factor_lab
../.venv/bin/python scripts/run_hstech_research.py --refresh
```

候选池来自 `config/hstech_constituents_20260608.csv`。脚本比较慢速趋势轮动与快速突破两类策略，参数选择只使用 2024 年以前的数据，2024 年以后作为样本外；输出参数网格、逐年收益、回撤区间、交易、权重和重点股归因。云音乐（9899）不是该期恒生科技成分，因此只观察、不进入组合。
