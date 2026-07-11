---
tags:
  - design
  - etf
  - guide
  - hstech
  - quant/backtest
  - quant/dynamic-pool
  - quant/factor
  - quant/pressure-test
  - quant/regime-switching
  - quant/strategy
---

# ETF轮动数据与回测指南

本文说明当前仓库的ETF数据从哪里来、如何获取、有哪些策略、如何运行回测、如何读取结果，以及当前实现的已知边界。

> 当前推荐的研究主线是五年双袖轮动V2：`scripts/backtest_etf_regime_dual_sleeve_5y_v2.py`。

## 1. 快速开始

在仓库根目录执行：

```bash
cd /Users/yansenz/Documents/marketing

# 使用系统Python
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v2.py

# 或使用Codex工作区自带Python
/Users/yansenz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  scripts/backtest_etf_regime_dual_sleeve_5y_v2.py
```

回测完成后查看：

```bash
# 回测摘要
cat a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2/dual_sleeve_v2_summary_latest.md

# 所有参数组合指标
open a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2/dual_sleeve_v2_metrics_latest.csv

# 最优策略净值、交易和信号
open a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2/top1_*_equity_latest.csv
open a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2/top1_*_trades_latest.csv
open a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2/top1_*_signals_latest.csv
```

生成V2 PDF报告：

```bash
python3 scripts/generate_etf_v2_backtest_pdf.py
open output/pdf/etf_regime_dual_sleeve_v2_backtest_report.pdf
```

## 2. 仓库结构

```text
scripts/
  etf_momentum_rotation.py                   ETF池、实时数据和日报基础逻辑
  backtest_etf_dynamic_pool_5y.py            五年动态池与长历史数据层
  backtest_etf_strategy_lab.py               通用策略评分、过滤、调仓和模拟器
  regime_engine_v2.py                        V2强弱市状态机
  backtest_etf_regime_dual_sleeve_5y_v2.py   当前V2主回测
  etf_rotation_v2_live.py                    V2实盘信号实验脚本
  generate_etf_v2_backtest_pdf.py            V2 PDF报告生成器

a_stock_daily_workflow/etf_rotation/
  backtests/                                  所有回测结果
  etf_rotation_v2_signal_latest.md            最新V2实验信号

output/pdf/                                   最终PDF报告
docs/                                         设计和使用文档
```

## 3. 当前数据来自哪里

### 3.1 ETF和指数日线

五年回测主要使用腾讯证券K线接口：

```text
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
```

核心实现位于：

- `scripts/backtest_etf_dynamic_pool_5y.py`
- `fetch_tencent_adjusted_day()`：下载单个ETF或指数日线。
- `load_history()`：并发下载ETF池、市场观察ETF和指数。
- `split_adjust_prices()`：修复ETF大比例份额拆分/合并造成的价格断层。

下载字段包括：

| 字段 | 含义 |
|---|---|
| `date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价 |
| `volume` | 成交量 |
| `amount_yi` | 按价格和成交量估算的成交额，单位亿元 |
| `split_adjust_factor` | 份额拆分连续化调整因子 |

长历史默认最多请求1800根日K，约7个交易年。V2会把请求上限至少设置为1500根。

### 3.2 ETF池

ETF池定义在：

```text
scripts/etf_momentum_rotation.py -> ETF_UNIVERSE
```

当前包含41只ETF，覆盖：

- 科技：半导体、芯片、半导体设备、科创芯片、通信、人工智能、游戏、传媒、软件。
- 金融：证券、银行、证券保险、金融地产。
- 消费和医药。
- 周期、新能源、制造、地产基建。
- 港股行业。
- 海外：纳指、标普500、中概互联、恒生科技。
- 红利质量：红利、现金流。

`MARKET_WATCH`另有沪深300、创业板、科创50三只观察ETF，不直接等同于指数数据。

### 3.3 V2市场指数池

V2强弱判断引擎使用六个指数：

| 内部代码 | 指数 | 腾讯代码 |
|---|---|---|
| `IDX_HS300` | 沪深300 | `sh000300` |
| `IDX_CHINEXT` | 创业板指 | `sz399006` |
| `IDX_SCI50` | 科创50 | `sh000688` |
| `IDX_CSI500` | 中证500 | `sh000905` |
| `IDX_HSI` | 恒生指数 | `hkHSI` |
| `IDX_HSTECH` | 恒生科技 | `hkHSTECH` |

定义位置：`scripts/regime_engine_v2.py -> BENCHMARK_INDEXES_V2`。

### 3.4 数据是否保存在本地

当前实现每次回测都会重新访问腾讯接口，历史原始K线主要保存在运行时内存中，没有统一的本地原始行情缓存。

本地持久化的是回测结果：

- `*_equity_latest.csv`：逐日净值和持仓。
- `*_trades_latest.csv`：交易明细。
- `*_signals_latest.csv`：信号和目标仓位。
- `*_metrics_latest.csv/json`：指标。
- `*_summary_latest.md`：摘要报告。

因此，重复回测时数据源修订或最新交易日变化可能造成小幅结果变化。需要严格复现实验时，应增加原始K线快照和数据版本号。

## 4. 数据如何进入回测

五年回测的数据流程如下：

```text
ETF_UNIVERSE / 指数池
        |
        v
腾讯日线接口 -> 拆分连续化 -> histories字典
        |
        v
ETF上市满252个交易日后进入动态候选池
        |
        v
收盘计算动量、均线、波动率、流动性和趋势质量
        |
        v
下一交易日开盘按目标权重成交
        |
        v
输出净值、交易、信号、指标和报告
```

### 动态ETF池

ETF不是从回测第一天就全部可买。只有已经积累至少252个交易日历史的ETF，才允许进入评分候选池。

这解决了“尚未上市却在历史上被选中”的问题，但没有完全解决幸存者偏差，因为ETF名单仍来自当前池。

## 5. 回测统一口径

五年研究脚本当前采用：

| 项目 | 规则 |
|---|---|
| 初始资金 | 100万元 |
| 回测长度 | 最近5年 |
| 动态入池 | ETF上市满252个交易日 |
| 信号时间 | 当日收盘后，只使用当日及以前数据 |
| 成交时间 | 下一交易日开盘 |
| 佣金 | 万0.5，即0.005% |
| 滑点 | 单边0.10% |
| 单边总成本 | 0.105% |
| 停牌/缺K线估值 | 使用最近可得收盘价估值 |
| 停牌/缺开盘价交易 | 当天不能交易该标的 |

买入成本通过提高实际买入成本处理，卖出成本从卖出所得中扣除。ETF回测不计算股票印花税。

## 6. 当前有哪些策略

### 6.1 三年早期策略族

| 策略 | 脚本 | 说明 |
|---|---|---|
| V1日频动量 | `backtest_etf_momentum_rotation_3y.py` | 日频按五维分数调整 |
| V2周频环境冷却 | `backtest_etf_momentum_rotation_3y_v2.py` | 周频、环境过滤、卖出冷却 |
| V3强市开仓 | `backtest_etf_momentum_rotation_3y_v3.py` | strong开仓、neutral持有、weak现金 |
| V4系列 | `backtest_etf_momentum_rotation_3y_v4_variants.py` | 月频、持仓期、再入场、分差等变体 |

注意：这里的“三年V2”与“五年双袖V2”不是同一个策略。

运行三年策略套件：

```bash
python3 scripts/run_etf_rotation_backtest_suite.py
```

### 6.2 通用策略实验室

`scripts/backtest_etf_strategy_lab.py`包含以下策略配置：

| 策略 | 核心规则 |
|---|---|
| RS36月频Top2 | 3个月和6个月收益各50% |
| RS36周频Top3+市场过滤 | RS36加2/3指数风险环境 |
| RS36月频Top3+市场过滤 | 月频市场过滤版本 |
| RS612月频Top2 | 6个月和12个月收益各50% |
| 6月绝对动量Top3 | 6个月收益排序 |
| 低波RS月频Top3 | 动量减去波动率惩罚 |
| 趋势池等权Top8 | 通过趋势过滤后选Top8 |
| 120日突破Top3 | 接近120日新高的周频策略 |
| 强趋势回调Top3 | 强趋势中的短期回调 |
| 核心卫星50/50 | 核心资产50%加行业卫星50% |
| RS36波动配权 | Top2按逆波动率分配 |
| RS36回撤门控 | 加入指数回撤过滤 |

运行：

```bash
python3 scripts/backtest_etf_strategy_lab.py
```

### 6.3 五年动态池策略

| 策略/实验 | 脚本 | 主要用途 |
|---|---|---|
| RS612与核心卫星五年基线 | `backtest_etf_dynamic_pool_5y.py` | 五年动态池基准 |
| MA120/MA200市场闸门 | `backtest_etf_dynamic_pool_5y_market_gate.py` | 比较市场过滤规则 |
| RS36 MA60/MA120闸门 | `backtest_etf_dynamic_pool_5y_rs36_ma_gate.py` | 快动量环境过滤 |
| 多因子叠加 | `backtest_etf_multifactor_overlay_5y.py` | 低波、趋势质量、近高点、流动性 |
| 高收益广搜 | `search_etf_high_return_strategy_5y.py` | 动量周期、TopN、调仓日、闸门搜索 |
| 胜出区域邻域搜索 | `search_etf_winner_neighborhood_5y.py` | 检查参数平台而不是单点最优 |
| 强弱市双袖V1 | `backtest_etf_regime_dual_sleeve_5y.py` | 强市行业、弱市海外 |
| 强弱市双袖V2 | `backtest_etf_regime_dual_sleeve_5y_v2.py` | MA斜率、防抖、六指数 |
| 强弱市双袖V2.1 | `backtest_etf_regime_dual_sleeve_5y_v21.py` | Top3限仓、紧急闸门、黄金防御及训练/OOS测试 |
| 双袖V3实验 | `backtest_etf_regime_dual_sleeve_5y_v3.py` | 波动率降仓实验 |

V3目前存在逻辑问题：波动率降仓条件位于不可能触发的分支，现有V3结果实际等同V2，不能作为有效增量验证。

## 7. 当前V2策略详细规则

### 7.1 强市进攻袖

ETF评分：

```text
基础动量 = 6个月收益 * 60% + 12个月收益 * 40%
综合评分 = 动量横截面排名 * 75%
         + 流动性排名 * 15%
         + 趋势质量排名 * 10%
```

过滤条件：

```text
收盘价 > MA200
12个月收益 > 0
成交额 >= 0.10亿元
```

持仓规则：

- 选择综合评分Top2。
- 两只ETF各50%。
- 常规调仓日为每月第3个交易日。

### 7.2 弱市防御袖

防御池：

```text
513100 纳指ETF
513500 标普500ETF
```

防御评分：

```text
6个月收益 * 50% + 12个月收益 * 50%
```

过滤条件：

```text
收盘价 > MA200
12个月收益 > 0
```

选择Top2满仓等权；没有合格资产时持有现金。

### 7.3 V2强弱判断

引擎位于 `scripts/regime_engine_v2.py`。

- 指数池：沪深300、创业板、科创50、中证500、恒生指数、恒生科技。
- 主均线：MA120。
- 斜率窗口：20个交易日。
- 真弱势：价格低于MA120，并且MA120下行或MA50低于MA120。
- 进入弱市：连续2日满足阈值。
- 恢复强市：当前最优配置连续1日确认。
- 冷却期：切换后5个交易日。
- 弱势比例：40%和50%在现有样本中结果完全相同，不能证明50%唯一最优。

### 7.4 当前可复现结果

数据截至2026-07-10：

| 指标 | V2最优结果 |
|---|---:|
| 总收益 | +442.86% |
| 年化收益 | +40.32% |
| 最大回撤 | -20.98% |
| Sharpe | 1.37 |
| 平均仓位 | 79.10% |
| 交易次数 | 104 |
| 压测最低年化 | +38.87% |
| 压测最差回撤 | -21.07% |

当前回测持仓：

```text
159516 半导体设备 50%
588200 科创芯片   50%
```

## 8. 如何运行不同回测

### 五年动态池基线

```bash
python3 scripts/backtest_etf_dynamic_pool_5y.py
```

输出：

```text
a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_cost_10bp_slippage/
```

### 市场闸门

```bash
python3 scripts/backtest_etf_dynamic_pool_5y_market_gate.py
python3 scripts/backtest_etf_dynamic_pool_5y_rs36_ma_gate.py
```

### 多因子叠加

```bash
python3 scripts/backtest_etf_multifactor_overlay_5y.py
```

### 参数广搜与邻域搜索

```bash
python3 scripts/search_etf_high_return_strategy_5y.py
python3 scripts/search_etf_winner_neighborhood_5y.py
```

参数广搜用于发现候选，邻域搜索用于检查相邻参数是否同样有效。不要只采用单个历史峰值参数。

### 双袖V1和V2

```bash
python3 scripts/backtest_etf_regime_dual_sleeve_5y.py
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v2.py
python3 scripts/backtest_etf_regime_dual_sleeve_5y_v21.py
```

V2每次会运行32个完整参数组合，并对候选执行不同起点和成本翻倍压力测试。

V2.1测试集中度、紧急下跌闸门和黄金防御。当前限仓候选采用Top3权重45%/45%/10%，全期总收益+390.45%、年化+37.50%、最大回撤-19.93%；它是风险边界版本，不替代收益更高的V2进攻版。

## 9. 如何读取回测结果

### `equity`文件

常见字段：

| 字段 | 含义 |
|---|---|
| `date` | 净值日期 |
| `equity` | 组合总资产 |
| `cash` | 现金 |
| `exposure` | 仓位比例 |
| `positions` | 当前持仓代码 |
| `market_bad` | 是否处于弱市 |
| `sleeve` | offensive或defensive |

### `trades`文件

| 字段 | 含义 |
|---|---|
| `date` | 成交日期 |
| `code` | ETF代码 |
| `side` | BUY或SELL |
| `price` | 用于模拟的开盘价 |
| `shares` | 份额 |
| `value` | 成交金额 |
| `fee` | 模拟交易成本 |
| `signal_date` | 产生信号的前一交易日 |
| `reason` | 月度调仓或市场切换 |

### `signals`文件

重点检查：

- `signal_date`和`trade_date`是否错开一个交易日。
- `regime_changed`何时发生。
- `market_note`使用了哪些指数判断。
- `selected`和`targets`是否符合策略规则。

### 指标解释

- 总收益：期末资产/初始资产-1。
- 年化收益：把总收益按实际日历天数折算成年化。
- 最大回撤：历史峰值到后续最低点的最大跌幅。
- Sharpe：日收益均值/日收益标准差，再乘以`sqrt(252)`；当前没有扣无风险利率。
- 平均仓位：每天持仓市值占组合净值的平均值。

## 10. 如何新增一个策略

优先复用 `scripts/backtest_etf_strategy_lab.py`，避免重复编写成交、估值和绩效逻辑。

### 第一步：定义假设

例如：

```text
过去6到12个月持续强势、流动性充足且趋势路径平滑的ETF，未来一个月更可能延续趋势。
```

### 第二步：定义配置

```python
config = {
    "id": "my_strategy",
    "label": "我的策略",
    "model": "rs612",
    "top_n": 2,
    "filter": "etf_ma200_ret12_pos",
    "weighting": "equal",
    "schedule": "month_start_3",
    "fee_rate": 0.00105,
}
```

### 第三步：只用信号日可得数据

任何特征都必须截断到`signal_date`：

```python
rows = histories[code][: indexes[code][signal_date] + 1]
```

不要读取`trade_date`收盘价、未来最高价、未来收益或完整区间最终排名。

### 第四步：运行并保存三类明细

至少保存：

```text
equity
trades
signals
```

没有逐笔交易和逐日信号的回测，无法审计未来函数和成交逻辑。

### 第五步：压力测试

至少测试：

- 成本1倍、1.5倍、2倍。
- 不同起始日期。
- 相邻动量权重。
- Top1、Top2、Top3。
- 相邻调仓日。
- 市场闸门MA100、MA120、MA150、MA200。
- 严格滚动样本外区间。

## 11. 当前已知问题

### 数据问题

1. 当前ETF名单来自现在，仍存在幸存者偏差。
2. 腾讯原始日线的拆分修正不等同于完整分红复权，红利ETF收益可能失真。
3. 原始行情没有统一保存为带版本号的本地快照。
4. 港股和A股交易日不完全一致，需要统一日历和缺失值规则。

### 回测问题

1. 改变起点和成本属于敏感度测试，不等同于严格样本外验证。
2. V2最优参数来自同一五年样本上的搜索，存在过拟合可能。
3. 104笔交易高于最低样本量，但仍未达到特别高的统计置信度。
4. 当前收益集中于2023、2025和2026年。

### 实盘脚本问题

`scripts/etf_rotation_v2_live.py`当前仍是实验版本：

1. 最新日期可能在预热和最终判断中重复执行，破坏“连续2日确认”。
2. 非调仓日会重新计算并展示最新Top2，与回测的持有逻辑不完全一致。
3. 数据全部缺失时引擎可能逐渐按强市处理。

在这些问题修复并完成逐日回放一致性测试前，实盘信号不能视为与V2回测严格等价。

## 12. 推荐验证顺序

```text
1. 固定原始行情快照和ETF池版本
2. 修复实盘与回测状态机一致性
3. 做逐日信号回放对账
4. 做滚动样本外测试
5. 做成本、滑点和成交失败压力测试
6. 模拟盘运行至少3到6个月
7. 再讨论真实资金和仓位上限
```

评价策略时优先看：

```text
样本外年化收益
最大回撤
年度稳定性
参数邻域稳定性
成本敏感度
收益是否依赖少数年份和少数ETF
```

不要只看样本内总收益最高值。
