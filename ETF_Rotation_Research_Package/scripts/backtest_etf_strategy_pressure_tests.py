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
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_lab/pressure_tests"


def load_lab_module():
    spec = importlib.util.spec_from_file_location("etf_strategy_lab_pressure", LAB_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LAB_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab = load_lab_module()


RESTORED_ETFS = [
    {"code": "159883", "theme": "医药", "label": "医疗器械"},
    {"code": "159837", "theme": "医药", "label": "生物科技"},
]


BASE_STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "core_satellite_50_50",
        "label": "核心卫星50/50",
        "schedule": "month_start",
        "model": "core_satellite",
        "top_n": 2,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "core_satellite",
    },
    {
        "id": "rs36_month_top3_market",
        "label": "RS36月频Top3+市场过滤",
        "schedule": "month_start",
        "model": "rs36",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "market_filter": "risk_on_2of3",
        "weighting": "equal",
    },
    {
        "id": "lowvol_rs_month_top3",
        "label": "低波RS月频Top3",
        "schedule": "month_start",
        "model": "rs36_lowvol",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
    },
    {
        "id": "rs612_month_top2",
        "label": "RS612月频Top2",
        "schedule": "month_start",
        "model": "rs612",
        "top_n": 2,
        "filter": "etf_ma200_ret12_pos",
        "weighting": "equal",
    },
]


SCENARIOS: list[dict[str, Any]] = [
    {"id": "base", "label": "基准"},
    {"id": "fee_2x", "label": "成本翻倍", "fee_mult": 2.0},
    {"id": "universe_43", "label": "ETF池还原43只", "restore_universe": True},
    {"id": "start_2024", "label": "2024开始", "start_date": "2024-01-02"},
    {"id": "start_2025", "label": "2025开始", "start_date": "2025-01-02"},
    {"id": "market_loose_1of3", "label": "市场过滤宽：1/3", "market_filter": "risk_on_1of3"},
    {"id": "market_base_2of3", "label": "市场过滤中：2/3", "market_filter": "risk_on_2of3"},
    {"id": "market_strict_3of3", "label": "市场过滤严：3/3", "market_filter": "risk_on_3of3"},
    {"id": "no_market_filter", "label": "无市场过滤", "remove_market_filter": True},
    {"id": "fee_2x_universe_43", "label": "成本翻倍+还原43只", "fee_mult": 2.0, "restore_universe": True},
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


def restored_universe() -> list[dict[str, str]]:
    existing = {item["code"] for item in lab.v3.ETF_UNIVERSE}
    return lab.v3.ETF_UNIVERSE + [item for item in RESTORED_ETFS if item["code"] not in existing]


def apply_scenario(strategy: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(strategy)
    out["scenario_id"] = scenario["id"]
    out["scenario_label"] = scenario["label"]
    if scenario.get("fee_mult"):
        out["fee_mult"] = scenario["fee_mult"]
    if scenario.get("start_date"):
        out["start_date"] = scenario["start_date"]
    if scenario.get("market_filter"):
        out["market_filter"] = scenario["market_filter"]
    if scenario.get("remove_market_filter"):
        out.pop("market_filter", None)
    return out


def run_one(strategy: dict[str, Any], scenario: dict[str, Any], histories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    config = apply_scenario(strategy, scenario)
    equity, trades, signals = lab.simulate_strategy(config, histories)
    metrics = lab.v3.perf_metrics(equity)
    return {
        "strategy_id": strategy["id"],
        "strategy": strategy["label"],
        "scenario_id": scenario["id"],
        "scenario": scenario["label"],
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
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


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ETF候选策略压力测试")
    lines.append("")
    lines.append("- 候选：核心卫星50/50、RS36月频Top3+市场过滤、低波RS月频Top3、RS612月频Top2。")
    lines.append("- 压力项：成本翻倍、还原长期亏损ETF、不同起始年份、市场过滤宽/中/严。")
    lines.append("- 共同成交：信号日收盘后计算，下一交易日开盘成交。")
    lines.append("")

    for strategy_id in [item["id"] for item in BASE_STRATEGIES]:
        subset = [row for row in rows if row["strategy_id"] == strategy_id]
        subset_by_dd = sorted(subset, key=lambda row: row["max_drawdown"], reverse=True)
        lines.append(f"## {subset[0]['strategy']}")
        lines.append("")
        lines.append("| 场景 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 区间 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for row in subset_by_dd:
            lines.append(
                f"| {row['scenario']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
                f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
                f"{row['trades']} | {row['start']}~{row['end']} |"
            )
        base = next(row for row in subset if row["scenario_id"] == "base")
        worst_return = min(subset, key=lambda row: row["total_return"])
        worst_dd = min(subset, key=lambda row: row["max_drawdown"])
        lines.append("")
        lines.append(
            f"- 基准：总收益 {fmt_pct(base['total_return'])}，最大回撤 {fmt_pct(base['max_drawdown'])}，Sharpe {base['sharpe']:.2f}。"
        )
        lines.append(
            f"- 最差收益场景：{worst_return['scenario']}，总收益 {fmt_pct(worst_return['total_return'])}。"
        )
        lines.append(
            f"- 最深回撤场景：{worst_dd['scenario']}，最大回撤 {fmt_pct(worst_dd['max_drawdown'])}。"
        )
        lines.append("")

    lines.append("## 初步结论")
    core_base = next(row for row in rows if row["strategy_id"] == "core_satellite_50_50" and row["scenario_id"] == "base")
    rs_base = next(row for row in rows if row["strategy_id"] == "rs36_month_top3_market" and row["scenario_id"] == "base")
    lowvol_base = next(row for row in rows if row["strategy_id"] == "lowvol_rs_month_top3" and row["scenario_id"] == "base")
    rs612_base = next(row for row in rows if row["strategy_id"] == "rs612_month_top2" and row["scenario_id"] == "base")
    lines.append(
        f"- 核心卫星基准偏稳：总收益 {fmt_pct(core_base['total_return'])}，最大回撤 {fmt_pct(core_base['max_drawdown'])}，平均仓位 {fmt_pct(core_base['avg_exposure'])}。"
    )
    lines.append(
        f"- RS36市场过滤基准收益更高：总收益 {fmt_pct(rs_base['total_return'])}，最大回撤 {fmt_pct(rs_base['max_drawdown'])}，平均仓位 {fmt_pct(rs_base['avg_exposure'])}。"
    )
    lines.append(
        f"- 低波RS基准最进攻：总收益 {fmt_pct(lowvol_base['total_return'])}，最大回撤 {fmt_pct(lowvol_base['max_drawdown'])}，平均仓位 {fmt_pct(lowvol_base['avg_exposure'])}。"
    )
    lines.append(
        f"- RS612基准趋势更慢：总收益 {fmt_pct(rs612_base['total_return'])}，最大回撤 {fmt_pct(rs612_base['max_drawdown'])}，平均仓位 {fmt_pct(rs612_base['avg_exposure'])}。"
    )
    lines.append("- 需要重点看不同起始年份；如果起始年份一换就断，说明策略更像吃到特定行情段。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    original_universe = list(lab.v3.ETF_UNIVERSE)
    lab.v3.ETF_UNIVERSE = original_universe
    current_histories, current_errors = lab.v3.load_history()

    lab.v3.ETF_UNIVERSE = restored_universe()
    restored_histories, restored_errors = lab.v3.load_history()
    lab.v3.ETF_UNIVERSE = original_universe

    rows: list[dict[str, Any]] = []
    for strategy in BASE_STRATEGIES:
        for scenario in SCENARIOS:
            histories = restored_histories if scenario.get("restore_universe") else current_histories
            row = run_one(strategy, scenario, histories)
            row["universe_count"] = len(restored_universe()) if scenario.get("restore_universe") else len(original_universe)
            rows.append(row)

    summary = build_summary(rows)
    summary_path = OUT_DIR / "pressure_tests_summary_latest.md"
    json_path = OUT_DIR / "pressure_tests_metrics_latest.json"
    csv_path = OUT_DIR / "pressure_tests_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "rows": rows,
                "current_universe_count": len(original_universe),
                "restored_universe_count": len(restored_universe()),
                "current_errors": current_errors,
                "restored_errors": restored_errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(csv_path, rows)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
