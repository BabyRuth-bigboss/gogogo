#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DYNAMIC_SCRIPT = ROOT / "scripts/backtest_etf_dynamic_pool_5y.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_market_gate_cost_10bp_slippage"


def load_dynamic_module():
    spec = importlib.util.spec_from_file_location("etf_dynamic_pool_5y_base", DYNAMIC_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {DYNAMIC_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_dynamic_module()
lab = base.lab
v3 = base.v3


GATES: list[dict[str, str]] = [
    {
        "id": "majority_below_ma120",
        "label": "2/3指数低于MA120空仓",
        "rule": "沪深300、创业板指、科创50中至少2个收盘低于MA120，则次日开盘清仓并保持现金；恢复后等下一次策略调仓再开仓。",
    },
    {
        "id": "majority_below_ma200",
        "label": "2/3指数低于MA200空仓",
        "rule": "沪深300、创业板指、科创50中至少2个收盘低于MA200，则次日开盘清仓并保持现金；恢复后等下一次策略调仓再开仓。",
    },
    {
        "id": "majority_below_ma120_and_ma200",
        "label": "2/3指数同时低于MA120和MA200空仓",
        "rule": "沪深300、创业板指、科创50中至少2个同时低于MA120和MA200，则次日开盘清仓并保持现金；恢复后等下一次策略调仓再开仓。",
    },
]


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


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def index_ma_state(
    code: str,
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> dict[str, float] | None:
    idx = indexes.get(code, {}).get(signal_date)
    if idx is None or idx < 200:
        return None
    rows = histories[code][: idx + 1]
    closes = [base.fnum(row["close"]) for row in rows]
    if len(closes) < 201 or min(closes[-201:]) <= 0:
        return None
    return {
        "close": closes[-1],
        "ma120": v3.ma(closes, 120),
        "ma200": v3.ma(closes, 200),
    }


def market_gate_bad(
    gate_id: str,
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[bool, str]:
    states = {
        "沪深300": index_ma_state("IDX_HS300", signal_date, histories, indexes),
        "创业板": index_ma_state("IDX_CHINEXT", signal_date, histories, indexes),
        "科创50": index_ma_state("IDX_SCI50", signal_date, histories, indexes),
    }
    valid = {name: state for name, state in states.items() if state}
    if len(valid) < 2:
        return False, "index_history_insufficient"
    bad_names: list[str] = []
    for name, state in valid.items():
        below120 = state["close"] < state["ma120"]
        below200 = state["close"] < state["ma200"]
        if gate_id == "majority_below_ma120" and below120:
            bad_names.append(name)
        elif gate_id == "majority_below_ma200" and below200:
            bad_names.append(name)
        elif gate_id == "majority_below_ma120_and_ma200" and below120 and below200:
            bad_names.append(name)
    bad = len(bad_names) >= 2
    detail = []
    for name, state in valid.items():
        detail.append(
            f"{name}:MA120{state['close']/state['ma120']-1:+.1%},MA200{state['close']/state['ma200']-1:+.1%}"
        )
    return bad, f"bad={len(bad_names)}/{len(valid)}({','.join(bad_names) or '-'})|" + ";".join(detail)


def simulate_with_gate(
    strategy: dict[str, Any],
    gate: dict[str, str],
    histories: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_cutoff = str(strategy.get("start_date") or (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat())
    start_idx = next(i for i, date in enumerate(dates) if date >= start_cutoff)
    start_idx = max(start_idx, 253)
    rebal_indices = lab.build_rebalance_indices(dates, start_idx, str(strategy["schedule"]))
    fee_rate = float(strategy.get("fee_rate", base.FEE_RATE))

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    current_targets: dict[str, float] = {}

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        rebal_day = i in rebal_indices
        market_bad, market_note = market_gate_bad(gate["id"], signal_date, histories, indexes)

        if rebal_day and not market_bad:
            current_targets, info = lab.build_targets(strategy, signal_date, histories, indexes)
        elif rebal_day:
            current_targets = {}
            info = {"market_note": market_note, "ranked": [], "selected": [], "eligible_count": ""}
        else:
            info = {"market_note": market_note if market_bad else "hold", "ranked": [], "selected": [], "eligible_count": ""}

        equity_open = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "open")
        if market_bad:
            targets: dict[str, float] = {}
            current_targets = {}
        elif rebal_day:
            targets = dict(current_targets)
        else:
            targets = {}
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
                "market_gate_bad": market_bad,
                "market_note": market_note,
                "eligible_count": info.get("eligible_count", ""),
                "selected": ";".join(row["label"] for row in selected),
                "targets": lab.target_label(ranked, targets) if rebal_day or market_bad else "",
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
                    "reason": "market_gate_cash" if market_bad else "rebalance",
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
                    "reason": "rebalance",
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
                "market_gate_bad": market_bad,
            }
        )

    return equity_rows, trades, signal_rows


def labels_for_positions(position_text: str) -> str:
    labels = {item["code"]: item["label"] for item in base.live.ETF_UNIVERSE + base.live.MARKET_WATCH}
    return ";".join(f"{labels.get(code, code)}({code})" for code in position_text.split(";") if code)


def build_summary(rows: list[dict[str, Any]], base_rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ETF 5年动态池市场闸门回测")
    lines.append("")
    lines.append(f"- 生成日期：{dt.date.today().isoformat()}")
    lines.append("- 回测基础：5年真实ETF动态池，ETF满252个交易日才可入池。")
    lines.append("- 市场闸门：沪深300、创业板指、科创50中多数弱于均线时，次日开盘清仓并保持现金；恢复后等下一次策略调仓再开仓。")
    lines.append(f"- 成本：佣金万0.5 + 滑点0.10%，单边合计 {base.FEE_RATE*100:.3f}%。")
    lines.append("")

    lines.append("## 对比结果")
    lines.append("")
    lines.append("| 策略 | 闸门 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 空仓/弱市天数 | 当前持仓 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in rows:
        m = row["metrics"]
        lines.append(
            f"| {row['strategy_label']} | {row['gate_label']} | {fmt_pct(m['total_return'])} | "
            f"{fmt_pct(m['cagr'])} | {fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | "
            f"{fmt_pct(m['avg_exposure'])} | {row['trade_count']} | {row['market_bad_days']} | {row['latest_positions']} |"
        )
    lines.append("")
    lines.append("## 无闸门基准")
    lines.append("")
    lines.append("| 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in base_rows:
        m = row["metrics"]
        lines.append(
            f"| {row['strategy_label']} | {fmt_pct(m['total_return'])} | {fmt_pct(m['cagr'])} | "
            f"{fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {fmt_pct(m['avg_exposure'])} |"
        )
    lines.append("")
    lines.append("## 观察")
    lines.append("")
    rs_rows = [row for row in rows if row["strategy_id"].startswith("rs612")]
    if rs_rows:
        best_dd = max(rs_rows, key=lambda row: row["metrics"]["max_drawdown"])
        best_sharpe = max(rs_rows, key=lambda row: row["metrics"]["sharpe"])
        lines.append(
            f"- RS612 回撤最小的是 `{best_dd['gate_label']}`：最大回撤 {fmt_pct(best_dd['metrics']['max_drawdown'])}，"
            f"总收益 {fmt_pct(best_dd['metrics']['total_return'])}，平均仓位 {fmt_pct(best_dd['metrics']['avg_exposure'])}。"
        )
        lines.append(
            f"- RS612 Sharpe 最高的是 `{best_sharpe['gate_label']}`：Sharpe {best_sharpe['metrics']['sharpe']:.2f}，"
            f"最大回撤 {fmt_pct(best_sharpe['metrics']['max_drawdown'])}。"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = base.load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_date = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    all_rows: list[dict[str, Any]] = []
    base_rows: list[dict[str, Any]] = []
    for strategy_base in base.STRATEGIES:
        strategy = dict(strategy_base)
        strategy["start_date"] = start_date
        baseline_equity, baseline_trades, baseline_signals = lab.simulate_strategy(strategy, histories)
        baseline_metrics = v3.perf_metrics(baseline_equity)
        base_rows.append(
            {
                "strategy_id": strategy["id"],
                "strategy_label": strategy["label"],
                "metrics": baseline_metrics,
            }
        )

        for gate in GATES:
            config = dict(strategy)
            config["id"] = f"{strategy['id']}_{gate['id']}"
            equity, trades, signals = simulate_with_gate(config, gate, histories)
            metrics = v3.perf_metrics(equity)
            market_bad_days = sum(1 for row in signals if row.get("market_gate_bad"))
            latest_positions = labels_for_positions(str(equity[-1].get("positions", ""))) if equity else ""
            out_row = {
                "strategy_id": strategy["id"],
                "strategy_label": strategy["label"],
                "gate_id": gate["id"],
                "gate_label": gate["label"],
                "gate_rule": gate["rule"],
                "metrics": metrics,
                "trade_count": len(trades),
                "market_bad_days": market_bad_days,
                "latest_positions": latest_positions,
            }
            all_rows.append(out_row)
            prefix = OUT_DIR / config["id"]
            write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
            write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
            write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
            prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
                json.dumps({"metrics": metrics, "config": config, "gate": gate, "errors": errors}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    metrics_csv = []
    for row in all_rows:
        m = row["metrics"]
        metrics_csv.append(
            {
                "strategy_id": row["strategy_id"],
                "strategy": row["strategy_label"],
                "gate_id": row["gate_id"],
                "gate": row["gate_label"],
                "total_return": m["total_return"],
                "cagr": m["cagr"],
                "max_drawdown": m["max_drawdown"],
                "max_dd_start": m["max_dd_start"],
                "max_dd_end": m["max_dd_end"],
                "volatility": m["volatility"],
                "sharpe": m["sharpe"],
                "avg_exposure": m["avg_exposure"],
                "trades": row["trade_count"],
                "market_bad_days": row["market_bad_days"],
                "latest_positions": row["latest_positions"],
                "yearly": json.dumps(m.get("yearly", {}), ensure_ascii=False),
            }
        )
    write_csv(OUT_DIR / "market_gate_5y_metrics_latest.csv", metrics_csv)
    summary = build_summary(all_rows, base_rows)
    summary_path = OUT_DIR / "market_gate_5y_summary_latest.md"
    summary_path.write_text(summary, encoding="utf-8")
    (OUT_DIR / "market_gate_5y_metrics_latest.json").write_text(
        json.dumps({"rows": all_rows, "baseline": base_rows, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
