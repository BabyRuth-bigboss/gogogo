#!/usr/bin/env python3
"""Train/OOS research for a monthly offensive/defensive ETF rotation sleeve."""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_START = os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
DATA_END = os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
START = os.environ.get("DUAL_SLEEVE_START", "2015-07-01")
TRAIN_END = os.environ.get("DUAL_SLEEVE_TRAIN_END", "2021-12-31")
OOS_START = os.environ.get("DUAL_SLEEVE_OOS_START", "2022-01-04")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_research_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF双袖月频轮动优化研究_2015-07至2026-07.md")

DEFENSIVE_ETFS = [
    {"code": "511010", "theme": "防守", "label": "国债ETF"},
    {"code": "511360", "theme": "防守", "label": "短融ETF"},
    {"code": "518880", "theme": "防守", "label": "黄金ETF"},
]
DEFENSIVE_CODES = {"511010", "511360", "518880", "510880", "159399", "513100", "513500", "513050", "513130"}
DEFENSIVE_POOLS = {
    "bond_gold": {"label": "国债+黄金", "codes": {"511010", "518880"}},
    "global_defensive": {"label": "国债+黄金+纳指+标普", "codes": {"511010", "518880", "513100", "513500"}},
    "all_weather": {"label": "国债+黄金+海外+红利", "codes": {"511010", "518880", "513100", "513500", "510880"}},
}
OFFENSIVE_SPECS = [
    ("ret6", "etf_ma120_ret6_pos", "6月绝对动量"),
    ("rs36", "etf_ma120_ret6_pos", "RS36"),
    ("rs612", "etf_ma200_ret12_pos", "RS612"),
    ("rs36_lowvol", "etf_ma120_ret6_pos", "低波RS"),
]
SCHEDULES = [("month_start", "月初第1交易日"), ("month_start_3", "月初第3交易日")]
RISK_VARIANTS = [
    ("reduce10_50", "组合回撤10%降至50%", {"portfolio_dd_reduce": 0.10, "portfolio_dd_reduce_to": 0.50}),
    ("reduce12_50", "组合回撤12%降至50%", {"portfolio_dd_reduce": 0.12, "portfolio_dd_reduce_to": 0.50}),
    ("clear10", "组合回撤10%清仓", {"portfolio_dd_clear": 0.10}),
    ("clear12", "组合回撤12%清仓", {"portfolio_dd_clear": 0.12}),
]
ROBUSTNESS_WINDOWS = [
    ("2016-2018", "2016-01-04", "2018-12-28"),
    ("2018-2020", "2018-01-02", "2020-12-31"),
    ("2020-2022", "2020-01-02", "2022-12-30"),
    ("2022-2024", "2022-01-04", "2024-12-31"),
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("dual_sleeve_dynamic_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")
lab = base.lab
v3 = base.v3


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


def pct(value: float | str | None) -> str:
    return "-" if value in (None, "") else f"{float(value) * 100:+.2f}%"


def period_metrics(equity: list[dict[str, Any]], start: str, end: str | None = None) -> dict[str, Any]:
    part = [row for row in equity if start <= str(row["date"]) <= (end or "9999-12-31")]
    if not part:
        return {}
    before = [row for row in equity if str(row["date"]) < str(part[0]["date"])]
    cash = float(before[-1]["equity"]) if before else v3.INITIAL_CASH
    date = str(before[-1]["date"]) if before else (dt.date.fromisoformat(str(part[0]["date"])) - dt.timedelta(days=1)).isoformat()
    return v3.perf_metrics([{"date": date, "equity": cash, "cash": cash, "exposure": 0.0, "positions": ""}, *part])


def market_is_on(signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]], need_of_three: int) -> tuple[bool, str]:
    states = [lab.index_state(code, signal_date, histories, indexes) for code in ("IDX_HS300", "IDX_CHINEXT", "IDX_SCI50")]
    valid = [state for state in states if state]
    if not valid:
        return False, "market_history_missing"
    good = sum(1 for state in valid if state["close"] > state["ma120"] and state["ret126"] > 0)
    required = max(1, math.ceil(len(valid) * need_of_three / 3))
    return good >= required, f"market_good={good}/{len(valid)};required={required}"


def defensive_eligible(row: dict[str, Any], rule: str) -> bool:
    if row["amount_yi"] < 0.10:
        return False
    if rule == "ma60_ret3":
        return row["close"] > row["ma60"] and row["ret63"] > 0
    if rule == "ma120_ret6":
        return row["close"] > row["ma120"] and row["ret126"] > 0
    raise ValueError(f"unknown defensive filter: {rule}")


def build_dual_targets(strategy: dict[str, Any], signal_date: str, histories: dict[str, list[dict[str, Any]],], indexes: dict[str, dict[str, int]]):
    rows = lab.etf_features(signal_date, histories, indexes)
    market_on, market_note = market_is_on(signal_date, histories, indexes, int(strategy["market_need"]))
    if market_on:
        candidates = []
        for row in rows:
            if row["code"] in DEFENSIVE_CODES:
                continue
            row["score"] = lab.score_row(row, str(strategy["model"]))
            if lab.eligible(row, str(strategy["filter"])):
                candidates.append(row)
        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        selected = ranked[: int(strategy["top_n"])]
        sleeve = "offensive"
    else:
        pool = DEFENSIVE_POOLS[str(strategy["defensive_pool"])]["codes"]
        candidates = []
        for row in rows:
            if row["code"] not in pool:
                continue
            row["score"] = lab.score_row(row, "rs36")
            if defensive_eligible(row, str(strategy["defensive_filter"])):
                candidates.append(row)
        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        selected = ranked[: int(strategy["defensive_top_n"])]
        sleeve = "defensive"
    targets = lab.normalized_weights(selected, "equal", strategy)
    return targets, {"market_note": f"{sleeve};{market_note}", "ranked": ranked, "selected": selected, "eligible_count": len(candidates)}


def make_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for model, filt, model_label in OFFENSIVE_SPECS:
        for top_n in (1, 2, 3):
            for schedule, schedule_label in SCHEDULES:
                for need in (1, 2, 3):
                    for pool_id, pool in DEFENSIVE_POOLS.items():
                        for defensive_filter, defensive_label in (("ma60_ret3", "MA60+3月正"), ("ma120_ret6", "MA120+6月正")):
                            for defensive_top_n in (1, 2):
                                ident = "_".join([model, f"top{top_n}", schedule, f"m{need}", pool_id, defensive_filter, f"dtop{defensive_top_n}"])
                                configs.append({
                                    "id": ident,
                                    "label": f"进攻:{model_label} Top{top_n}｜{schedule_label}｜市场{need}/3强｜弱市:{pool['label']} Top{defensive_top_n} {defensive_label}",
                                    "model": model,
                                    "filter": filt,
                                    "top_n": top_n,
                                    "schedule": schedule,
                                    "weighting": "equal",
                                    "market_need": need,
                                    "defensive_pool": pool_id,
                                    "defensive_filter": defensive_filter,
                                    "defensive_top_n": defensive_top_n,
                                    "fee_rate": base.FEE_RATE,
                                    "start_date": START,
                                    "risk_label": "无组合回撤闸门",
                                })
    return configs


def run(config: dict[str, Any], histories: dict[str, list[dict[str, Any]]], end: str | None = None) -> dict[str, Any]:
    active = histories if not end else {code: [row for row in rows if str(row["date"]) <= end] for code, rows in histories.items()}
    equity, trades, signals = lab.simulate_strategy(config, active)
    return {
        "config": config,
        "equity": equity,
        "trades": trades,
        "signals": signals,
        "full": v3.perf_metrics(equity),
        "train": period_metrics(equity, START, TRAIN_END),
        "oos": period_metrics(equity, OOS_START, end),
    }


def rank_train(run_data: dict[str, Any]) -> tuple[float, float, float]:
    m = run_data["train"]
    return (float(m.get("cagr", -1.0)) / max(abs(float(m.get("max_drawdown", 0))), 0.03), float(m.get("cagr", -1.0)), float(m.get("sharpe", -99.0)))


def rank_final(run_data: dict[str, Any]) -> tuple[int, float, float, float, float]:
    full, train, oos = run_data["full"], run_data["train"], run_data["oos"]
    target = int(full.get("cagr", 0.0) >= 0.20 and full.get("max_drawdown", -1.0) >= -0.20)
    return (target, min(float(train.get("cagr", -1)), float(oos.get("cagr", -1))), float(oos.get("sharpe", -99)), float(full.get("cagr", -1)), float(full.get("max_drawdown", -1)))


def compact(run_data: dict[str, Any], phase: str, selected: bool) -> dict[str, Any]:
    c, full, train, oos = run_data["config"], run_data["full"], run_data["train"], run_data["oos"]
    return {
        "id": c["id"], "label": c["label"], "phase": phase, "selected_by_train": selected,
        "model": c["model"], "top_n": c["top_n"], "schedule": c["schedule"], "market_need": c["market_need"],
        "defensive_pool": c["defensive_pool"], "defensive_filter": c["defensive_filter"], "defensive_top_n": c["defensive_top_n"],
        "risk_rule": c["risk_label"], "fee_rate_one_way": c["fee_rate"], "trades": len(run_data["trades"]),
        "train_cagr": train.get("cagr", ""), "train_max_drawdown": train.get("max_drawdown", ""), "train_sharpe": train.get("sharpe", ""),
        "oos_cagr": oos.get("cagr", ""), "oos_max_drawdown": oos.get("max_drawdown", ""), "oos_sharpe": oos.get("sharpe", ""),
        "full_total_return": full.get("total_return", ""), "full_cagr": full.get("cagr", ""), "full_max_drawdown": full.get("max_drawdown", ""),
        "full_sharpe": full.get("sharpe", ""), "avg_exposure": full.get("avg_exposure", ""),
        "objective_met": bool(full.get("cagr", 0) >= 0.20 and full.get("max_drawdown", -1) >= -0.20),
    }


def summary(rows: list[dict[str, Any]], robustness: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: (bool(row["objective_met"]), float(row["oos_cagr"] or -1), float(row["full_cagr"] or -1)), reverse=True)
    hit = [row for row in ordered if row["objective_met"]]
    lines = [
        "# ETF双袖月频轮动优化研究（T0）",
        "",
        f"- 输入：腾讯日线缓存 {DATA_START} 至 {DATA_END}；强市用行业/主题动量，弱市用防守资产趋势择强。",
        f"- 训练期：{START} 至 {TRAIN_END}；样本外：{OOS_START} 至 {DATA_END}。成本为单边 {base.FEE_RATE * 100:.3f}%。",
        "- 弱市不是简单空仓：国债、黄金、纳指、标普、红利按各池规则月频择强；没有满足趋势条件的防守资产才留现金。",
        "- 目标：全期年化至少20%、最大回撤不超过20%；训练期筛选后才叠加组合回撤闸门。",
        "",
        "## 目标状态",
        "",
        f"- 测试组合数：{len(rows)}；全期同时达标：{len(hit)}。",
        "",
        "## 策略表现（前30；完整表见 CSV）",
        "",
        "| 排名 | 策略 | 风险闸门 | 训练年化/回撤 | 样本外年化/回撤 | 全期年化/回撤 | Sharpe | 交易笔数 |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(ordered[:30], 1):
        lines.append(f"| {i} | {row['label']} | {row['risk_rule']} | {pct(row['train_cagr'])}/{pct(row['train_max_drawdown'])} | {pct(row['oos_cagr'])}/{pct(row['oos_max_drawdown'])} | {pct(row['full_cagr'])}/{pct(row['full_max_drawdown'])} | {float(row['full_sharpe'] or 0):.2f} | {row['trades']} |")
    lines.extend(["", "## 成本加倍与不同起点", "", "| 候选 | 场景 | 年化 | 最大回撤 | Sharpe |", "|---|---|---:|---:|---:|"])
    for row in robustness:
        lines.append(f"| {row['label']} | {row['scenario']} | {pct(row['cagr'])} | {pct(row['max_drawdown'])} | {float(row['sharpe']):.2f} |")
    lines.extend([
        "",
        "## 审计说明",
        "",
        "- 详细策略、行情缓存 SHA-256 和成交文件均保存在本回测目录；相同行情快照可重复运行。",
        "- 价格使用二级市场日线与ETF拆分连续化调整，不等同于分红再投资总回报；ETF池存在当前可交易标的的幸存者偏差。",
        f"- 完整指标：`{OUT_DIR / 'dual_sleeve_all_metrics_latest.csv'}`",
        f"- 运行配置：`{OUT_DIR / 'experiment_config_latest.json'}`",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for etf in DEFENSIVE_ETFS:
        if etf["code"] not in {item["code"] for item in base.live.ETF_UNIVERSE}:
            base.live.ETF_UNIVERSE.append(etf)
        if etf["code"] not in {item["code"] for item in v3.ETF_UNIVERSE}:
            v3.ETF_UNIVERSE.append(etf)
    base.KLINE_LIMIT = 1800
    histories, errors = base.load_history()
    if errors:
        raise RuntimeError(f"data load errors: {errors}")
    original_targets = lab.build_targets
    lab.build_targets = build_dual_targets
    try:
        base_runs = [run(config, histories) for config in make_configs()]
        eligible = [item for item in base_runs if len(item["trades"]) >= 30]
        high_return = sorted(eligible, key=lambda item: float(item["train"].get("cagr", -1)), reverse=True)[:8]
        high_calmar = sorted(eligible, key=rank_train, reverse=True)[:8]
        selected = {item["config"]["id"]: item for item in [*high_return, *high_calmar]}
        all_runs = list(base_runs)
        for original in selected.values():
            for risk_id, risk_label, rules in RISK_VARIANTS:
                config = deepcopy(original["config"])
                config["id"] = f"{config['id']}_{risk_id}"
                config["label"] = f"{config['label']}｜{risk_label}"
                config["risk_label"] = risk_label
                config.update(rules)
                all_runs.append(run(config, histories))
        all_runs.sort(key=rank_final, reverse=True)
        selected_ids = set(selected)
        rows = [compact(item, "base" if item["config"]["risk_label"] == "无组合回撤闸门" else "risk", item["config"]["id"].split("_reduce")[0].split("_clear")[0] in selected_ids) for item in all_runs]
        write_csv(OUT_DIR / "dual_sleeve_all_metrics_latest.csv", rows)
        finalists = all_runs[:5]
        for rank, item in enumerate(finalists, 1):
            prefix = OUT_DIR / f"top{rank}_{item['config']['id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), item["equity"])
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), item["trades"])
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), item["signals"])
        robustness: list[dict[str, Any]] = []
        for item in finalists:
            doubled = deepcopy(item["config"])
            doubled["fee_rate"] = base.FEE_RATE * 2
            stressed = run(doubled, histories)
            robustness.append({"label": item["config"]["label"], "scenario": "成本加倍", **stressed["full"]})
            for name, window_start, window_end in ROBUSTNESS_WINDOWS:
                windowed = deepcopy(item["config"])
                windowed["start_date"] = window_start
                measured = run(windowed, histories, window_end)
                robustness.append({"label": item["config"]["label"], "scenario": name, **measured["full"]})
        write_csv(OUT_DIR / "dual_sleeve_robustness_latest.csv", robustness)
        config_output = {
            "data_window": {"start": DATA_START, "end": DATA_END}, "backtest_start": START,
            "train_end": TRAIN_END, "oos_start": OOS_START, "fee_rate_one_way": base.FEE_RATE,
            "base_runs": len(base_runs), "train_selected": len(selected), "total_runs": len(all_runs),
            "objective": {"cagr_min": 0.20, "max_drawdown_min": -0.20}, "defensive_etfs": DEFENSIVE_ETFS,
        }
        (OUT_DIR / "experiment_config_latest.json").write_text(json.dumps(config_output, ensure_ascii=False, indent=2), encoding="utf-8")
        report = summary(rows, robustness)
        (OUT_DIR / "dual_sleeve_research_summary_latest.md").write_text(report, encoding="utf-8")
        NOTE_PATH.write_text(report, encoding="utf-8")
        print(report)
        print(f"wrote {OUT_DIR / 'dual_sleeve_research_summary_latest.md'}")
    finally:
        lab.build_targets = original_targets


if __name__ == "__main__":
    main()
