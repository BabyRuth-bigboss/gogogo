#!/usr/bin/env python3
"""
V2 双袖轮动 — 实盘信号生成器

用法:
    python scripts/etf_rotation_v2_live.py

输出:
    etf_rotation/etf_rotation_v2_signal_latest.md
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation"
SCRIPTS = ROOT / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ── 加载依赖 ──────────────────────────────────────────
search_base = load_module("search_base", SCRIPTS / "search_etf_high_return_strategy_5y.py")
neighbor_mod = load_module("neighbor", SCRIPTS / "search_etf_winner_neighborhood_5y.py")
regime_engine_mod = load_module("regime_engine", SCRIPTS / "regime_engine_v2.py")

search = neighbor_mod.search
base = neighbor_mod.base
v3_mod = neighbor_mod.v3
RegimeEngine = regime_engine_mod.RegimeEngine
BENCHMARK_INDEXES_V2 = regime_engine_mod.BENCHMARK_INDEXES_V2

# ── V2 最优参数 ──────────────────────────────────────
REGIME_CONFIG = {
    "ma_days": 120,
    "slope_lookback": 20,
    "slope_threshold": -0.005,
    "weak_ratio": 0.50,
    "confirm_days": 2,
    "recovery_confirm_days": 1,
    "cooldown_days": 5,
}

DEFENSIVE_CONFIG = {
    "defensive_pool": "overseas",
    "defensive_model": "rs612",
    "defensive_top_n": 2,
    "defensive_allocation": 1.00,
    "defensive_filter": "ma200_ret12",
}

DEFENSIVE_POOLS = {
    "overseas": {"label": "纳指+标普", "codes": {"513100", "513500"}},
}

OFFENSIVE_HORIZON = {
    "label": "6月60%+12月40%",
    "weights": {"ret126": 0.6, "ret252": 0.4},
    "filter": "ma200_ret12",
}

# ── 工具函数 ──────────────────────────────────────────
def fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v*100:+.2f}%"


def fmt_num(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def percentile_ranks(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    ordered = sorted(rows, key=lambda r: (float(r[key]), str(r["code"])))
    if len(ordered) <= 1:
        return {str(r["code"]): 0.5 for r in ordered}
    return {str(r["code"]): idx / (len(ordered) - 1) for idx, r in enumerate(ordered)}


# ── 防御 sleeve ─────────────────────────────────────
def defensive_targets_live(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], list[dict[str, Any]], str]:
    pool = DEFENSIVE_POOLS[DEFENSIVE_CONFIG["defensive_pool"]]
    rows = [
        r for r in search.cached_features(signal_date, histories, indexes)
        if r["code"] in pool["codes"]
    ]
    candidates = []
    for row in rows:
        eligible = row["close"] > row["ma200"] and row["ret252"] > 0
        if not eligible:
            continue
        row["momentum"] = row["ret126"] * 0.5 + row["ret252"] * 0.5
        candidates.append(row)

    if not candidates:
        return {}, [], "无合格防御ETF → 全仓现金"

    m_rank = percentile_ranks(candidates, "momentum")
    for r in candidates:
        r["score"] = m_rank[r["code"]] * 0.9 + 0.1

    selected = sorted(candidates, key=lambda r: r["score"], reverse=True)[: DEFENSIVE_CONFIG["defensive_top_n"]]
    alloc = float(DEFENSIVE_CONFIG["defensive_allocation"])
    targets = {r["code"]: alloc / len(selected) for r in selected}
    note = f"防御sleeve: {'+'.join(r['label'] for r in selected)} (各{alloc/len(selected):.0%})"
    return targets, selected, note


# ── 进攻 sleeve (V2: 6月+12月动量) ──────────────────
def offensive_targets_live(
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> tuple[dict[str, float], list[dict[str, Any]], str]:
    config = {
        "horizon_id": "offensive_m612_60_40",
        "filter": "ma200_ret12",
        "factor_weights": {"momentum": 0.75, "liquidity": 0.15, "trend_quality": 0.10},
        "top_n": 2,
        "weighting": "equal",
    }
    targets, info = search.build_targets(config, signal_date, histories, indexes)
    ranked = info.get("ranked", [])
    selected = info.get("selected", [])
    note = f"进攻sleeve: {'+'.join(r['label'] for r in selected)} (各50%)" if selected else "无可选标的"
    return targets, selected, note


# ── 主函数 ──────────────────────────────────────────
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 加载数据（带 V2 指数池）
    base.KLINE_LIMIT = max(base.KLINE_LIMIT, 1500)
    base.BENCHMARK_INDEXES = BENCHMARK_INDEXES_V2
    search.HORIZONS["offensive_m612_60_40"] = OFFENSIVE_HORIZON
    histories, errors = base.load_history()

    indexes = {code: {str(r["date"]): idx for idx, r in enumerate(rows)} for code, rows in histories.items()}
    all_dates = sorted(set(str(r["date"]) for rows in histories.values() for r in rows))
    latest = all_dates[-1]

    # V2 引擎
    engine = RegimeEngine(**REGIME_CONFIG, indices=BENCHMARK_INDEXES_V2)
    engine.reset()
    for i, date in enumerate(all_dates[-60:]):
        engine.classify(date, histories, indexes, day_index=i + 10000)
    market_bad, market_note = engine.classify(latest, histories, indexes, day_index=99999)

    # 指数诊断
    states = regime_engine_mod.diagnose_indices(engine, latest, histories, indexes)

    # 生成持仓
    if market_bad:
        targets, selected, sleeve_note = defensive_targets_live(latest, histories, indexes)
        sleeve = "defensive"
    else:
        targets, selected, sleeve_note = offensive_targets_live(latest, histories, indexes)
        sleeve = "offensive"

    # ── 写出报告 ──
    lines = []
    lines.append(f"# V2 双袖轮动 · 实盘信号")
    lines.append("")
    lines.append(f"**{latest}**  |  {'🔴 弱市' if market_bad else '🟢 强市'} → {sleeve} sleeve")
    lines.append("")
    lines.append(f"> {sleeve_note}")
    lines.append("")

    # 持仓
    lines.append("## 今日持仓")
    lines.append("")
    if targets:
        lines.append("| 代码 | 名称 | 仓位 | 6月动量 | 12月动量 |")
        lines.append("|---:|---:|---:|---:|---:|")
        for code, weight in targets.items():
            name = ""
            ret126 = None
            ret252 = None
            for r in selected:
                if r["code"] == code:
                    name = r.get("label", code)
                    ret126 = r.get("ret126") if sleeve == "offensive" else r.get("ret126")
                    ret252 = r.get("ret252") if sleeve == "offensive" else r.get("ret252")
                    break
            if not name:
                name = code
            lines.append(f"| {code} | {name} | {weight:.0%} | {fmt_pct(ret126)} | {fmt_pct(ret252)} |")
    else:
        lines.append("**全仓现金** — 无合格标的")
    lines.append("")

    # 排名 Top8（仅进攻时展示）
    if sleeve == "offensive":
        config = {
            "horizon_id": "offensive_m612_60_40",
            "filter": "ma200_ret12",
            "factor_weights": {"momentum": 0.75, "liquidity": 0.15, "trend_quality": 0.10},
            "top_n": 2,
            "weighting": "equal",
        }
        _, info = search.build_targets(config, latest, histories, indexes)
        ranked_all = info.get("ranked", [])[:8]
        if ranked_all:
            lines.append("## 进攻排名 Top8")
            lines.append("")
            lines.append("| # | 代码 | 名称 | 评分 | 6月动量 | 12月动量 | 成交(亿) |")
            lines.append("|---:|---:|---:|---:|---:|---:|---:|")
            for i, r in enumerate(ranked_all):
                score = r.get("score", 0)
                r126 = r.get("ret126", 0) or 0
                r252 = r.get("ret252", 0) or 0
                amt = r.get("amount_yi", 0) or 0
                tag = " ★" if r["code"] in targets else ""
                lines.append(f"| {i+1} | {r['code']} | {r['label']}{tag} | {score:.1f} | {fmt_pct(r126)} | {fmt_pct(r252)} | {amt:.2f} |")
            lines.append("")

    # 指数诊断
    lines.append("## 六指数强弱诊断")
    lines.append("")
    lines.append("| 指数 | 价格 | MA120 | MA20日Δ | 判断 |")
    lines.append("|---:|---:|---:|---:|---:|")
    for s in states:
        if "error" in s:
            lines.append(f"| {s['label']} | — | — | — | 数据不可用 |")
            continue
        if s["true_strong"]:
            st = "🟢 真强势"
        elif s["true_weak"]:
            st = "🔴 真弱势"
        elif s.get("false_weak"):
            st = "🟡 假弱"
        else:
            st = "⚪ 中性"
        lines.append(f"| {s['label']} | {fmt_num(s['price'], 2)} | {fmt_num(s['ma'], 2)} | {fmt_pct(s['ma_slope'])} | {st} |")

    true_weak = [s for s in states if "error" not in s and s["true_weak"]]
    lines.append("")
    if market_bad:
        lines.append(f"⚠️ **弱市确认**: {len(true_weak)}/6 真弱势 ≥ 50% 阈值")
    else:
        lines.append(f"✅ **强市维持**: {len(true_weak)}/6 真弱势 < 50% 阈值，不触发切换")
    lines.append("")

    # 风控提示
    lines.append("## 操作提示")
    lines.append("")
    lines.append("- 今日信号仅作参考，不构成投资建议")
    if sleeve == "offensive":
        lines.append("- 进攻模式: 月初第3交易日调仓，其余时间仅强弱切换触发调仓")
    else:
        lines.append("- 防御模式: 每日确认市场是否恢复，恢复立即切回")
    lines.append("- V2 引擎参数: MA120斜率 + 确认2天 + 冷却5天 + 港股指数")
    lines.append("")

    report = "\n".join(lines)
    report_path = OUT_DIR / f"etf_rotation_v2_signal_{latest}.md"
    latest_path = OUT_DIR / "etf_rotation_v2_signal_latest.md"
    report_path.write_text(report, encoding="utf-8")
    shutil.copyfile(report_path, latest_path)

    print(report)
    print(f"\nwrote {report_path}")
    print(f"wrote {latest_path}")


if __name__ == "__main__":
    main()
