---
tags:
  - quant/strategy
  - quant/backtest
  - review
---

# A 股每日量化交易工作流仓库审查报告 (a_stock_daily_workflow)

本报告总结了对 `/Users/yansenz/Documents/marketing/a_stock_daily_workflow` 仓库的结构和策略机制审查结果，并作为知识库笔记沉淀。

---

## 1. 仓库基本信息
- **绝对路径**：`/Users/yansenz/Documents/marketing/a_stock_daily_workflow`
- **定位**：A股量化交易与ETF轮动策略的**每日工作流运行数据仓**。该仓库主要用于记录每日扫描信号、回测指标敏感性分析、模拟交易盘日志、策略复盘日志和图表生成结果，不直接包含运行代码（核心代码通常保存在 `marketing/scripts/` 中）。

---

## 2. 核心模块与策略结构

该仓库的核心结构由以下模块组成：

### 2.1 ETF 轮动模块 (`etf_rotation/`)
* **核心内容**：每日 ETF 评分、轮动信号和模型假设。
* **主要文件**：
  - `etf_rotation_signal_latest.md` & `etf_rotation_scores_latest.csv`：最新的日频轮动信号与评分数据。
  - `etf_momentum_rotation_model.md`：详细阐述了 ETF 动量轮动模型的假设与规则。
* **策略机制**：
  - **交易池**：固定 41 只场内 ETF，覆盖科技、金融、消费、医药、周期、新能源、港股及海外方向（不含债券和宽基）。
  - **五维评分系统**：
    1. 动量得分：10日动量 (30%) + 20日动量 (70%) 进行横截面百分位排名。
    2. 溢价率：相较天天基金等实时估算净值超过 5% 一票否决。
    3. 最大回撤：20日回撤 > 15% 禁买，> 12% 预警。
    4. 相对强度：20日涨幅全池排名。
    5. 动量差距：第 1 名与第 2 名的差距决定仓位集中度。
  - **风控退出**：跌破 MA20 且 10日动量转负，或回撤达 12%，次日强制退出。

### 2.2 十字交点突破模块 (`intersection_breakout/`)
* **核心内容**：围绕特定突破形态（交点突破）的股票池扫描结果。
* **主要文件**：
  - 每日候选票池 `intersection_breakout_candidates_[date].csv`。
  - 精选短名单 `intersection_breakout_strict_shortlist_[date].md/.csv`。
  - 每日汇总报告 `intersection_breakout_summary_[date].md`。
* **作用**：基于 K 线技术形态，结合主线板块过滤强波动候选个股。

### 2.3 模拟盘交易模块 (`paper_trading/`)
* **核心内容**：落地“水水体系”的模拟盘追踪。
* **主要文件**：
  - `account_state.json`：追踪账户余额（初始资金 3,000,000 RMB）及持仓状态。
  - `daily_review_latest.md`：记录每日持仓、前20只观察票的触发与剔除状态、计划仓位比例。
  - `trade_log.csv`：模拟交易成交记录。
* **执行纪律**：
  - “非盘中确认不买；跌破止损直接卖；跌回交点下方不修复则剔除”。
  - 限制模型集中度，严格规定主板（单票初始 3%-8%）、创业板/科创板（2%-5%）的仓位上限。

### 2.4 智能资金概念扫描与回测 (`smc_scan/` / `smc_backtest/` / `smc_backtest_random500_filtered/`)
* **核心内容**：Smart Money Concepts（聪明的钱/机构资金结构）扫描与统计测试。
* **主要文件**：
  - `smc_summary_latest.md` & `smc_tight_shortlist_latest.md`：SMC 突破形态短名单。
  - 同花顺自选股导入文件（`ths_smc_A_35.sel` 等），支持一键导入炒股软件。
  - `smc_backtest/`：通过随机 50 只样本测试交易敏感性。
  - `smc_backtest_random500_filtered/`：通过 500 只样本，对比了不同过滤规则（Base, Strict, Loose, Balanced）下的回测表现。

### 2.5 历史运行记录与持久化文件 (`runs/` / `persistent/` / `charts/`)
* **runs/**：包含以日期命名的回测/运行文件夹（如 `2026-06-26`），保存单次运行的具体环境状态。
* **persistent/**：
  - `all_run_summaries.md`：汇总所有运行状态。
  - `daily_candidate_log.csv`：长期候选股跟踪日志。
  - `shuishui_strategy_review_[date].md`：详细的策略复盘日志，复盘突破失败率、追高风险、数据滞后性，并迭代规则。
* **charts/**：
  - 存放个股的 K 线走势分析图（如 `300162_leiman_kline_review_2026-07-03.png`）。

---

## 3. 核心交易思想与痛点总结

从复盘文件（如 `shuishui_strategy_review_2026-07-05.md`）中可梳理出该工作流遵循的交易哲学和待解决痛点：

### 3.1 交易哲学
1. **股票池 ≠ 买入清单**：股票池只提供备选，不符合触发条件、盈亏比（RR）不足 2 或追高幅度超过 5% 的一律不买。
2. **止损是刚性计划**：破位或跌破止损必须立刻退出，绝不向下摊平，绝不将短线交易被动改成长线持有。
3. **分批次日确认**：突破票首笔买入 1/2，等 1-3 天站稳后再补仓 1/2，降低假突破磨损。

### 3.2 运行痛点与改进方向
* **信息滞后**：回测和选股计划数据存在 1-2 天的滞后性。后续要求**突破/追踪类计划有效期仅设 1 天**，过期必须重算。
* **模型单一过拟合**：曾因过度集中于 `A_breakout` 模型导致在震荡市回撤较大。后续计划限制突破类模型仅占观察池 50%-60%，需配比 `D_pullback` (回调) 和 `C_box` (箱体) 等其他模型。
