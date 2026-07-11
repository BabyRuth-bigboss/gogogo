#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LAB_SCRIPT = ROOT / "scripts/backtest_etf_strategy_lab.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_lab/month_start3_cash_end_cost_10bp_slippage"
COMMISSION_RATE = 0.00005
SLIPPAGE_RATE = 0.00100
FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE


def load_lab_module():
    spec = importlib.util.spec_from_file_location("etf_strategy_lab_month_start3_cash_end", LAB_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LAB_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab = load_lab_module()


BASE_STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "lowvol_rs_top3",
        "label": "低波RS Top3",
        "model": "rs36_lowvol",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
    },
    {
        "id": "rs612_top2",
        "label": "RS612 Top2",
        "model": "rs612",
        "top_n": 2,
        "filter": "etf_ma200_ret12_pos",
        "weighting": "equal",
    },
    {
        "id": "core_satellite_50_50",
        "label": "核心卫星50/50",
        "model": "core_satellite",
        "top_n": 2,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "core_satellite",
    },
    {
        "id": "rs36_top3_market",
        "label": "RS36 Top3+市场过滤",
        "model": "rs36",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "market_filter": "risk_on_2of3",
        "weighting": "equal",
    },
]


SCENARIOS = [
    {
        "id": "month_start",
        "label": "月初",
        "schedule": "month_start",
        "rule": "每月第一个交易日开盘调仓",
    },
    {
        "id": "month_start_3",
        "label": "月初第3日",
        "schedule": "month_start_3",
        "rule": "每月第3个交易日开盘调仓",
    },
    {
        "id": "month_start_cash_end3",
        "label": "月初+月末3日空仓",
        "schedule": "month_start",
        "cash_last_n_month_days": 3,
        "rule": "月初调仓；每月最后3个交易日开盘清仓并保持现金",
    },
    {
        "id": "month_start_3_cash_end3",
        "label": "第3日+月末3日空仓",
        "schedule": "month_start_3",
        "cash_last_n_month_days": 3,
        "rule": "每月第3个交易日调仓；每月最后3个交易日开盘清仓并保持现金",
    },
]


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_config(strategy: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(strategy)
    out["id"] = f"{strategy['id']}_{scenario['id']}"
    out["base_id"] = strategy["id"]
    out["label"] = f"{strategy['label']}｜{scenario['label']}"
    out["scenario"] = scenario["label"]
    out["scenario_rule"] = scenario["rule"]
    out["schedule"] = scenario["schedule"]
    out["fee_rate"] = FEE_RATE
    if scenario.get("cash_last_n_month_days"):
        out["cash_last_n_month_days"] = scenario["cash_last_n_month_days"]
    return out


def run_one(config: dict[str, Any], histories: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    equity, trades, signals = lab.simulate_strategy(config, histories)
    metrics = lab.v3.perf_metrics(equity)
    cash_dates = sorted({trade["date"] for trade in trades if trade.get("reason") == "month_end_cash"})
    row = {
        "id": config["id"],
        "base_id": config["base_id"],
        "strategy": config["label"],
        "strategy_label": config["label"].split("｜")[0],
        "scenario": config["scenario"],
        "scenario_rule": config["scenario_rule"],
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "max_dd_start": metrics["max_dd_start"],
        "max_dd_end": metrics["max_dd_end"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trades": len(trades),
        "trade_days": len({trade["date"] for trade in trades}),
        "month_end_cash_events": len(cash_dates),
        "month_end_cash_dates": ";".join(cash_dates),
        "cash_rebalance_periods": sum(1 for row in signals if row["rebalance_day"] and not row["targets"]),
        "start": metrics["start"],
        "end": metrics["end"],
        "yearly": metrics["yearly"],
    }
    return row, equity, trades, signals


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ETF月初第3日与月末空仓回测")
    lines.append("")
    lines.append(f"- 单边成本：佣金{COMMISSION_RATE*100:.3f}% + 滑点{SLIPPAGE_RATE*100:.2f}% = {FEE_RATE*100:.3f}%。")
    lines.append("- 无组合回撤清仓、无降半仓闸门；只测试调仓日和月末现金规则。")
    lines.append("- 信号：调仓日前一交易日收盘后计算；成交：调仓日开盘成交。")
    lines.append("- 月末空仓：每月最后3个交易日开盘清仓并保持现金，下月调仓日再进场。")
    lines.append("")

    lines.append("## 策略说明")
    lines.append("")
    lines.append("- 低波RS Top3：评分=3个月收益45% + 6个月收益45% - 60日波动10%；收盘价高于MA120且6个月收益为正；取前3名等权。")
    lines.append("- RS612 Top2：评分=6个月收益50% + 12个月收益50%；收盘价高于MA200且12个月收益为正；取前2名等权。")
    lines.append("- 核心卫星50/50：核心仓从红利、纳指、标普500、现金流里选1只占50%；其余50%给RS强的行业/主题ETF。")
    lines.append("- RS36 Top3+市场过滤：评分=3个月收益50% + 6个月收益50%；沪深300、创业板、科创50至少2个高于MA120且6个月收益为正才开仓；取前3名等权。")
    lines.append("")

    lines.append("## 核心对比")
    lines.append("")
    lines.append("| 策略 | 月初 | 月初第3日 | 月初+月末空仓 | 第3日+月末空仓 |")
    lines.append("|---|---:|---:|---:|---:|")
    for strategy in BASE_STRATEGIES:
        subset = {row["scenario"]: row for row in rows if row["base_id"] == strategy["id"]}
        lines.append(
            f"| {strategy['label']} | "
            f"{fmt_pct(subset['月初']['total_return'])} / {fmt_pct(subset['月初']['max_drawdown'])} | "
            f"{fmt_pct(subset['月初第3日']['total_return'])} / {fmt_pct(subset['月初第3日']['max_drawdown'])} | "
            f"{fmt_pct(subset['月初+月末3日空仓']['total_return'])} / {fmt_pct(subset['月初+月末3日空仓']['max_drawdown'])} | "
            f"{fmt_pct(subset['第3日+月末3日空仓']['total_return'])} / {fmt_pct(subset['第3日+月末3日空仓']['max_drawdown'])} |"
        )

    lines.append("")
    lines.append("## 全量指标")
    lines.append("")
    lines.append("| 策略 | 场景 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 月末清仓次数 | 最大回撤区间 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in sorted(rows, key=lambda item: (item["base_id"], item["scenario"])):
        lines.append(
            f"| {row['strategy_label']} | {row['scenario']} | {fmt_pct(row['total_return'])} | "
            f"{fmt_pct(row['cagr'])} | {fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | "
            f"{fmt_pct(row['avg_exposure'])} | {row['trades']} | {row['month_end_cash_events']} | "
            f"{row['max_dd_start']}~{row['max_dd_end']} |"
        )

    lines.append("")
    lines.append("## 初步解释")
    lines.append("")
    lines.append("- 月中调仓差很多，主要是趋势策略的信号半衰期问题：强势ETF常在月初就切换/启动，等到月中再买，已经少吃半个月趋势，反而更容易接到回撤。")
    lines.append("- 月初第3日是在保留月初效应的同时，避开第一个交易日的跳空和拥挤成交；如果收益没有明显掉、回撤下降，就值得继续测第2/第5日。")
    lines.append("- 月末3日空仓是在测试月末资金面和调仓噪音是否拖累策略；如果收益大幅下降，说明这些强趋势策略需要连续持有，月底空仓会切断趋势。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = lab.v3.load_history()
    rows: list[dict[str, Any]] = []

    for strategy in BASE_STRATEGIES:
        for scenario in SCENARIOS:
            config = build_config(strategy, scenario)
            row, equity, trades, signals = run_one(config, histories)
            rows.append(row)
            prefix = OUT_DIR / f"{row['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)

    summary = build_summary(rows)
    summary_path = OUT_DIR / "month_start3_cash_end_summary_latest.md"
    json_path = OUT_DIR / "month_start3_cash_end_metrics_latest.json"
    csv_path = OUT_DIR / "month_start3_cash_end_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
