#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3_SCRIPT = ROOT / "scripts/backtest_etf_momentum_rotation_3y_v3.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests"


def load_v3_module():
    spec = importlib.util.spec_from_file_location("etf_rotation_v3_base", V3_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V3_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v3 = load_v3_module()


VARIANTS: list[dict[str, Any]] = [
    {
        "id": "v4_strong_top20",
        "label": "V4 strong+ETF前20%",
        "rule": "strong市场开仓 + 目标ETF收盘在MA20上方 + 20日动量排名前20%",
        "rebalance": "weekly",
        "min_hold_days": 5,
        "require_top20_for_new": True,
    },
    {
        "id": "v4b_monthly_first_week",
        "label": "V4b 每月第一周调仓",
        "rule": "只在每月第一周首个交易日做普通调仓，其他沿用V3",
        "rebalance": "monthly_first_week",
        "min_hold_days": 5,
        "require_top20_for_new": False,
    },
    {
        "id": "v4c_minhold10",
        "label": "V4c 最少持有10日",
        "rule": "持仓至少10个交易日不做普通轮动卖出，其他沿用V3",
        "rebalance": "weekly",
        "min_hold_days": 10,
        "require_top20_for_new": False,
    },
    {
        "id": "v4d_weekly_score_gap",
        "label": "V4d 周频分差确认",
        "rule": "周频调仓，但只有第一名final_score领先第二名至少4分时才执行普通换仓",
        "rebalance": "weekly",
        "min_hold_days": 5,
        "require_top20_for_new": False,
        "require_score_gap_for_rebalance": True,
        "score_gap_min": 4.0,
    },
    {
        "id": "v4e_monthly_reentry_gap",
        "label": "V4e 月频+清仓后周频再入",
        "rule": "保持V4b月频防守；风控清仓后，若周频窗口为strong且第一名领先第二名至少4分，则允许提前再入场",
        "rebalance": "monthly_first_week",
        "min_hold_days": 5,
        "require_top20_for_new": False,
        "allow_weekly_reentry_after_clear": True,
        "reentry_score_gap_min": 4.0,
    },
    {
        "id": "v4f_reentry_top1",
        "label": "V4f 再入只买第一名",
        "rule": "保持V4e，但提前再入场只买final_score第一名，不买第二第三名",
        "rebalance": "monthly_first_week",
        "min_hold_days": 5,
        "require_top20_for_new": False,
        "allow_weekly_reentry_after_clear": True,
        "reentry_score_gap_min": 4.0,
        "reentry_top_n": 1,
    },
    {
        "id": "v4g_reentry_wait5",
        "label": "V4g 再入等待5日",
        "rule": "保持V4e，但风控清仓后至少等待5个交易日，再允许周频分差再入场",
        "rebalance": "monthly_first_week",
        "min_hold_days": 5,
        "require_top20_for_new": False,
        "allow_weekly_reentry_after_clear": True,
        "reentry_score_gap_min": 4.0,
        "reentry_wait_days": 5,
    },
    {
        "id": "v4h_reentry_top1_wait5",
        "label": "V4h 再入Top1+等待5日",
        "rule": "保持V4e，但提前再入场只买第一名，且清仓后至少等待5个交易日",
        "rebalance": "monthly_first_week",
        "min_hold_days": 5,
        "require_top20_for_new": False,
        "allow_weekly_reentry_after_clear": True,
        "reentry_score_gap_min": 4.0,
        "reentry_top_n": 1,
        "reentry_wait_days": 5,
    },
]


def monthly_first_week_rebalance(dates: list[str], i: int, start_idx: int) -> bool:
    if i == start_idx:
        return True
    trade_date = dt.date.fromisoformat(dates[i + 1])
    previous_trade_date = dt.date.fromisoformat(dates[i])
    return trade_date.month != previous_trade_date.month and trade_date.day <= 7


def should_rebalance(config: dict[str, Any], dates: list[str], i: int, start_idx: int) -> bool:
    if config["rebalance"] == "monthly_first_week":
        return monthly_first_week_rebalance(dates, i, start_idx)
    return v3.is_weekly_rebalance(dates, i, start_idx)


def top20_ret20_codes(ranked: list[Any]) -> set[str]:
    ordered = sorted(ranked, key=lambda row: row.ret20, reverse=True)
    top_n = max(1, math.ceil(len(ordered) * 0.20))
    return {row.code for row in ordered[:top_n]}


def target_label(ranked: list[Any], targets: dict[str, float]) -> str:
    return ";".join(f"{row.label}:{int(targets[row.code] * 100)}%" for row in ranked if row.code in targets)


def simulate_variant(histories: dict[str, list[dict[str, Any]]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_cutoff = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * v3.BACKTEST_YEARS))).isoformat()
    start_idx = next(i for i, date in enumerate(dates) if date >= start_cutoff)
    start_idx = max(start_idx, 61)

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    cooldown_until: dict[str, int] = {}
    entry_index: dict[str, int] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    waiting_reentry_after_clear = False
    last_risk_clear_trade_idx: int | None = None

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        ranked = v3.build_signal_for_date(signal_date, trade_date, histories, indexes)
        row_by_code = {row.code: row for row in ranked}
        top20_codes = top20_ret20_codes(ranked) if ranked else set()
        top_snapshot = ranked[:5]
        score_gap = (
            ranked[0].final_score - ranked[1].final_score
            if len(ranked) >= 2
            else (ranked[0].final_score if ranked else 0.0)
        )
        gap_allows_rebalance = (
            not config.get("require_score_gap_for_rebalance")
            or score_gap >= float(config.get("score_gap_min", 0.0))
        )
        regime, regime_note = v3.market_regime(signal_date, histories, indexes)
        ordinary_rebalance_day = should_rebalance(config, dates, i, start_idx)
        weekly_rebalance_day = v3.is_weekly_rebalance(dates, i, start_idx)
        trade_idx = i + 1
        reentry_wait_done = (
            last_risk_clear_trade_idx is None
            or trade_idx - last_risk_clear_trade_idx >= int(config.get("reentry_wait_days", 0))
        )
        reentry_day = (
            bool(config.get("allow_weekly_reentry_after_clear"))
            and waiting_reentry_after_clear
            and not positions
            and not ordinary_rebalance_day
            and weekly_rebalance_day
            and regime == "strong"
            and score_gap >= float(config.get("reentry_score_gap_min", 0.0))
            and reentry_wait_done
        )
        rebalance_day = ordinary_rebalance_day or reentry_day
        equity_open = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        targets: dict[str, float] = {}
        trade_reasons: dict[str, str] = {}

        if regime == "weak":
            targets = {}
            for code in positions:
                trade_reasons[code] = "market_weak_force_cash"
        elif rebalance_day:
            if regime == "strong":
                if gap_allows_rebalance:
                    for rank_idx, row in enumerate(ranked):
                        if row.target_pct <= 0:
                            continue
                        if reentry_day and config.get("reentry_top_n") and rank_idx >= int(config["reentry_top_n"]):
                            continue
                        is_new = row.code not in positions
                        if is_new and cooldown_until.get(row.code, -1) >= trade_idx:
                            row.position_note = f"冷却中至{dates[cooldown_until[row.code]]}"
                            continue
                        if is_new and config.get("require_top20_for_new") and row.code not in top20_codes:
                            row.position_note = "20日动量未进前20%"
                            continue
                        if is_new and config.get("require_top20_for_new") and row.close < row.ma20:
                            row.position_note = "未站上MA20"
                            continue
                        targets[row.code] = row.target_pct / 100
                        if reentry_day:
                            trade_reasons[row.code] = "weekly_reentry_after_clear"
                        else:
                            trade_reasons[row.code] = "score_gap_rebalance" if config.get("require_score_gap_for_rebalance") else "rebalance"

                    for code, shares in positions.items():
                        held_days = (i + 1) - entry_index.get(code, i + 1)
                        if code not in targets and held_days < config["min_hold_days"]:
                            open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                            if open_price is not None and equity_open > 0:
                                targets[code] = shares * open_price / equity_open
                                trade_reasons[code] = f"min_hold_{held_days}d"
                else:
                    for code, shares in positions.items():
                        open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                        if open_price is not None and equity_open > 0:
                            targets[code] = shares * open_price / equity_open
                            trade_reasons[code] = f"score_gap_wait_{score_gap:.1f}"
            else:
                for code, shares in positions.items():
                    open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                    if open_price is not None and equity_open > 0:
                        targets[code] = shares * open_price / equity_open
                        trade_reasons[code] = f"market_{regime}_hold_only"
        else:
            for code, shares in positions.items():
                open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                if open_price is not None and equity_open > 0:
                    targets[code] = shares * open_price / equity_open

        forced_clear: list[str] = []
        for code in list(positions):
            signal_row = row_by_code.get(code)
            if signal_row and signal_row.clear_reasons:
                targets.pop(code, None)
                trade_reasons[code] = "daily_forced_clear:" + "|".join(signal_row.clear_reasons)
                forced_clear.append(code)

        if top_snapshot:
            signal_rows.append(
                {
                    "signal_date": signal_date,
                    "trade_date": trade_date,
                    "rebalance_day": rebalance_day,
                    "ordinary_rebalance_day": ordinary_rebalance_day,
                    "weekly_rebalance_day": weekly_rebalance_day,
                    "reentry_day": reentry_day,
                    "waiting_reentry_after_clear": waiting_reentry_after_clear,
                    "reentry_wait_done": reentry_wait_done,
                    "last_risk_clear_trade_idx": last_risk_clear_trade_idx if last_risk_clear_trade_idx is not None else "",
                    "market_regime": regime,
                    "market_note": regime_note,
                    "top1": top_snapshot[0].label,
                    "top1_score": f"{top_snapshot[0].final_score:.2f}",
                    "score_gap": f"{score_gap:.2f}",
                    "switch_allowed": gap_allows_rebalance,
                    "targets": target_label(ranked, targets),
                    "forced_clear": ";".join(forced_clear),
                    "top20_ret20": ";".join(row.label for row in ranked if row.code in top20_codes),
                    "top5": ";".join(f"{row.label}:{row.final_score:.1f}" for row in top_snapshot),
                }
            )

        trade_happened = False
        risk_clear_exit_happened = False

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
            shares_to_sell = min(positions[code], diff_value / open_price)
            cash += shares_to_sell * open_price * (1 - v3.FEE_RATE)
            positions[code] -= shares_to_sell
            if positions[code] <= 1e-8:
                del positions[code]
                cooldown_until[code] = min(len(dates) - 1, i + 1 + v3.COOLDOWN_TRADING_DAYS)
                entry_index.pop(code, None)
                reason = trade_reasons.get(code, "reduce_or_exit")
                if reason.startswith("daily_forced_clear") or reason == "market_weak_force_cash":
                    risk_clear_exit_happened = True
            trade_happened = True
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "SELL",
                    "price": f"{open_price:.4f}",
                    "shares": f"{shares_to_sell:.4f}",
                    "value": f"{shares_to_sell * open_price:.2f}",
                    "fee": f"{shares_to_sell * open_price * v3.FEE_RATE:.2f}",
                    "signal_date": signal_date,
                    "reason": trade_reasons.get(code, "reduce_or_exit"),
                }
            )

        if risk_clear_exit_happened and not positions:
            waiting_reentry_after_clear = True
            last_risk_clear_trade_idx = i + 1

        for code, weight in targets.items():
            open_price = v3.price_at(histories, indexes, code, trade_date, "open")
            if open_price is None:
                continue
            current_value = positions.get(code, 0.0) * open_price
            target_value = equity_open * weight
            diff_value = target_value - current_value
            if diff_value <= equity_open * v3.REBALANCE_THRESHOLD:
                continue
            fill_price = open_price * (1 + v3.FEE_RATE)
            spend = min(cash, diff_value)
            if spend <= 0:
                continue
            shares = spend / fill_price
            was_new = code not in positions or positions.get(code, 0.0) <= 1e-8
            positions[code] = positions.get(code, 0.0) + shares
            if was_new:
                entry_index[code] = i + 1
            cash -= spend
            waiting_reentry_after_clear = False
            last_risk_clear_trade_idx = None
            trade_happened = True
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "BUY",
                    "price": f"{open_price:.4f}",
                    "shares": f"{shares:.4f}",
                    "value": f"{spend:.2f}",
                    "fee": f"{spend * v3.FEE_RATE:.2f}",
                    "signal_date": signal_date,
                    "reason": trade_reasons.get(code, "rebalance"),
                }
            )

        equity_close = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        invested_value = equity_close - cash
        equity_rows.append(
            {
                "date": trade_date,
                "equity": equity_close,
                "cash": cash,
                "exposure": invested_value / equity_close if equity_close > 0 else 0.0,
                "positions": ";".join(sorted(positions)),
                "trade_happened": trade_happened,
                "rebalance_day": rebalance_day,
                "ordinary_rebalance_day": ordinary_rebalance_day,
                "weekly_rebalance_day": weekly_rebalance_day,
                "reentry_day": reentry_day,
                "waiting_reentry_after_clear": waiting_reentry_after_clear,
                "reentry_wait_done": reentry_wait_done,
                "market_regime": regime,
            }
        )

    return equity_rows, trades, signal_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def metrics_row(label: str, config: dict[str, Any], metrics: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "variant": label,
        "rule": config["rule"],
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trade_days": len({trade["date"] for trade in trades}),
        "trades": len(trades),
        "buys": sum(1 for trade in trades if trade["side"] == "BUY"),
        "sells": sum(1 for trade in trades if trade["side"] == "SELL"),
        "yearly": metrics["yearly"],
        "reason_counts": dict(Counter(trade.get("reason", "") for trade in trades).most_common()),
    }


def build_variant_summary(row: dict[str, Any], benchmarks: dict[str, dict[str, Any]]) -> str:
    metrics = row
    lines = [f"# ETF动量轮动3年回测 {metrics['variant']}", ""]
    lines.append(f"规则：{metrics['rule']}")
    lines.append("")
    lines.append("| 项目 | 策略 | 沪深300 | 创业板指 | 科创50 |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| 总收益 | {fmt_pct(metrics['total_return'])} | {fmt_pct(benchmarks['hs300']['total_return'])} | "
        f"{fmt_pct(benchmarks['chinext']['total_return'])} | {fmt_pct(benchmarks['sci50']['total_return'])} |"
    )
    lines.append(
        f"| 年化 | {fmt_pct(metrics['cagr'])} | {fmt_pct(benchmarks['hs300']['cagr'])} | "
        f"{fmt_pct(benchmarks['chinext']['cagr'])} | {fmt_pct(benchmarks['sci50']['cagr'])} |"
    )
    lines.append(
        f"| 最大回撤 | {fmt_pct(metrics['max_drawdown'])} | {fmt_pct(benchmarks['hs300']['max_drawdown'])} | "
        f"{fmt_pct(benchmarks['chinext']['max_drawdown'])} | {fmt_pct(benchmarks['sci50']['max_drawdown'])} |"
    )
    lines.append(f"| Sharpe | {metrics['sharpe']:.2f} | {benchmarks['hs300']['sharpe']:.2f} | {benchmarks['chinext']['sharpe']:.2f} | {benchmarks['sci50']['sharpe']:.2f} |")
    lines.append("")
    lines.append("## 年度收益")
    for year, value in metrics["yearly"].items():
        lines.append(f"- {year}: {fmt_pct(value)}")
    lines.append("")
    lines.append("## 交易")
    lines.append(f"- 调仓日：{metrics['trade_days']}，买卖笔数：{metrics['trades']}，平均仓位：{fmt_pct(metrics['avg_exposure'])}。")
    lines.append("- 主要原因：" + "；".join(f"{k}={v}" for k, v in list(metrics["reason_counts"].items())[:8]))
    return "\n".join(lines) + "\n"


def build_comparison(rows: list[dict[str, Any]], v3_metrics: dict[str, Any]) -> str:
    lines = ["# ETF轮动 V3/V4系列 对比", ""]
    lines.append("| 版本 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 调仓日 | 买卖笔数 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| V3 基准 | {fmt_pct(v3_metrics['total_return'])} | {fmt_pct(v3_metrics['cagr'])} | "
        f"{fmt_pct(v3_metrics['max_drawdown'])} | {v3_metrics['sharpe']:.2f} | "
        f"{fmt_pct(v3_metrics['avg_exposure'])} | - | - |"
    )
    for row in rows:
        lines.append(
            f"| {row['variant']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
            f"{row['trade_days']} | {row['trades']} |"
        )
    lines.append("")
    lines.append("## 规则")
    for row in rows:
        lines.append(f"- {row['variant']}：{row['rule']}")
    lines.append("")
    best_dd = min(rows, key=lambda item: abs(item["max_drawdown"]))
    best_return = max(rows, key=lambda item: item["total_return"])
    lines.append(f"- 回撤最小：{best_dd['variant']}，最大回撤 {fmt_pct(best_dd['max_drawdown'])}。")
    lines.append(f"- 收益最好：{best_return['variant']}，总收益 {fmt_pct(best_return['total_return'])}。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = v3.load_history()
    dates = v3.common_calendar(histories)
    # Use the full benchmark range aligned with strategy rows for each variant.
    results: list[dict[str, Any]] = []
    benchmark_cache: dict[str, dict[str, Any]] | None = None

    for config in VARIANTS:
        equity_rows, trades, signal_rows = simulate_variant(histories, config)
        aligned_dates = [row["date"] for row in equity_rows]
        benchmarks = {
            "hs300": v3.perf_metrics(v3.benchmark_series(histories, "IDX_HS300", aligned_dates)),
            "chinext": v3.perf_metrics(v3.benchmark_series(histories, "IDX_CHINEXT", aligned_dates)),
            "sci50": v3.perf_metrics(v3.benchmark_series(histories, "IDX_SCI50", aligned_dates)),
        }
        benchmark_cache = benchmarks
        metrics = v3.perf_metrics(equity_rows)
        row = metrics_row(config["label"], config, metrics, trades)
        results.append(row)

        prefix = OUT_DIR / f"etf_rotation_3y_{config['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity_rows)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signal_rows)
        prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
            json.dumps({"strategy": metrics, "benchmarks": benchmarks, "errors": errors, "config": config}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        prefix.with_name(prefix.name + "_summary_latest.md").write_text(build_variant_summary(row, benchmarks), encoding="utf-8")

    v3_metrics = json.loads((OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_metrics_latest.json").read_text(encoding="utf-8"))["strategy"]
    comparison = build_comparison(results, v3_metrics)
    comparison_path = OUT_DIR / "etf_rotation_3y_v3_v4_variants_comparison.md"
    comparison_path.write_text(comparison, encoding="utf-8")
    (OUT_DIR / "etf_rotation_3y_v4_variants_metrics_latest.json").write_text(
        json.dumps({"variants": results, "v3": v3_metrics, "benchmarks": benchmark_cache}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(comparison)
    print(f"wrote {comparison_path}")


if __name__ == "__main__":
    main()
