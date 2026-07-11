#!/usr/bin/env python3
from __future__ import annotations

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
V2_SCRIPT = ROOT / "scripts/backtest_etf_regime_dual_sleeve_5y_v2.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v21"

TRAIN_END = "2023-12-29"
OOS_START = "2024-01-02"

GOLD = {"code": "518880", "theme": "防御", "label": "黄金ETF"}
QDII_CODES = {"513100", "513500"}
DEFENSIVE_POOLS = {
    "overseas": {"label": "纳指+标普", "codes": {"513100", "513500"}},
    "overseas_gold": {"label": "纳指+标普+黄金", "codes": {"513100", "513500", "518880"}},
}
OFFENSIVE_SCHEMES = {
    "top2_equal": {"top_n": 2, "weights": [0.50, 0.50]},
    "top3_45_45_10": {"top_n": 3, "weights": [0.45, 0.45, 0.10]},
    "top3_45_40_15": {"top_n": 3, "weights": [0.45, 0.40, 0.15]},
    "top3_42_42_16": {"top_n": 3, "weights": [0.42, 0.42, 0.16]},
    "top3_45_35_20": {"top_n": 3, "weights": [0.45, 0.35, 0.20]},
    "top3_40_40_20": {"top_n": 3, "weights": [0.40, 0.40, 0.20]},
    "top3_equal": {"top_n": 3, "weights": [1 / 3, 1 / 3, 1 / 3]},
    "top4_equal": {"top_n": 4, "weights": [0.25, 0.25, 0.25, 0.25]},
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v2 = load_module("etf_dual_sleeve_v21_base", V2_SCRIPT)
base = v2.base
lab = v2.lab
search = v2.search
v3 = v2.v3
BaseRegimeEngine = v2.RegimeEngine

ACTIVE_CONFIG: dict[str, Any] = {}


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


def percentile_ranks(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (float(row[key]), str(row["code"])))
    if len(ordered) <= 1:
        return {str(row["code"]): 0.5 for row in ordered}
    return {str(row["code"]): idx / (len(ordered) - 1) for idx, row in enumerate(ordered)}


def gold_feature(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> dict[str, Any] | None:
    code = GOLD["code"]
    idx = indexes.get(code, {}).get(signal_date)
    if idx is None or idx < 252:
        return None
    history = histories[code][: idx + 1]
    closes = [base.fnum(row["close"]) for row in history]
    if len(closes) < 253 or min(closes[-253:]) <= 0:
        return None
    stats = lab.returns(closes)
    amounts = [max(base.fnum(row.get("amount_yi")), 0.0) for row in history[-20:]]
    return {
        "code": code,
        "label": GOLD["label"],
        "theme": GOLD["theme"],
        "close": closes[-1],
        "ma60": v3.ma(closes, 60),
        "ma120": v3.ma(closes, 120),
        "ma200": v3.ma(closes, 200),
        "high120": max(closes[-120:]),
        "amount_yi": base.fnum(history[-1].get("amount_yi")),
        "liquidity": math.log1p(statistics.fmean(amounts)) if amounts else 0.0,
        **stats,
    }


def offensive_targets(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], dict[str, Any]]:
    scheme = OFFENSIVE_SCHEMES[str(ACTIVE_CONFIG["offensive_scheme"])]
    strategy = {
        "horizon_id": "offensive_m612_60_40",
        "filter": "ma200_ret12",
        "factor_weights": {"momentum": 0.75, "liquidity": 0.15, "trend_quality": 0.10},
        "top_n": int(scheme["top_n"]),
        "weighting": "equal",
    }
    _, info = search.build_targets(strategy, signal_date, histories, indexes)
    selected = info.get("selected", [])
    targets = {
        row["code"]: float(weight)
        for row, weight in zip(selected, scheme["weights"])
    }
    return targets, info


def defensive_targets(
    config: dict[str, Any],
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], dict[str, Any]]:
    rows = [
        row for row in search.cached_features(signal_date, histories, indexes)
        if row["code"] in DEFENSIVE_POOLS[config["defensive_pool"]]["codes"]
    ]
    if GOLD["code"] in DEFENSIVE_POOLS[config["defensive_pool"]]["codes"]:
        gold = gold_feature(signal_date, histories, indexes)
        if gold:
            rows.append(gold)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if config["defensive_filter"] == "ma120_ret6":
            eligible = row["close"] > row["ma120"] and row["ret126"] > 0
        else:
            eligible = row["close"] > row["ma200"] and row["ret252"] > 0
        if not eligible or row["amount_yi"] < 0.10:
            continue
        row["momentum"] = row["ret126"] * 0.5 + row["ret252"] * 0.5
        candidates.append(row)
    momentum_rank = percentile_ranks(candidates, "momentum")
    liquidity_rank = percentile_ranks(candidates, "liquidity")
    for row in candidates:
        row["score"] = momentum_rank[row["code"]] * 0.9 + liquidity_rank[row["code"]] * 0.1
    selected = sorted(candidates, key=lambda row: row["score"], reverse=True)[: int(config["defensive_top_n"])]
    targets = {row["code"]: 1.0 / len(selected) for row in selected} if selected else {}
    return targets, {"ranked": candidates, "selected": selected, "eligible_count": len(candidates)}


class EmergencyRegimeEngine(BaseRegimeEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._emergency_until = -1

    def _fast_crash(
        self,
        signal_date: str,
        histories: dict[str, list[dict[str, Any]]],
        indexes: dict[str, dict[str, int]],
    ) -> tuple[bool, str]:
        threshold = float(ACTIVE_CONFIG.get("fast_crash_5d", 0.0) or 0.0)
        if threshold >= 0:
            return False, "fast_off"
        weak: list[str] = []
        valid = 0
        for label, code in (
            ("沪深300", "IDX_HS300"),
            ("创业板", "IDX_CHINEXT"),
            ("科创50", "IDX_SCI50"),
            ("中证500", "IDX_CSI500"),
        ):
            idx = indexes.get(code, {}).get(signal_date)
            if idx is None or idx < 5:
                continue
            closes = [float(row["close"]) for row in histories[code][: idx + 1]]
            if len(closes) < 6 or closes[-6] <= 0:
                continue
            valid += 1
            if closes[-1] / closes[-6] - 1 <= threshold:
                weak.append(label)
        return valid >= 3 and len(weak) >= 2, f"fast5={len(weak)}/{valid}({','.join(weak) or '-'})"

    def classify(
        self,
        signal_date: str,
        histories: dict[str, list[dict[str, Any]]],
        indexes: dict[str, dict[str, int]],
        day_index: int = 0,
    ) -> tuple[bool, str]:
        base_bad, note = super().classify(signal_date, histories, indexes, day_index)
        if note.startswith("no_valid_index"):
            return self._prev_regime == -1, note + "|数据不足维持状态"
        fast_bad, fast_note = self._fast_crash(signal_date, histories, indexes)
        if fast_bad:
            hold_days = int(ACTIVE_CONFIG.get("emergency_hold_days", 5))
            self._emergency_until = max(self._emergency_until, day_index + hold_days - 1)
        emergency = day_index <= self._emergency_until
        if emergency:
            return True, note + f"|{fast_note}|紧急防御至{self._emergency_until}"
        return base_bad, note + "|" + fast_note


def make_config(
    offensive_scheme: str,
    fast_crash_5d: float,
    defensive_pool: str,
    defensive_filter: str,
    defensive_top_n: int,
) -> dict[str, Any]:
    fast_label = "off" if fast_crash_5d >= 0 else f"m{abs(int(fast_crash_5d * 100))}"
    scheme = OFFENSIVE_SCHEMES[offensive_scheme]
    config_id = "_".join(
        [
            offensive_scheme,
            f"fast{fast_label}",
            defensive_pool,
            defensive_filter,
            f"deftop{defensive_top_n}",
        ]
    )
    return {
        "id": config_id,
        "offensive_scheme": offensive_scheme,
        "offensive_top_n": scheme["top_n"],
        "fast_crash_5d": fast_crash_5d,
        "emergency_hold_days": 5,
        "defensive_pool": defensive_pool,
        "defensive_model": "rs612",
        "defensive_top_n": defensive_top_n,
        "defensive_allocation": 1.0,
        "defensive_filter": defensive_filter,
        "recovery_mode": "immediate",
        "regime": {
            "use_hk_indices": True,
            "ma_days": 120,
            "slope_lookback": 20,
            "slope_threshold": -0.005,
            "weak_ratio": 0.50,
            "confirm_days": 2,
            "recovery_confirm_days": 1,
            "cooldown_days": 5,
        },
    }


def config_label(config: dict[str, Any]) -> str:
    fast = "无紧急闸门" if config["fast_crash_5d"] >= 0 else f"2/4指数5日<= {config['fast_crash_5d']:.0%}"
    filt = "MA120+6月正" if config["defensive_filter"] == "ma120_ret6" else "MA200+12月正"
    return (
        f"进攻{config['offensive_scheme']}｜{fast}｜"
        f"弱市{DEFENSIVE_POOLS[config['defensive_pool']]['label']} Top{config['defensive_top_n']}｜{filt}"
    )


def period_metrics(
    equity: list[dict[str, Any]],
    start: str,
    end: str | None = None,
    initial_cash: float | None = None,
) -> dict[str, Any]:
    selected = [row for row in equity if row["date"] >= start and (end is None or row["date"] <= end)]
    if not selected:
        return {}
    previous = [row for row in equity if row["date"] < start]
    if previous:
        base_equity = float(previous[-1]["equity"])
        base_date = previous[-1]["date"]
    else:
        base_equity = float(initial_cash or v3.INITIAL_CASH)
        base_date = (dt.date.fromisoformat(selected[0]["date"]) - dt.timedelta(days=1)).isoformat()
    initial = {"date": base_date, "equity": base_equity, "cash": base_equity, "exposure": 0.0, "positions": ""}
    return v3.perf_metrics([initial, *selected])


def run_config(
    config: dict[str, Any],
    histories: dict[str, list[dict[str, Any]]],
    start_date: str,
    fee_mult: float = 1.0,
) -> dict[str, Any]:
    global ACTIVE_CONFIG
    ACTIVE_CONFIG = config
    equity, trades, signals = v2.simulate(config, histories, start_date, fee_mult)
    full = v2.portfolio_metrics(equity)
    train = period_metrics(equity, start_date, TRAIN_END, v3.INITIAL_CASH)
    oos = period_metrics(equity, OOS_START)
    return {
        "id": config["id"],
        "label": config_label(config),
        "config": config,
        "full": full,
        "train": train,
        "oos": oos,
        "trade_count": len(trades),
        "equity": equity,
        "trades": trades,
        "signals": signals,
        "fee_mult": fee_mult,
    }


def train_rank(row: dict[str, Any]) -> tuple[int, float, float]:
    metrics = row["train"]
    return int(metrics["max_drawdown"] >= -0.20), metrics["total_return"], metrics["sharpe"]


def research_rank(row: dict[str, Any]) -> tuple[int, float, float]:
    return (
        int(row["full"]["max_drawdown"] >= -0.20 and row["oos"]["max_drawdown"] >= -0.20),
        row["oos"]["total_return"],
        row["full"]["total_return"],
    )


def compact(row: dict[str, Any], selected_by_train: bool = False) -> dict[str, Any]:
    config = row["config"]
    return {
        "id": row["id"],
        "label": row["label"],
        "selected_by_train": selected_by_train,
        "offensive_scheme": config["offensive_scheme"],
        "offensive_top_n": config["offensive_top_n"],
        "fast_crash_5d": config["fast_crash_5d"],
        "defensive_pool": config["defensive_pool"],
        "defensive_filter": config["defensive_filter"],
        "defensive_top_n": config["defensive_top_n"],
        "fee_mult": row["fee_mult"],
        "full_return": row["full"]["total_return"],
        "full_cagr": row["full"]["cagr"],
        "full_max_drawdown": row["full"]["max_drawdown"],
        "full_sharpe": row["full"]["sharpe"],
        "train_return": row["train"].get("total_return", ""),
        "train_cagr": row["train"].get("cagr", ""),
        "train_max_drawdown": row["train"].get("max_drawdown", ""),
        "oos_return": row["oos"].get("total_return", ""),
        "oos_cagr": row["oos"].get("cagr", ""),
        "oos_max_drawdown": row["oos"].get("max_drawdown", ""),
        "oos_sharpe": row["oos"].get("sharpe", ""),
        "trades": row["trade_count"],
    }


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def current_positions(row: dict[str, Any]) -> str:
    if not row["equity"]:
        return "现金"
    labels = {item["code"]: item["label"] for item in v3.ETF_UNIVERSE}
    labels[GOLD["code"]] = GOLD["label"]
    codes = [code for code in str(row["equity"][-1].get("positions", "")).split(";") if code]
    return ";".join(f"{labels.get(code, code)}({code})" for code in codes) or "现金"


def summary(
    rows: list[dict[str, Any]],
    train_selected: list[dict[str, Any]],
    recommended: dict[str, Any],
    baseline: dict[str, Any],
    stresses: list[dict[str, Any]],
) -> str:
    lines = [
        "# ETF双袖轮动V2.1优化回测",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 目标：收益优先，同时尽量将全期和样本外最大回撤控制在20%以内。",
        "- 训练期：2021-07至2023-12；独立测试期：2024-01至最新。",
        "- 新增：Top2/3/4、短期急跌紧急闸门、黄金防御候选。",
        "- QDII说明：回测使用二级市场成交价，历史溢价变化已进入价格，但没有NAV/IOPV，未伪造2%溢价门控。",
        "",
        "## V2基线与V2.1研究候选",
        "",
        "| 版本 | 全期收益 | 全期年化 | 全期回撤 | OOS收益 | OOS年化 | OOS回撤 | Sharpe | 当前持仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, row in (("V2基线", baseline), ("V2.1候选", recommended)):
        lines.append(
            f"| {name} | {pct(row['full']['total_return'])} | {pct(row['full']['cagr'])} | {pct(row['full']['max_drawdown'])} | "
            f"{pct(row['oos']['total_return'])} | {pct(row['oos']['cagr'])} | {pct(row['oos']['max_drawdown'])} | "
            f"{row['full']['sharpe']:.2f} | {current_positions(row)} |"
        )
    lines.extend(
        [
            "",
            "## 训练期选出的Top10及样本外表现",
            "",
            "| 排名 | 参数 | 训练收益 | 训练回撤 | OOS收益 | OOS回撤 | 全期收益 | 全期回撤 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, row in enumerate(train_selected, 1):
        lines.append(
            f"| {idx} | {row['label']} | {pct(row['train']['total_return'])} | {pct(row['train']['max_drawdown'])} | "
            f"{pct(row['oos']['total_return'])} | {pct(row['oos']['max_drawdown'])} | "
            f"{pct(row['full']['total_return'])} | {pct(row['full']['max_drawdown'])} |"
        )
    lines.extend(["", "## 候选压力测试", "", "| 场景 | 总收益 | 年化 | 最大回撤 | Sharpe |", "|---|---:|---:|---:|---:|"])
    for row in stresses:
        lines.append(
            f"| 成本{row['fee_mult']:.0f}倍，起点{row['full']['start']} | {pct(row['full']['total_return'])} | "
            f"{pct(row['full']['cagr'])} | {pct(row['full']['max_drawdown'])} | {row['full']['sharpe']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 判断",
            "",
            f"- 全期共测试 {len(rows)} 组结构参数；研究候选为 `{recommended['label']}`。",
            f"- 与V2基线相比，全期年化变化 {pct(recommended['full']['cagr'] - baseline['full']['cagr'])}，"
            f"最大回撤变化 {pct(recommended['full']['max_drawdown'] - baseline['full']['max_drawdown'])}。",
            "- 样本外仍只有约2.5年；若参数只在该区间突出，仍应视为研究候选而非实盘定稿。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    search.FEATURE_CACHE.clear()
    base.KLINE_LIMIT = max(base.KLINE_LIMIT, 1800)
    base.BENCHMARK_INDEXES = v2.BENCHMARK_INDEXES_V2
    search.HORIZONS["offensive_m612_60_40"] = v2.OFFENSIVE_HORIZON

    histories, errors = base.load_history()
    try:
        histories[GOLD["code"]] = base.fetch_tencent_adjusted_day(base.tencent_sec(GOLD["code"]))
    except Exception as exc:
        errors[GOLD["code"]] = str(exc)
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    full_start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    configs = [
        make_config(off_scheme, fast, pool, filt, def_top)
        for off_scheme in OFFENSIVE_SCHEMES
        for fast in (0.0, -0.05, -0.07, -0.09)
        for pool in ("overseas", "overseas_gold")
        for filt in ("ma120_ret6", "ma200_ret12")
        for def_top in (1, 2)
    ]

    original_engine = v2.RegimeEngine
    original_offensive = v2.offensive_targets
    original_defensive = v2.defensive_targets
    v2.RegimeEngine = EmergencyRegimeEngine
    v2.offensive_targets = offensive_targets
    v2.defensive_targets = defensive_targets
    try:
        rows = [run_config(config, histories, full_start) for config in configs]
        train_selected = sorted(rows, key=train_rank, reverse=True)[:10]
        risk_controlled = [
            row for row in rows
            if row["full"]["max_drawdown"] >= -0.20 and row["oos"]["max_drawdown"] >= -0.20
        ]
        recommended = max(risk_controlled, key=lambda row: row["full"]["total_return"])
        baseline = next(
            row for row in rows
            if row["config"]["offensive_scheme"] == "top2_equal"
            and row["config"]["fast_crash_5d"] == 0
            and row["config"]["defensive_pool"] == "overseas"
            and row["config"]["defensive_filter"] == "ma200_ret12"
            and row["config"]["defensive_top_n"] == 2
        )
        stresses = [
            recommended,
            run_config(recommended["config"], histories, full_start, 2.0),
            run_config(recommended["config"], histories, "2022-01-04", 1.0),
            run_config(recommended["config"], histories, "2023-01-03", 2.0),
        ]

        selected_ids = {row["id"] for row in train_selected}
        write_csv(OUT_DIR / "v21_all_metrics_latest.csv", [compact(row, row["id"] in selected_ids) for row in rows])
        write_csv(OUT_DIR / "v21_stress_metrics_latest.csv", [compact(row, row["id"] in selected_ids) for row in stresses])

        for prefix_name, row in (("baseline", baseline), ("recommended", recommended)):
            prefix = OUT_DIR / prefix_name
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), row["equity"])
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), row["trades"])
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), row["signals"])

        report = summary(rows, train_selected, recommended, baseline, stresses)
        report_path = OUT_DIR / "v21_summary_latest.md"
        report_path.write_text(report, encoding="utf-8")
        (OUT_DIR / "v21_metrics_latest.json").write_text(
            json.dumps(
                {
                    "baseline": compact(baseline),
                    "recommended": compact(recommended, True),
                    "train_selected": [compact(row, True) for row in train_selected],
                    "stress": [compact(row, True) for row in stresses],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(report)
        print(f"wrote {report_path}")
    finally:
        v2.RegimeEngine = original_engine
        v2.offensive_targets = original_offensive
        v2.defensive_targets = original_defensive


if __name__ == "__main__":
    main()
