---
tags:
  - etf
  - guide
  - quant/backtest
  - quant/comparison
  - quant/dynamic-pool
  - quant/factor
  - quant/regime-switching
  - quant/strategy
---

# ETF 轮动策略体系与回测新手指南

本指南旨在帮助新加入团队的成员快速理解当前仓库中的 ETF 轮动策略体系、数据获取机制、回测环境依赖，以及如何运行回测、生成报告和理解回测结果。

---

## 1. 策略体系概览

当前仓库的策略体系经历了从**单袖绝对动量轮动**（弱市空仓）到**双袖自适应强弱市轮动**（弱市切入防御资产）的迭代。主要分为以下三个板块：

```text
                               ┌────────────────────────┐
                               │  1. 3年早期策略 (V1-V4) │ ◄── 熊市防守测试
                               └───────────┬────────────┘
                                           │
                                           ▼
                               ┌────────────────────────┐
                               │  2. 5年单袖动态池回测   │ ◄── 市场闸门与量化因子叠加
                               └───────────┬────────────┘
                                           │
                                           ▼
                               ┌────────────────────────┐
                               │  3. 5年双袖自适应轮动   │ ◄── V1/V2/V3 (强弱市状态机+海外/债/金防御)
                               └────────────────────────┘
```

### 1.1 3年早期策略族 (V1 - V4)
* **目的**：测试在 2023-2026 年（A股极端弱市/熊市环境）下动量轮动的表现。
* **演进**：
  * **V1（日频）**：频繁换仓，在熊市中磨损严重。
  * **V2（周频+冷却期）**：降低换手。
  * **V3（强市开仓，弱市现金）**：引入强弱判断，弱市全仓保持现金。
  * **V4（各种变体）**：限制目标 ETF 必须在 MA20 上方，其中 **V4b（每月第一周调仓）** 防守效果最好，在3年大熊市中通过极低的持仓（平均仓位 14%）实现了正收益（+16.59%）和极低的回撤（-15.44%）。

### 1.2 5年单袖动态池与市场闸门策略
* **目的**：利用较长历史（5年，2021-2026）验证“动态入池”（ETF上市满252个交易日才允许买入）以及“市场大盘闸门”的有效性。
* **核心结论**：**市场闸门是单袖策略的生命线**。当沪深300、创业板指、科创50中有2个跌破 MA120 时，次日全仓清仓保持现金。该闸门使策略最大回撤从 `-41.2%` 大幅降至 `-18.49%`，Sharpe 提升至 `1.02`。
* **多因子叠加**：在动量基础之上，横截面叠加**流动性因子 (20%)** 和 **趋势质量 (10%)** 能够显著提高收益率并避免小微 ETF 的交易滑点。

### 1.3 5年双袖轮动策略 (Regime Switching) —— **当前主线**
* **核心逻辑**：强市（进攻袖）全仓轮动行业 ETF，弱市（防御袖）不空仓，而是配置流动性好、相对独立的防御性资产（如美股 ETF、国债 ETF、黄金 ETF）。
* **版本迭代**：
  * **V1 双袖**：使用简单大盘闸门切换，弱市买入纳指与标普 500。
  * **V2 双袖**：引入 **RegimeEngine 强弱市自适应状态机**。利用 6 大指数的 MA120 斜率、死叉判断、连续 2 天确认防抖、5天冷却期。交易笔数大幅下降（从 152 次降到 104 次），年化收益达到 **40.32%**，最大回撤仅 **-20.98%**。
  * **V2.1 双袖**：将进攻仓位从 Top 2 变为 Top 3（配权 `45/45/10`），成功将全期最大回撤压到 **`-19.93%`** 的硬风控红线内。
  * **V3 双袖**：修复了 V2 波动率动态降仓的 Bug；扩展了防御池，引入**短融、国债、黄金**以隔离 QDII 溢价泡沫；实现了**滚动样本外测试（Walk-Forward Analysis）**。

---

## 2. 依赖与运行环境

### 2.1 核心回测引擎
* **零外部依赖**：为了最大程度地保证回测效率并消除第三方库版本冲突，当前所有的核心回测代码（包括 `RegimeEngine`、模拟交易模块、指标计算模块）都基于 **Python 3 标准库** 实现。
* **依赖模块**：`csv`、`json`、`math`、`statistics`、`pathlib`、`importlib`、`datetime`、`concurrent.futures`。
* **运行命令**：使用系统自带的 `python3` 即可直接运行。

### 2.2 报告生成依赖
* **依赖库**：生成 PDF 图表报告需要使用 `reportlab` 库。
* **推荐环境**：在 Codex 工作区运行，使用自带的 Python 路径，其中已预装了 `reportlab` 等环境：
  ```bash
  /Users/yansenz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
  ```

---

## 3. 数据源与获取机制

仓库中所有策略的数据获取与调整逻辑位于 [etf_momentum_rotation.py](file:///Users/yansenz/Documents/marketing/scripts/etf_momentum_rotation.py) 与 [backtest_etf_dynamic_pool_5y.py](file:///Users/yansenz/Documents/marketing/scripts/backtest_etf_dynamic_pool_5y.py)。

### 3.1 核心数据接口 (腾讯证券 K 线)
回测使用腾讯的日线行情接口，相比东财接口，它能提供长达 7 个交易年（最多 1800 根日K）的历史原始 K 线：
```text
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{limit},
```
* **获取逻辑**：代码在 `fetch_tencent_adjusted_day()` 中请求未复权的原始日线，然后利用 `split_adjust_prices()` 执行连续化调整。

### 3.2 份额拆分与合并调整机制 (`split_adjust_prices`)
* **痛点**：ETF 经常发生份额折算（拆分或合并），导致价格产生折半或翻倍的断层，若直接回测会导致动量评分及净值计算彻底失真。
* **解决**：在载入历史数据时，逆向检测相邻交易日开盘价/收盘价变化比例。若跳空比例超限（`< 0.55` 或 `> 1.85`），则将其判定为份额折算事件，计算调整因子（Split Factor）并对历史所有价格进行前复权式连续化修正。同时保留原始成交额以进行流动性过滤。

### 3.3 实盘/实时数据接口（用于实盘信号生成）
在每日实盘信号脚本 [etf_rotation_v2_live.py](file:///Users/yansenz/Documents/marketing/scripts/etf_rotation_v2_live.py) 中，除了下载历史 K 线，还会调用以下实时接口：
1. **实时二级市场价格**（东财实时 Quote）：
   ```text
   https://push2.eastmoney.com/api/qt/stock/get?secid={market_prefix}.{code}&fields=f43,f44,...
   ```
2. **实时基金净值估算**（天天基金估值）：
   ```text
   https://fundgz.1234567.com.cn/js/{code}.js
   ```
   * **作用**：实时二级市场价格除以天天基金的估算 NAV，计算出**实时溢价率**。当溢价率 `> 5%` 时直接一票否决禁止买入，规避高溢价买入海外 QDII 的溢价风险。

---

## 4. 回测规范与基准口径

为了保证各个策略的对比具有科学性，所有五年期的研究脚本都采用以下统一口径：

| 项目 | 口径与规则 |
|---|---|
| **初始资金** | 1,000,000.0 元 (100万) |
| **交易成本** | 佣金万0.5 (0.005%) + 交易滑点单边0.10% = 单边合计 **0.105%** |
| **开仓前置** | 动态入池：ETF 必须在回测时间点已上市并积累了至少 252 个交易日历史，才可进入打分池 |
| **信号确认** | 前一交易日收盘后产生信号（无未来函数） |
| **交易执行** | 下一交易日开盘价成交（Open Price Execution） |
| **停牌处理** | 停牌期间不能交易；估值使用最近可得收盘价（Prev Close）兜底计算，不污染最大回撤 |

---

## 5. 如何运行不同策略的回测

运行回测前，需先进入仓库根目录：
```bash
cd /Users/yansenz/Documents/marketing
```

### 5.1 运行 3年早期策略套件
```bash
# 一键运行 V1 - V4 的 3年期策略对比
python3 scripts/run_etf_rotation_backtest_suite.py
```

### 5.2 运行 5年动态池基线与大盘闸门策略
```bash
# 运行动态池基线 (无闸门)
python3 scripts/backtest_etf_dynamic_pool_5y.py

# 运行大盘均线闸门策略 (测试不同MA均线效果)
python3 scripts/backtest_etf_dynamic_pool_5y_market_gate.py
```

### 5.3 运行 5年多因子叠加与参数广搜
```bash
# 叠加低波、趋势质量、近高点、流动性因子
python3 scripts/backtest_etf_multifactor_overlay_5y.py

# 在大范围内搜索最优参数 plateau
python3 scripts/search_etf_high_return_strategy_5y.py
python3 scripts/search_etf_winner_neighborhood_5y.py
```

### 5.4 运行双袖轮动 V2 与 V3 策略
```bash
# 运行双袖自适应轮动 V2 (状态机切换)
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v2.py

# 运行双袖轮动 V3 (修复波动率控制，扩展债券/黄金防御，执行 Walk-Forward 测试)
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v3.py
```

---

## 6. 如何读取回测结果与生成报告

### 6.1 输出文件路径与命名规范
回测生成的所有明细都存储在 `a_stock_daily_workflow/etf_rotation/backtests/` 的对应子目录中。以双袖 V3 为例，输出位于：
`a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v3/`

核心输出文件包括：
* **`*_summary_latest.md`**：回测的文字总结，包含收益排名表、对比表和核心结论。
* **`*_metrics_latest.csv`**：所有网格搜索参数组合的全期、样本内（IS）、样本外（OOS）指标汇总表。
* **`top1_*_equity_latest.csv`**：最优策略的逐日组合总资产、持仓比例、现金明细。
* **`top1_*_trades_latest.csv`**：最优策略的逐笔交易账单（包含交易代码、方向、价格、费用、调仓原因等）。
* **`top1_*_signals_latest.csv`**：最优策略的逐日决策信号（包括当天指数强弱状态、目标持仓等）。

### 6.2 生成 PDF 报告
对于双袖 V2 策略，可以运行专属的脚本生成包含精美净值图的 PDF 报告：
```bash
# 使用 Codex 工作区 Python 运行生成器
/Users/yansenz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/generate_etf_v2_backtest_pdf.py

# 查看报告
open output/pdf/etf_regime_dual_sleeve_v2_backtest_report.pdf
```

---

## 7. 给新人的建议：如何评判一个策略？

团队中评估 ETF 轮动策略时，切忌只看“全期总收益最高点”。建议新人遵循以下规则进行审计：

1. **观察参数邻域稳定性（Plateau）**：使用 `search_etf_winner_neighborhood_5y.py`。如果一个最优参数的相邻参数（如调仓日提早或延后1天，MA均线调整正负10天）表现断崖式下跌，说明该最优值极大概率是过拟合的“孤峰”，实盘不可用。
2. **注重样本外（OOS）验证**：查看 V3 报告中的 `OOS 收益` 与 `OOS Sharpe`。只有在 2024 年以后的独立样本外期间依然能取得稳健表现的参数组合，才具备实盘参考价值。
3. **警惕 QDII 溢价风险**：如果回测显示弱市中持有美股 ETF 收益极高，在实盘中切记核对溢价。若溢价 `> 2%`，应手动改为持有短融或现金防守。
4. **控制交易换手**：优先选择交易笔数较少（如 5年内交易 100 次左右）且 Sharpe 比率高的策略，过于频繁的交易会被滑点和佣金吞噬掉全部超额收益。
