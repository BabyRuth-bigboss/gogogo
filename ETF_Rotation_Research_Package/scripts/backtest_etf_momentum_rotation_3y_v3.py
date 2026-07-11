#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "scripts/etf_momentum_rotation.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests"
INITIAL_CASH = 1_000_000.0
KLINE_LIMIT = 950
BACKTEST_YEARS = 3
MIN_AMOUNT_YI = 0.10
COMMISSION = 0.0003
SLIPPAGE = 0.0005
FEE_RATE = COMMISSION + SLIPPAGE
REBALANCE_THRESHOLD = 0.02
COOLDOWN_TRADING_DAYS = 10
MIN_HOLD_TRADING_DAYS = 5
ALLOWED_OPEN_REGIMES = {"strong"}


def load_live_module():
    spec = importlib.util.spec_from_file_location("etf_momentum_rotation_live", LIVE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LIVE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live = load_live_module()
ETF_UNIVERSE = live.ETF_UNIVERSE
MARKET_WATCH = live.MARKET_WATCH
BENCHMARK_INDEXES = {
    "IDX_HS300": {"sec": "sh000300", "label": "沪深300指数"},
    "IDX_CHINEXT": {"sec": "sz399006", "label": "创业板指"},
    "IDX_SCI50": {"sec": "sh000688", "label": "科创50指数"},
}


@dataclass
class HistRow:
    code: str
    label: str
    theme: str
    name: str
    date: str
    close: float
    open: float
    amount_yi: float
    ret10: float
    ret20: float
    ret60: float
    weighted_momentum: float
    mdd20: float
    ma20: float
    ma60: float
    momentum_score: float = 0.0
    premium_score: float = 70.0
    drawdown_score: float = 0.0
    rs_score: float = 0.0
    gap_score: float = 0.0
    final_score: float = 0.0
    rank: int = 0
    target_pct: int = 0
    position_note: str = ""
    eligible: bool = False
    reasons: list[str] = field(default_factory=list)
    clear_reasons: list[str] = field(default_factory=list)


def fnum(value: Any, default: float = 0.0) -> float:
    return live.fnum(value, default)


def ma(values: list[float], n: int) -> float:
    if len(values) < n:
        return 0.0
    return statistics.mean(values[-n:])


def max_drawdown(values: list[float]) -> float:
    return live.max_drawdown(values)


def percentile_scores(values: list[float]) -> list[float]:
    return live.percentile_scores(values)


def drawdown_score(mdd20: float) -> float:
    return live.drawdown_score(mdd20)


def gap_score_from_pct(gap_pct: float) -> float:
    return live.gap_score_from_pct(gap_pct)


def rotation_group(row: HistRow) -> str:
    return live.rotation_group(row)


def fetch_one(item: dict[str, str], limit: int = KLINE_LIMIT) -> tuple[str, list[dict[str, Any]], str | None]:
    code = item["code"]
    try:
        rows = live.fetch_tencent_klines(code, limit=limit)
        clean: list[dict[str, Any]] = []
        for row in rows:
            close = fnum(row.get("close"))
            volume = fnum(row.get("volume"))
            clean.append(
                {
                    "date": str(row.get("date")),
                    "open": fnum(row.get("open")),
                    "high": fnum(row.get("high")),
                    "low": fnum(row.get("low")),
                    "close": close,
                    "volume": volume,
                    "amount_yi": close * volume * 100 / 100_000_000 if close > 0 and volume > 0 else 0.0,
                }
            )
        return code, clean, None
    except Exception as exc:
        return code, [], str(exc)


def fetch_sec_klines(sec: str, limit: int = KLINE_LIMIT) -> list[dict[str, Any]]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    params = urlencode({"param": f"{sec},day,,,{limit},qfq"})
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + params
    req = Request(url, headers={"User-Agent": live.UA, "Referer": "https://gu.qq.com/"})
    with urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw_data = payload.get("data", {}).get(sec, {})
    raw = raw_data.get("qfqday") or raw_data.get("day") or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if len(item) < 6:
            continue
        close = fnum(item[2])
        volume = fnum(item[5])
        rows.append(
            {
                "date": item[0],
                "open": fnum(item[1]),
                "high": fnum(item[3]),
                "low": fnum(item[4]),
                "close": close,
                "volume": volume,
                "amount_yi": close * volume * 100 / 100_000_000 if close > 0 and volume > 0 else 0.0,
            }
        )
    return rows


def fetch_index_one(key: str, sec: str, limit: int = KLINE_LIMIT) -> tuple[str, list[dict[str, Any]], str | None]:
    try:
        return key, fetch_sec_klines(sec, limit), None
    except Exception as exc:
        return key, [], str(exc)


def load_history() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    items = ETF_UNIVERSE + MARKET_WATCH
    data: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch_one, item) for item in items]
        futures += [pool.submit(fetch_index_one, key, item["sec"]) for key, item in BENCHMARK_INDEXES.items()]
        for future in as_completed(futures):
            code, rows, err = future.result()
            if err:
                errors[code] = err
            else:
                data[code] = rows
    return data, errors


def row_name(item: dict[str, str]) -> str:
    return item.get("label", item["code"])


def build_signal_for_date(
    signal_date: str,
    trade_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> list[HistRow]:
    rows: list[HistRow] = []
    item_by_code = {item["code"]: item for item in ETF_UNIVERSE}
    for code, item in item_by_code.items():
        idx = indexes.get(code, {}).get(signal_date)
        trade_idx = indexes.get(code, {}).get(trade_date)
        if idx is None or trade_idx is None or idx < 60:
            continue
        history = histories[code][: idx + 1]
        closes = [fnum(row["close"]) for row in history]
        if len(closes) < 61 or min(closes[-61:]) <= 0:
            continue
        row = history[-1]
        close = fnum(row["close"])
        ret10 = (closes[-1] / closes[-11] - 1) * 100
        ret20 = (closes[-1] / closes[-21] - 1) * 100
        ret60 = (closes[-1] / closes[-61] - 1) * 100
        weighted = ret10 * 0.30 + ret20 * 0.70
        mdd20 = max_drawdown(closes[-20:])
        out = HistRow(
            code=code,
            label=item["label"],
            theme=item["theme"],
            name=item["label"],
            date=signal_date,
            close=close,
            open=fnum(row["open"]),
            amount_yi=fnum(row["amount_yi"]),
            ret10=ret10,
            ret20=ret20,
            ret60=ret60,
            weighted_momentum=weighted,
            mdd20=mdd20,
            ma20=ma(closes, 20),
            ma60=ma(closes, 60),
        )
        rows.append(out)

    if not rows:
        return []

    mom_scores = percentile_scores([row.weighted_momentum for row in rows])
    rs_scores = percentile_scores([row.ret20 for row in rows])
    for row, mom_score, rs_score in zip(rows, mom_scores, rs_scores):
        row.momentum_score = mom_score
        row.rs_score = rs_score
        row.drawdown_score = drawdown_score(row.mdd20)
        # Historical ETF premium is not reliably available from the live data source.
        # Use a neutral premium score and disable historical premium veto.
        row.premium_score = 70.0

    by_momentum = sorted(rows, key=lambda item: item.weighted_momentum, reverse=True)
    for idx, row in enumerate(by_momentum):
        next_mom = by_momentum[idx + 1].weighted_momentum if idx + 1 < len(by_momentum) else row.weighted_momentum
        row.gap_score = gap_score_from_pct(max(0.0, row.weighted_momentum - next_mom))

    for row in rows:
        row.final_score = (
            row.momentum_score * 0.35
            + row.premium_score * 0.15
            + row.drawdown_score * 0.15
            + row.rs_score * 0.25
            + row.gap_score * 0.10
        )
        if row.mdd20 <= -15:
            row.reasons.append(f"20日最大回撤{abs(row.mdd20):.1f}%>15%")
        if row.mdd20 <= -12:
            row.clear_reasons.append(f"20日回撤{abs(row.mdd20):.1f}%>12%")
        if row.amount_yi < MIN_AMOUNT_YI:
            row.reasons.append(f"成交额{row.amount_yi:.2f}亿偏低")
        if row.weighted_momentum <= 0:
            row.reasons.append("动量为负")
        if row.ret20 <= 0:
            row.reasons.append("20日动量为负")
        if row.close < row.ma20:
            row.reasons.append("未站上MA20")
        if row.close < row.ma20 and row.ret10 < 0:
            row.clear_reasons.append("跌破MA20且10日动量转负")
        blocked = row.mdd20 <= -15 or row.amount_yi < MIN_AMOUNT_YI or bool(row.clear_reasons)
        row.eligible = (not blocked) and row.weighted_momentum > 0 and row.ret20 > 0 and row.close >= row.ma20

    ranked = sorted(rows, key=lambda item: item.final_score, reverse=True)
    for idx, row in enumerate(ranked, 1):
        row.rank = idx
    assign_targets(ranked)
    return ranked


def assign_targets(ranked: list[HistRow]) -> None:
    used_groups: set[str] = set()
    group_counts: dict[str, int] = {}
    eligible: list[HistRow] = []
    for row in ranked:
        row.target_pct = 0
        row.position_note = ""
        if not row.eligible:
            continue
        group = rotation_group(row)
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in used_groups:
            row.position_note = f"同类第{group_counts[group]}未入选"
            continue
        used_groups.add(group)
        eligible.append(row)

    if not eligible:
        return
    top = eligible[0]
    second = eligible[1] if len(eligible) > 1 else None
    third = eligible[2] if len(eligible) > 2 else None
    gap = top.final_score - second.final_score if second else top.final_score

    if top.final_score >= 90 and gap >= 10:
        top.target_pct = 60
        if second and second.final_score >= 75:
            second.target_pct = 20
    elif top.final_score >= 85:
        top.target_pct = 50
        if second and second.final_score >= 75:
            second.target_pct = 25
        if third and gap < 8 and third.final_score >= 72:
            third.target_pct = 10
    elif top.final_score >= 75:
        top.target_pct = 40
        if second and second.final_score >= 70:
            second.target_pct = 20
    elif top.final_score >= 70:
        top.target_pct = 20

    for row in ranked:
        if row.target_pct <= 0:
            continue
        cap = row.target_pct
        if row.ret20 >= 30:
            cap = min(cap, 45)
            row.reasons.append("20日动量过热，衰减锁仓")
        if row.mdd20 <= -10:
            cap = min(cap, 45)
            row.reasons.append("回撤接近警戒")
        row.target_pct = cap
        row.position_note = f"{cap}%"


def common_calendar(histories: dict[str, list[dict[str, Any]]]) -> list[str]:
    bench = histories.get("IDX_HS300")
    if bench:
        return [str(row["date"]) for row in bench]
    longest = max(histories.values(), key=len)
    return [str(row["date"]) for row in longest]


def price_at(histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]], code: str, date: str, field: str) -> float | None:
    idx = indexes.get(code, {}).get(date)
    if idx is None:
        return None
    price = fnum(histories[code][idx].get(field))
    return price if price > 0 else None


def portfolio_equity(cash: float, positions: dict[str, float], histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]], date: str, field: str) -> float:
    equity = cash
    for code, shares in positions.items():
        price = price_at(histories, indexes, code, date, field)
        if price is not None:
            equity += shares * price
    return equity


def max_dd(equity: list[float]) -> tuple[float, int, int]:
    peak = -math.inf
    peak_idx = 0
    worst = 0.0
    worst_peak = 0
    worst_idx = 0
    for idx, value in enumerate(equity):
        if value > peak:
            peak = value
            peak_idx = idx
        if peak > 0:
            dd = value / peak - 1
            if dd < worst:
                worst = dd
                worst_peak = peak_idx
                worst_idx = idx
    return worst, worst_peak, worst_idx


def index_features(code: str, date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> dict[str, float] | None:
    idx = indexes.get(code, {}).get(date)
    if idx is None or idx < 60:
        return None
    rows = histories.get(code, [])[: idx + 1]
    closes = [fnum(row["close"]) for row in rows]
    if len(closes) < 61 or closes[-21] <= 0 or closes[-61] <= 0:
        return None
    return {
        "close": closes[-1],
        "ma20": ma(closes, 20),
        "ma60": ma(closes, 60),
        "ret20": closes[-1] / closes[-21] - 1,
        "ret60": closes[-1] / closes[-61] - 1,
    }


def market_regime(date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> tuple[str, str]:
    hs300 = index_features("IDX_HS300", date, histories, indexes)
    chinext = index_features("IDX_CHINEXT", date, histories, indexes)
    sci50 = index_features("IDX_SCI50", date, histories, indexes)
    if not hs300:
        return "unknown", "沪深300历史不足"
    growth = [item for item in [chinext, sci50] if item]
    growth_ret20 = statistics.mean([item["ret20"] for item in growth]) if growth else 0.0
    growth_above_ma20 = any(item["close"] >= item["ma20"] for item in growth)
    strong = (
        (hs300["close"] >= hs300["ma20"] >= hs300["ma60"] and hs300["ret20"] > 0)
        or (growth_ret20 > 0.05 and growth_above_ma20)
    )
    neutral = (
        (hs300["close"] >= hs300["ma60"] and hs300["ret20"] > -0.04)
        or (hs300["close"] >= hs300["ma20"] and hs300["ret60"] > -0.06)
        or (growth_ret20 > -0.03 and growth_above_ma20)
    )
    note = (
        f"hs300_ret20={hs300['ret20']*100:.1f}%, hs300_ret60={hs300['ret60']*100:.1f}%, "
        f"growth_ret20={growth_ret20*100:.1f}%"
    )
    if strong:
        return "strong", note
    if neutral:
        return "neutral", note
    return "weak", note


def is_weekly_rebalance(dates: list[str], i: int, start_idx: int) -> bool:
    trade_date = dt.date.fromisoformat(dates[i + 1])
    if i == start_idx:
        return True
    previous_trade_date = dt.date.fromisoformat(dates[i])
    return trade_date.isocalendar()[:2] != previous_trade_date.isocalendar()[:2]


def simulate(histories: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = common_calendar(histories)
    end_date = dates[-1]
    start_cutoff = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * BACKTEST_YEARS))).isoformat()
    start_idx = next(i for i, date in enumerate(dates) if date >= start_cutoff)
    # Need one previous signal day and one next trade day.
    start_idx = max(start_idx, 61)
    cash = INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    cooldown_until: dict[str, int] = {}
    entry_index: dict[str, int] = {}

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        ranked = build_signal_for_date(signal_date, trade_date, histories, indexes)
        row_by_code = {row.code: row for row in ranked}
        top_snapshot = [row for row in ranked[:5]]
        weekly_rebalance = is_weekly_rebalance(dates, i, start_idx)
        regime, regime_note = market_regime(signal_date, histories, indexes)
        equity_open = portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        targets: dict[str, float] = {}
        trade_reasons: dict[str, str] = {}

        if regime == "weak":
            targets = {}
            for code in positions:
                trade_reasons[code] = "market_weak_force_cash"
        elif weekly_rebalance:
            if regime in ALLOWED_OPEN_REGIMES:
                for row in ranked:
                    if row.target_pct <= 0:
                        continue
                    trade_idx = i + 1
                    if row.code not in positions and cooldown_until.get(row.code, -1) >= trade_idx:
                        row.position_note = f"冷却中至{dates[cooldown_until[row.code]]}"
                        continue
                    targets[row.code] = row.target_pct / 100
                    trade_reasons[row.code] = "weekly_rebalance"
                for code, shares in positions.items():
                    held_days = (i + 1) - entry_index.get(code, i + 1)
                    if code not in targets and held_days < MIN_HOLD_TRADING_DAYS:
                        open_price = price_at(histories, indexes, code, trade_date, "open")
                        if open_price is not None and equity_open > 0:
                            targets[code] = shares * open_price / equity_open
                            trade_reasons[code] = f"min_hold_{held_days}d"
            else:
                for code, shares in positions.items():
                    open_price = price_at(histories, indexes, code, trade_date, "open")
                    if open_price is not None and equity_open > 0:
                        targets[code] = shares * open_price / equity_open
                        trade_reasons[code] = f"market_{regime}_hold_only"
        else:
            for code, shares in positions.items():
                open_price = price_at(histories, indexes, code, trade_date, "open")
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
                    "weekly_rebalance": weekly_rebalance,
                    "market_regime": regime,
                    "market_note": regime_note,
                    "top1": top_snapshot[0].label,
                    "top1_score": f"{top_snapshot[0].final_score:.2f}",
                    "targets": ";".join(f"{row.label}:{int(targets[row.code] * 100)}%" for row in ranked if row.code in targets),
                    "forced_clear": ";".join(forced_clear),
                    "top5": ";".join(f"{row.label}:{row.final_score:.1f}" for row in top_snapshot),
                }
            )

        trade_happened = False

        # Sell or reduce first.
        for code in list(positions):
            open_price = price_at(histories, indexes, code, trade_date, "open")
            if open_price is None:
                continue
            current_value = positions[code] * open_price
            target_value = equity_open * targets.get(code, 0.0)
            diff_value = current_value - target_value
            if diff_value <= equity_open * REBALANCE_THRESHOLD and targets.get(code, 0.0) > 0:
                continue
            if diff_value <= 0:
                continue
            shares_to_sell = min(positions[code], diff_value / open_price)
            cash += shares_to_sell * open_price * (1 - FEE_RATE)
            positions[code] -= shares_to_sell
            if positions[code] <= 1e-8:
                del positions[code]
                cooldown_until[code] = min(len(dates) - 1, i + 1 + COOLDOWN_TRADING_DAYS)
                entry_index.pop(code, None)
            trade_happened = True
            trades.append(
                {
                    "date": trade_date,
                    "code": code,
                    "side": "SELL",
                    "price": f"{open_price:.4f}",
                    "shares": f"{shares_to_sell:.4f}",
                    "value": f"{shares_to_sell * open_price:.2f}",
                    "fee": f"{shares_to_sell * open_price * FEE_RATE:.2f}",
                    "signal_date": signal_date,
                    "reason": trade_reasons.get(code, "reduce_or_exit"),
                }
            )

        # Buy or increase.
        for code, weight in targets.items():
            open_price = price_at(histories, indexes, code, trade_date, "open")
            if open_price is None:
                continue
            current_value = positions.get(code, 0.0) * open_price
            target_value = equity_open * weight
            diff_value = target_value - current_value
            if diff_value <= equity_open * REBALANCE_THRESHOLD:
                continue
            fill_price = open_price * (1 + FEE_RATE)
            spend = min(cash, diff_value)
            if spend <= 0:
                continue
            shares = spend / fill_price
            was_new_position = code not in positions or positions.get(code, 0.0) <= 1e-8
            positions[code] = positions.get(code, 0.0) + shares
            if was_new_position:
                entry_index[code] = i + 1
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
                    "fee": f"{spend * FEE_RATE:.2f}",
                    "signal_date": signal_date,
                    "reason": trade_reasons.get(code, "weekly_rebalance"),
                }
            )

        equity_close = portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        invested_value = equity_close - cash
        equity_rows.append(
            {
                "date": trade_date,
                "equity": equity_close,
                "cash": cash,
                "exposure": invested_value / equity_close if equity_close > 0 else 0.0,
                "positions": ";".join(sorted(positions)),
                "trade_happened": trade_happened,
                "weekly_rebalance": weekly_rebalance,
                "market_regime": regime,
            }
        )

    return equity_rows, trades, signal_rows


def benchmark_series(histories: dict[str, list[dict[str, Any]]], code: str, dates: list[str]) -> list[dict[str, Any]]:
    rows = histories.get(code, [])
    idx = {str(row["date"]): i for i, row in enumerate(rows)}
    start_price = None
    out: list[dict[str, Any]] = []
    for date in dates:
        row_idx = idx.get(date)
        if row_idx is None:
            continue
        close = fnum(rows[row_idx]["close"])
        if close <= 0:
            continue
        if start_price is None:
            start_price = close
        out.append({"date": date, "equity": INITIAL_CASH * close / start_price})
    return out


def perf_metrics(equity_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not equity_rows:
        return {}
    equities = [float(row["equity"]) for row in equity_rows]
    dates = [str(row["date"]) for row in equity_rows]
    total_return = equities[-1] / equities[0] - 1
    days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days or 1
    cagr = (equities[-1] / equities[0]) ** (365.25 / days) - 1
    returns = [equities[i] / equities[i - 1] - 1 for i in range(1, len(equities))]
    vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = (statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252)) if len(returns) > 1 and statistics.stdev(returns) > 0 else 0.0
    mdd, peak_idx, trough_idx = max_dd(equities)
    exposures = [float(row.get("exposure", 1.0)) for row in equity_rows]
    yearly: dict[str, float] = {}
    years = sorted({date[:4] for date in dates})
    for year in years:
        year_rows = [(date, equity) for date, equity in zip(dates, equities) if date.startswith(year)]
        if len(year_rows) >= 2:
            yearly[year] = year_rows[-1][1] / year_rows[0][1] - 1
    return {
        "start": dates[0],
        "end": dates[-1],
        "start_equity": equities[0],
        "end_equity": equities[-1],
        "total_return": total_return,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "max_dd_start": dates[peak_idx],
        "max_dd_end": dates[trough_idx],
        "avg_exposure": statistics.mean(exposures) if exposures else 0.0,
        "trading_days": len(equity_rows),
        "yearly": yearly,
    }


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


def build_summary(
    metrics: dict[str, Any],
    hs300_metrics: dict[str, Any],
    cyb_metrics: dict[str, Any],
    k50_metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    errors: dict[str, str],
) -> str:
    lines: list[str] = []
    lines.append("# ETF动量轮动3年回测 V3")
    lines.append("")
    lines.append(f"区间：{metrics['start']} 至 {metrics['end']}  |  标的池：{len(ETF_UNIVERSE)}只行业/主题ETF")
    lines.append("")
    lines.append("## 规则")
    lines.append("- 每日收盘后用当日及之前数据评分；普通轮动只在每周首个交易日开盘调仓。")
    lines.append("- 持仓若触发20日最大回撤超过12%或跌破MA20且10日动量转负，次日强制清仓，不等周频。")
    lines.append("- 只允许 strong 市场开新仓；neutral 只持有不新开；weak 次日强制降到现金。")
    lines.append(f"- 持仓至少{MIN_HOLD_TRADING_DAYS}个交易日不做普通轮动卖出，除非触发清仓/弱市现金规则。")
    lines.append(f"- ETF完整卖出后冷却{COOLDOWN_TRADING_DAYS}个交易日，冷却期内不重新买回同一ETF。")
    lines.append("- 五维：10/20日动量、历史溢价中性分、20日回撤、相对强度、动量差距。")
    lines.append("- 同主题ETF只取最强；单只最高60%，最多3只；20日动量>30%仓位上限45%。")
    lines.append("- 成本：ETF无印花税，单边佣金0.03% + 滑点0.05%；小于组合净值2%的微调不交易。")
    lines.append("- 注意：历史ETF溢价数据未稳定还原，本次不做历史溢价否决，实盘日报仍会使用实时溢价。")
    lines.append("")
    lines.append("## 收益")
    lines.append("| 项目 | 轮动策略 | 沪深300指数 | 创业板指 | 科创50指数 |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| 总收益 | {fmt_pct(metrics['total_return'])} | {fmt_pct(hs300_metrics['total_return'])} | "
        f"{fmt_pct(cyb_metrics['total_return'])} | {fmt_pct(k50_metrics['total_return'])} |"
    )
    lines.append(
        f"| 年化收益 | {fmt_pct(metrics['cagr'])} | {fmt_pct(hs300_metrics['cagr'])} | "
        f"{fmt_pct(cyb_metrics['cagr'])} | {fmt_pct(k50_metrics['cagr'])} |"
    )
    lines.append(
        f"| 最大回撤 | {fmt_pct(metrics['max_drawdown'])} | {fmt_pct(hs300_metrics['max_drawdown'])} | "
        f"{fmt_pct(cyb_metrics['max_drawdown'])} | {fmt_pct(k50_metrics['max_drawdown'])} |"
    )
    lines.append(
        f"| 波动率 | {fmt_pct(metrics['volatility'])} | {fmt_pct(hs300_metrics['volatility'])} | "
        f"{fmt_pct(cyb_metrics['volatility'])} | {fmt_pct(k50_metrics['volatility'])} |"
    )
    lines.append(
        f"| Sharpe | {metrics['sharpe']:.2f} | {hs300_metrics['sharpe']:.2f} | "
        f"{cyb_metrics['sharpe']:.2f} | {k50_metrics['sharpe']:.2f} |"
    )
    lines.append("")
    lines.append("## 年度收益")
    lines.append("| 年份 | 轮动策略 | 沪深300指数 | 创业板指 | 科创50指数 |")
    lines.append("|---|---:|---:|---:|---:|")
    years = sorted(set(metrics["yearly"]) | set(hs300_metrics["yearly"]) | set(cyb_metrics["yearly"]) | set(k50_metrics["yearly"]))
    for year in years:
        lines.append(
            f"| {year} | {fmt_pct(metrics['yearly'].get(year, 0.0))} | {fmt_pct(hs300_metrics['yearly'].get(year, 0.0))} | "
            f"{fmt_pct(cyb_metrics['yearly'].get(year, 0.0))} | {fmt_pct(k50_metrics['yearly'].get(year, 0.0))} |"
        )
    lines.append("")
    lines.append("## 交易行为")
    buy_count = sum(1 for trade in trades if trade["side"] == "BUY")
    sell_count = sum(1 for trade in trades if trade["side"] == "SELL")
    rebal_days = len({trade["date"] for trade in trades})
    lines.append(f"- 交易日数：{metrics['trading_days']}，发生调仓的交易日：{rebal_days}。")
    lines.append(f"- 买入 {buy_count} 笔，卖出 {sell_count} 笔。")
    lines.append(f"- 平均仓位暴露：{fmt_pct(metrics['avg_exposure'])}。")
    lines.append(f"- 最大回撤区间：{metrics['max_dd_start']} -> {metrics['max_dd_end']}。")
    if signal_rows:
        recent = signal_rows[-1]
        lines.append(f"- 最近一次目标仓位：{recent['trade_date']} 执行，{recent['targets'] or '空仓'}。")
    if errors:
        lines.append(f"- 数据拉取失败：{len(errors)}只，已跳过。")
    lines.append("")
    lines.append("## 判断")
    if metrics["total_return"] > hs300_metrics["total_return"] and metrics["max_drawdown"] > hs300_metrics["max_drawdown"]:
        lines.append("- 这版轮动在收益上跑赢沪深300，但回撤控制仍要看与成长类基准的对比。")
    elif metrics["total_return"] > hs300_metrics["total_return"]:
        lines.append("- 这版轮动跑赢沪深300，同时回撤不比沪深300更差，初步值得继续压测。")
    else:
        lines.append("- 这版轮动没有跑赢沪深300，说明单纯短动量轮动还需要加入市场环境/趋势过滤或降低换手。")
    lines.append("- 下一步应做参数压力测试：周频调仓、衰减阈值25/30/35%、回撤清仓10/12/15%、成本翻倍。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = load_history()
    equity_rows, trades, signal_rows = simulate(histories)
    dates = [row["date"] for row in equity_rows]
    hs300_rows = benchmark_series(histories, "IDX_HS300", dates)
    cyb_rows = benchmark_series(histories, "IDX_CHINEXT", dates)
    k50_rows = benchmark_series(histories, "IDX_SCI50", dates)
    metrics = perf_metrics(equity_rows)
    hs300_metrics = perf_metrics(hs300_rows)
    cyb_metrics = perf_metrics(cyb_rows)
    k50_metrics = perf_metrics(k50_rows)

    summary = build_summary(metrics, hs300_metrics, cyb_metrics, k50_metrics, trades, signal_rows, errors)
    summary_path = OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_summary_latest.md"
    equity_path = OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_equity_latest.csv"
    trades_path = OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_trades_latest.csv"
    signals_path = OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_signals_latest.csv"
    metrics_path = OUT_DIR / "etf_rotation_3y_v3_strong_only_cash_minhold_metrics_latest.json"

    summary_path.write_text(summary, encoding="utf-8")
    write_csv(equity_path, equity_rows)
    write_csv(trades_path, trades)
    write_csv(signals_path, signal_rows)
    metrics_path.write_text(
        json.dumps(
            {
                "strategy": metrics,
                "hs300": hs300_metrics,
                "chinext": cyb_metrics,
                "sci50": k50_metrics,
                "data_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {equity_path}")
    print(f"wrote {trades_path}")
    print(f"wrote {signals_path}")


if __name__ == "__main__":
    main()
