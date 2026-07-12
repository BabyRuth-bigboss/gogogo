#!/usr/bin/env python3
"""Independent T0 reproduction of the documented V5 concentrated ETF strategy.

This script never imports or runs Vibe-Trading.  It transcribes the published
V5 rule onto the project's audited Tencent cache and emits both the documented
four-asset defensive sleeve and the two-asset sleeve the Vibe source can
actually populate without fetching 511010/518880.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
START = os.environ.get("V5_T0_START", "2015-07-01")
OUT_DIR = Path(os.environ.get("V5_T0_OUT_DIR", str(ROOT / "a_stock_daily_workflow/etf_rotation/backtests/v5_concentrated_dual_sleeve_t0_2015_2026")))
NOTE_PATH = Path(os.environ.get("V5_T0_NOTE_PATH", "/Users/yansenz/Documents/note/ETF_V5集中轮动_T0独立复测_2015-07至2026-07.md"))

DEFENSIVE_ETFS = [
    {"code": "511010", "theme": "防守", "label": "国债ETF"},
    {"code": "518880", "theme": "防守", "label": "黄金ETF"},
]
MARKET_CODES = ("IDX_HS300", "IDX_CHINEXT", "IDX_SCI50")
VIBE_IMPLEMENTED_DEFENSIVE = {"513100", "513500"}
DOCUMENTED_DEFENSIVE = {"511010", "518880", "513100", "513500"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("v5_t0_dynamic_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")
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


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def monthly_signal_indices(dates: list[str], start_idx: int, trading_day: int = 1) -> set[int]:
    """Vibe semantics: signal on the Nth trading day, then execute next open."""
    months: dict[tuple[int, int], list[int]] = {}
    for index in range(start_idx, len(dates) - 1):
        day = dt.date.fromisoformat(dates[index])
        months.setdefault((day.year, day.month), []).append(index)
    return {indices[min(trading_day - 1, len(indices) - 1)] for indices in months.values() if indices}


def ranks(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (float(row[field]), str(row["code"])))
    if len(ordered) <= 1:
        return {str(row["code"]): 0.5 for row in ordered}
    return {str(row["code"]): index / (len(ordered) - 1) for index, row in enumerate(ordered)}


def raw_market_bad(signal_date: str, histories: dict[str, list[dict[str, Any]]], indexes: dict[str, dict[str, int]]) -> tuple[bool, str]:
    weak: list[str] = []
    notes: list[str] = []
    for code in MARKET_CODES:
        index = indexes.get(code, {}).get(signal_date)
        if index is None or index < 139:
            continue
        closes = [base.fnum(row["close"]) for row in histories[code][: index + 1]]
        if min(closes[-140:]) <= 0:
            continue
        price = closes[-1]
        ma120 = v3.ma(closes, 120)
        ma50 = v3.ma(closes, 50)
        old_ma120 = sum(closes[-140:-20]) / 120
        slope = ma120 / old_ma120 - 1 if old_ma120 > 0 else 0.0
        is_weak = price < ma120 and (slope < -0.005 or ma50 < ma120)
        if is_weak:
            weak.append(code)
        notes.append(f"{code}:p_ma={price/ma120-1:+.2%},slope={slope:+.2%},dc={ma50 < ma120}")
    return bool(weak), f"raw_weak={','.join(weak) or '-'}|" + ";".join(notes)


def select_targets(
    market_bad: bool,
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    offensive_codes: list[str],
    defensive_codes: set[str],
) -> tuple[dict[str, float], str]:
    if market_bad:
        candidates: list[dict[str, Any]] = []
        for code in defensive_codes:
            index = indexes.get(code, {}).get(signal_date)
            if index is None or index < 126:
                continue
            closes = [base.fnum(row["close"]) for row in histories[code][: index + 1]]
            if min(closes[-126:]) <= 0:
                continue
            price = closes[-1]
            ma120 = v3.ma(closes, 120)
            ret126 = price / closes[-126] - 1 if closes[-126] > 0 else 0.0
            if price > ma120 and ret126 > 0:
                candidates.append({"code": code, "score": ret126})
        candidates.sort(key=lambda row: (row["score"], row["code"]), reverse=True)
        selected = candidates[:1]
        return ({selected[0]["code"]: 1.0} if selected else {}), "defensive:" + ";".join(f"{row['code']}:{row['score']:.3f}" for row in selected)

    candidates = []
    for code in offensive_codes:
        index = indexes.get(code, {}).get(signal_date)
        if index is None or index < 126:
            continue
        closes = [base.fnum(row["close"]) for row in histories[code][: index + 1]]
        if min(closes[-126:]) <= 0:
            continue
        price = closes[-1]
        rs36 = 0.5 * (price / closes[-63] - 1) + 0.5 * (price / closes[-126] - 1)
        breakout = price / max(closes[-120:]) if max(closes[-120:]) > 0 else 0.0
        candidates.append({"code": code, "rs36": rs36, "breakout": breakout})
    rs_rank = ranks(candidates, "rs36")
    breakout_rank = ranks(candidates, "breakout")
    for row in candidates:
        row["score"] = 0.80 * rs_rank[row["code"]] + 0.20 * breakout_rank[row["code"]]
    candidates.sort(key=lambda row: (row["score"], row["code"]), reverse=True)
    selected = candidates[:1]
    return ({selected[0]["code"]: 1.0} if selected else {}), "offensive:" + ";".join(f"{row['code']}:{row['score']:.3f}" for row in selected)


def simulate(name: str, histories: dict[str, list[dict[str, Any]]], defensive_codes: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): index for index, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    start_idx = max(next(index for index, date in enumerate(dates) if date >= START), 252)
    rebal_indices = monthly_signal_indices(dates, start_idx, 1)
    offensive_codes = [item["code"] for item in base.live.ETF_UNIVERSE if item["code"] not in {"511010", "518880"}]
    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    current_targets: dict[str, float] = {}
    previous_bad = False
    last_switch = -999

    for index in range(start_idx, len(dates) - 1):
        signal_date = dates[index]
        trade_date = dates[index + 1]
        raw_bad, market_note = raw_market_bad(signal_date, histories, indexes)
        in_cooldown = index - last_switch < 5
        market_bad = previous_bad
        switched = False
        if not in_cooldown:
            if raw_bad and not previous_bad:
                market_bad = True
                last_switch = index
                switched = True
            elif not raw_bad and previous_bad:
                market_bad = False
                last_switch = index
                switched = True
        rebalance = index in rebal_indices
        if rebalance or switched:
            current_targets, selection = select_targets(market_bad, signal_date, histories, indexes, offensive_codes, defensive_codes)
        else:
            selection = "hold"
        open_equity = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        if rebalance or switched:
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
            if difference <= open_equity * v3.REBALANCE_THRESHOLD and targets.get(code, 0.0) > 0:
                continue
            if difference <= 0:
                continue
            shares = min(positions[code], difference / price)
            cash += shares * price * (1 - base.FEE_RATE)
            positions[code] -= shares
            if positions[code] <= 1e-8:
                del positions[code]
            trades.append({"date": trade_date, "code": code, "side": "SELL", "price": f"{price:.4f}", "shares": f"{shares:.4f}", "signal_date": signal_date, "reason": "regime_switch" if switched else "monthly_rebalance"})
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
            trades.append({"date": trade_date, "code": code, "side": "BUY", "price": f"{price:.4f}", "shares": f"{shares:.4f}", "signal_date": signal_date, "reason": "regime_switch" if switched else "monthly_rebalance"})
        close_equity = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        equity.append({"date": trade_date, "equity": close_equity, "cash": cash, "exposure": (close_equity - cash) / close_equity if close_equity else 0.0, "positions": ";".join(sorted(positions)), "market_bad": market_bad})
        signals.append({"signal_date": signal_date, "trade_date": trade_date, "rebalance": rebalance, "regime_switch": switched, "market_bad": market_bad, "market_note": market_note, "selection": selection, "targets": ";".join(f"{code}:{weight:.0%}" for code, weight in targets.items()) if rebalance or switched else ""})
        previous_bad = market_bad
    return equity, trades, signals


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
        raise RuntimeError(errors)
    variants = [
        ("documented_4def", "文档V5：国债+黄金+纳指+标普", DOCUMENTED_DEFENSIVE),
        ("vibe_source_2def", "源码实际可用：纳指+标普", VIBE_IMPLEMENTED_DEFENSIVE),
    ]
    rows: list[dict[str, Any]] = []
    for identifier, label, defensive in variants:
        equity, trades, signals = simulate(identifier, histories, defensive)
        metrics = v3.perf_metrics(equity)
        rows.append({"id": identifier, "label": label, "total_return": metrics["total_return"], "cagr": metrics["cagr"], "max_drawdown": metrics["max_drawdown"], "sharpe": metrics["sharpe"], "avg_exposure": metrics["avg_exposure"], "trades": len(trades), "start": metrics["start"], "end": metrics["end"]})
        prefix = OUT_DIR / identifier
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
    write_csv(OUT_DIR / "v5_t0_metrics_latest.csv", rows)
    lines = [
        "# V5集中ETF轮动：T0独立复测", "",
        "- 本回测没有调用 Vibe-Trading；只使用本项目缓存的腾讯日线、拆分连续化调整、信号日收盘后计算及下一交易日开盘成交。",
        f"- 区间：{START} 至 {os.environ['V2_DATA_END_DATE']}；单边成本：佣金万0.5 + 滑点0.10% = {base.FEE_RATE * 100:.3f}%。",
        "- 进攻：41只ETF横截面排名，RS36 的百分位80% + 近120日高点百分位20%，Top1满仓。",
        "- 强弱：任一沪深300/创业板/科创50出现价格低于MA120且MA120 20日斜率<-0.5%或MA50<MA120，立即转弱；状态切换后冷却5个交易日。",
        "- 弱市：合格防守资产需价格高于MA120且126日收益为正，按126日收益取Top1。",
        "", "| 版本 | 区间 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 成交笔数 |", "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(f"| {row['label']} | {row['start']}~{row['end']} | {pct(row['total_return'])} | {pct(row['cagr'])} | {pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {pct(row['avg_exposure'])} | {row['trades']} |")
    lines.extend([
        "", "## 与24.71%记录的口径差异", "",
        "- Vibe当前配置写的是 `execution_price: close`，而策略信号本身使用当日收盘价；这不是严格的次日开盘执行。T0复测使用次日开盘，避免同收盘价信号/成交的时间重叠。",
        "- Vibe源码的防守资产列表包含国债/黄金，但其 `all_codes` 未加载这两只；实际可选防守只剩纳指/标普。故同时报告文档版4资产和源码实际版2资产。",
        "- Vibe当前 `equity.csv` 实际起始于2018-04-12；T0独立复测从2015-07开始并使用252日预热，因此不能把两者年化直接视作同一份样本。",
        "", f"- 指标CSV：`{OUT_DIR / 'v5_t0_metrics_latest.csv'}`", "",
    ])
    report = "\n".join(lines)
    (OUT_DIR / "v5_t0_summary_latest.md").write_text(report, encoding="utf-8")
    NOTE_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
