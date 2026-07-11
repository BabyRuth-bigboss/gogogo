#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEARCH_SCRIPT = ROOT / "scripts/search_etf_high_return_strategy_5y.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/high_return_winner_neighborhood_5y"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


search = load_module("etf_high_return_search_base", SEARCH_SCRIPT)
base = search.base
lab = search.lab
gate_mod = search.gate_mod
v3 = search.v3


CUSTOM_HORIZONS = {
    "m612_30_70": {"label": "6月30%+12月70%", "weights": {"ret126": 0.3, "ret252": 0.7}, "filter": "ma200_ret12"},
    "m612_40_60": {"label": "6月40%+12月60%", "weights": {"ret126": 0.4, "ret252": 0.6}, "filter": "ma200_ret12"},
    "m612_50_50": {"label": "6月50%+12月50%", "weights": {"ret126": 0.5, "ret252": 0.5}, "filter": "ma200_ret12"},
    "m612_60_40": {"label": "6月60%+12月40%", "weights": {"ret126": 0.6, "ret252": 0.4}, "filter": "ma200_ret12"},
    "m612_70_30": {"label": "6月70%+12月30%", "weights": {"ret126": 0.7, "ret252": 0.3}, "filter": "ma200_ret12"},
    "m1612_10_40_50": {"label": "1月10%+6月40%+12月50%", "weights": {"ret20": 0.1, "ret126": 0.4, "ret252": 0.5}, "filter": "ma200_ret12"},
    "m1612_20_40_40": {"label": "1月20%+6月40%+12月40%", "weights": {"ret20": 0.2, "ret126": 0.4, "ret252": 0.4}, "filter": "ma200_ret12"},
    "m3612_10_45_45": {"label": "3月10%+6月45%+12月45%", "weights": {"ret63": 0.1, "ret126": 0.45, "ret252": 0.45}, "filter": "ma200_ret12"},
}

SCHEDULES = {f"month_start_{day}": f"月初第{day}日" for day in range(1, 6)}
GATES = [
    {"id": f"majority_below_ma{days}", "label": f"2/3指数低于MA{days}空仓", "ma_days": days, "bad_count": 2}
    for days in (100, 110, 120, 130, 140, 150)
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def custom_rebalance_indices(dates: list[str], start_idx: int, schedule: str) -> set[int]:
    if not schedule.startswith("month_start_"):
        return ORIGINAL_REBALANCE(dates, start_idx, schedule)
    day = int(schedule.rsplit("_", 1)[1])
    by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i in range(start_idx, len(dates) - 1):
        trade_date = dt.date.fromisoformat(dates[i + 1])
        by_month[(trade_date.year, trade_date.month)].append(i)
    out = {start_idx}
    for month in by_month.values():
        out.add(month[min(day - 1, len(month) - 1)])
    return out


def factor_variant(liquidity_weight: float, trend_weight: float) -> dict[str, Any]:
    momentum_weight = 1.0 - liquidity_weight - trend_weight
    parts = []
    weights = {"momentum": momentum_weight}
    if liquidity_weight:
        weights["liquidity"] = liquidity_weight
        parts.append(f"流动性{liquidity_weight:.0%}")
    if trend_weight:
        weights["trend_quality"] = trend_weight
        parts.append(f"趋势质量{trend_weight:.0%}")
    return {
        "id": f"liq{int(liquidity_weight*100)}_trend{int(trend_weight*100)}",
        "label": "+".join(parts) if parts else "纯动量",
        "weights": weights,
    }


def make_config(
    horizon_id: str,
    schedule: str,
    gate: dict[str, Any],
    factor: dict[str, Any],
    weighting: str = "equal",
) -> dict[str, Any]:
    horizon = search.HORIZONS[horizon_id]
    config_id = "_".join([horizon_id, schedule, "top2", gate["id"], factor["id"], weighting])
    return {
        "id": config_id,
        "label": f"{horizon['label']}｜{SCHEDULES[schedule]} Top2｜{gate['label']}｜{factor['label']}｜{weighting}",
        "horizon_id": horizon_id,
        "model": horizon_id,
        "top_n": 2,
        "filter": horizon["filter"],
        "weighting": weighting,
        "schedule": schedule,
        "factor_id": factor["id"],
        "factor_weights": dict(factor["weights"]),
        "gate": dict(gate),
        "fee_rate": base.FEE_RATE,
    }


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def rank_key(row: dict[str, Any]) -> tuple[int, float, float]:
    metrics = row["metrics"]
    return int(metrics["max_drawdown"] >= -0.20), metrics["total_return"], metrics["sharpe"]


def compact(row: dict[str, Any], stage: str) -> dict[str, Any]:
    config = row["config"]
    metrics = row["metrics"]
    return {
        "stage": stage,
        "id": row["id"],
        "label": row["label"],
        "horizon": config["horizon_id"],
        "schedule": config["schedule"],
        "gate": config["gate"]["id"],
        "factor": config["factor_id"],
        "weighting": config["weighting"],
        "fee_mult": row["fee_mult"],
        "requested_start": row["start_date_requested"],
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trades": row["trade_count"],
        "yearly": json.dumps(metrics.get("yearly", {}), ensure_ascii=False),
    }


def build_summary(
    coarse: list[dict[str, Any]],
    refined: list[dict[str, Any]],
    stress: list[dict[str, Any]],
    robust: list[dict[str, Any]],
) -> str:
    top = sorted(refined, key=rank_key, reverse=True)
    lines = [
        "# ETF高收益胜出区域邻域搜索",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 搜索范围：月初第1至5个交易日、MA100至MA150市场闸门、8组动量配比。",
        "- 精细因子：流动性10%至30%，趋势质量0%至10%。",
        "- 目标：总收益优先，最大回撤尽量不超过20%。",
        "",
        "## 样本内 Top15",
        "",
        "| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 仓位 | 交易数 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(top[:15], 1):
        m = row["metrics"]
        lines.append(
            f"| {idx} | {row['label']} | {fmt_pct(m['total_return'])} | {fmt_pct(m['cagr'])} | "
            f"{fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {fmt_pct(m['avg_exposure'])} | {row['trade_count']} |"
        )
    lines.extend(
        [
            "",
            "## 压力测试排名",
            "",
            "| 排名 | 策略 | 全期收益 | 全期回撤 | 压测中位年化 | 压测最低年化 | 压测最差回撤 |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(robust, 1):
        full = row["full"]
        lines.append(
            f"| {idx} | {full['label']} | {fmt_pct(full['metrics']['total_return'])} | "
            f"{fmt_pct(full['metrics']['max_drawdown'])} | {fmt_pct(row['median_cagr'])} | "
            f"{fmt_pct(row['min_cagr'])} | {fmt_pct(row['worst_drawdown'])} |"
        )
    best = top[0]
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 邻域最高收益：`{best['label']}`，总收益 {fmt_pct(best['metrics']['total_return'])}，年化 {fmt_pct(best['metrics']['cagr'])}，最大回撤 {fmt_pct(best['metrics']['max_drawdown'])}。",
            f"- 本轮完成 {len(coarse) + len(refined) + len(stress)} 次回测：粗搜索 {len(coarse)}、因子精细搜索 {len(refined)}、压力测试 {len(stress)}。",
            "- 若最佳点周围多个相邻参数都保持接近结果，可视为平台；若只有单个调仓日或单个权重突出，应按过拟合处理。",
            "",
        ]
    )
    return "\n".join(lines)


ORIGINAL_REBALANCE = lab.build_rebalance_indices


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    search.HORIZONS.update(CUSTOM_HORIZONS)
    search.SCHEDULES.update(SCHEDULES)
    histories, errors = base.load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    full_start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    original_targets = lab.build_targets
    original_gate = gate_mod.market_gate_bad
    lab.build_targets = search.build_targets
    lab.build_rebalance_indices = custom_rebalance_indices
    gate_mod.market_gate_bad = search.generic_market_gate_bad
    try:
        pure = factor_variant(0.0, 0.0)
        coarse_configs = [
            make_config(horizon_id, schedule, gate, pure)
            for horizon_id in CUSTOM_HORIZONS
            for schedule in SCHEDULES
            for gate in GATES
        ]
        coarse = [search.run_one(config, histories, full_start) for config in coarse_configs]

        seeds = sorted(coarse, key=rank_key, reverse=True)[:20]
        refined_configs: dict[str, dict[str, Any]] = {}
        for seed in seeds:
            config = seed["config"]
            for liq in (0.10, 0.15, 0.20, 0.25, 0.30):
                for trend in (0.0, 0.05, 0.10):
                    factor = factor_variant(liq, trend)
                    candidate = make_config(
                        str(config["horizon_id"]),
                        str(config["schedule"]),
                        dict(config["gate"]),
                        factor,
                    )
                    refined_configs[candidate["id"]] = candidate
        refined = [search.run_one(config, histories, full_start) for config in refined_configs.values()]

        finalists = sorted(refined, key=rank_key, reverse=True)[:8]
        stress: list[dict[str, Any]] = []
        for finalist in finalists:
            variants = [finalist["config"]]
            inverse = dict(finalist["config"])
            inverse["id"] = inverse["id"].replace("_equal", "_inverse_vol")
            inverse["label"] = inverse["label"].replace("｜equal", "｜inverse_vol")
            inverse["weighting"] = "inverse_vol"
            variants.append(inverse)
            for config in variants:
                for start_date in (full_start, "2022-01-04", "2023-01-03"):
                    for fee_mult in (1.0, 2.0):
                        stress.append(search.run_one(config, histories, start_date, fee_mult))

        robust: list[dict[str, Any]] = []
        for finalist in finalists:
            cases = [row for row in stress if row["id"] in (finalist["id"], finalist["id"].replace("_equal", "_inverse_vol"))]
            cagrs = [row["metrics"]["cagr"] for row in cases]
            robust.append(
                {
                    "full": finalist,
                    "median_cagr": statistics.median(cagrs),
                    "min_cagr": min(cagrs),
                    "worst_drawdown": min(row["metrics"]["max_drawdown"] for row in cases),
                }
            )
        robust.sort(key=lambda row: (row["median_cagr"], row["min_cagr"]), reverse=True)

        write_csv(OUT_DIR / "coarse_metrics_latest.csv", [compact(row, "coarse") for row in coarse])
        write_csv(OUT_DIR / "refined_metrics_latest.csv", [compact(row, "refined") for row in refined])
        write_csv(OUT_DIR / "stress_metrics_latest.csv", [compact(row, "stress") for row in stress])
        for idx, row in enumerate(sorted(refined, key=rank_key, reverse=True)[:10], 1):
            prefix = OUT_DIR / f"top{idx}_{row['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), row["equity"])
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), row["trades"])
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), row["signals"])

        summary = build_summary(coarse, refined, stress, robust)
        summary_path = OUT_DIR / "winner_neighborhood_summary_latest.md"
        summary_path.write_text(summary, encoding="utf-8")
        (OUT_DIR / "winner_neighborhood_metrics_latest.json").write_text(
            json.dumps(
                {
                    "coarse": [compact(row, "coarse") for row in coarse],
                    "refined": [compact(row, "refined") for row in refined],
                    "stress": [compact(row, "stress") for row in stress],
                    "robust": [
                        {
                            "id": row["full"]["id"],
                            "label": row["full"]["label"],
                            "full_metrics": row["full"]["metrics"],
                            "median_cagr": row["median_cagr"],
                            "min_cagr": row["min_cagr"],
                            "worst_drawdown": row["worst_drawdown"],
                        }
                        for row in robust
                    ],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(summary)
        print(f"wrote {summary_path}")
    finally:
        lab.build_targets = original_targets
        lab.build_rebalance_indices = ORIGINAL_REBALANCE
        gate_mod.market_gate_bad = original_gate


if __name__ == "__main__":
    main()
