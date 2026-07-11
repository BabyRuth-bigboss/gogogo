#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NEIGHBOR_SCRIPT = ROOT / "scripts/search_etf_winner_neighborhood_5y.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


neighbor = load_module("etf_regime_neighbor", NEIGHBOR_SCRIPT)
search = neighbor.search
base = neighbor.base
lab = neighbor.lab
gate_mod = neighbor.gate_mod
v3 = neighbor.v3

DEFENSIVE_POOLS = {
    "overseas": {"label": "纳指+标普", "codes": {"513100", "513500"}},
    "core4": {"label": "纳指+标普+红利+现金流", "codes": {"513100", "513500", "510880", "159399"}},
}

OFFENSIVE_HORIZON = {
    "label": "6月60%+12月40%",
    "weights": {"ret126": 0.6, "ret252": 0.4},
    "filter": "ma200_ret12",
}


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


def offensive_targets(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], dict[str, Any]]:
    config = {
        "horizon_id": "offensive_m612_60_40",
        "filter": "ma200_ret12",
        "factor_weights": {"momentum": 0.75, "liquidity": 0.15, "trend_quality": 0.10},
        "top_n": 2,
        "weighting": "equal",
    }
    return search.build_targets(config, signal_date, histories, indexes)


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
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if config["defensive_filter"] == "ma120_ret6":
            eligible = row["close"] > row["ma120"] and row["ret126"] > 0
        else:
            eligible = row["close"] > row["ma200"] and row["ret252"] > 0
        if not eligible or row["amount_yi"] < 0.10:
            continue
        row["momentum"] = (
            row["ret63"] * 0.5 + row["ret126"] * 0.5
            if config["defensive_model"] == "rs36"
            else row["ret126"] * 0.5 + row["ret252"] * 0.5
        )
        candidates.append(row)
    momentum_rank = percentile_ranks(candidates, "momentum")
    liquidity_rank = percentile_ranks(candidates, "liquidity")
    for row in candidates:
        row["score"] = momentum_rank[row["code"]] * 0.9 + liquidity_rank[row["code"]] * 0.1
    selected = sorted(candidates, key=lambda row: row["score"], reverse=True)[: config["defensive_top_n"]]
    allocation = float(config["defensive_allocation"])
    targets = {row["code"]: allocation / len(selected) for row in selected} if selected else {}
    return targets, {"ranked": candidates, "selected": selected, "eligible_count": len(candidates)}


def simulate(
    config: dict[str, Any],
    histories: dict[str, list[dict[str, Any]]],
    start_date: str,
    fee_mult: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    start_idx = next(i for i, date in enumerate(dates) if date >= start_date)
    start_idx = max(start_idx, 253)
    rebal_indices = lab.build_rebalance_indices(dates, start_idx, "month_start_3")
    fee_rate = base.FEE_RATE * fee_mult

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    previous_bad = False
    current_targets: dict[str, float] = {}

    gate = {"id": "majority_below_ma120", "label": "2/3指数低于MA120", "ma_days": 120, "bad_count": 2}
    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        rebal_day = i in rebal_indices
        market_bad, market_note = search.generic_market_gate_bad(gate, signal_date, histories, indexes)
        regime_changed = market_bad != previous_bad
        should_reselect = rebal_day or (market_bad and not previous_bad)
        if not market_bad and previous_bad and config["recovery_mode"] == "immediate":
            should_reselect = True

        selected: list[dict[str, Any]] = []
        if should_reselect:
            if market_bad:
                current_targets, info = defensive_targets(config, signal_date, histories, indexes)
            else:
                current_targets, info = offensive_targets(signal_date, histories, indexes)
            selected = info.get("selected", [])

        equity_open = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        if should_reselect:
            targets = dict(current_targets)
        else:
            targets: dict[str, float] = {}
            for code, shares in positions.items():
                open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                if open_price is not None and equity_open > 0:
                    targets[code] = shares * open_price / equity_open

        signals.append(
            {
                "signal_date": signal_date,
                "trade_date": trade_date,
                "rebalance_day": rebal_day,
                "regime_changed": regime_changed,
                "market_bad": market_bad,
                "market_note": market_note,
                "sleeve": "defensive" if market_bad else "offensive",
                "selected": ";".join(row["label"] for row in selected),
                "targets": ";".join(f"{code}:{weight:.1%}" for code, weight in targets.items()) if should_reselect else "",
            }
        )

        for code in list(positions):
            open_price = v3.price_at(histories, indexes, code, trade_date, "open")
            if open_price is None:
                continue
            current_value = positions[code] * open_price
            target_value = equity_open * targets.get(code, 0.0)
            diff_value = current_value - target_value
            if diff_value <= equity_open * v3.REBALANCE_THRESHOLD and targets.get(code, 0.0) > 0:
                continue
            if diff_value <= 0:
                continue
            shares = min(positions[code], diff_value / open_price)
            cash += shares * open_price * (1 - fee_rate)
            positions[code] -= shares
            if positions[code] <= 1e-8:
                del positions[code]
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "SELL",
                    "price": f"{open_price:.4f}",
                    "shares": f"{shares:.4f}",
                    "value": f"{shares * open_price:.2f}",
                    "fee": f"{shares * open_price * fee_rate:.2f}",
                    "signal_date": signal_date,
                    "reason": "regime_switch" if regime_changed else "rebalance",
                }
            )

        for code, weight in targets.items():
            open_price = v3.price_at(histories, indexes, code, trade_date, "open")
            if open_price is None:
                continue
            current_value = positions.get(code, 0.0) * open_price
            target_value = equity_open * weight
            diff_value = target_value - current_value
            if diff_value <= equity_open * v3.REBALANCE_THRESHOLD:
                continue
            spend = min(cash, diff_value)
            if spend <= 0:
                continue
            shares = spend / (open_price * (1 + fee_rate))
            positions[code] = positions.get(code, 0.0) + shares
            cash -= spend
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "BUY",
                    "price": f"{open_price:.4f}",
                    "shares": f"{shares:.4f}",
                    "value": f"{spend:.2f}",
                    "fee": f"{spend * fee_rate:.2f}",
                    "signal_date": signal_date,
                    "reason": "regime_switch" if regime_changed else "rebalance",
                }
            )

        equity_close = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        invested = max(0.0, equity_close - cash)
        equity_rows.append(
            {
                "date": trade_date,
                "equity": equity_close,
                "cash": cash,
                "exposure": invested / equity_close if equity_close > 0 else 0.0,
                "positions": ";".join(sorted(positions)),
                "market_bad": market_bad,
                "sleeve": "defensive" if market_bad else "offensive",
            }
        )
        previous_bad = market_bad
    return equity_rows, trades, signals


def label(config: dict[str, Any]) -> str:
    pool = DEFENSIVE_POOLS[config["defensive_pool"]]["label"]
    filter_label = "MA120+6月正" if config["defensive_filter"] == "ma120_ret6" else "MA200+12月正"
    recovery = "恢复立即切回" if config["recovery_mode"] == "immediate" else "恢复等月调仓"
    return (
        f"进攻6月60%+12月40% Top2｜弱市{pool} {config['defensive_model'].upper()} Top{config['defensive_top_n']} "
        f"{config['defensive_allocation']:.0%}仓｜{filter_label}｜{recovery}"
    )


def compact(row: dict[str, Any], stage: str) -> dict[str, Any]:
    m = row["metrics"]
    return {
        "stage": stage,
        "id": row["id"],
        "label": row["label"],
        "fee_mult": row["fee_mult"],
        "start_date": row["start_date"],
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


def portfolio_metrics(equity: list[dict[str, Any]]) -> dict[str, Any]:
    if not equity:
        return {}
    first_date = dt.date.fromisoformat(str(equity[0]["date"])) - dt.timedelta(days=1)
    initial_row = {
        "date": first_date.isoformat(),
        "equity": v3.INITIAL_CASH,
        "cash": v3.INITIAL_CASH,
        "exposure": 0.0,
        "positions": "",
    }
    return v3.perf_metrics([initial_row, *equity])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    search.HORIZONS["offensive_m612_60_40"] = OFFENSIVE_HORIZON
    histories, errors = base.load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    full_start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    configs = []
    for pool in DEFENSIVE_POOLS:
        for model in ("rs36", "rs612"):
            for top_n in (1, 2):
                for allocation in (0.50, 0.75, 1.00):
                    for filt in ("ma120_ret6", "ma200_ret12"):
                        for recovery in ("immediate", "scheduled"):
                            config = {
                                "defensive_pool": pool,
                                "defensive_model": model,
                                "defensive_top_n": top_n,
                                "defensive_allocation": allocation,
                                "defensive_filter": filt,
                                "recovery_mode": recovery,
                            }
                            config["id"] = "_".join(
                                [pool, model, f"top{top_n}", f"alloc{int(allocation*100)}", filt, recovery]
                            )
                            configs.append(config)

    rows: list[dict[str, Any]] = []
    for config in configs:
        equity, trades, signals = simulate(config, histories, full_start)
        rows.append(
            {
                "id": config["id"],
                "label": label(config),
                "config": config,
                "metrics": portfolio_metrics(equity),
                "trade_count": len(trades),
                "equity": equity,
                "trades": trades,
                "signals": signals,
                "fee_mult": 1.0,
                "start_date": full_start,
            }
        )

    rows.sort(key=lambda row: (row["metrics"]["total_return"], row["metrics"]["max_drawdown"]), reverse=True)
    finalists = rows[:8]
    safe_finalists = [row for row in rows if row["metrics"]["max_drawdown"] >= -0.20][:8]
    stress_finalists = {row["id"]: row for row in finalists + safe_finalists}
    stress: list[dict[str, Any]] = []
    for finalist in stress_finalists.values():
        for start_date in (full_start, "2022-01-04", "2023-01-03"):
            for fee_mult in (1.0, 2.0):
                equity, trades, signals = simulate(finalist["config"], histories, start_date, fee_mult)
                stress.append(
                    {
                        "id": finalist["id"],
                        "label": finalist["label"],
                        "metrics": portfolio_metrics(equity),
                        "trade_count": len(trades),
                        "fee_mult": fee_mult,
                        "start_date": start_date,
                    }
                )

    robust = []
    for finalist in stress_finalists.values():
        cases = [row for row in stress if row["id"] == finalist["id"]]
        robust.append(
            {
                "full": finalist,
                "median_cagr": statistics.median(row["metrics"]["cagr"] for row in cases),
                "min_cagr": min(row["metrics"]["cagr"] for row in cases),
                "worst_drawdown": min(row["metrics"]["max_drawdown"] for row in cases),
            }
        )
    robust.sort(key=lambda row: (row["median_cagr"], row["min_cagr"]), reverse=True)
    robust_safe = [row for row in robust if row["full"]["metrics"]["max_drawdown"] >= -0.20]

    write_csv(OUT_DIR / "dual_sleeve_metrics_latest.csv", [compact(row, "full") for row in rows])
    write_csv(OUT_DIR / "dual_sleeve_stress_latest.csv", [compact(row, "stress") for row in stress])
    for idx, row in enumerate(rows[:10], 1):
        prefix = OUT_DIR / f"top{idx}_{row['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), row["equity"])
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), row["trades"])
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), row["signals"])
    for idx, row in enumerate(safe_finalists, 1):
        prefix = OUT_DIR / f"safe_top{idx}_{row['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), row["equity"])
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), row["trades"])
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), row["signals"])

    lines = [
        "# ETF强弱市双层轮动回测",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 强市：行业ETF 6月60%+12月40%动量，叠加流动性15%和趋势质量10%，Top2。",
        "- 弱市：从纳指/标普或纳指/标普/红利/现金流中选择自身趋势向上的ETF，否则保留现金。",
        "- 强弱判断：沪深300、创业板、科创50中至少2个低于MA120为弱市。",
        "",
        "## 收益 Top15",
        "",
        "| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易数 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(rows[:15], 1):
        m = row["metrics"]
        lines.append(
            f"| {idx} | {row['label']} | {fmt_pct(m['total_return'])} | {fmt_pct(m['cagr'])} | "
            f"{fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {fmt_pct(m['avg_exposure'])} | {row['trade_count']} |"
        )
    lines.extend(["", "## 压力测试", "", "| 排名 | 策略 | 全期收益 | 中位年化 | 最低年化 | 最差回撤 |", "|---:|---|---:|---:|---:|---:|"])
    for idx, row in enumerate(robust, 1):
        full = row["full"]
        lines.append(
            f"| {idx} | {full['label']} | {fmt_pct(full['metrics']['total_return'])} | {fmt_pct(row['median_cagr'])} | "
            f"{fmt_pct(row['min_cagr'])} | {fmt_pct(row['worst_drawdown'])} |"
        )
    lines.extend(["", "## 全期回撤不超过20%的候选", "", "| 排名 | 策略 | 全期收益 | 全期年化 | 全期回撤 | 压测中位年化 | 压测最差回撤 |", "|---:|---|---:|---:|---:|---:|---:|"])
    for idx, row in enumerate(robust_safe, 1):
        full = row["full"]
        lines.append(
            f"| {idx} | {full['label']} | {fmt_pct(full['metrics']['total_return'])} | {fmt_pct(full['metrics']['cagr'])} | "
            f"{fmt_pct(full['metrics']['max_drawdown'])} | {fmt_pct(row['median_cagr'])} | {fmt_pct(row['worst_drawdown'])} |"
        )
    best = rows[0]
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"- 最高收益版本总收益 {fmt_pct(best['metrics']['total_return'])}，年化 {fmt_pct(best['metrics']['cagr'])}，最大回撤 {fmt_pct(best['metrics']['max_drawdown'])}。",
            f"- 完成 {len(rows) + len(stress)} 次回测，其中完整参数组合 {len(rows)}，压力测试 {len(stress)}。",
            "",
        ]
    )
    summary = "\n".join(lines)
    summary_path = OUT_DIR / "dual_sleeve_summary_latest.md"
    summary_path.write_text(summary, encoding="utf-8")
    (OUT_DIR / "dual_sleeve_metrics_latest.json").write_text(
        json.dumps(
            {
                "rows": [compact(row, "full") for row in rows],
                "stress": [compact(row, "stress") for row in stress],
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
