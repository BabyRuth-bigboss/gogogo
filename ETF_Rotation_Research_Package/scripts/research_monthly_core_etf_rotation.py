#!/usr/bin/env python3
"""Reproducible monthly ETF rotation research on the cached Tencent history.

The search deliberately separates structure selection (2015-2021) from the
2022-2026 evaluation period.  It is a research tool, not a live signal script.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_START = os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
DATA_END = os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
BACKTEST_START = os.environ.get("MONTHLY_RESEARCH_START", "2015-07-01")
TRAIN_END = os.environ.get("MONTHLY_RESEARCH_TRAIN_END", "2021-12-31")
OOS_START = os.environ.get("MONTHLY_RESEARCH_OOS_START", "2022-01-04")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_core_research_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF月频轮动优化研究_2015-07至2026-07.md")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("monthly_core_dynamic_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")
lab = base.lab
v3 = base.v3


MODEL_SPECS = [
    ("rs36", "etf_ma120_ret6_pos", "RS36"),
    ("ret6", "etf_ma120_ret6_pos", "6月绝对动量"),
    ("rs36_lowvol", "etf_ma120_ret6_pos", "低波RS"),
    ("rs612", "etf_ma200_ret12_pos", "RS612"),
]
SCHEDULES = [
    ("month_start", "月初第1交易日"),
    ("month_start_3", "月初第3交易日"),
    ("mid_month", "月中首个15日后交易日"),
]
MARKET_FILTERS = [
    (None, "无市场过滤"),
    ("risk_on_1of3", "市场1/3强"),
    ("risk_on_2of3", "市场2/3强"),
    ("risk_on_3of3", "市场3/3强"),
]
RISK_VARIANTS = [
    ("base", "无组合回撤闸门", {}),
    ("reduce08_50", "回撤8%降至50%", {"portfolio_dd_reduce": 0.08, "portfolio_dd_reduce_to": 0.50}),
    ("reduce10_50", "回撤10%降至50%", {"portfolio_dd_reduce": 0.10, "portfolio_dd_reduce_to": 0.50}),
    ("reduce12_50", "回撤12%降至50%", {"portfolio_dd_reduce": 0.12, "portfolio_dd_reduce_to": 0.50}),
    ("reduce10_25", "回撤10%降至25%", {"portfolio_dd_reduce": 0.10, "portfolio_dd_reduce_to": 0.25}),
    ("reduce12_25", "回撤12%降至25%", {"portfolio_dd_reduce": 0.12, "portfolio_dd_reduce_to": 0.25}),
    ("clear08", "回撤8%清仓", {"portfolio_dd_clear": 0.08}),
    ("clear10", "回撤10%清仓", {"portfolio_dd_clear": 0.10}),
    ("clear12", "回撤12%清仓", {"portfolio_dd_clear": 0.12}),
]
ROBUSTNESS_WINDOWS = [
    ("2016-2018", "2016-01-04", "2018-12-28"),
    ("2018-2020", "2018-01-02", "2020-12-31"),
    ("2020-2022", "2020-01-02", "2022-12-30"),
    ("2022-2024", "2022-01-04", "2024-12-31"),
]


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
    if value in (None, ""):
        return "-"
    return f"{float(value) * 100:+.2f}%"


def metrics_for_period(equity: list[dict[str, Any]], start: str, end: str | None = None) -> dict[str, Any]:
    selected = [row for row in equity if start <= str(row["date"]) <= (end or "9999-12-31")]
    if not selected:
        return {}
    prior = [row for row in equity if str(row["date"]) < str(selected[0]["date"])]
    start_equity = float(prior[-1]["equity"]) if prior else v3.INITIAL_CASH
    start_date = str(prior[-1]["date"]) if prior else (dt.date.fromisoformat(str(selected[0]["date"])) - dt.timedelta(days=1)).isoformat()
    seed = {"date": start_date, "equity": start_equity, "cash": start_equity, "exposure": 0.0, "positions": ""}
    return v3.perf_metrics([seed, *selected])


def make_base_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for model, filt, model_label in MODEL_SPECS:
        for top_n in (1, 2, 3):
            for schedule, schedule_label in SCHEDULES:
                for market_filter, market_label in MARKET_FILTERS:
                    identifier = "_".join(
                        [model, f"top{top_n}", schedule, market_filter or "market_off"]
                    )
                    config: dict[str, Any] = {
                        "id": identifier,
                        "label": f"{model_label} Top{top_n}｜{schedule_label}｜{market_label}",
                        "model": model,
                        "filter": filt,
                        "top_n": top_n,
                        "schedule": schedule,
                        "weighting": "equal",
                        "fee_rate": base.FEE_RATE,
                        "start_date": BACKTEST_START,
                    }
                    if market_filter:
                        config["market_filter"] = market_filter
                    configs.append(config)
    return configs


def run_config(config: dict[str, Any], histories: dict[str, list[dict[str, Any]]], end: str | None = None) -> dict[str, Any]:
    active_histories = histories
    if end:
        active_histories = {code: [row for row in rows if str(row["date"]) <= end] for code, rows in histories.items()}
    equity, trades, signals = lab.simulate_strategy(config, active_histories)
    full = v3.perf_metrics(equity)
    train = metrics_for_period(equity, BACKTEST_START, TRAIN_END)
    oos = metrics_for_period(equity, OOS_START, end)
    return {"config": config, "equity": equity, "trades": trades, "signals": signals, "full": full, "train": train, "oos": oos}


def rank_train(run: dict[str, Any]) -> tuple[float, float, float]:
    metric = run["train"]
    drawdown = abs(float(metric.get("max_drawdown", 0.0)))
    calmar = float(metric.get("cagr", -1.0)) / max(drawdown, 0.03)
    return (calmar, float(metric.get("cagr", -1.0)), float(metric.get("sharpe", -99.0)))


def rank_final(run: dict[str, Any]) -> tuple[int, float, float, float, float]:
    full = run["full"]
    train = run["train"]
    oos = run["oos"]
    reaches_target = int(full.get("cagr", 0.0) >= 0.20 and full.get("max_drawdown", -1.0) >= -0.20)
    stable_cagr = min(float(train.get("cagr", -1.0)), float(oos.get("cagr", -1.0)))
    return (
        reaches_target,
        stable_cagr,
        float(oos.get("sharpe", -99.0)),
        float(full.get("cagr", -1.0)),
        float(full.get("max_drawdown", -1.0)),
    )


def compact(run: dict[str, Any], phase: str, selected_by_train: bool) -> dict[str, Any]:
    config = run["config"]
    full = run["full"]
    train = run["train"]
    oos = run["oos"]
    return {
        "id": config["id"],
        "label": config["label"],
        "phase": phase,
        "selected_by_train": selected_by_train,
        "model": config["model"],
        "filter": config["filter"],
        "top_n": config["top_n"],
        "schedule": config["schedule"],
        "market_filter": config.get("market_filter", "off"),
        "risk_rule": config.get("risk_label", "无组合回撤闸门"),
        "fee_rate_one_way": config["fee_rate"],
        "trades": len(run["trades"]),
        "trade_days": len({trade["date"] for trade in run["trades"]}),
        "train_total_return": train.get("total_return", ""),
        "train_cagr": train.get("cagr", ""),
        "train_max_drawdown": train.get("max_drawdown", ""),
        "train_sharpe": train.get("sharpe", ""),
        "oos_total_return": oos.get("total_return", ""),
        "oos_cagr": oos.get("cagr", ""),
        "oos_max_drawdown": oos.get("max_drawdown", ""),
        "oos_sharpe": oos.get("sharpe", ""),
        "full_total_return": full.get("total_return", ""),
        "full_cagr": full.get("cagr", ""),
        "full_max_drawdown": full.get("max_drawdown", ""),
        "full_sharpe": full.get("sharpe", ""),
        "avg_exposure": full.get("avg_exposure", ""),
        "max_dd_start": full.get("max_dd_start", ""),
        "max_dd_end": full.get("max_dd_end", ""),
        "objective_met": bool(full.get("cagr", 0.0) >= 0.20 and full.get("max_drawdown", -1.0) >= -0.20),
    }


def cache_audit(histories: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sec_by_code = {item["code"]: base.tencent_sec(item["code"]) for item in base.live.ETF_UNIVERSE}
    sec_by_code.update({key: item["sec"] for key, item in base.BENCHMARK_INDEXES.items()})
    labels = {item["code"]: item["label"] for item in base.live.ETF_UNIVERSE}
    labels.update({key: item["label"] for key, item in base.BENCHMARK_INDEXES.items()})
    rows: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    for code, history in sorted(histories.items()):
        sec = sec_by_code.get(code, code)
        expected = base.CACHE_DIR / f"{sec}_{DATA_START}_{DATA_END}_{base.KLINE_LIMIT}.json"
        cache_path = expected if expected.exists() else next(iter(sorted(base.CACHE_DIR.glob(f"{sec}_*.json"))), None)
        raw_rows: list[dict[str, Any]] = []
        digest = ""
        size = 0
        if cache_path and cache_path.exists():
            raw = cache_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            size = len(raw)
            raw_rows = json.loads(raw.decode("utf-8"))
        dates = [str(row["date"]) for row in history]
        positive = sum(1 for row in history if float(row.get("open", 0)) > 0 and float(row.get("close", 0)) > 0)
        split_events = sorted({str(row.get("split_events", "")) for row in history if row.get("split_events")})
        audit = {
            "code": code,
            "label": labels.get(code, code),
            "tencent_sec": sec,
            "source": "Tencent fqkline raw day",
            "endpoint": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "cache_path": str(cache_path) if cache_path else "",
            "cache_sha256": digest,
            "cache_bytes": size,
            "raw_rows": len(raw_rows),
            "adjusted_rows": len(history),
            "start": dates[0] if dates else "",
            "end": dates[-1] if dates else "",
            "unique_dates": len(set(dates)),
            "strict_date_order": dates == sorted(dates) and len(dates) == len(set(dates)),
            "positive_open_close_rows": positive,
            "price_invalid_rows": len(history) - positive,
            "eligible_from_252d": dates[252] if len(dates) > 252 else "",
            "split_adjusted": bool(split_events),
            "split_events": ";".join(split_events),
        }
        rows.append(audit)
        files[code] = {key: audit[key] for key in ("tencent_sec", "cache_path", "cache_sha256", "raw_rows", "start", "end")}
    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_window_requested": {"start": DATA_START, "end": DATA_END},
        "source": "Tencent appstock fqkline raw day; local cache is immutable input for this run.",
        "adjustment": "Large ETF split/reverse-split price continuity adjustment; not total-return dividend reinvestment.",
        "eligibility": "ETF must have at least 252 daily observations before it can be scored.",
        "files": files,
    }
    return rows, manifest


def build_summary(rows: list[dict[str, Any]], audit: list[dict[str, Any]], finalists: list[dict[str, Any]], robustness: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: (bool(row["objective_met"]), float(row["oos_cagr"] or -1), float(row["full_cagr"] or -1)), reverse=True)
    target_rows = [row for row in ordered if row["objective_met"]]
    lines = [
        "# 月频核心ETF轮动优化研究（T0）",
        "",
        f"- 行情输入：腾讯 `fqkline` 日线缓存，{DATA_START} 至 {DATA_END}；本次运行未依赖实时接口。",
        f"- 回测：{BACKTEST_START} 至 {DATA_END}；训练期 {BACKTEST_START} 至 {TRAIN_END}，样本外 {OOS_START} 至 {DATA_END}。",
        f"- 成本：单边佣金万0.5 + 滑点0.10%，合计 {base.FEE_RATE * 100:.3f}%；信号日收盘后计算、下一交易日开盘成交。",
        "- 先固定四类月频动量结构、Top1/2/3、三种月度时点和四档市场过滤；只按训练期挑候选，再叠加组合回撤闸门。",
        "- 目标：全期年化至少20%、最大回撤不超过20%。达标才可进入下一阶段，不把高收益局部最优直接视为实盘策略。",
        "",
        "## 目标状态",
        "",
        f"- 本轮测试 {len(rows)} 个组合，其中全期同时达标 {len(target_rows)} 个。",
        "- 若为 0，代表当前可审计数据和这组规则下尚未找到达标策略，而不是把目标放宽。",
        "",
        "## 全部策略排名（完整表见 CSV）",
        "",
        "| 排名 | 策略 | 风险闸门 | 训练年化/回撤 | 样本外年化/回撤 | 全期年化/回撤 | Sharpe | 交易笔数 |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(ordered[:30], 1):
        lines.append(
            f"| {index} | {row['label']} | {row['risk_rule']} | {pct(row['train_cagr'])}/{pct(row['train_max_drawdown'])} | "
            f"{pct(row['oos_cagr'])}/{pct(row['oos_max_drawdown'])} | {pct(row['full_cagr'])}/{pct(row['full_max_drawdown'])} | "
            f"{float(row['full_sharpe'] or 0):.2f} | {row['trades']} |"
        )

    lines.extend(["", "## 最终候选的成本和不同起点验证", "", "| 候选 | 场景 | 年化 | 最大回撤 | Sharpe |", "|---|---|---:|---:|---:|"])
    for row in robustness:
        lines.append(f"| {row['label']} | {row['scenario']} | {pct(row['cagr'])} | {pct(row['max_drawdown'])} | {float(row['sharpe']):.2f} |")

    lines.extend(["", "## 数据审计摘要", "", f"- 已加载 {len(audit)} 组ETF/指数历史；缓存文件与 SHA-256 见 `data_audit_latest.csv` 和 `data_manifest_latest.json`。"])
    invalid = [row for row in audit if not row["strict_date_order"] or row["price_invalid_rows"]]
    lines.append(f"- 日期顺序或正价格检查异常：{len(invalid)} 组。")
    lines.extend([
        "- 数据限制：使用二级市场日线和拆分连续化调整，不等同于分红再投资总回报；当前 ETF 池依然有幸存者偏差，252日门槛只避免了上市前交易。",
        "- 市场过滤只依据当时可得的指数收盘信息；回测不使用调仓日之后的价格生成信号。",
        "",
        "## 审计产物",
        "",
        f"- 全策略指标：`{OUT_DIR / 'monthly_core_all_metrics_latest.csv'}`",
        f"- 研究配置：`{OUT_DIR / 'experiment_config_latest.json'}`",
        f"- 行情审计：`{OUT_DIR / 'data_audit_latest.csv'}`",
        f"- 行情清单与哈希：`{OUT_DIR / 'data_manifest_latest.json'}`",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base.KLINE_LIMIT = 1800
    histories, errors = base.load_history()
    if errors:
        raise RuntimeError(f"data load errors: {errors}")
    audit, manifest = cache_audit(histories)
    write_csv(OUT_DIR / "data_audit_latest.csv", audit)
    (OUT_DIR / "data_manifest_latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    base_runs = [run_config(config, histories) for config in make_base_configs()]
    eligible = [run for run in base_runs if len(run["trades"]) >= 30]
    high_return = sorted(eligible, key=lambda run: float(run["train"].get("cagr", -1)), reverse=True)[:10]
    high_calmar = sorted(eligible, key=rank_train, reverse=True)[:10]
    selected: dict[str, dict[str, Any]] = {run["config"]["id"]: run for run in [*high_return, *high_calmar]}

    all_runs = list(base_runs)
    for base_run in selected.values():
        for risk_id, risk_label, risk_rules in RISK_VARIANTS[1:]:
            config = deepcopy(base_run["config"])
            config["id"] = f"{config['id']}_{risk_id}"
            config["label"] = f"{config['label']}｜{risk_label}"
            config["risk_label"] = risk_label
            config.update(risk_rules)
            all_runs.append(run_config(config, histories))

    all_runs.sort(key=rank_final, reverse=True)
    selected_ids = set(selected)
    rows = [compact(run, "base" if run["config"]["id"] in selected_ids else "risk", run["config"]["id"].split("_reduce")[0].split("_clear")[0] in selected_ids) for run in all_runs]
    write_csv(OUT_DIR / "monthly_core_all_metrics_latest.csv", rows)

    finalists = all_runs[:5]
    for rank, run in enumerate(finalists, 1):
        prefix = OUT_DIR / f"top{rank}_{run['config']['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), run["equity"])
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), run["trades"])
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), run["signals"])

    robustness: list[dict[str, Any]] = []
    for run in finalists:
        config = run["config"]
        double_cost = deepcopy(config)
        double_cost["fee_rate"] = base.FEE_RATE * 2
        stressed = run_config(double_cost, histories)
        robustness.append({"label": config["label"], "scenario": "成本加倍", **stressed["full"]})
        for window_name, start, end in ROBUSTNESS_WINDOWS:
            window_config = deepcopy(config)
            window_config["start_date"] = start
            result = run_config(window_config, histories, end)
            robustness.append({"label": config["label"], "scenario": window_name, **result["full"]})
    write_csv(OUT_DIR / "monthly_core_robustness_latest.csv", robustness)

    experiment = {
        "data_start": DATA_START,
        "data_end": DATA_END,
        "backtest_start": BACKTEST_START,
        "train_end": TRAIN_END,
        "oos_start": OOS_START,
        "fee_rate_one_way": base.FEE_RATE,
        "base_config_count": len(base_runs),
        "train_selected_count": len(selected),
        "risk_variants_per_selected": len(RISK_VARIANTS) - 1,
        "total_run_count": len(all_runs),
        "objective": {"cagr_min": 0.20, "max_drawdown_min": -0.20},
        "model_specs": MODEL_SPECS,
        "schedules": SCHEDULES,
        "market_filters": MARKET_FILTERS,
        "risk_variants": RISK_VARIANTS,
    }
    (OUT_DIR / "experiment_config_latest.json").write_text(json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = build_summary(rows, audit, finalists, robustness)
    (OUT_DIR / "monthly_core_research_summary_latest.md").write_text(summary, encoding="utf-8")
    NOTE_PATH.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"wrote {OUT_DIR / 'monthly_core_research_summary_latest.md'}")
    print(f"wrote {NOTE_PATH}")


if __name__ == "__main__":
    main()
