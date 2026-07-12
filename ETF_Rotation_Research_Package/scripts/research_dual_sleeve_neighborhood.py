#!/usr/bin/env python3
"""Focused factor/weight neighborhood around the surviving monthly dual sleeve."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_neighborhood_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF双袖月频轮动邻域优化_2015-07至2026-07.md")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dual = load_module("monthly_dual_sleeve_base", ROOT / "scripts/research_monthly_dual_sleeve.py")
base, lab, v3 = dual.base, dual.lab, dual.v3

MODELS = {
    "rs36": ("RS36", "etf_ma120_ret6_pos", {"momentum": 1.0}),
    "rs36_quality": ("RS36+趋势质量", "etf_ma120_ret6_pos", {"momentum": 0.80, "trend_quality": 0.20}),
    "rs36_breakout": ("RS36+近高点", "etf_ma120_ret6_pos", {"momentum": 0.80, "breakout": 0.20}),
    "m612": ("6/12月动量", "etf_ma200_ret12_pos", {"momentum612": 1.0}),
    "m612_quality": ("6/12月动量+趋势质量", "etf_ma200_ret12_pos", {"momentum612": 0.80, "trend_quality": 0.20}),
    "m612_breakout": ("6/12月动量+近高点", "etf_ma200_ret12_pos", {"momentum612": 0.80, "breakout": 0.20}),
}
WEIGHTS = {
    "equal": "等权",
    "top_heavy": "首位加权",
}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (float(row[field]), str(row["code"])))
    if len(ordered) <= 1:
        return {str(row["code"]): 0.5 for row in ordered}
    return {str(row["code"]): index / (len(ordered) - 1) for index, row in enumerate(ordered)}


def enrich(row: dict[str, Any], history: list[dict[str, Any]]) -> None:
    closes = [float(item["close"]) for item in history]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 60), len(closes)) if closes[i - 1] > 0]
    path = sum(abs(value) for value in returns)
    row["momentum"] = row["ret63"] * 0.5 + row["ret126"] * 0.5
    row["momentum612"] = row["ret126"] * 0.6 + row["ret252"] * 0.4
    row["trend_quality"] = abs(row["ret63"]) / path if path > 0 else 0.0
    row["breakout"] = row["close"] / max(closes[-120:]) if len(closes) >= 120 else 0.0


def weighted_targets(selected: list[dict[str, Any]], style: str) -> dict[str, float]:
    if not selected:
        return {}
    if style == "equal" or len(selected) == 1:
        return {row["code"]: 1 / len(selected) for row in selected}
    if len(selected) == 2:
        weights = (0.60, 0.40)
    else:
        weights = (0.50, 0.30, 0.20)
    return {row["code"]: weights[index] for index, row in enumerate(selected)}


def build_targets(strategy: dict[str, Any], signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]):
    rows = lab.etf_features(signal_date, histories, indexes)
    market_on, note = dual.market_is_on(signal_date, histories, indexes, 3)
    if not market_on:
        candidates = []
        for row in rows:
            if row["code"] not in dual.DEFENSIVE_POOLS["bond_gold"]["codes"]:
                continue
            if dual.defensive_eligible(row, "ma120_ret6"):
                row["score"] = row["ret63"] * 0.5 + row["ret126"] * 0.5
                candidates.append(row)
        ranked = sorted(candidates, key=lambda row: row["score"], reverse=True)
        selected = ranked[:1]
        return weighted_targets(selected, "equal"), {"market_note": f"defensive;{note}", "ranked": ranked, "selected": selected, "eligible_count": len(candidates)}

    _, filt, weights = MODELS[str(strategy["model"])]
    candidates = []
    for row in rows:
        if row["code"] in dual.DEFENSIVE_CODES:
            continue
        if not lab.eligible(row, filt):
            continue
        index = indexes[row["code"]][signal_date]
        enrich(row, histories[row["code"]][: index + 1])
        candidates.append(row)
    ranks = {factor: percentile(candidates, factor) for factor in weights}
    for row in candidates:
        row["score"] = sum(weight * ranks[factor][row["code"]] for factor, weight in weights.items())
    ranked = sorted(candidates, key=lambda row: row["score"], reverse=True)
    selected = ranked[: int(strategy["top_n"])]
    return weighted_targets(selected, str(strategy["weight_style"])), {"market_note": f"offensive;{note}", "ranked": ranked, "selected": selected, "eligible_count": len(candidates)}


def make_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for model, (label, _, _) in MODELS.items():
        for top_n in (2, 3):
            for schedule, schedule_label in (("month_start", "月初第1交易日"), ("month_start_3", "月初第3交易日")):
                for weight_style, weight_label in WEIGHTS.items():
                    ident = f"{model}_top{top_n}_{schedule}_{weight_style}"
                    configs.append({
                        "id": ident,
                        "label": f"进攻:{label} Top{top_n} {weight_label}｜{schedule_label}｜市场3/3强｜弱市国债+黄金 Top1 MA120+6月正",
                        "model": model,
                        "filter": "custom",
                        "top_n": top_n,
                        "schedule": schedule,
                        "weighting": "equal",
                        "weight_style": weight_style,
                        "fee_rate": base.FEE_RATE,
                        "start_date": dual.START,
                        "risk_label": "无组合回撤闸门",
                    })
    return configs


def run(config: dict[str, Any], histories: dict[str, list[dict[str, Any]]], end: str | None = None) -> dict[str, Any]:
    active = histories if end is None else {code: [row for row in rows if str(row["date"]) <= end] for code, rows in histories.items()}
    equity, trades, signals = lab.simulate_strategy(config, active)
    return {"config": config, "equity": equity, "trades": trades, "signals": signals, "full": v3.perf_metrics(equity), "train": dual.period_metrics(equity, dual.START, dual.TRAIN_END), "oos": dual.period_metrics(equity, dual.OOS_START, end)}


def rank(run_data: dict[str, Any]) -> tuple[int, float, float, float]:
    full, train, oos = run_data["full"], run_data["train"], run_data["oos"]
    target = int(full.get("cagr", 0) >= 0.20 and full.get("max_drawdown", -1) >= -0.20)
    return target, min(float(train.get("cagr", -1)), float(oos.get("cagr", -1))), float(full.get("cagr", -1)), float(full.get("max_drawdown", -1))


def compact(run_data: dict[str, Any]) -> dict[str, Any]:
    c, full, train, oos = run_data["config"], run_data["full"], run_data["train"], run_data["oos"]
    return {
        "id": c["id"], "label": c["label"], "risk_rule": c["risk_label"], "model": c["model"], "top_n": c["top_n"], "schedule": c["schedule"], "weight_style": c["weight_style"], "trades": len(run_data["trades"]),
        "train_cagr": train.get("cagr", ""), "train_max_drawdown": train.get("max_drawdown", ""), "oos_cagr": oos.get("cagr", ""), "oos_max_drawdown": oos.get("max_drawdown", ""),
        "full_total_return": full.get("total_return", ""), "full_cagr": full.get("cagr", ""), "full_max_drawdown": full.get("max_drawdown", ""), "full_sharpe": full.get("sharpe", ""), "avg_exposure": full.get("avg_exposure", ""),
        "objective_met": bool(full.get("cagr", 0) >= 0.20 and full.get("max_drawdown", -1) >= -0.20),
    }


def pct(value: float | str | None) -> str:
    return "-" if value in (None, "") else f"{float(value) * 100:+.2f}%"


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
    lab.build_targets = build_targets
    try:
        baseline = [run(config, histories) for config in make_configs()]
        selected = sorted(baseline, key=lambda item: (float(item["train"].get("cagr", -1)) / max(abs(float(item["train"].get("max_drawdown", 0))), .03), float(item["train"].get("cagr", -1))), reverse=True)[:12]
        all_runs = list(baseline)
        for item in selected:
            for risk_id, risk_label, rules in dual.RISK_VARIANTS:
                config = deepcopy(item["config"])
                config["id"] = f"{config['id']}_{risk_id}"
                config["label"] = f"{config['label']}｜{risk_label}"
                config["risk_label"] = risk_label
                config.update(rules)
                all_runs.append(run(config, histories))
        all_runs.sort(key=rank, reverse=True)
        rows = [compact(item) for item in all_runs]
        write_csv(OUT_DIR / "neighborhood_metrics_latest.csv", rows)
        finalists = all_runs[:5]
        for n, item in enumerate(finalists, 1):
            prefix = OUT_DIR / f"top{n}_{item['config']['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), item["equity"])
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), item["trades"])
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), item["signals"])
        lines = [
            "# ETF双袖月频轮动邻域优化（T0）", "",
            "- 固定骨架：月频调仓，市场3/3强时做行业动量；弱市仅在国债/黄金中择强；单边成本0.105%。",
            "- 邻域只改变进攻评分（动量、趋势质量、近高点）和Top2/Top3权重；训练期为2015-2021，样本外为2022-2026。",
            "", "| 排名 | 版本 | 风险闸门 | 训练年化/回撤 | 样本外年化/回撤 | 全期年化/回撤 | Sharpe |", "|---:|---|---|---:|---:|---:|---:|",
        ]
        for n, row in enumerate([compact(item) for item in all_runs[:30]], 1):
            lines.append(f"| {n} | {row['label']} | {row['risk_rule']} | {pct(row['train_cagr'])}/{pct(row['train_max_drawdown'])} | {pct(row['oos_cagr'])}/{pct(row['oos_max_drawdown'])} | {pct(row['full_cagr'])}/{pct(row['full_max_drawdown'])} | {float(row['full_sharpe'] or 0):.2f} |")
        targets = sum(row["objective_met"] for row in rows)
        lines.extend(["", f"- 全部测试 {len(rows)} 组；年化>=20%且最大回撤<=20%的组合：{targets} 组。", f"- 全量数据：`{OUT_DIR / 'neighborhood_metrics_latest.csv'}`", ""])
        report = "\n".join(lines)
        (OUT_DIR / "neighborhood_summary_latest.md").write_text(report, encoding="utf-8")
        NOTE_PATH.write_text(report, encoding="utf-8")
        (OUT_DIR / "experiment_config_latest.json").write_text(json.dumps({"models": MODELS, "weights": WEIGHTS, "base_runs": len(baseline), "total_runs": len(all_runs)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report)
    finally:
        lab.build_targets = original


if __name__ == "__main__":
    main()
