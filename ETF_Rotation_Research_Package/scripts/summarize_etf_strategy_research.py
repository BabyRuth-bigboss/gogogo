#!/usr/bin/env python3
"""Merge all T0 ETF research outputs into one auditable performance table."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/strategy_research_master_2015_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF月频轮动策略总排名_2015-07至2026-07.md")
TARGET_CAGR = 0.15
TARGET_DRAWDOWN = -0.20
SOURCES = [
    ("历史策略基线", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/historical_strategy_suite_2015_2026/historical_strategy_metrics.csv", "label", "cagr", "max_drawdown", "sharpe", "trades", "avg_exposure", "total_return"),
    ("单袖月频网格", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_core_research_2015_2026/monthly_core_all_metrics_latest.csv", "label", "full_cagr", "full_max_drawdown", "full_sharpe", "trades", "avg_exposure", "full_total_return"),
    ("双袖月频网格", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_research_2015_2026/dual_sleeve_all_metrics_latest.csv", "label", "full_cagr", "full_max_drawdown", "full_sharpe", "trades", "avg_exposure", "full_total_return"),
    ("双袖因子邻域", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_neighborhood_2015_2026/neighborhood_metrics_latest.csv", "label", "full_cagr", "full_max_drawdown", "full_sharpe", "trades", "avg_exposure", "full_total_return"),
    ("双袖月内紧急退出", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_dual_sleeve_fast_exit_2015_2026/fast_exit_metrics_latest.csv", "trigger", "cagr", "max_drawdown", "sharpe", "trades", "avg_exposure", "total_return"),
    ("V5 T0独立复测", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/v5_concentrated_dual_sleeve_t0_2015_2026/v5_t0_metrics_latest.csv", "label", "cagr", "max_drawdown", "sharpe", "trades", "avg_exposure", "total_return"),
    ("月频策略袖套组合", ROOT / "a_stock_daily_workflow/etf_rotation/backtests/monthly_sleeve_blends_2015_2026/sleeve_blend_metrics_latest.csv", "label", "cagr", "max_drawdown", "sharpe", "trades", "avg_exposure", "total_return"),
]


def number(row: dict[str, str], field: str) -> float:
    try:
        return float(row.get(field, "") or 0)
    except ValueError:
        return 0.0


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows: list[dict[str, object]] = []
    for source, path, label_field, cagr_field, dd_field, sharpe_field, trades_field, exposure_field, total_field in SOURCES:
        for raw in csv.DictReader(path.open(encoding="utf-8-sig")):
            if source == "双袖月内紧急退出" and raw.get("trigger") == "monthly_only":
                continue
            trades = int(float(raw.get(trades_field, "0") or 0))
            if trades <= 0:
                continue
            cagr = number(raw, cagr_field)
            drawdown = number(raw, dd_field)
            rows.append({
                "source": source,
                "id": raw.get("id", ""),
                "strategy": raw.get(label_field, raw.get("variant", "")),
                "risk_rule": raw.get("risk_rule", raw.get("rule", "无")),
                "total_return": number(raw, total_field),
                "cagr": cagr,
                "max_drawdown": drawdown,
                "sharpe": number(raw, sharpe_field),
                "avg_exposure": number(raw, exposure_field),
                "trades": trades,
                "objective_met": cagr >= TARGET_CAGR and drawdown >= TARGET_DRAWDOWN,
            })
    rows.sort(key=lambda row: (bool(row["objective_met"]), float(row["cagr"]), float(row["max_drawdown"])), reverse=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "all_t0_strategy_metrics_latest.csv", rows)
    qualified = [row for row in rows if bool(row["objective_met"])]
    low_dd = [row for row in rows if float(row["max_drawdown"]) >= -0.20]
    sharpe = sorted(rows, key=lambda row: float(row["sharpe"]), reverse=True)
    lines = [
        "# ETF月频轮动策略总排名（T0）", "",
        "- 汇总口径：2015-07 至 2026-07，使用已审计的腾讯日线缓存；信号日收盘、下一交易日开盘成交。",
        "- 成本：新研究统一为佣金万0.5加滑点0.10%单边；早期历史策略沿用原脚本，排名时应优先看同研究族比较。",
        f"- 当前目标：年化>={TARGET_CAGR:.0%}、最大回撤<={abs(TARGET_DRAWDOWN):.0%}；有效策略/变体：{len(rows)} 个；同时达标：{len(qualified)} 个。",
        "",
        "## 回撤不超过20%的收益排名", "",
        "| 排名 | 来源 | 策略 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 |", "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(sorted(low_dd, key=lambda item: float(item["cagr"]), reverse=True)[:30], 1):
        lines.append(f"| {index} | {row['source']} | {row['strategy']} | {pct(float(row['cagr']))} | {pct(float(row['max_drawdown']))} | {float(row['sharpe']):.2f} | {pct(float(row['avg_exposure']))} | {row['trades']} |")
    lines.extend(["", "## Sharpe排名", "", "| 排名 | 来源 | 策略 | 年化 | 最大回撤 | Sharpe |", "|---:|---|---|---:|---:|---:|"])
    for index, row in enumerate(sharpe[:20], 1):
        lines.append(f"| {index} | {row['source']} | {row['strategy']} | {pct(float(row['cagr']))} | {pct(float(row['max_drawdown']))} | {float(row['sharpe']):.2f} |")
    lines.extend([
        "", "## 当前结论", "",
        "- 目前最强的无杠杆、低回撤候选是：RS36+近120日高点评分、行业Top3按50%/30%/20%、月初调仓、三大指数3/3强市才进攻，弱市在国债/黄金中择强。全期年化16.17%、最大回撤17.07%、Sharpe 0.88。",
        "- 该候选通过成本加倍与四个独立起点窗口检查，但没有达到年化20%的预设目标。因此它是研究候选，不是“目标已达成”的实盘承诺。",
        "- 月内MA60/5日急跌紧急退出在相同行情上显著降低年化且扩大回撤，属于已验证的失败补丁，不纳入候选规则。",
        "- V5集中Top1的T0独立复测为年化14.23%至16.79%、最大回撤33%至39%，未复现Vibe的24.71%记录，且不符合低回撤约束。",
        "- 将V5作为20%独立进攻袖套、80%保留核心月频袖后，年化16.28%、最大回撤18.97%，满足当前目标；V5权重超过20%会使回撤超过20%。",
        "- 要把长期年化推到20%以上，当前无杠杆、月频、现有ETF池的证据不足；下一步应扩充可交易历史ETF池并做严格动态成分验证，或明确允许杠杆/更高频风险退出，两者都需要单独审计。",
        "", f"- 全量排名CSV：`{OUT_DIR / 'all_t0_strategy_metrics_latest.csv'}`", "",
    ])
    report = "\n".join(lines)
    (OUT_DIR / "all_t0_strategy_summary_latest.md").write_text(report, encoding="utf-8")
    NOTE_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
