#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V4_SCRIPT = ROOT / "scripts/backtest_etf_momentum_rotation_3y_v4_variants.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests"


def load_v4_module():
    spec = importlib.util.spec_from_file_location("etf_rotation_v4_gap_sensitivity", V4_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V4_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v4 = load_v4_module()


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


def signal_stats(signal_rows: list[dict[str, Any]]) -> dict[str, int]:
    weekly_strong = [
        row for row in signal_rows
        if row.get("rebalance_day") is True and row.get("market_regime") == "strong"
    ]
    allowed = [row for row in weekly_strong if row.get("switch_allowed") is True]
    return {
        "strong_weekly_windows": len(weekly_strong),
        "allowed_windows": len(allowed),
        "blocked_windows": len(weekly_strong) - len(allowed),
        "target_windows": sum(1 for row in signal_rows if row.get("rebalance_day") is True and row.get("targets")),
        "forced_clear_days": sum(1 for row in signal_rows if row.get("forced_clear")),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = v4.v3.load_history()
    thresholds = [1.5, 2.0, 3.0, 4.0, 5.0, 6.0]
    rows: list[dict[str, Any]] = []

    for threshold in thresholds:
        config = {
            "id": f"v4d_gap_{threshold:g}",
            "label": f"V4d 分差>={threshold:g}",
            "rule": f"周频调仓，第一名final_score领先第二名至少{threshold:g}分才执行普通换仓",
            "rebalance": "weekly",
            "min_hold_days": 5,
            "require_top20_for_new": False,
            "require_score_gap_for_rebalance": True,
            "score_gap_min": threshold,
        }
        equity_rows, trades, signal_rows = v4.simulate_variant(histories, config)
        metrics = v4.v3.perf_metrics(equity_rows)
        stats = signal_stats(signal_rows)
        rows.append(
            {
                "threshold": threshold,
                "total_return": metrics["total_return"],
                "cagr": metrics["cagr"],
                "max_drawdown": metrics["max_drawdown"],
                "sharpe": metrics["sharpe"],
                "avg_exposure": metrics["avg_exposure"],
                "trades": len(trades),
                **stats,
            }
        )

    md_lines = ["# ETF轮动 V4d 分差阈值敏感性", ""]
    md_lines.append(f"- 当前ETF池：{len(v4.v3.ETF_UNIVERSE)}只")
    if errors:
        md_lines.append(f"- 数据拉取失败：{len(errors)}只，已跳过。")
    md_lines.append("- 规则：只允许strong市场开新仓；周频窗口里，第一名final_score领先第二名达到阈值才普通换仓；弱市和强制清仓仍然优先。")
    md_lines.append("")
    md_lines.append("| 分差阈值 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | strong窗口通过/总数 |")
    md_lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        md_lines.append(
            f"| {row['threshold']:g} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {fmt_pct(row['avg_exposure'])} | "
            f"{row['trades']} | {row['allowed_windows']}/{row['strong_weekly_windows']} |"
        )
    best_dd = max(rows, key=lambda row: row["max_drawdown"])
    best_return = max(rows, key=lambda row: row["total_return"])
    md_lines.append("")
    md_lines.append(f"- 回撤最小：阈值 {best_dd['threshold']:g}，最大回撤 {fmt_pct(best_dd['max_drawdown'])}。")
    md_lines.append(f"- 收益最好：阈值 {best_return['threshold']:g}，总收益 {fmt_pct(best_return['total_return'])}。")

    md_path = OUT_DIR / "etf_rotation_3y_v4d_gap_sensitivity_latest.md"
    csv_path = OUT_DIR / "etf_rotation_3y_v4d_gap_sensitivity_latest.csv"
    json_path = OUT_DIR / "etf_rotation_3y_v4d_gap_sensitivity_latest.json"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    write_csv(csv_path, rows)
    json_path.write_text(json.dumps({"thresholds": rows, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {md_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
