#!/usr/bin/env python3
"""Test daily emergency exits without changing the candidate's monthly selection cadence."""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_fast_exit_2015_2026"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate = load_module("fast_exit_candidate", ROOT / "scripts/research_dual_sleeve_neighborhood.py")
base, dual, lab, v3 = candidate.base, candidate.dual, candidate.lab, candidate.v3
CONFIG = {
    "id": "rs36_breakout_top3_month_start_top_heavy",
    "label": "RS36+近120日高点 Top3首位加权｜月初｜3/3强市｜弱市国债+黄金Top1",
    "model": "rs36_breakout", "filter": "custom", "top_n": 3, "schedule": "month_start", "weighting": "equal",
    "weight_style": "top_heavy", "fee_rate": base.FEE_RATE, "start_date": dual.START, "risk_label": "无",
}
TRIGGERS = ["monthly_only", "ma60_2of3", "drop5_m5", "drop5_m7", "ma60_or_drop5_m5"]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def defensive_targets(signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> dict[str, float]:
    rows = []
    for row in lab.etf_features(signal_date, histories, indexes):
        if row["code"] not in dual.DEFENSIVE_POOLS["bond_gold"]["codes"]:
            continue
        if dual.defensive_eligible(row, "ma120_ret6"):
            row["score"] = row["ret63"] * 0.5 + row["ret126"] * 0.5
            rows.append(row)
    selected = sorted(rows, key=lambda row: row["score"], reverse=True)[:1]
    return {row["code"]: 1.0 for row in selected}


def emergency(trigger: str, signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> tuple[bool, str]:
    if trigger == "monthly_only":
        return False, "monthly_only"
    states = []
    for code in ("IDX_HS300", "IDX_CHINEXT", "IDX_SCI50"):
        idx = indexes.get(code, {}).get(signal_date)
        if idx is None or idx < 60:
            continue
        closes = [float(row["close"]) for row in histories[code][: idx + 1]]
        states.append({"below60": closes[-1] < v3.ma(closes, 60), "ret5": closes[-1] / closes[-6] - 1 if len(closes) > 5 else 0.0})
    if len(states) < 2:
        return False, "index_history_insufficient"
    below = sum(state["below60"] for state in states) >= 2
    threshold = -0.05 if trigger.endswith("m5") else -0.07
    drop = sum(state["ret5"] <= threshold for state in states) >= 2
    active = (trigger == "ma60_2of3" and below) or (trigger.startswith("drop5") and drop) or (trigger == "ma60_or_drop5_m5" and (below or drop))
    return active, f"below60={sum(state['below60'] for state in states)}/{len(states)};drop5={sum(state['ret5'] <= threshold for state in states)}/{len(states)}"


def simulate(trigger: str, histories: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): index for index, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    start_idx = max(next(index for index, date in enumerate(dates) if date >= dual.START), 253)
    rebal = lab.build_rebalance_indices(dates, start_idx, "month_start")
    cash, positions, current_targets, emergency_mode = v3.INITIAL_CASH, {}, {}, False
    equity, trades, signals = [], [], []
    for i in range(start_idx, len(dates) - 1):
        signal_date, trade_date = dates[i], dates[i + 1]
        is_rebal = i in rebal
        reason = "hold"
        if is_rebal:
            current_targets, info = candidate.build_targets(CONFIG, signal_date, histories, indexes)
            emergency_mode = str(info["market_note"]).startswith("defensive")
            reason = "monthly_rebalance"
        active, trigger_note = emergency(trigger, signal_date, histories, indexes)
        if active and not emergency_mode:
            current_targets = defensive_targets(signal_date, histories, indexes)
            emergency_mode = True
            reason = "fast_exit"
        open_equity = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        if is_rebal or reason == "fast_exit":
            targets = dict(current_targets)
        else:
            targets = {}
            for code, shares in positions.items():
                price = v3.price_at(histories, indexes, code, trade_date, "open")
                if price is not None and open_equity > 0:
                    targets[code] = shares * price / open_equity
        for code in list(positions):
            price = v3.price_at(histories, indexes, code, trade_date, "open")
            if price is None:
                continue
            difference = positions[code] * price - open_equity * targets.get(code, 0.0)
            if difference <= open_equity * v3.REBALANCE_THRESHOLD and targets.get(code, 0) > 0:
                continue
            if difference <= 0:
                continue
            shares = min(positions[code], difference / price)
            cash += shares * price * (1 - base.FEE_RATE)
            positions[code] -= shares
            if positions[code] <= 1e-8:
                del positions[code]
            trades.append({"date": trade_date, "code": code, "side": "SELL", "price": f"{price:.4f}", "shares": f"{shares:.4f}", "signal_date": signal_date, "reason": reason})
        for code, weight in targets.items():
            price = v3.price_at(histories, indexes, code, trade_date, "open")
            if price is None:
                continue
            difference = open_equity * weight - positions.get(code, 0.0) * price
            if difference <= open_equity * v3.REBALANCE_THRESHOLD:
                continue
            spend = min(cash, difference)
            if spend <= 0:
                continue
            shares = spend / (price * (1 + base.FEE_RATE))
            positions[code] = positions.get(code, 0.0) + shares
            cash -= spend
            trades.append({"date": trade_date, "code": code, "side": "BUY", "price": f"{price:.4f}", "shares": f"{shares:.4f}", "signal_date": signal_date, "reason": reason})
        close_equity = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        equity.append({"date": trade_date, "equity": close_equity, "cash": cash, "exposure": (close_equity-cash)/close_equity if close_equity else 0.0, "positions": ";".join(sorted(positions))})
        signals.append({"signal_date": signal_date, "trade_date": trade_date, "trigger": trigger, "trigger_note": trigger_note, "emergency_mode": emergency_mode, "reason": reason, "targets": ";".join(f"{code}:{weight:.1%}" for code, weight in targets.items()) if reason != "hold" else ""})
    return equity, trades, signals


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for etf in dual.DEFENSIVE_ETFS:
        if etf["code"] not in {item["code"] for item in base.live.ETF_UNIVERSE}:
            base.live.ETF_UNIVERSE.append(etf)
        if etf["code"] not in {item["code"] for item in v3.ETF_UNIVERSE}:
            v3.ETF_UNIVERSE.append(etf)
    base.KLINE_LIMIT = 1800
    histories, errors = base.load_history()
    if errors:
        raise RuntimeError(errors)
    rows = []
    for trigger in TRIGGERS:
        equity, trades, signals = simulate(trigger, histories)
        metrics = v3.perf_metrics(equity)
        rows.append({"trigger": trigger, "total_return": metrics["total_return"], "cagr": metrics["cagr"], "max_drawdown": metrics["max_drawdown"], "sharpe": metrics["sharpe"], "avg_exposure": metrics["avg_exposure"], "trades": len(trades), "fast_exit_days": len({item["trade_date"] for item in signals if item["reason"] == "fast_exit"})})
        prefix = OUT_DIR / trigger
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
    write_csv(OUT_DIR / "fast_exit_metrics_latest.csv", rows)
    print(rows)


if __name__ == "__main__":
    main()
