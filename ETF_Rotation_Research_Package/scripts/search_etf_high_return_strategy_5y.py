#!/usr/bin/env python3
from __future__ import annotations

import copy
import csv
import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / "scripts/backtest_etf_dynamic_pool_5y_rs36_ma_gate.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/high_return_strategy_search_5y"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_mod = load_module("etf_high_return_gate", GATE_SCRIPT)
base = gate_mod.base
lab = gate_mod.lab
v3 = gate_mod.v3


HORIZONS: dict[str, dict[str, Any]] = {
    "m36": {"label": "3月50%+6月50%", "weights": {"ret63": 0.5, "ret126": 0.5}, "filter": "ma120_ret6"},
    "m612": {"label": "6月50%+12月50%", "weights": {"ret126": 0.5, "ret252": 0.5}, "filter": "ma200_ret12"},
    "m312": {"label": "3月50%+12月50%", "weights": {"ret63": 0.5, "ret252": 0.5}, "filter": "ma120_ret12"},
    "m136": {"label": "1月20%+3月30%+6月50%", "weights": {"ret20": 0.2, "ret63": 0.3, "ret126": 0.5}, "filter": "ma120_ret6"},
    "m1612": {"label": "1月20%+6月40%+12月40%", "weights": {"ret20": 0.2, "ret126": 0.4, "ret252": 0.4}, "filter": "ma200_ret12"},
    "m6": {"label": "6月100%", "weights": {"ret126": 1.0}, "filter": "ma120_ret6"},
}

SCHEDULES = {
    "month_start": "月初",
    "month_start_3": "月初第3日",
    "mid_month": "月中",
}

GATES: list[dict[str, Any]] = [
    {"id": "no_gate", "label": "无闸门", "ma_days": 0, "bad_count": 0},
    *[
        {"id": f"majority_below_ma{days}", "label": f"2/3指数低于MA{days}空仓", "ma_days": days, "bad_count": 2}
        for days in (100, 120, 150, 200)
    ],
]

FACTOR_VARIANTS: list[dict[str, Any]] = [
    {"id": "base", "label": "纯动量", "weights": {"momentum": 1.0}},
    {"id": "liq10", "label": "流动性10%", "weights": {"momentum": 0.9, "liquidity": 0.1}},
    {"id": "liq20", "label": "流动性20%", "weights": {"momentum": 0.8, "liquidity": 0.2}},
    {"id": "liq30", "label": "流动性30%", "weights": {"momentum": 0.7, "liquidity": 0.3}},
    {"id": "trend10", "label": "趋势质量10%", "weights": {"momentum": 0.9, "trend_quality": 0.1}},
    {"id": "trend20", "label": "趋势质量20%", "weights": {"momentum": 0.8, "trend_quality": 0.2}},
    {"id": "trend30", "label": "趋势质量30%", "weights": {"momentum": 0.7, "trend_quality": 0.3}},
    {"id": "liq10_trend10", "label": "流动性10%+趋势质量10%", "weights": {"momentum": 0.8, "liquidity": 0.1, "trend_quality": 0.1}},
    {"id": "liq20_trend10", "label": "流动性20%+趋势质量10%", "weights": {"momentum": 0.7, "liquidity": 0.2, "trend_quality": 0.1}},
]

FEATURE_CACHE: dict[str, list[dict[str, Any]]] = {}
GATE_CACHE: dict[tuple[int, int, str], tuple[bool, str]] = {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def percentile_ranks(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (float(row[key]), str(row["code"])))
    if len(ordered) <= 1:
        return {str(row["code"]): 0.5 for row in ordered}
    return {str(row["code"]): idx / (len(ordered) - 1) for idx, row in enumerate(ordered)}


def cached_features(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    if signal_date in FEATURE_CACHE:
        return copy.deepcopy(FEATURE_CACHE[signal_date])
    rows = lab.etf_features(signal_date, histories, indexes)
    for row in rows:
        code = str(row["code"])
        idx = indexes[code][signal_date]
        history = histories[code][: idx + 1]
        closes = [base.fnum(item["close"]) for item in history]
        window = closes[-127:]
        path = sum(abs(math.log(window[i] / window[i - 1])) for i in range(1, len(window)) if window[i - 1] > 0)
        net = abs(math.log(window[-1] / window[0])) if len(window) >= 2 and window[0] > 0 else 0.0
        amounts = [max(base.fnum(item.get("amount_yi")), 0.0) for item in history[-20:]]
        row["trend_quality"] = net / path if path > 0 else 0.0
        row["liquidity"] = math.log1p(statistics.fmean(amounts)) if amounts else 0.0
    FEATURE_CACHE[signal_date] = copy.deepcopy(rows)
    return rows


def is_eligible(row: dict[str, Any], filter_name: str) -> bool:
    if row["amount_yi"] < 0.10:
        return False
    if filter_name == "ma120_ret6":
        return row["close"] > row["ma120"] and row["ret126"] > 0
    if filter_name == "ma200_ret12":
        return row["close"] > row["ma200"] and row["ret252"] > 0
    if filter_name == "ma120_ret12":
        return row["close"] > row["ma120"] and row["ret252"] > 0
    raise ValueError(f"unknown filter {filter_name}")


def build_targets(
    strategy: dict[str, Any],
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], dict[str, Any]]:
    rows = cached_features(signal_date, histories, indexes)
    horizon = HORIZONS[str(strategy["horizon_id"])]
    for row in rows:
        row["momentum"] = sum(float(weight) * float(row[key]) for key, weight in horizon["weights"].items())
        row["eligible"] = is_eligible(row, str(strategy["filter"]))
    candidates = [row for row in rows if row["eligible"]]
    factor_weights = strategy["factor_weights"]
    ranks = {factor: percentile_ranks(candidates, factor) for factor in factor_weights}
    for row in rows:
        row["score"] = (
            sum(float(weight) * ranks[factor][str(row["code"])] for factor, weight in factor_weights.items())
            if row["eligible"]
            else -1.0
        )
    ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
    selected = [row for row in ranked if row["eligible"]][: int(strategy["top_n"])]
    targets = lab.normalized_weights(selected, str(strategy["weighting"]), strategy)
    return targets, {
        "market_note": "market_gate_external",
        "ranked": ranked,
        "selected": selected,
        "eligible_count": len(candidates),
    }


def generic_market_gate_bad(
    gate: dict[str, Any],
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[bool, str]:
    days = int(gate.get("ma_days", 0))
    bad_count = int(gate.get("bad_count", 0))
    if days <= 0:
        return False, "no_gate"
    cache_key = (days, bad_count, signal_date)
    if cache_key in GATE_CACHE:
        return GATE_CACHE[cache_key]
    bad_names: list[str] = []
    valid = 0
    for label, code in (("沪深300", "IDX_HS300"), ("创业板", "IDX_CHINEXT"), ("科创50", "IDX_SCI50")):
        idx = indexes.get(code, {}).get(signal_date)
        if idx is None or idx < days:
            continue
        closes = [base.fnum(row["close"]) for row in histories[code][: idx + 1]]
        if len(closes) <= days or min(closes[-days:]) <= 0:
            continue
        valid += 1
        if closes[-1] < v3.ma(closes, days):
            bad_names.append(label)
    bad = valid >= 2 and len(bad_names) >= bad_count
    result = (bad, f"bad={len(bad_names)}/{valid}|MA{days}|{','.join(bad_names) or '-'}")
    GATE_CACHE[cache_key] = result
    return result


def make_config(
    horizon_id: str,
    schedule: str,
    top_n: int,
    gate: dict[str, Any],
    factor_variant: dict[str, Any],
    weighting: str = "equal",
) -> dict[str, Any]:
    horizon = HORIZONS[horizon_id]
    config_id = "_".join(
        [horizon_id, schedule, f"top{top_n}", gate["id"], factor_variant["id"], weighting]
    )
    return {
        "id": config_id,
        "label": f"{horizon['label']}｜{SCHEDULES[schedule]} Top{top_n}｜{gate['label']}｜{factor_variant['label']}｜{weighting}",
        "horizon_id": horizon_id,
        "model": horizon_id,
        "top_n": top_n,
        "filter": horizon["filter"],
        "weighting": weighting,
        "schedule": schedule,
        "factor_id": factor_variant["id"],
        "factor_weights": dict(factor_variant["weights"]),
        "gate": dict(gate),
        "fee_rate": base.FEE_RATE,
    }


def run_one(
    config: dict[str, Any],
    histories: dict[str, list[dict[str, Any]]],
    start_date: str,
    fee_mult: float = 1.0,
) -> dict[str, Any]:
    strategy = dict(config)
    strategy["start_date"] = start_date
    strategy["fee_rate"] = base.FEE_RATE * fee_mult
    equity, trades, signals = gate_mod.simulate_with_gate(strategy, strategy["gate"], histories)
    metrics = v3.perf_metrics(equity)
    return {
        "id": config["id"],
        "label": config["label"],
        "config": config,
        "metrics": metrics,
        "trade_count": len(trades),
        "equity": equity,
        "trades": trades,
        "signals": signals,
        "start_date_requested": start_date,
        "fee_mult": fee_mult,
    }


def compact(row: dict[str, Any], stage: str) -> dict[str, Any]:
    config = row["config"]
    m = row["metrics"]
    return {
        "stage": stage,
        "id": row["id"],
        "label": row["label"],
        "horizon": config["horizon_id"],
        "schedule": config["schedule"],
        "top_n": config["top_n"],
        "gate": config["gate"]["id"],
        "factor": config["factor_id"],
        "weighting": config["weighting"],
        "fee_mult": row["fee_mult"],
        "requested_start": row["start_date_requested"],
        "actual_start": m["start"],
        "end": m["end"],
        "total_return": m["total_return"],
        "cagr": m["cagr"],
        "max_drawdown": m["max_drawdown"],
        "sharpe": m["sharpe"],
        "avg_exposure": m["avg_exposure"],
        "trades": row["trade_count"],
        "yearly": json.dumps(m.get("yearly", {}), ensure_ascii=False),
    }


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def result_rank(row: dict[str, Any]) -> tuple[int, float, float, float]:
    m = row["metrics"]
    within_dd = int(m["max_drawdown"] >= -0.20)
    return within_dd, m["total_return"], m["cagr"], m["sharpe"]


def build_summary(
    broad: list[dict[str, Any]],
    refined: list[dict[str, Any]],
    stress: list[dict[str, Any]],
    robust: list[dict[str, Any]],
) -> str:
    lines = [
        "# ETF高收益策略搜索与压力测试",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 优化目标：总收益优先；同等收益下优先低回撤，并把20%最大回撤作为重点风险线。",
        "- 数据与执行：5年动态ETF池，上市满252日才可入池；收盘生成信号，次日开盘交易。",
        f"- 默认成本：佣金万0.5 + 滑点0.10%，单边 {base.FEE_RATE * 100:.3f}%。",
        "- 搜索结构：先广搜动量周期/调仓日/TopN/市场闸门，再对胜出组合叠加因子与仓位权重。",
        "",
        "## 精细搜索 Top15",
        "",
        "| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易数 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(sorted(refined, key=result_rank, reverse=True)[:15], 1):
        m = row["metrics"]
        lines.append(
            f"| {idx} | {row['label']} | {fmt_pct(m['total_return'])} | {fmt_pct(m['cagr'])} | "
            f"{fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {fmt_pct(m['avg_exposure'])} | {row['trade_count']} |"
        )
    lines.extend(
        [
            "",
            "## 稳健候选",
            "",
            "稳健排名依据为不同起点与成本场景的中位年化，仍以收益为主；同时展示最差回撤。",
            "",
            "| 排名 | 策略 | 全期总收益 | 全期年化 | 全期回撤 | 压测中位年化 | 压测最低年化 | 压测最差回撤 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(robust[:10], 1):
        full = row["full"]["metrics"]
        lines.append(
            f"| {idx} | {row['full']['label']} | {fmt_pct(full['total_return'])} | {fmt_pct(full['cagr'])} | "
            f"{fmt_pct(full['max_drawdown'])} | {fmt_pct(row['median_cagr'])} | "
            f"{fmt_pct(row['min_cagr'])} | {fmt_pct(row['worst_drawdown'])} |"
        )
    best_broad = max(broad, key=result_rank)
    best_refined = max(refined, key=result_rank)
    lines.extend(
        [
            "",
            "## 核心观察",
            "",
            f"- 广搜最佳：`{best_broad['label']}`，总收益 {fmt_pct(best_broad['metrics']['total_return'])}，最大回撤 {fmt_pct(best_broad['metrics']['max_drawdown'])}。",
            f"- 叠加因子后样本内最佳：`{best_refined['label']}`，总收益 {fmt_pct(best_refined['metrics']['total_return'])}，最大回撤 {fmt_pct(best_refined['metrics']['max_drawdown'])}。",
            f"- 共完成 {len(broad) + len(refined) + len(stress)} 次回测，其中广搜 {len(broad)} 次、精细搜索 {len(refined)} 次、压力测试 {len(stress)} 次。",
            "- 最高收益版本和最稳健版本可能不同；实际观察池优先采用压力测试后仍靠前的参数平台。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = base.load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    full_start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    original_targets = lab.build_targets
    original_gate = gate_mod.market_gate_bad
    lab.build_targets = build_targets
    gate_mod.market_gate_bad = generic_market_gate_bad
    try:
        broad_configs = [
            make_config(horizon_id, schedule, top_n, gate, FACTOR_VARIANTS[0])
            for horizon_id in HORIZONS
            for schedule in SCHEDULES
            for top_n in (1, 2, 3)
            for gate in GATES
        ]
        broad = [run_one(config, histories, full_start) for config in broad_configs]

        broad_seed = sorted(broad, key=result_rank, reverse=True)[:12]
        refined_configs: dict[str, dict[str, Any]] = {}
        for seed in broad_seed:
            base_config = seed["config"]
            for factor_variant in FACTOR_VARIANTS:
                for weighting in ("equal", "inverse_vol"):
                    config = make_config(
                        str(base_config["horizon_id"]),
                        str(base_config["schedule"]),
                        int(base_config["top_n"]),
                        dict(base_config["gate"]),
                        factor_variant,
                        weighting,
                    )
                    refined_configs[config["id"]] = config
        refined = [run_one(config, histories, full_start) for config in refined_configs.values()]

        finalists = sorted(refined, key=result_rank, reverse=True)[:8]
        stress: list[dict[str, Any]] = []
        stress_starts = [full_start, "2022-01-04", "2023-01-03"]
        for finalist in finalists:
            for start_date in stress_starts:
                for fee_mult in (1.0, 2.0):
                    stress.append(run_one(finalist["config"], histories, start_date, fee_mult))

        robust_rows: list[dict[str, Any]] = []
        for finalist in finalists:
            cases = [row for row in stress if row["id"] == finalist["id"]]
            cagrs = [row["metrics"]["cagr"] for row in cases]
            robust_rows.append(
                {
                    "full": finalist,
                    "median_cagr": statistics.median(cagrs),
                    "min_cagr": min(cagrs),
                    "worst_drawdown": min(row["metrics"]["max_drawdown"] for row in cases),
                }
            )
        robust_rows.sort(key=lambda row: (row["median_cagr"], row["min_cagr"]), reverse=True)

        write_csv(OUT_DIR / "broad_search_metrics_latest.csv", [compact(row, "broad") for row in broad])
        write_csv(OUT_DIR / "refined_search_metrics_latest.csv", [compact(row, "refined") for row in refined])
        write_csv(OUT_DIR / "stress_test_metrics_latest.csv", [compact(row, "stress") for row in stress])

        for idx, row in enumerate(sorted(refined, key=result_rank, reverse=True)[:10], 1):
            prefix = OUT_DIR / f"top{idx}_{row['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), row["equity"])
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), row["trades"])
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), row["signals"])

        summary = build_summary(broad, refined, stress, robust_rows)
        summary_path = OUT_DIR / "high_return_strategy_search_summary_latest.md"
        summary_path.write_text(summary, encoding="utf-8")
        (OUT_DIR / "high_return_strategy_search_metrics_latest.json").write_text(
            json.dumps(
                {
                    "broad": [compact(row, "broad") for row in broad],
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
                        for row in robust_rows
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
        gate_mod.market_gate_bad = original_gate


if __name__ == "__main__":
    main()
