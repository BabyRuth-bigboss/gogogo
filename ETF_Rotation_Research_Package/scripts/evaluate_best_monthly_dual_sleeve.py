#!/usr/bin/env python3
"""Cost and multi-window validation for the best no-leverage monthly candidate."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_neighborhood_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF双袖月频轮动最优候选压力验证.md")
WINDOWS = [
    ("2016-2018", "2016-01-04", "2018-12-28"),
    ("2018-2020", "2018-01-02", "2020-12-31"),
    ("2020-2022", "2020-01-02", "2022-12-30"),
    ("2022-2024", "2022-01-04", "2024-12-31"),
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate = load_module("neighborhood_candidate", ROOT / "scripts/research_dual_sleeve_neighborhood.py")
base, dual, lab, v3 = candidate.base, candidate.dual, candidate.lab, candidate.v3


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def config() -> dict[str, Any]:
    return {
        "id": "rs36_breakout_top3_month_start_top_heavy",
        "label": "RS36+近120日高点 Top3首位加权｜月初｜3/3强市｜弱市国债+黄金Top1",
        "model": "rs36_breakout",
        "filter": "custom",
        "top_n": 3,
        "schedule": "month_start",
        "weighting": "equal",
        "weight_style": "top_heavy",
        "fee_rate": base.FEE_RATE,
        "start_date": dual.START,
        "risk_label": "无组合回撤闸门",
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for etf in dual.DEFENSIVE_ETFS:
        if etf["code"] not in {item["code"] for item in base.live.ETF_UNIVERSE}:
            base.live.ETF_UNIVERSE.append(etf)
        if etf["code"] not in {item["code"] for item in v3.ETF_UNIVERSE}:
            v3.ETF_UNIVERSE.append(etf)
    base.KLINE_LIMIT = 1800
    histories, errors = base.load_history()
    if errors:
        raise RuntimeError(errors)
    original = lab.build_targets
    lab.build_targets = candidate.build_targets
    try:
        rows: list[dict[str, Any]] = []
        runs: list[tuple[str, dict[str, Any]]] = []
        baseline = candidate.run(config(), histories)
        runs.append(("全期基准", baseline))
        doubled = deepcopy(config())
        doubled["fee_rate"] = base.FEE_RATE * 2
        runs.append(("成本加倍", candidate.run(doubled, histories)))
        for name, start, end in WINDOWS:
            windowed = deepcopy(config())
            windowed["start_date"] = start
            runs.append((name, candidate.run(windowed, histories, end)))
        for scenario, run in runs:
            metrics = run["full"]
            rows.append({
                "scenario": scenario,
                "start": metrics["start"],
                "end": metrics["end"],
                "total_return": metrics["total_return"],
                "cagr": metrics["cagr"],
                "max_drawdown": metrics["max_drawdown"],
                "sharpe": metrics["sharpe"],
                "avg_exposure": metrics["avg_exposure"],
                "trades": len(run["trades"]),
            })
        write_csv(OUT_DIR / "best_candidate_robustness_latest.csv", rows)
        report = [
            "# 最优双袖月频候选：压力验证", "",
            "- 固定策略：RS36 + 近120日高点（80%/20%）评分，行业Top3按50%/30%/20%配置；月初调仓；三大指数均为强市时进攻，否则在国债/黄金中择强。",
            "- 信号使用前一交易日收盘；下一交易日开盘成交；单边成本基准为佣金万0.5加滑点0.10%。",
            "", "| 场景 | 区间 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 |", "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            report.append(f"| {row['scenario']} | {row['start']}~{row['end']} | {pct(row['total_return'])} | {pct(row['cagr'])} | {pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {pct(row['avg_exposure'])} | {row['trades']} |")
        report.extend(["", "- 这些窗口独立从起点重新建仓，非从全期权益曲线截取；用于检查策略是否依赖某一段行情。", ""])
        output = "\n".join(report)
        (OUT_DIR / "best_candidate_robustness_latest.md").write_text(output, encoding="utf-8")
        NOTE_PATH.write_text(output, encoding="utf-8")
        (OUT_DIR / "best_candidate_config_latest.json").write_text(json.dumps(config(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(output)
    finally:
        lab.build_targets = original


if __name__ == "__main__":
    main()
