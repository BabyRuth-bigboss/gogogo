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
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_lab/risk_gate_cost_10bp_slippage"
COMMISSION_RATE = 0.00005
SLIPPAGE_RATE = 0.00100
FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE


def load_lab_module():
    spec = importlib.util.spec_from_file_location("etf_strategy_lab_risk_gate", LAB_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LAB_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab = load_lab_module()


BASE_STRATEGIES: list[dict[str, Any]] = [
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
]


SCENARIOS: list[dict[str, Any]] = [
    {"id": "base", "label": "无闸门"},
    {"id": "clear12", "label": "12%清仓", "portfolio_dd_clear": 0.12},
    {"id": "reduce10", "label": "10%降半仓", "portfolio_dd_reduce": 0.10, "portfolio_dd_reduce_to": 0.50},
    {"id": "reduce12", "label": "12%降半仓", "portfolio_dd_reduce": 0.12, "portfolio_dd_reduce_to": 0.50},
    {"id": "reduce15", "label": "15%降半仓", "portfolio_dd_reduce": 0.15, "portfolio_dd_reduce_to": 0.50},
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


def config_with_scenario(strategy: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(strategy)
    out["variant_id"] = f"{strategy['id']}_{scenario['id']}"
    out["variant_label"] = f"{strategy['label']}｜{scenario['label']}"
    out["fee_rate"] = FEE_RATE
    if scenario.get("portfolio_dd_clear"):
        out["portfolio_dd_clear"] = scenario["portfolio_dd_clear"]
    if scenario.get("portfolio_dd_reduce"):
        out["portfolio_dd_reduce"] = scenario["portfolio_dd_reduce"]
        out["portfolio_dd_reduce_to"] = scenario.get("portfolio_dd_reduce_to", 0.50)
    return out


def run_one(strategy: dict[str, Any], scenario: dict[str, Any], histories: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    config = config_with_scenario(strategy, scenario)
    equity, trades, signals = lab.simulate_strategy(config, histories)
    metrics = lab.v3.perf_metrics(equity)
    clear_dates = sorted({trade["date"] for trade in trades if trade.get("reason") == "risk_gate_clear"})
    reduce_dates = sorted({trade["date"] for trade in trades if trade.get("reason") == "risk_gate_reduce"})
    row = {
        "strategy_id": strategy["id"],
        "strategy": strategy["label"],
        "variant_id": config["variant_id"],
        "variant": config["variant_label"],
        "scenario_id": scenario["id"],
        "scenario": scenario["label"],
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
        "risk_clear_events": len(clear_dates),
        "risk_reduce_events": len(reduce_dates),
        "risk_clear_dates": ";".join(clear_dates),
        "risk_reduce_dates": ";".join(reduce_dates),
        "start": metrics["start"],
        "end": metrics["end"],
        "yearly": metrics["yearly"],
    }
    return row, equity, trades, signals


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ETF组合回撤风险闸门回测：佣金万0.5 + 滑点0.10%")
    lines.append("")
    lines.append("- 规则：信号日收盘后，如果组合权益相对本轮峰值回撤超过阈值，则下一交易日开盘执行风险动作。")
    lines.append("- 本版主测：12%不清仓，改为把组合目标仓位降到50%。")
    lines.append("- 对照：保留12%清仓；附带10%、15%降半仓用于敏感性观察。")
    lines.append(f"- 成交口径：信号日收盘后计算，下一交易日开盘成交；单边成本=佣金{COMMISSION_RATE*100:.3f}% + 滑点{SLIPPAGE_RATE*100:.2f}% = {FEE_RATE*100:.3f}%。")
    lines.append("")

    lines.append("## 策略说明")
    lines.append("")
    lines.append("- 低波RS月频Top3：每月第一个交易日调仓；评分=3个月收益45% + 6个月收益45% - 60日波动10%；只买收盘价高于MA120且6个月收益为正的ETF；取前3名等权。它是进攻型强趋势策略。")
    lines.append("- RS612月频Top2：每月第一个交易日调仓；评分=6个月收益50% + 12个月收益50%；只买收盘价高于MA200且12个月收益为正的ETF；取前2名等权。它更慢，更依赖中长期趋势延续。")
    lines.append("- 核心卫星50/50：每月第一个交易日调仓；核心资产从红利、纳指、标普500、现金流里选1只占50%，其余50%给RS强的行业/主题ETF。它是防守对照组。")
    lines.append("- RS36月频Top3+市场过滤：每月第一个交易日调仓；评分=3个月收益50% + 6个月收益50%；沪深300、创业板、科创50里至少2个满足高于MA120且6个月收益为正，才允许开仓；取前3名等权。它是带市场环境过滤的趋势策略。")
    lines.append("")
    lines.append("## 风险闸门说明")
    lines.append("")
    lines.append("- 无闸门：策略正常月频调仓。")
    lines.append("- 12%清仓：组合权益相对本轮峰值回撤超过12%，下一交易日开盘全部卖出，之后等下次调仓信号。")
    lines.append("- 10%/12%/15%降半仓：组合权益相对本轮峰值回撤超过阈值，下一交易日开盘把当时目标仓位降到50%，之后等下次正常调仓恢复。")
    lines.append("")

    lines.append("## 12%清仓 vs 12%降半仓")
    lines.append("")
    lines.append("| 策略 | 基准收益 | 基准回撤 | 12%清仓收益/回撤 | 12%降半仓收益/回撤 | 降半仓收益变化 | 降半仓回撤变化 | 降半仓次数 | 平均仓位 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for strategy_id in [item["id"] for item in BASE_STRATEGIES]:
        base = next(row for row in rows if row["strategy_id"] == strategy_id and row["scenario_id"] == "base")
        clear12 = next(row for row in rows if row["strategy_id"] == strategy_id and row["scenario_id"] == "clear12")
        reduce12 = next(row for row in rows if row["strategy_id"] == strategy_id and row["scenario_id"] == "reduce12")
        lines.append(
            f"| {base['strategy']} | {fmt_pct(base['total_return'])} | {fmt_pct(base['max_drawdown'])} | "
            f"{fmt_pct(clear12['total_return'])} / {fmt_pct(clear12['max_drawdown'])} | "
            f"{fmt_pct(reduce12['total_return'])} / {fmt_pct(reduce12['max_drawdown'])} | "
            f"{fmt_pct(reduce12['total_return'] - base['total_return'])} | "
            f"{fmt_pct(reduce12['max_drawdown'] - base['max_drawdown'])} | "
            f"{reduce12['risk_reduce_events']} | {fmt_pct(reduce12['avg_exposure'])} |"
        )

    lines.append("")
    lines.append("## 降半仓阈值敏感性")
    lines.append("")
    lines.append("| 策略 | 场景 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 清仓次数 | 降半仓次数 | 最大回撤区间 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for strategy_id in [item["id"] for item in BASE_STRATEGIES]:
        subset = [row for row in rows if row["strategy_id"] == strategy_id]
        for row in subset:
            lines.append(
                f"| {row['strategy']} | {row['scenario']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
                f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
                f"{row['risk_clear_events']} | {row['risk_reduce_events']} | {row['max_dd_start']}~{row['max_dd_end']} |"
            )

    lines.append("")
    lines.append("## 初步判断")
    lowvol_base = next(row for row in rows if row["strategy_id"] == "lowvol_rs_month_top3" and row["scenario_id"] == "base")
    lowvol_reduce12 = next(row for row in rows if row["strategy_id"] == "lowvol_rs_month_top3" and row["scenario_id"] == "reduce12")
    rs612_base = next(row for row in rows if row["strategy_id"] == "rs612_month_top2" and row["scenario_id"] == "base")
    rs612_reduce12 = next(row for row in rows if row["strategy_id"] == "rs612_month_top2" and row["scenario_id"] == "reduce12")
    lines.append(
        f"- 低波RS：12%降半仓把回撤从 {fmt_pct(lowvol_base['max_drawdown'])} 调到 {fmt_pct(lowvol_reduce12['max_drawdown'])}，收益从 {fmt_pct(lowvol_base['total_return'])} 调到 {fmt_pct(lowvol_reduce12['total_return'])}。"
    )
    lines.append(
        f"- RS612：12%降半仓把回撤从 {fmt_pct(rs612_base['max_drawdown'])} 调到 {fmt_pct(rs612_reduce12['max_drawdown'])}，收益从 {fmt_pct(rs612_base['total_return'])} 调到 {fmt_pct(rs612_reduce12['total_return'])}。"
    )
    lines.append("- 如果降半仓仍不能改善回撤，说明单纯组合权益回撤触发太滞后；下一版应改成指数状态先行触发。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = lab.v3.load_history()
    rows: list[dict[str, Any]] = []

    for strategy in BASE_STRATEGIES:
        for scenario in SCENARIOS:
            row, equity, trades, signals = run_one(strategy, scenario, histories)
            rows.append(row)
            prefix = OUT_DIR / f"{row['variant_id']}"
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)

    summary = build_summary(rows)
    summary_path = OUT_DIR / "risk_gate_summary_latest.md"
    json_path = OUT_DIR / "risk_gate_metrics_latest.json"
    csv_path = OUT_DIR / "risk_gate_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
