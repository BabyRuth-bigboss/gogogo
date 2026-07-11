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
GATE_SCRIPT = ROOT / "scripts/backtest_etf_dynamic_pool_5y_rs36_ma_gate.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_multifactor_overlay"


def load_gate_module():
    spec = importlib.util.spec_from_file_location("etf_multifactor_gate", GATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GATE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate_mod = load_gate_module()
base = gate_mod.base
lab = gate_mod.lab
v3 = gate_mod.v3

MA120_GATE = next(item for item in gate_mod.GATES if item["id"] == "majority_below_ma120")

STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "rs612_top2_month_start_3",
        "label": "RS612 Top2｜月初第3日",
        "model": "rs612",
        "top_n": 2,
        "filter": "etf_ma200_ret12_pos",
        "weighting": "equal",
        "schedule": "month_start_3",
        "fee_rate": base.FEE_RATE,
    },
    {
        "id": "rs36_top3_month_start",
        "label": "RS36 Top3｜月初",
        "model": "rs36",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
        "schedule": "month_start",
        "fee_rate": base.FEE_RATE,
    },
]

VARIANTS: list[dict[str, Any]] = [
    {
        "id": "momentum_baseline",
        "label": "纯动量基线",
        "weights": {"momentum": 1.0},
    },
    {
        "id": "lowvol20",
        "label": "动量80%+低波20%",
        "weights": {"momentum": 0.80, "lowvol": 0.20},
    },
    {
        "id": "trend_quality20",
        "label": "动量80%+趋势质量20%",
        "weights": {"momentum": 0.80, "trend_quality": 0.20},
    },
    {
        "id": "high_proximity20",
        "label": "动量80%+近高点20%",
        "weights": {"momentum": 0.80, "high_proximity": 0.20},
    },
    {
        "id": "liquidity20",
        "label": "动量80%+流动性20%",
        "weights": {"momentum": 0.80, "liquidity": 0.20},
    },
    {
        "id": "balanced_multi",
        "label": "动量60%+低波15%+趋势质量15%+近高点10%",
        "weights": {"momentum": 0.60, "lowvol": 0.15, "trend_quality": 0.15, "high_proximity": 0.10},
    },
]


def percentile_ranks(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (float(row[key]), str(row["code"])))
    if len(ordered) <= 1:
        return {str(row["code"]): 0.5 for row in ordered}
    return {str(row["code"]): idx / (len(ordered) - 1) for idx, row in enumerate(ordered)}


def extra_features(
    row: dict[str, Any],
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> None:
    code = str(row["code"])
    idx = indexes[code][signal_date]
    history = histories[code][: idx + 1]
    closes = [base.fnum(item["close"]) for item in history]
    window = closes[-127:]
    log_moves = [abs(math.log(window[i] / window[i - 1])) for i in range(1, len(window)) if window[i - 1] > 0]
    path = sum(log_moves)
    net = abs(math.log(window[-1] / window[0])) if len(window) >= 2 and window[0] > 0 else 0.0
    amounts = [max(base.fnum(item.get("amount_yi")), 0.0) for item in history[-20:]]

    momentum = (
        row["ret126"] * 0.5 + row["ret252"] * 0.5
        if row.get("factor_model") == "rs612"
        else row["ret63"] * 0.5 + row["ret126"] * 0.5
    )
    row.update(
        {
            "momentum": momentum,
            "lowvol": -float(row["vol60"]),
            "trend_quality": net / path if path > 0 else 0.0,
            "high_proximity": float(row["close"]) / float(row["high120"]),
            "liquidity": math.log1p(statistics.fmean(amounts)) if amounts else 0.0,
        }
    )


def build_multifactor_targets(
    strategy: dict[str, Any],
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], dict[str, Any]]:
    rows = lab.etf_features(signal_date, histories, indexes)
    for row in rows:
        row["factor_model"] = strategy["model"]
        row["eligible"] = lab.eligible(row, str(strategy["filter"]))
        extra_features(row, signal_date, histories, indexes)

    candidates = [row for row in rows if row["eligible"]]
    factor_weights = strategy["factor_weights"]
    ranks = {factor: percentile_ranks(candidates, factor) for factor in factor_weights}
    for row in rows:
        if row["eligible"]:
            row["score"] = sum(
                float(weight) * ranks[factor][str(row["code"])]
                for factor, weight in factor_weights.items()
            )
        else:
            row["score"] = -1.0
    ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
    selected = [row for row in ranked if row["eligible"]][: int(strategy["top_n"])]
    targets = lab.normalized_weights(selected, str(strategy["weighting"]), strategy)
    return targets, {
        "market_note": "market_gate_external",
        "ranked": ranked,
        "selected": selected,
        "eligible_count": len(candidates),
    }


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


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def position_labels(equity: list[dict[str, Any]]) -> str:
    if not equity:
        return "现金"
    labels = {item["code"]: item["label"] for item in base.live.ETF_UNIVERSE + base.live.MARKET_WATCH}
    codes = [code for code in str(equity[-1].get("positions", "")).split(";") if code]
    return ";".join(f"{labels.get(code, code)}({code})" for code in codes) or "现金"


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# ETF 动量策略叠加量化因子：5年动态池回测",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 共同口径：ETF上市满252个交易日才入池；前一交易日收盘计算信号，下一交易日开盘成交。",
        f"- 成本：佣金万0.5 + 滑点0.10%，单边合计 {base.FEE_RATE * 100:.3f}%。",
        "- 市场闸门：沪深300、创业板指、科创50中至少2个低于MA120时空仓。",
        "- 因子合成：仅在各策略自身趋势过滤后的候选池内做横截面百分位排名。",
        "",
        "## 回测结果",
        "",
        "| 基础策略 | 因子版本 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 当前持仓 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        m = row["metrics"]
        lines.append(
            f"| {row['strategy_label']} | {row['variant_label']} | {fmt_pct(m['total_return'])} | "
            f"{fmt_pct(m['cagr'])} | {fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | "
            f"{fmt_pct(m['avg_exposure'])} | {row['trade_count']} | {row['latest_positions']} |"
        )
    lines.extend(["", "## 因子定义", ""])
    lines.extend(
        [
            "- 动量：RS612 为6个月与12个月收益各50%；RS36为3个月与6个月收益各50%。",
            "- 低波：60日年化波动率越低，得分越高。",
            "- 趋势质量：126日净价格位移 / 每日绝对路径长度，越接近单向趋势越高。",
            "- 近高点：收盘价 / 120日最高收盘价，越接近阶段高点越高。",
            "- 流动性：近20日平均成交额的对数，越高得分越高。",
            "",
            "## 判断",
            "",
        ]
    )
    for strategy_id in {row["strategy_id"] for row in rows}:
        group = [row for row in rows if row["strategy_id"] == strategy_id]
        baseline = next(row for row in group if row["variant_id"] == "momentum_baseline")
        best = max(group, key=lambda row: (row["metrics"]["sharpe"], row["metrics"]["max_drawdown"]))
        delta_cagr = best["metrics"]["cagr"] - baseline["metrics"]["cagr"]
        delta_dd = best["metrics"]["max_drawdown"] - baseline["metrics"]["max_drawdown"]
        lines.append(
            f"- {baseline['strategy_label']}：Sharpe最高版本为 `{best['variant_label']}`，"
            f"相对纯动量年化变化 {fmt_pct(delta_cagr)}，最大回撤改善 {fmt_pct(delta_dd)}。"
        )
    lines.extend(
        [
            "- 这是一轮固定权重筛查，不应直接把样本内最佳版本视为最终策略；下一步要做因子权重扰动、分年度和滚动样本外检验。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = base.load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_date = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    original_build_targets = lab.build_targets
    lab.build_targets = build_multifactor_targets
    results: list[dict[str, Any]] = []
    try:
        for strategy_base in STRATEGIES:
            for variant in VARIANTS:
                strategy = dict(strategy_base)
                strategy.update(
                    {
                        "id": f"{strategy_base['id']}_{variant['id']}",
                        "label": f"{strategy_base['label']}｜{variant['label']}",
                        "factor_weights": dict(variant["weights"]),
                        "start_date": start_date,
                    }
                )
                equity, trades, signals = gate_mod.simulate_with_gate(strategy, MA120_GATE, histories)
                metrics = v3.perf_metrics(equity)
                prefix = OUT_DIR / strategy["id"]
                write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
                write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
                write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
                result = {
                    "strategy_id": strategy_base["id"],
                    "strategy_label": strategy_base["label"],
                    "variant_id": variant["id"],
                    "variant_label": variant["label"],
                    "factor_weights": variant["weights"],
                    "metrics": metrics,
                    "trade_count": len(trades),
                    "latest_positions": position_labels(equity),
                }
                results.append(result)
                prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
                    json.dumps({"result": result, "config": strategy, "gate": MA120_GATE}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    finally:
        lab.build_targets = original_build_targets

    metric_rows: list[dict[str, Any]] = []
    for row in results:
        m = row["metrics"]
        metric_rows.append(
            {
                "strategy_id": row["strategy_id"],
                "strategy": row["strategy_label"],
                "variant_id": row["variant_id"],
                "variant": row["variant_label"],
                "factor_weights": json.dumps(row["factor_weights"], ensure_ascii=False),
                "total_return": m["total_return"],
                "cagr": m["cagr"],
                "max_drawdown": m["max_drawdown"],
                "max_dd_start": m["max_dd_start"],
                "max_dd_end": m["max_dd_end"],
                "volatility": m["volatility"],
                "sharpe": m["sharpe"],
                "avg_exposure": m["avg_exposure"],
                "trades": row["trade_count"],
                "latest_positions": row["latest_positions"],
                "yearly": json.dumps(m.get("yearly", {}), ensure_ascii=False),
            }
        )
    write_csv(OUT_DIR / "multifactor_overlay_5y_metrics_latest.csv", metric_rows)
    summary = build_summary(results)
    summary_path = OUT_DIR / "multifactor_overlay_5y_summary_latest.md"
    summary_path.write_text(summary, encoding="utf-8")
    (OUT_DIR / "multifactor_overlay_5y_metrics_latest.json").write_text(
        json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
