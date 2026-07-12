#!/usr/bin/env python3
"""Static-capital blends of independently executed monthly ETF strategy sleeves."""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_sleeve_blends_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF月频策略袖套组合回测_2015-07至2026-07.md")
CORE_EQUITY = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_neighborhood_2015_2026/top2_rs36_breakout_top3_month_start_top_heavy_equity_latest.csv"
V5_EQUITY = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/v5_concentrated_dual_sleeve_t0_2015_2026/documented_4def_equity_latest.csv"
WINDOWS = [
    ("2016-2018", "2016-01-04", "2018-12-28"),
    ("2018-2020", "2018-01-02", "2020-12-31"),
    ("2020-2022", "2020-01-02", "2022-12-30"),
    ("2022-2024", "2022-01-04", "2024-12-31"),
]
RANDOM_WINDOW_YEARS = random.Random(20260712).sample(list(range(2015, 2024)), 4)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("blend_metrics_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")
v3 = base.v3


def read_equity(path: Path) -> dict[str, dict[str, float]]:
    with path.open(encoding="utf-8-sig") as handle:
        return {
            row["date"]: {"equity": float(row["equity"]), "exposure": float(row.get("exposure", 1.0) or 0.0)}
            for row in csv.DictReader(handle)
        }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def series_with_cash_before_start(equity: dict[str, dict[str, float]], dates: list[str], initial: float = 1_000_000.0) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    current = {"equity": initial, "exposure": 0.0}
    for date in dates:
        if date in equity:
            current = equity[date]
        out[date] = dict(current)
    return out


def period_metrics(rows: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    selected = [row for row in rows if start <= str(row["date"]) <= end]
    if not selected:
        return {}
    prior = [row for row in rows if str(row["date"]) < str(selected[0]["date"])]
    initial = float(prior[-1]["equity"]) if prior else 1_000_000.0
    seed_date = str(prior[-1]["date"]) if prior else (dt.date.fromisoformat(str(selected[0]["date"])) - dt.timedelta(days=1)).isoformat()
    return v3.perf_metrics([{"date": seed_date, "equity": initial, "cash": initial, "exposure": 0.0, "positions": ""}, *selected])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    core = read_equity(CORE_EQUITY)
    v5 = read_equity(V5_EQUITY)
    dates = sorted(set(core) | set(v5))
    core_series = series_with_cash_before_start(core, dates)
    v5_series = series_with_cash_before_start(v5, dates)
    summary_rows: list[dict[str, Any]] = []
    robustness: list[dict[str, Any]] = []
    for v5_weight in (0.0, 0.10, 0.20, 0.30, 0.40, 0.50):
        core_weight = 1.0 - v5_weight
        equity = [
            {
                "date": date,
                "equity": core_weight * core_series[date]["equity"] + v5_weight * v5_series[date]["equity"],
                "cash": 0.0,
                "exposure": (
                    core_weight * core_series[date]["equity"] * core_series[date]["exposure"]
                    + v5_weight * v5_series[date]["equity"] * v5_series[date]["exposure"]
                ) / (core_weight * core_series[date]["equity"] + v5_weight * v5_series[date]["equity"]),
                "positions": f"核心袖{core_weight:.0%};V5袖{v5_weight:.0%}",
            }
            for date in dates
        ]
        metrics = v3.perf_metrics(equity)
        identifier = f"core{int(core_weight * 100)}_v5{int(v5_weight * 100)}"
        summary_rows.append({
            "id": identifier,
            "label": f"核心月频袖{core_weight:.0%}+V5集中袖{v5_weight:.0%}",
            "core_weight": core_weight,
            "v5_weight": v5_weight,
            "total_return": metrics["total_return"],
            "cagr": metrics["cagr"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe": metrics["sharpe"],
            "avg_exposure": metrics["avg_exposure"],
            "trades": (173 if core_weight else 0) + (207 if v5_weight else 0),
        })
        write_csv(OUT_DIR / f"{identifier}_equity_latest.csv", equity)
        for name, start, end in WINDOWS:
            window = period_metrics(equity, start, end)
            robustness.append({"id": identifier, "label": f"核心月频袖{core_weight:.0%}+V5集中袖{v5_weight:.0%}", "scenario": name, "start": window.get("start", ""), "end": window.get("end", ""), "cagr": window.get("cagr", 0.0), "max_drawdown": window.get("max_drawdown", 0.0), "sharpe": window.get("sharpe", 0.0)})
        for year in RANDOM_WINDOW_YEARS:
            start, end = f"{year}-07-01", f"{year + 3}-06-30"
            window = period_metrics(equity, start, end)
            robustness.append({"id": identifier, "label": f"核心月频袖{core_weight:.0%}+V5集中袖{v5_weight:.0%}", "scenario": f"随机种子20260712:{year}-{year + 3}", "start": window.get("start", ""), "end": window.get("end", ""), "cagr": window.get("cagr", 0.0), "max_drawdown": window.get("max_drawdown", 0.0), "sharpe": window.get("sharpe", 0.0)})
    summary_rows.sort(key=lambda row: (int(row["cagr"] >= 0.15 and row["max_drawdown"] >= -0.20), row["cagr"], row["sharpe"]), reverse=True)
    write_csv(OUT_DIR / "sleeve_blend_metrics_latest.csv", summary_rows)
    write_csv(OUT_DIR / "sleeve_blend_robustness_latest.csv", robustness)
    lines = [
        "# ETF月频策略袖套组合回测（T0）", "",
        "- 组合方式：初始资金按固定比例分配给两个独立策略袖套，之后各自独立调仓；不做事后每日再平衡，也不共享信号。",
        "- 核心袖：RS36+近120日高点Top3 50/30/20，3/3强市进攻、弱市国债/黄金Top1。",
        "- V5袖：集中Top1、任一指数真弱即切防守，文档版国债/黄金/纳指/标普防守池。",
        "", "| 配比 | 总收益 | 年化 | 最大回撤 | Sharpe |", "|---|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(f"| {row['label']} | {pct(float(row['total_return']))} | {pct(float(row['cagr']))} | {pct(float(row['max_drawdown']))} | {float(row['sharpe']):.2f} |")
    eligible = [row for row in summary_rows if float(row["cagr"]) >= 0.15 and float(row["max_drawdown"]) >= -0.20]
    lines.extend(["", f"- 满足年化>=15%且最大回撤<=20%的组合：{len(eligible)} 个。", "- 分段检验包含固定多市场窗口，以及随机种子 `20260712` 固定抽取的四个三年窗口，便于复现。", "", "## 分段检验", "", "| 配比 | 窗口 | 年化 | 最大回撤 | Sharpe |", "|---|---|---:|---:|---:|"])
    for row in robustness:
        lines.append(f"| {row['label']} | {row['scenario']} | {pct(float(row['cagr']))} | {pct(float(row['max_drawdown']))} | {float(row['sharpe']):.2f} |")
    lines.extend(["", f"- 指标CSV：`{OUT_DIR / 'sleeve_blend_metrics_latest.csv'}`", ""])
    report = "\n".join(lines)
    (OUT_DIR / "sleeve_blend_summary_latest.md").write_text(report, encoding="utf-8")
    NOTE_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
