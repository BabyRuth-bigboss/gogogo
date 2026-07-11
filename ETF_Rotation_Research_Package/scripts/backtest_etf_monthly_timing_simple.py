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
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_lab/monthly_timing_simple_cost_10bp_slippage"
COMMISSION_RATE = 0.00005
SLIPPAGE_RATE = 0.00100
FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE


def load_lab_module():
    spec = importlib.util.spec_from_file_location("etf_strategy_lab_monthly_timing", LAB_SCRIPT)
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


SCHEDULES = [
    {"id": "month_start", "label": "月初", "rule": "每月第一个交易日开盘调仓"},
    {"id": "mid_month", "label": "月中", "rule": "每月首个15号或之后的交易日开盘调仓"},
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


def build_config(strategy: dict[str, Any], schedule: dict[str, str]) -> dict[str, Any]:
    out = deepcopy(strategy)
    out["id"] = f"{strategy['id']}_{schedule['id']}"
    out["label"] = f"{strategy['label']}｜{schedule['label']}"
    out["schedule"] = schedule["id"]
    out["schedule_label"] = schedule["label"]
    out["schedule_rule"] = schedule["rule"]
    out["fee_rate"] = FEE_RATE
    return out


def run_one(config: dict[str, Any], histories: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    equity, trades, signals = lab.simulate_strategy(config, histories)
    metrics = lab.v3.perf_metrics(equity)
    row = {
        "id": config["id"],
        "base_id": config["id"].replace("_month_start", "").replace("_mid_month", ""),
        "strategy": config["label"],
        "schedule": config["schedule_label"],
        "schedule_rule": config["schedule_rule"],
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
        "cash_rebalance_periods": sum(1 for row in signals if row["rebalance_day"] and not row["targets"]),
        "start": metrics["start"],
        "end": metrics["end"],
        "yearly": metrics["yearly"],
    }
    return row, equity, trades, signals


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ETF月初 vs 月中简单策略回测")
    lines.append("")
    lines.append(f"- 单边成本：佣金{COMMISSION_RATE*100:.3f}% + 滑点{SLIPPAGE_RATE*100:.2f}% = {FEE_RATE*100:.3f}%。")
    lines.append("- 无组合回撤清仓、无降半仓闸门；只保留策略本身的趋势/市场过滤条件。")
    lines.append("- 信号：调仓日前一交易日收盘后计算；成交：调仓日开盘成交。")
    lines.append("- 月初：每月第一个交易日；月中：每月首个15号或之后的交易日。")
    lines.append("")

    lines.append("## 策略规则")
    lines.append("")
    lines.append("- 低波RS Top3：评分=3个月收益45% + 6个月收益45% - 60日波动10%；收盘价高于MA120且6个月收益为正；取前3名等权。")
    lines.append("- RS612 Top2：评分=6个月收益50% + 12个月收益50%；收盘价高于MA200且12个月收益为正；取前2名等权。")
    lines.append("- 核心卫星50/50：核心仓从红利、纳指、标普500、现金流里选1只占50%；其余50%给RS强的行业/主题ETF。")
    lines.append("- RS36 Top3+市场过滤：评分=3个月收益50% + 6个月收益50%；沪深300、创业板、科创50至少2个高于MA120且6个月收益为正才开仓；取前3名等权。")
    lines.append("")

    lines.append("## 月初 vs 月中")
    lines.append("")
    lines.append("| 策略 | 月初收益/回撤 | 月中收益/回撤 | 月中收益变化 | 月中回撤变化 | 月中Sharpe | 月中平均仓位 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for strategy in BASE_STRATEGIES:
        start = next(row for row in rows if row["base_id"] == strategy["id"] and row["schedule"] == "月初")
        mid = next(row for row in rows if row["base_id"] == strategy["id"] and row["schedule"] == "月中")
        lines.append(
            f"| {strategy['label']} | {fmt_pct(start['total_return'])} / {fmt_pct(start['max_drawdown'])} | "
            f"{fmt_pct(mid['total_return'])} / {fmt_pct(mid['max_drawdown'])} | "
            f"{fmt_pct(mid['total_return'] - start['total_return'])} | {fmt_pct(mid['max_drawdown'] - start['max_drawdown'])} | "
            f"{mid['sharpe']:.2f} | {fmt_pct(mid['avg_exposure'])} |"
        )

    lines.append("")
    lines.append("## 全量指标")
    lines.append("")
    lines.append("| 策略 | 调仓 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 空仓调仓期 | 最大回撤区间 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in sorted(rows, key=lambda item: (item["base_id"], item["schedule"])):
        lines.append(
            f"| {row['strategy']} | {row['schedule']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
            f"{row['trades']} | {row['cash_rebalance_periods']} | {row['max_dd_start']}~{row['max_dd_end']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = lab.v3.load_history()
    rows: list[dict[str, Any]] = []

    for strategy in BASE_STRATEGIES:
        for schedule in SCHEDULES:
            config = build_config(strategy, schedule)
            row, equity, trades, signals = run_one(config, histories)
            rows.append(row)
            prefix = OUT_DIR / f"{row['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)

    summary = build_summary(rows)
    summary_path = OUT_DIR / "monthly_timing_simple_summary_latest.md"
    json_path = OUT_DIR / "monthly_timing_simple_metrics_latest.json"
    csv_path = OUT_DIR / "monthly_timing_simple_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
