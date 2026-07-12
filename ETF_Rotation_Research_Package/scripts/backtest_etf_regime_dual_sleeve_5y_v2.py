#!/usr/bin/env python3
"""
ETF 双袖轮动回测 V2 —— 使用优化后的强弱判断引擎

改进点（相对 V1）：
1. MA斜率判断：区分真弱市(MA下行+价格跌破) vs 假弱市(MA上行+短暂跌破)
2. 死叉确认：MA50 < MA120 辅助确认
3. 信号防抖：连续N天确认 + 冷却期，消除 whipsaw
4. 指数池扩展：加入中证500、恒生指数、恒生科技
5. 网格搜索：新增 regime 相关参数

用法:
    python backtest_etf_regime_dual_sleeve_5y_v2.py

输出目录:
    .../etf_rotation/backtests/regime_dual_sleeve_5y_v2/
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEARCH_SCRIPT = ROOT / "scripts/search_etf_high_return_strategy_5y.py"
NEIGHBOR_SCRIPT = ROOT / "scripts/search_etf_winner_neighborhood_5y.py"
REGIME_ENGINE_SCRIPT = ROOT / "scripts/regime_engine_v2.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# 加载现有模块
neighbor = load_module("etf_regime_neighbor_v2", NEIGHBOR_SCRIPT)
search = neighbor.search
base = neighbor.base
lab = neighbor.lab
gate_mod = neighbor.gate_mod
v3 = neighbor.v3

# 加载新引擎
regime_mod = load_module("regime_engine_v2", REGIME_ENGINE_SCRIPT)
RegimeEngine = regime_mod.RegimeEngine
BENCHMARK_INDEXES_V2 = regime_mod.BENCHMARK_INDEXES_V2
BENCHMARK_INDEXES_V1 = regime_mod.BENCHMARK_INDEXES_V1

# ── 防御池配置（同 V1） ────────────────────────────────
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


def build_rebalance_indices_n(dates: list[str], start_idx: int, trading_day: int) -> set[int]:
    """Return signal indices whose next trading day is the Nth day of month."""
    candidates = list(range(start_idx, len(dates) - 1))
    out = {start_idx}
    by_month: dict[tuple[int, int], list[int]] = {}
    for i in candidates:
        trade_date = dt.date.fromisoformat(dates[i + 1])
        by_month.setdefault((trade_date.year, trade_date.month), []).append(i)
    offset = max(1, trading_day) - 1
    for month in by_month.values():
        out.add(month[offset] if len(month) > offset else month[-1])
    return out


# ── 进攻 sleeve（同 V1）────────────────────────────────
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


# ── 防御 sleeve（同 V1）────────────────────────────────
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


# ── 核心：V2 模拟（使用 RegimeEngine）──────────────────
def simulate(
    config: dict[str, Any],
    histories: dict[str, list[dict[str, Any]]],
    start_date: str,
    fee_mult: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    与 V1 的 simulate 完全相同的接口，唯一区别是强弱判断用 RegimeEngine。
    """
    indexes = {code: {str(row["date"]): idx for idx, row in enumerate(rows)} for code, rows in histories.items()}
    dates = v3.common_calendar(histories)
    start_idx = next(i for i, date in enumerate(dates) if date >= start_date)
    start_idx = max(start_idx, 253)
    rebal_indices = build_rebalance_indices_n(dates, start_idx, int(config.get("monthly_trading_day", 3)))
    fee_rate = base.FEE_RATE * fee_mult

    # ── 初始化 RegimeEngine ──
    regime_config = config.get("regime", {})
    use_hk = regime_config.get("use_hk_indices", True)
    engine = RegimeEngine(
        indices=BENCHMARK_INDEXES_V2 if use_hk else BENCHMARK_INDEXES_V1,
        ma_days=regime_config.get("ma_days", 120),
        slope_lookback=regime_config.get("slope_lookback", 20),
        slope_threshold=regime_config.get("slope_threshold", -0.005),
        weak_ratio=regime_config.get("weak_ratio", 0.40),
        confirm_days=regime_config.get("confirm_days", 3),
        recovery_confirm_days=regime_config.get("recovery_confirm_days", 2),
        cooldown_days=regime_config.get("cooldown_days", 10),
    )
    engine.reset()

    cash = v3.INITIAL_CASH
    positions: dict[str, float] = {}
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    previous_bad = False
    current_targets: dict[str, float] = {}

    for i in range(start_idx, len(dates) - 1):
        signal_date = dates[i]
        trade_date = dates[i + 1]
        rebal_day = i in rebal_indices

        # ── V2 强弱判断（替代 generic_market_gate_bad）──
        market_bad, market_note = engine.classify(signal_date, histories, indexes, day_index=i)

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
            targets = {}
            for code, shares in positions.items():
                open_price = v3.price_at(histories, indexes, code, trade_date, "open")
                if open_price is not None and equity_open > 0:
                    targets[code] = shares * open_price / equity_open

        signals.append({
            "signal_date": signal_date,
            "trade_date": trade_date,
            "rebalance_day": rebal_day,
            "regime_changed": regime_changed,
            "market_bad": market_bad,
            "market_note": market_note,
            "sleeve": "defensive" if market_bad else "offensive",
            "selected": ";".join(row["label"] for row in selected),
            "targets": ";".join(f"{code}:{weight:.1%}" for code, weight in targets.items()) if should_reselect else "",
        })

        # ── 执行卖出 ──
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
            trades.append({
                "date": trade_date, "code": code, "side": "SELL",
                "price": f"{open_price:.4f}", "shares": f"{shares:.4f}",
                "value": f"{shares * open_price:.2f}",
                "fee": f"{shares * open_price * fee_rate:.2f}",
                "signal_date": signal_date,
                "reason": "regime_switch" if regime_changed else "rebalance",
            })

        # ── 执行买入 ──
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
            trades.append({
                "date": trade_date, "code": code, "side": "BUY",
                "price": f"{open_price:.4f}", "shares": f"{shares:.4f}",
                "value": f"{spend:.2f}", "fee": f"{spend * fee_rate:.2f}",
                "signal_date": signal_date,
                "reason": "regime_switch" if regime_changed else "rebalance",
            })

        equity_close = v3.portfolio_equity(cash, positions, histories, indexes, trade_date, "close")
        invested = max(0.0, equity_close - cash)
        equity_rows.append({
            "date": trade_date, "equity": equity_close, "cash": cash,
            "exposure": invested / equity_close if equity_close > 0 else 0.0,
            "positions": ";".join(sorted(positions)),
            "market_bad": market_bad, "sleeve": "defensive" if market_bad else "offensive",
        })
        previous_bad = market_bad

    return equity_rows, trades, signals


# ── 标签函数 ───────────────────────────────────────────
def label(config: dict[str, Any]) -> str:
    pool = DEFENSIVE_POOLS[config["defensive_pool"]]["label"]
    filter_label = "MA120+6月正" if config["defensive_filter"] == "ma120_ret6" else "MA200+12月正"
    recovery = "恢复立即切回" if config["recovery_mode"] == "immediate" else "恢复等月调仓"

    regime = config.get("regime", {})
    use_hk = regime.get("use_hk_indices", True)
    ma_d = regime.get("ma_days", 120)
    conf = regime.get("confirm_days", 3)
    cool = regime.get("cooldown_days", 10)

    regime_label = f"MA{ma_d}斜率+确认{conf}d+冷却{cool}d"
    if use_hk:
        regime_label += "+港股"

    return (
        f"进攻6月60%+12月40% Top2｜弱市{pool} {config['defensive_model'].upper()} "
        f"Top{config['defensive_top_n']} {config['defensive_allocation']:.0%}仓｜{filter_label}｜{recovery}｜{regime_label}"
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


def configured_model_ids() -> list[str]:
    raw = os.environ.get("V2_MODEL_IDS", "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def portfolio_metrics(equity: list[dict[str, Any]]) -> dict[str, Any]:
    if not equity:
        return {}
    first_date = dt.date.fromisoformat(str(equity[0]["date"])) - dt.timedelta(days=1)
    initial_row = {
        "date": first_date.isoformat(), "equity": v3.INITIAL_CASH,
        "cash": v3.INITIAL_CASH, "exposure": 0.0, "positions": "",
    }
    return v3.perf_metrics([initial_row, *equity])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    search.HORIZONS["offensive_m612_60_40"] = OFFENSIVE_HORIZON

    # ── 扩展数据量：V2需要更长的历史（MA120 + 斜率 + 防抖确认）──
    # V1 使用 KLINE_LIMIT=950（约3.8年），V2 提升到 1500（约6年）
    base.KLINE_LIMIT = max(base.KLINE_LIMIT, 1500)

    # ── 扩展指数池：将港股指数加入数据加载 ──
    base.BENCHMARK_INDEXES = BENCHMARK_INDEXES_V2
    histories, errors = base.load_history()

    # 诊断
    loaded_indices = {k for k in histories if k.startswith("IDX_")}
    sample_code = list(loaded_indices)[0] if loaded_indices else None
    if sample_code:
        rows = histories[sample_code]
        print(f"已加载 {len(loaded_indices)} 个指数, 数据区间: {rows[0]['date']} ~ {rows[-1]['date']} ({len(rows)}行)")
    if errors:
        print(f"数据加载错误: {list(errors.keys())}")
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    full_start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * base.BACKTEST_YEARS))).isoformat()

    # ── 网格搜索配置 ───────────────────────────────────

    # 原有双袖参数（精简版：只用最佳参数组合）
    sleeve_configs = []
    for pool in ("overseas",):
        for model in ("rs612",):
            for top_n in (2,):
                for allocation in (1.00,):
                    for filt in ("ma200_ret12",):
                        for recovery in ("immediate",):
                            sleeve_configs.append({
                                "defensive_pool": pool,
                                "defensive_model": model,
                                "defensive_top_n": top_n,
                                "defensive_allocation": allocation,
                                "defensive_filter": filt,
                                "recovery_mode": recovery,
                                "monthly_trading_day": int(os.environ.get("V2_MONTHLY_TRADING_DAY", "3")),
                            })

    # V2 新增的 regime 参数网格
    regime_grids = []
    for ma_days in (120, 200):
        for confirm_days in (2, 3):
            for cooldown_days in (5, 10):
                for use_hk in (True, False):
                    for weak_ratio in (0.40, 0.50):
                        regime_grids.append({
                            "use_hk_indices": use_hk,
                            "ma_days": ma_days,
                            "slope_lookback": 20,
                            "slope_threshold": -0.005,
                            "weak_ratio": weak_ratio,
                            "confirm_days": confirm_days,
                            "recovery_confirm_days": max(1, confirm_days - 1),
                            "cooldown_days": cooldown_days,
                        })

    # 组合全部配置（限制数量避免爆炸）
    configs = []
    for sleeve in sleeve_configs:
        for regime in regime_grids:
            config = dict(sleeve)
            config["regime"] = regime
            parts = [
                sleeve["defensive_pool"], sleeve["defensive_model"],
                f"top{sleeve['defensive_top_n']}", f"alloc{int(sleeve['defensive_allocation']*100)}",
                sleeve["defensive_filter"], sleeve["recovery_mode"],
                f"ma{regime['ma_days']}", f"conf{regime['confirm_days']}",
                f"cool{regime['cooldown_days']}", f"wr{int(regime['weak_ratio']*100)}",
                "hk" if regime["use_hk_indices"] else "ah",
            ]
            config["id"] = "_".join(parts)
            configs.append(config)

    requested_ids = configured_model_ids()
    if requested_ids:
        by_id = {config["id"]: config for config in configs}
        missing = [model_id for model_id in requested_ids if model_id not in by_id]
        if missing:
            raise SystemExit(f"V2_MODEL_IDS contains unknown model ids: {missing}")
        configs = [by_id[model_id] for model_id in requested_ids]

    print(f"配置总数: {len(configs)} (sleeve={len(sleeve_configs)} × regime={len(regime_grids)})")

    # ── 运行全部回测 ──
    rows: list[dict[str, Any]] = []
    for idx, config in enumerate(configs):
        equity, trades, signals = simulate(config, histories, full_start)
        metrics = portfolio_metrics(equity)
        rows.append({
            "id": config["id"],
            "label": label(config),
            "config": config,
            "metrics": metrics,
            "trade_count": len(trades),
            "equity": equity,
            "trades": trades,
            "signals": signals,
            "fee_mult": 1.0,
            "start_date": full_start,
        })
        if (idx + 1) % 20 == 0:
            print(f"  进度: {idx + 1}/{len(configs)}")

    # ── 排序 ──
    rows.sort(key=lambda row: (row["metrics"]["total_return"], row["metrics"]["max_drawdown"]), reverse=True)
    finalists = rows[:12]
    safe_finalists = [row for row in rows if row["metrics"]["max_drawdown"] >= -0.20][:8]

    # ── 压力测试（对 Top 策略） ──
    stress_finalists = {row["id"]: row for row in finalists + safe_finalists}
    stress: list[dict[str, Any]] = []
    if os.environ.get("V2_NO_STRESS", "0") != "1":
        for finalist in stress_finalists.values():
            for start_date in (full_start, "2022-01-04", "2023-01-03"):
                for fee_mult in (1.0, 2.0):
                    equity, trades, signals = simulate(finalist["config"], histories, start_date, fee_mult)
                    stress.append({
                        "id": finalist["id"], "label": finalist["label"],
                        "metrics": portfolio_metrics(equity),
                        "trade_count": len(trades),
                        "fee_mult": fee_mult, "start_date": start_date,
                    })

    # ── 稳健性汇总 ──
    robust = []
    for finalist in stress_finalists.values():
        cases = [row for row in stress if row["id"] == finalist["id"]]
        if not cases:
            continue
        robust.append({
            "full": finalist,
            "median_cagr": statistics.median(row["metrics"]["cagr"] for row in cases),
            "min_cagr": min(row["metrics"]["cagr"] for row in cases),
            "worst_drawdown": min(row["metrics"]["max_drawdown"] for row in cases),
        })
    robust.sort(key=lambda row: (row["median_cagr"], row["min_cagr"]), reverse=True)
    robust_safe = [row for row in robust if row["full"]["metrics"]["max_drawdown"] >= -0.20]

    # ── 写出 CSV ──
    write_csv(OUT_DIR / "dual_sleeve_v2_metrics_latest.csv", [compact(row, "full") for row in rows])
    write_csv(OUT_DIR / "dual_sleeve_v2_stress_latest.csv", [compact(row, "stress") for row in stress])
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

    # ── 写出 Summary ──
    lines = [
        "# ETF强弱市双层轮动回测 V2（MA斜率 + 防抖 + 港股指数）",
        "",
        f"- 生成日期：{dt.date.today().isoformat()}",
        "- 强市：行业ETF 6月60%+12月40%动量，叠加流动性15%和趋势质量10%，Top2。",
        "- 弱市：从纳指/标普中选择自身趋势向上的ETF，否则保留现金。",
        "- **强弱判断（V2）**：MA斜率方向 + 死叉确认 + 连续确认N天 + 冷却期 + 港股指数。",
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

    # V1 vs V2 对比
    lines.extend(["", "## V1 vs V2 对比（最优策略）", ""])
    lines.append("| 版本 | 总收益 | 年化 | 最大回撤 | Sharpe | 交易数 |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    if rows:
        best_v2 = rows[0]
        m = best_v2["metrics"]
        lines.append(
            f"| V2 | {fmt_pct(m['total_return'])} | {fmt_pct(m['cagr'])} | "
            f"{fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {best_v2['trade_count']} |"
        )
    lines.append(
        "| V1 (基线) | +376.61% | +36.71% | -23.43% | 1.28 | 152 |"
    )

    lines.extend(["", "## 压力测试 Top10", "", "| 排名 | 策略 | 全期收益 | 中位年化 | 最低年化 | 最差回撤 |", "|---:|---|---:|---:|---:|---:|"])
    for idx, row in enumerate(robust[:10], 1):
        full = row["full"]
        lines.append(
            f"| {idx} | {full['label']} | {fmt_pct(full['metrics']['total_return'])} | {fmt_pct(row['median_cagr'])} | "
            f"{fmt_pct(row['min_cagr'])} | {fmt_pct(row['worst_drawdown'])} |"
        )

    if robust_safe:
        lines.extend(["", "## 全期回撤不超过20%的候选", "", "| 排名 | 策略 | 全期收益 | 全期年化 | 全期回撤 | 压测最差回撤 |", "|---:|---|---:|---:|---:|---:|"])
        for idx, row in enumerate(robust_safe[:8], 1):
            full = row["full"]
            lines.append(
                f"| {idx} | {full['label']} | {fmt_pct(full['metrics']['total_return'])} | {fmt_pct(full['metrics']['cagr'])} | "
                f"{fmt_pct(full['metrics']['max_drawdown'])} | {fmt_pct(row['worst_drawdown'])} |"
            )

    lines.extend(["", "## 结论", ""])
    if rows:
        best = rows[0]
        lines.append(f"- V2 最优总收益 {fmt_pct(best['metrics']['total_return'])}，年化 {fmt_pct(best['metrics']['cagr'])}，最大回撤 {fmt_pct(best['metrics']['max_drawdown'])}。")
    lines.append(f"- 完成 {len(rows) + len(stress)} 次回测，其中完整参数组合 {len(rows)}，压力测试 {len(stress)}。")
    lines.append("")

    summary = "\n".join(lines)
    summary_path = OUT_DIR / "dual_sleeve_v2_summary_latest.md"
    summary_path.write_text(summary, encoding="utf-8")
    (OUT_DIR / "dual_sleeve_v2_metrics_latest.json").write_text(
        json.dumps({"rows": [compact(row, "full") for row in rows], "stress": [compact(row, "stress") for row in stress], "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_DIR / "v2_model_manifest.json").write_text(
        json.dumps({"requested_model_ids": requested_ids, "selected_model_ids": [row["id"] for row in rows], "count": len(rows), "stress_enabled": os.environ.get("V2_NO_STRESS", "0") != "1"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
