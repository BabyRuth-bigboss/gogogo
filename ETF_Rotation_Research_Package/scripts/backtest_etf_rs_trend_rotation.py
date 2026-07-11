#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3_SCRIPT = ROOT / "scripts/backtest_etf_momentum_rotation_3y_v3.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests"


def load_v3_module():
    spec = importlib.util.spec_from_file_location("etf_rotation_v3_for_rs_trend", V3_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V3_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v3 = load_v3_module()


SCHEDULES: list[dict[str, str]] = [
    {"id": "month_start", "label": "月初", "rule": "每月第一个交易日"},
    {"id": "weekly", "label": "周频", "rule": "每周首个交易日"},
    {"id": "biweekly", "label": "双周", "rule": "隔周首个交易日"},
    {"id": "mid_month", "label": "月中", "rule": "每月首个>=15日的交易日"},
    {"id": "month_end", "label": "月末", "rule": "每月最后一个交易日"},
]

VARIANTS: list[dict[str, Any]] = [
    {
        "id": f"rs_3_6m_{schedule['id']}_top{top_n}",
        "label": f"RS趋势 {schedule['label']} Top{top_n}",
        "top_n": top_n,
        "schedule": schedule["id"],
        "schedule_label": schedule["label"],
        "schedule_rule": schedule["rule"],
    }
    for schedule in SCHEDULES
    for top_n in (1, 2, 3)
]

RET_3M_DAYS = 63
RET_6M_DAYS = 126
TREND_MA_DAYS = 120
MIN_AMOUNT_YI = 0.10


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


def build_rebalance_indices(dates: list[str], start_idx: int, schedule: str) -> set[int]:
    candidates = list(range(start_idx, len(dates) - 1))
    out: set[int] = {start_idx}
    if schedule == "weekly":
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            previous_trade_date = dt.date.fromisoformat(dates[i])
            if i == start_idx or trade_date.isocalendar()[:2] != previous_trade_date.isocalendar()[:2]:
                out.add(i)
        return out

    if schedule == "biweekly":
        weekly = []
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            previous_trade_date = dt.date.fromisoformat(dates[i])
            if i == start_idx or trade_date.isocalendar()[:2] != previous_trade_date.isocalendar()[:2]:
                weekly.append(i)
        return {i for idx, i in enumerate(weekly) if idx % 2 == 0}

    by_month: dict[tuple[int, int], list[int]] = {}
    for i in candidates:
        trade_date = dt.date.fromisoformat(dates[i + 1])
        by_month.setdefault((trade_date.year, trade_date.month), []).append(i)

    if schedule == "month_start":
        out.update(month_indices[0] for month_indices in by_month.values() if month_indices)
        return out

    if schedule == "mid_month":
        for month_indices in by_month.values():
            mid_candidates = [i for i in month_indices if dt.date.fromisoformat(dates[i + 1]).day >= 15]
            if mid_candidates:
                out.add(mid_candidates[0])
        return out

    if schedule == "month_end":
        out.update(month_indices[-1] for month_indices in by_month.values() if month_indices)
        return out

    raise ValueError(f"unknown rebalance schedule: {schedule}")


def build_signal_for_date(
    signal_date: str,
    trade_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    item_by_code = {item["code"]: item for item in v3.ETF_UNIVERSE}
    for code, item in item_by_code.items():
        idx = indexes.get(code, {}).get(signal_date)
        trade_idx = indexes.get(code, {}).get(trade_date)
        if idx is None or trade_idx is None or idx < RET_6M_DAYS:
            continue
        history = histories[code][: idx + 1]
        closes = [v3.fnum(row["close"]) for row in history]
        if len(closes) < RET_6M_DAYS + 1 or min(closes[-RET_6M_DAYS - 1 :]) <= 0:
            continue
        close = closes[-1]
        ma120 = v3.ma(closes, TREND_MA_DAYS)
        ret3m = close / closes[-RET_3M_DAYS - 1] - 1
        ret6m = close / closes[-RET_6M_DAYS - 1] - 1
        score = ret3m * 0.50 + ret6m * 0.50
        amount_yi = v3.fnum(history[-1].get("amount_yi"))
        reasons: list[str] = []
        if amount_yi < MIN_AMOUNT_YI:
            reasons.append(f"成交额{amount_yi:.2f}亿偏低")
        if close < ma120:
            reasons.append("收盘低于MA120")
        if ret6m <= 0:
            reasons.append("6个月收益为负")
        eligible = not reasons
        rows.append(
            {
                "code": code,
                "label": item["label"],
                "theme": item["theme"],
                "date": signal_date,
                "close": close,
                "amount_yi": amount_yi,
                "ret3m": ret3m,
                "ret6m": ret6m,
                "score": score,
                "ma120": ma120,
                "eligible": eligible,
                "reasons": "|".join(reasons),
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def target_label(ranked: list[dict[str, Any]], targets: dict[str, float]) -> str:
    return ";".join(f"{row['label']}:{targets[row['code']] * 100:.0f}%" for row in ranked if row["code"] in targets)


def simulate_variant(histories: dict[str, list[dict[str, Any]]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_cutoff = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * v3.BACKTEST_YEARS))).isoformat()
    start_idx = next(i for i, date in enumerate(dates) if date >= start_cutoff)
    start_idx = max(start_idx, RET_6M_DAYS + 1)
    rebal_indices = build_rebalance_indices(dates, start_idx, str(config["schedule"]))

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        ranked = build_signal_for_date(signal_date, trade_date, histories, indexes)
        rebal_day = i in rebal_indices
        targets: dict[str, float] = {}
        selected: list[dict[str, Any]] = []
        if rebal_day:
            candidates = [row for row in ranked if row["eligible"]]
            selected = candidates[: int(config["top_n"])]
            if selected:
                weight = 1.0 / len(selected)
                targets = {row["code"]: weight for row in selected}
        else:
            equity_open_hold = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
            for code, shares in positions.items():
                open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                if open_price is not None and equity_open_hold > 0:
                    targets[code] = shares * open_price / equity_open_hold

        top5 = ranked[:5]
        signal_rows.append(
            {
                "signal_date": signal_date,
                "trade_date": trade_date,
                "rebalance_day": rebal_day,
                "top1": top5[0]["label"] if top5 else "",
                "top1_score": f"{top5[0]['score'] * 100:.2f}" if top5 else "",
                "top1_ret3m": f"{top5[0]['ret3m'] * 100:.2f}" if top5 else "",
                "top1_ret6m": f"{top5[0]['ret6m'] * 100:.2f}" if top5 else "",
                "eligible_count": sum(1 for row in ranked if row["eligible"]),
                "targets": target_label(ranked, targets),
                "selected": ";".join(row["label"] for row in selected),
                "top5": ";".join(f"{row['label']}:{row['score'] * 100:.1f}" for row in top5),
            }
        )

        equity_open = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        trade_happened = False

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
                    "reason": "monthly_rebalance",
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
            fill_price = open_price * (1 + v3.FEE_RATE)
            spend = min(cash, diff_value)
            if spend <= 0:
                continue
            positions[code] = positions.get(code, 0.0) + spend / fill_price
            cash -= spend
            trade_happened = True
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "BUY",
                    "price": f"{open_price:.4f}",
                    "shares": f"{spend / fill_price:.4f}",
                    "value": f"{spend:.2f}",
                    "fee": f"{spend * v3.FEE_RATE:.2f}",
                    "signal_date": signal_date,
                    "reason": "monthly_rebalance",
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
                "rebalance_day": rebal_day,
            }
        )

    return equity_rows, trades, signal_rows


def metrics_row(label: str, config: dict[str, Any], metrics: dict[str, Any], trades: list[dict[str, Any]], signal_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "variant": label,
        "rule": f"{config['schedule_label']}；3月/6月RS评分；收盘>MA120且6月收益>0；买入Top{config['top_n']}等权",
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trades": len(trades),
        "trade_days": len({trade["date"] for trade in trades}),
        "avg_eligible": statistics.mean(int(row["eligible_count"]) for row in signal_rows) if signal_rows else 0.0,
        "cash_rebalance_periods": sum(1 for row in signal_rows if row["rebalance_day"] and not row["targets"]),
        "yearly": metrics["yearly"],
        "reason_counts": dict(Counter(trade.get("reason", "") for trade in trades).most_common()),
    }


def build_summary(rows: list[dict[str, Any]], benchmarks: dict[str, dict[str, Any]], v4b_metrics: dict[str, Any] | None) -> str:
    by_drawdown = sorted(rows, key=lambda row: row["max_drawdown"], reverse=True)
    lines: list[str] = []
    lines.append("# ETF 3月/6月相对强度 + 绝对趋势过滤回测")
    lines.append("")
    lines.append(f"- 生成时间：{dt.date.today().isoformat()}")
    lines.append(f"- 当前ETF池：{len(v3.ETF_UNIVERSE)}只")
    lines.append("- 规则：按各策略调仓日开盘调仓；信号只使用上一交易日收盘前数据。")
    lines.append("- 调仓日：月初、周频、双周、月中、月末；回测起始日统一允许首次建仓一次。")
    lines.append("- 评分：3个月收益与6个月收益各50%；绝对趋势过滤：收盘价 > MA120 且 6个月收益 > 0。")
    lines.append("- 成本：单边佣金0.03% + 滑点0.05%，ETF无印花税。")
    lines.append("")
    lines.append("| 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 空仓调仓期 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in by_drawdown:
        lines.append(
            f"| {row['variant']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
            f"{row['trades']} | {row['cash_rebalance_periods']} |"
        )
    lines.append("")
    lines.append("## 基准")
    lines.append("| 基准 | 总收益 | 年化 | 最大回撤 | Sharpe |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, metrics in benchmarks.items():
        lines.append(
            f"| {name} | {fmt_pct(metrics['total_return'])} | {fmt_pct(metrics['cagr'])} | "
            f"{fmt_pct(metrics['max_drawdown'])} | {metrics['sharpe']:.2f} |"
        )
    if v4b_metrics:
        lines.append(
            f"| V4b防守基线 | {fmt_pct(v4b_metrics['total_return'])} | {fmt_pct(v4b_metrics['cagr'])} | "
            f"{fmt_pct(v4b_metrics['max_drawdown'])} | {v4b_metrics['sharpe']:.2f} |"
        )
    lines.append("")
    best_dd = by_drawdown[0]
    best_return = max(rows, key=lambda row: row["total_return"])
    lines.append("## 判断")
    lines.append(f"- 回撤最小：{best_dd['variant']}，最大回撤 {fmt_pct(best_dd['max_drawdown'])}。")
    lines.append(f"- 收益最好：{best_return['variant']}，总收益 {fmt_pct(best_return['total_return'])}。")
    if v4b_metrics and best_return["total_return"] < v4b_metrics["total_return"]:
        lines.append("- 这组三个月/六个月趋势轮动没有跑赢 V4b 防守基线，仍不能直接实盘化。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = v3.load_history()
    results: list[dict[str, Any]] = []
    benchmark_cache: dict[str, dict[str, Any]] | None = None

    for config in VARIANTS:
        equity_rows, trades, signal_rows = simulate_variant(histories, config)
        aligned_dates = [row["date"] for row in equity_rows]
        benchmarks = {
            "沪深300": v3.perf_metrics(v3.benchmark_series(histories, "IDX_HS300", aligned_dates)),
            "创业板指": v3.perf_metrics(v3.benchmark_series(histories, "IDX_CHINEXT", aligned_dates)),
            "科创50": v3.perf_metrics(v3.benchmark_series(histories, "IDX_SCI50", aligned_dates)),
        }
        benchmark_cache = benchmarks
        metrics = v3.perf_metrics(equity_rows)
        row = metrics_row(config["label"], config, metrics, trades, signal_rows)
        results.append(row)

        prefix = OUT_DIR / f"etf_rotation_3y_{config['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity_rows)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signal_rows)
        prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
            json.dumps({"strategy": metrics, "benchmarks": benchmarks, "errors": errors, "config": config}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    v4b_path = OUT_DIR / "etf_rotation_3y_v4b_monthly_first_week_metrics_latest.json"
    v4b_metrics = None
    if v4b_path.exists():
        v4b_metrics = json.loads(v4b_path.read_text(encoding="utf-8"))["strategy"]

    summary = build_summary(results, benchmark_cache or {}, v4b_metrics)
    summary_path = OUT_DIR / "etf_rotation_3y_rs_trend_summary_latest.md"
    json_path = OUT_DIR / "etf_rotation_3y_rs_trend_metrics_latest.json"
    csv_path = OUT_DIR / "etf_rotation_3y_rs_trend_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(json.dumps({"variants": results, "benchmarks": benchmark_cache, "v4b": v4b_metrics, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, results)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
