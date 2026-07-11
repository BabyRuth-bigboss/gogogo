#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3_SCRIPT = ROOT / "scripts/backtest_etf_momentum_rotation_3y_v3.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_lab"


def load_v3_module():
    spec = importlib.util.spec_from_file_location("etf_rotation_v3_for_strategy_lab", V3_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V3_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v3 = load_v3_module()


CORE_CODES = {"510880", "513100", "513500", "159399"}

STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "rs36_month_top2",
        "label": "RS36月频Top2",
        "schedule": "month_start",
        "model": "rs36",
        "top_n": 2,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
    },
    {
        "id": "rs36_weekly_top3_market",
        "label": "RS36周频Top3+市场过滤",
        "schedule": "weekly",
        "model": "rs36",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "market_filter": "risk_on_2of3",
        "weighting": "equal",
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
        "id": "dual_momentum_6m_top3",
        "label": "6月绝对动量Top3",
        "schedule": "month_start",
        "model": "ret6",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
    },
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
        "id": "trend_pool_top8_equal",
        "label": "趋势池等权Top8",
        "schedule": "month_start",
        "model": "rs36",
        "top_n": 8,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "equal",
    },
    {
        "id": "breakout_120d_top3",
        "label": "120日突破Top3",
        "schedule": "weekly",
        "model": "breakout120",
        "top_n": 3,
        "filter": "breakout120",
        "weighting": "equal",
    },
    {
        "id": "pullback_rs_top3",
        "label": "强趋势回调Top3",
        "schedule": "weekly",
        "model": "rs36",
        "top_n": 3,
        "filter": "trend_pullback",
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
        "id": "rs36_month_top2_vol_weight",
        "label": "RS36月频Top2波动配权",
        "schedule": "month_start",
        "model": "rs36",
        "top_n": 2,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "inverse_vol",
    },
    {
        "id": "rs36_month_top3_drawdown_gate",
        "label": "RS36月频Top3回撤门控",
        "schedule": "month_start",
        "model": "rs36",
        "top_n": 3,
        "filter": "etf_ma120_ret6_pos",
        "market_filter": "no_index_dd15",
        "weighting": "equal",
    },
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


def build_rebalance_indices(dates: list[str], start_idx: int, schedule: str) -> set[int]:
    candidates = list(range(start_idx, len(dates) - 1))
    out = {start_idx}
    if schedule == "weekly":
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            previous_trade_date = dt.date.fromisoformat(dates[i])
            if i == start_idx or trade_date.isocalendar()[:2] != previous_trade_date.isocalendar()[:2]:
                out.add(i)
        return out
    if schedule == "biweekly":
        weekly = sorted(build_rebalance_indices(dates, start_idx, "weekly"))
        return {i for idx, i in enumerate(weekly) if idx % 2 == 0}
    if schedule == "month_start":
        by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            by_month[(trade_date.year, trade_date.month)].append(i)
        out.update(month[0] for month in by_month.values() if month)
        return out
    if schedule == "month_start_3":
        by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            by_month[(trade_date.year, trade_date.month)].append(i)
        for month in by_month.values():
            out.add(month[2] if len(month) >= 3 else month[-1])
        return out
    if schedule == "mid_month":
        by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in candidates:
            trade_date = dt.date.fromisoformat(dates[i + 1])
            by_month[(trade_date.year, trade_date.month)].append(i)
        for month in by_month.values():
            mid_candidates = [i for i in month if dt.date.fromisoformat(dates[i + 1]).day >= 15]
            if mid_candidates:
                out.add(mid_candidates[0])
        return out
    raise ValueError(f"unknown schedule: {schedule}")


def build_month_end_cash_indices(dates: list[str], start_idx: int, days: int) -> set[int]:
    if days <= 0:
        return set()
    by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i in range(start_idx, len(dates) - 1):
        trade_date = dt.date.fromisoformat(dates[i + 1])
        by_month[(trade_date.year, trade_date.month)].append(i)
    out: set[int] = set()
    for month in by_month.values():
        out.update(month[-days:])
    return out


def returns(closes: list[float]) -> dict[str, float]:
    def ret(days: int) -> float:
        return closes[-1] / closes[-days - 1] - 1 if len(closes) > days and closes[-days - 1] > 0 else 0.0

    daily = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 60), len(closes)) if closes[i - 1] > 0]
    vol60 = statistics.stdev(daily) * math.sqrt(252) if len(daily) > 2 else 0.0
    dd60 = v3.max_drawdown(closes[-60:]) if len(closes) >= 60 else 0.0
    return {
        "ret20": ret(20),
        "ret60": ret(60),
        "ret63": ret(63),
        "ret126": ret(126),
        "ret252": ret(252),
        "vol60": vol60,
        "dd60": dd60,
    }


def index_state(code: str, signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> dict[str, float] | None:
    idx = indexes.get(code, {}).get(signal_date)
    if idx is None or idx < 252:
        return None
    rows = histories[code][: idx + 1]
    closes = [v3.fnum(row["close"]) for row in rows]
    if len(closes) < 253 or min(closes[-253:]) <= 0:
        return None
    stats = returns(closes)
    return {
        **stats,
        "close": closes[-1],
        "ma120": v3.ma(closes, 120),
        "ma200": v3.ma(closes, 200),
        "dd126": v3.max_drawdown(closes[-126:]),
    }


def market_allows(strategy: dict[str, Any], signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> tuple[bool, str]:
    filt = strategy.get("market_filter")
    if not filt:
        return True, "no_market_filter"
    states = [index_state(code, signal_date, histories, indexes) for code in ("IDX_HS300", "IDX_CHINEXT", "IDX_SCI50")]
    states = [state for state in states if state]
    if not states:
        return False, "market_history_missing"
    if filt == "risk_on_2of3":
        good = sum(1 for state in states if state["close"] > state["ma120"] and state["ret126"] > 0)
        return good >= 2, f"risk_on_2of3={good}/3"
    if filt == "risk_on_1of3":
        good = sum(1 for state in states if state["close"] > state["ma120"] and state["ret126"] > 0)
        return good >= 1, f"risk_on_1of3={good}/3"
    if filt == "risk_on_3of3":
        good = sum(1 for state in states if state["close"] > state["ma120"] and state["ret126"] > 0)
        return good >= 3, f"risk_on_3of3={good}/3"
    if filt == "no_index_dd15":
        worst_dd = min(state["dd126"] for state in states)
        return worst_dd > -0.15, f"worst_index_dd126={worst_dd*100:.1f}%"
    raise ValueError(f"unknown market filter: {filt}")


def etf_features(signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_code = {item["code"]: item for item in v3.ETF_UNIVERSE}
    for code, item in by_code.items():
        idx = indexes.get(code, {}).get(signal_date)
        if idx is None or idx < 252:
            continue
        rows = histories[code][: idx + 1]
        closes = [v3.fnum(row["close"]) for row in rows]
        if len(closes) < 253 or min(closes[-253:]) <= 0:
            continue
        stats = returns(closes)
        high120 = max(closes[-120:])
        amount_yi = v3.fnum(rows[-1].get("amount_yi"))
        row = {
            "code": code,
            "label": item["label"],
            "theme": item["theme"],
            "close": closes[-1],
            "ma60": v3.ma(closes, 60),
            "ma120": v3.ma(closes, 120),
            "ma200": v3.ma(closes, 200),
            "high120": high120,
            "amount_yi": amount_yi,
            **stats,
        }
        out.append(row)
    return out


def score_row(row: dict[str, Any], model: str) -> float:
    if model == "rs36":
        return row["ret63"] * 0.5 + row["ret126"] * 0.5
    if model == "rs612":
        return row["ret126"] * 0.5 + row["ret252"] * 0.5
    if model == "ret6":
        return row["ret126"]
    if model == "rs36_lowvol":
        return row["ret63"] * 0.45 + row["ret126"] * 0.45 - row["vol60"] * 0.10
    if model == "breakout120":
        return row["ret63"] * 0.4 + row["ret126"] * 0.4 + (row["close"] / row["high120"] - 1) * 0.2
    if model == "core_satellite":
        return row["ret63"] * 0.5 + row["ret126"] * 0.5
    raise ValueError(f"unknown model: {model}")


def eligible(row: dict[str, Any], filter_name: str) -> bool:
    if row["amount_yi"] < 0.10:
        return False
    if filter_name == "etf_ma120_ret6_pos":
        return row["close"] > row["ma120"] and row["ret126"] > 0
    if filter_name == "etf_ma200_ret12_pos":
        return row["close"] > row["ma200"] and row["ret252"] > 0
    if filter_name == "breakout120":
        return row["close"] >= row["high120"] * 0.995 and row["ret63"] > 0 and row["ret126"] > 0
    if filter_name == "trend_pullback":
        return row["close"] > row["ma120"] and row["ret126"] > 0 and row["ret20"] < 0 and row["dd60"] > -0.15
    raise ValueError(f"unknown filter: {filter_name}")


def normalized_weights(selected: list[dict[str, Any]], weighting: str, strategy: dict[str, Any]) -> dict[str, float]:
    if not selected:
        return {}
    if weighting == "equal":
        return {row["code"]: 1 / len(selected) for row in selected}
    if weighting == "inverse_vol":
        inv = {row["code"]: 1 / max(row["vol60"], 0.08) for row in selected}
        total = sum(inv.values())
        return {code: value / total for code, value in inv.items()}
    if weighting == "core_satellite":
        core = [row for row in selected if row["code"] in CORE_CODES]
        satellite = [row for row in selected if row["code"] not in CORE_CODES]
        weights: dict[str, float] = {}
        if core:
            weights[core[0]["code"]] = 0.50
        elif satellite:
            # If no defensive core passes, keep the core half in cash.
            pass
        sat = satellite[: int(strategy["top_n"])]
        if sat:
            for row in sat:
                weights[row["code"]] = weights.get(row["code"], 0.0) + 0.50 / len(sat)
        return weights
    raise ValueError(f"unknown weighting: {weighting}")


def target_label(rows: list[dict[str, Any]], targets: dict[str, float]) -> str:
    labels = {row["code"]: row["label"] for row in rows}
    return ";".join(f"{labels.get(code, code)}:{weight*100:.0f}%" for code, weight in targets.items())


def build_targets(strategy: dict[str, Any], signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> tuple[dict[str, float], dict[str, Any]]:
    market_ok, market_note = market_allows(strategy, signal_date, histories, indexes)
    rows = etf_features(signal_date, histories, indexes)
    for row in rows:
        row["score"] = score_row(row, str(strategy["model"]))
        row["eligible"] = eligible(row, str(strategy["filter"]))
    ranked = sorted(rows, key=lambda item: item["score"], reverse=True)
    if not market_ok:
        return {}, {"market_note": market_note, "ranked": ranked, "selected": [], "eligible_count": sum(1 for row in rows if row["eligible"])}
    candidates = [row for row in ranked if row["eligible"]]
    if strategy["weighting"] == "core_satellite":
        core_candidates = [row for row in candidates if row["code"] in CORE_CODES]
        satellite_candidates = [row for row in candidates if row["code"] not in CORE_CODES]
        selected = core_candidates[:1] + satellite_candidates[: int(strategy["top_n"])]
    else:
        selected = candidates[: int(strategy["top_n"])]
    targets = normalized_weights(selected, str(strategy["weighting"]), strategy)
    return targets, {
        "market_note": market_note,
        "ranked": ranked,
        "selected": selected,
        "eligible_count": len(candidates),
    }


def simulate_strategy(strategy: dict[str, Any], histories: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_cutoff = str(strategy.get("start_date") or (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * v3.BACKTEST_YEARS))).isoformat())
    start_idx = next(i for i, date in enumerate(dates) if date >= start_cutoff)
    start_idx = max(start_idx, 253)
    rebal_indices = build_rebalance_indices(dates, start_idx, str(strategy["schedule"]))
    month_end_cash_indices = build_month_end_cash_indices(dates, start_idx, int(strategy.get("cash_last_n_month_days", 0) or 0))
    fee_rate = float(strategy.get("fee_rate", v3.FEE_RATE)) * float(strategy.get("fee_mult", 1.0))
    portfolio_dd_clear = float(strategy.get("portfolio_dd_clear", 0.0) or 0.0)
    portfolio_dd_reduce = float(strategy.get("portfolio_dd_reduce", 0.0) or 0.0)
    portfolio_dd_reduce_to = float(strategy.get("portfolio_dd_reduce_to", 0.5) or 0.5)

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    current_targets: dict[str, float] = {}
    risk_peak = v3.INITIAL_CASH

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        rebal_day = i in rebal_indices
        month_end_cash_day = i in month_end_cash_indices
        if rebal_day:
            current_targets, info = build_targets(strategy, signal_date, histories, indexes)
        else:
            info = {"market_note": "hold", "ranked": [], "selected": [], "eligible_count": ""}

        signal_equity = v3.portfolio_equity(cash, positions, histories, indexes, signal_date, "close")
        risk_peak = max(risk_peak, signal_equity)
        portfolio_drawdown = signal_equity / risk_peak - 1 if risk_peak > 0 else 0.0
        risk_gate_clear = bool(portfolio_dd_clear > 0 and positions and portfolio_drawdown <= -portfolio_dd_clear)
        risk_gate_reduce = bool(
            not risk_gate_clear
            and portfolio_dd_reduce > 0
            and positions
            and portfolio_drawdown <= -portfolio_dd_reduce
        )
        if risk_gate_clear:
            current_targets = {}
        if month_end_cash_day:
            current_targets = {}

        equity_open = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        targets: dict[str, float] = {}
        if month_end_cash_day:
            targets = {}
        elif risk_gate_clear:
            targets = {}
        elif risk_gate_reduce:
            if rebal_day:
                targets = {code: weight * portfolio_dd_reduce_to for code, weight in current_targets.items()}
            else:
                for code, shares in positions.items():
                    open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                    if open_price is not None and equity_open > 0:
                        targets[code] = shares * open_price / equity_open * portfolio_dd_reduce_to
        elif rebal_day:
            targets = dict(current_targets)
        else:
            for code, shares in positions.items():
                open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                if open_price is not None and equity_open > 0:
                    targets[code] = shares * open_price / equity_open

        ranked = info.get("ranked", [])
        selected = info.get("selected", [])
        signal_rows.append(
            {
                "signal_date": signal_date,
                "trade_date": trade_date,
                "rebalance_day": rebal_day,
                "market_note": (
                    f"{info.get('market_note', '')};month_end_cash"
                    if month_end_cash_day
                    else
                    f"{info.get('market_note', '')};portfolio_dd_clear={portfolio_drawdown*100:.2f}%"
                    if risk_gate_clear
                    else f"{info.get('market_note', '')};portfolio_dd_reduce={portfolio_drawdown*100:.2f}%"
                    if risk_gate_reduce
                    else info.get("market_note", "")
                ),
                "portfolio_drawdown": f"{portfolio_drawdown:.6f}",
                "risk_gate_clear": risk_gate_clear,
                "risk_gate_reduce": risk_gate_reduce,
                "month_end_cash": month_end_cash_day,
                "eligible_count": info.get("eligible_count", ""),
                "selected": ";".join(row["label"] for row in selected),
                "targets": target_label(ranked, targets) if rebal_day or risk_gate_reduce or month_end_cash_day else "",
                "top5": ";".join(f"{row['label']}:{row['score']*100:.1f}" for row in ranked[:5]) if ranked else "",
            }
        )

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
            cash += shares_to_sell * open_price * (1 - fee_rate)
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
                    "fee": f"{shares_to_sell * open_price * fee_rate:.2f}",
                    "signal_date": signal_date,
                    "reason": "month_end_cash" if month_end_cash_day else "risk_gate_clear" if risk_gate_clear else "risk_gate_reduce" if risk_gate_reduce else "rebalance",
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
            fill_price = open_price * (1 + fee_rate)
            spend = min(cash, diff_value)
            if spend <= 0:
                continue
            shares = spend / fill_price
            positions[code] = positions.get(code, 0.0) + shares
            cash -= spend
            trade_happened = True
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
                    "reason": "risk_gate_reduce" if risk_gate_reduce else "rebalance",
                }
            )

        equity_close = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        if risk_gate_clear or risk_gate_reduce:
            risk_peak = equity_close
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


def strategy_row(strategy: dict[str, Any], metrics: dict[str, Any], trades: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
    risk_rule = f" | dd_clear={float(strategy['portfolio_dd_clear'])*100:.0f}%" if strategy.get("portfolio_dd_clear") else ""
    if strategy.get("portfolio_dd_reduce"):
        risk_rule += (
            f" | dd_reduce={float(strategy['portfolio_dd_reduce'])*100:.0f}%"
            f"->{float(strategy.get('portfolio_dd_reduce_to', 0.5))*100:.0f}%"
        )
    if strategy.get("cash_last_n_month_days"):
        risk_rule += f" | cash_last_{int(strategy['cash_last_n_month_days'])}d"
    return {
        "id": strategy["id"],
        "variant": strategy["label"],
        "rule": f"{strategy['model']} | {strategy['filter']} | {strategy.get('market_filter','no_market')} | {strategy['schedule']} | {strategy['weighting']} | top{strategy['top_n']}{risk_rule}",
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trades": len(trades),
        "trade_days": len({trade["date"] for trade in trades}),
        "risk_clear_events": len({trade["date"] for trade in trades if trade.get("reason") == "risk_gate_clear"}),
        "risk_reduce_events": len({trade["date"] for trade in trades if trade.get("reason") == "risk_gate_reduce"}),
        "month_end_cash_events": len({trade["date"] for trade in trades if trade.get("reason") == "month_end_cash"}),
        "cash_rebalance_periods": sum(1 for row in signals if row["rebalance_day"] and not row["targets"]),
        "yearly": metrics["yearly"],
        "reason_counts": dict(Counter(trade.get("reason", "") for trade in trades).most_common()),
    }


def build_summary(rows: list[dict[str, Any]], benchmarks: dict[str, dict[str, Any]]) -> str:
    valid_rows = [row for row in rows if row["trades"] > 0]
    invalid_rows = [row for row in rows if row["trades"] == 0]
    by_drawdown = sorted(valid_rows, key=lambda row: row["max_drawdown"], reverse=True)
    by_return = sorted(valid_rows, key=lambda row: row["total_return"], reverse=True)
    by_sharpe = sorted(valid_rows, key=lambda row: row["sharpe"], reverse=True)
    lines = ["# ETF策略实验室回测", ""]
    lines.append(f"- 生成时间：{dt.date.today().isoformat()}")
    lines.append(f"- 当前ETF池：{len(v3.ETF_UNIVERSE)}只")
    lines.append("- 共同假设：信号日收盘后计算，下一交易日开盘成交；单边佣金0.03% + 滑点0.05%。")
    lines.append("- 注意：这是想法筛选，不是最终实盘版本；优先看稳定性和失败点。")
    lines.append("")
    lines.append("## 按最大回撤排序")
    lines.append("| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 规则 |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---|")
    for idx, row in enumerate(by_drawdown, 1):
        safe_rule = str(row["rule"]).replace("|", "/")
        lines.append(
            f"| {idx} | {row['variant']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | {row['trades']} | {safe_rule} |"
        )
    if invalid_rows:
        lines.append("")
        lines.append("## 无效或过严策略")
        for row in invalid_rows:
            lines.append(f"- {row['variant']}：无交易，规则过严或样本期内无信号。")
    lines.append("")
    lines.append("## 收益Top5")
    for row in by_return[:5]:
        lines.append(f"- {row['variant']}：总收益 {fmt_pct(row['total_return'])}，最大回撤 {fmt_pct(row['max_drawdown'])}，Sharpe {row['sharpe']:.2f}")
    lines.append("")
    lines.append("## Sharpe Top5")
    for row in by_sharpe[:5]:
        lines.append(f"- {row['variant']}：Sharpe {row['sharpe']:.2f}，总收益 {fmt_pct(row['total_return'])}，最大回撤 {fmt_pct(row['max_drawdown'])}")
    lines.append("")
    lines.append("## 基准")
    lines.append("| 基准 | 总收益 | 年化 | 最大回撤 | Sharpe |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, metrics in benchmarks.items():
        lines.append(f"| {name} | {fmt_pct(metrics['total_return'])} | {fmt_pct(metrics['cagr'])} | {fmt_pct(metrics['max_drawdown'])} | {metrics['sharpe']:.2f} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = v3.load_history()
    results: list[dict[str, Any]] = []
    benchmark_cache: dict[str, dict[str, Any]] | None = None

    for strategy in STRATEGIES:
        equity, trades, signals = simulate_strategy(strategy, histories)
        dates = [row["date"] for row in equity]
        benchmarks = {
            "沪深300": v3.perf_metrics(v3.benchmark_series(histories, "IDX_HS300", dates)),
            "创业板指": v3.perf_metrics(v3.benchmark_series(histories, "IDX_CHINEXT", dates)),
            "科创50": v3.perf_metrics(v3.benchmark_series(histories, "IDX_SCI50", dates)),
        }
        benchmark_cache = benchmarks
        metrics = v3.perf_metrics(equity)
        row = strategy_row(strategy, metrics, trades, signals)
        results.append(row)
        prefix = OUT_DIR / f"strategy_lab_{strategy['id']}"
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
        prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
            json.dumps({"strategy": metrics, "benchmarks": benchmarks, "errors": errors, "config": strategy}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary = build_summary(results, benchmark_cache or {})
    summary_path = OUT_DIR / "strategy_lab_summary_latest.md"
    json_path = OUT_DIR / "strategy_lab_metrics_latest.json"
    csv_path = OUT_DIR / "strategy_lab_metrics_latest.csv"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(json.dumps({"strategies": results, "benchmarks": benchmark_cache, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, results)
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
