#!/usr/bin/env python3
"""Run the previously tested T0 strategy families on one historical window."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = os.environ.get("HISTORICAL_SUITE_START", "2015-07-01")
DATA_START = os.environ.get("HISTORICAL_SUITE_DATA_START", "2014-07-01")
END = os.environ.get("HISTORICAL_SUITE_END", "2021-07-30")
PERIOD_SLUG = f"{START[:7]}至{END[:7]}"
OUT_DIR = ROOT / f"a_stock_daily_workflow/etf_rotation/backtests/historical_strategy_suite_{START[:4]}_{END[:4]}"
NOTE_DIR = Path("/Users/yansenz/Documents/note")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    os.environ["V2_DATA_START_DATE"] = DATA_START
    os.environ["V2_DATA_END_DATE"] = END

    base = load("historical_dynamic_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")
    lab = load("historical_strategy_lab", ROOT / "scripts/backtest_etf_strategy_lab.py")
    base.KLINE_LIMIT = 2000
    histories, errors = base.load_history()
    lab.v3.BACKTEST_YEARS = 6

    rows: list[dict] = []
    for original in lab.STRATEGIES:
        strategy = dict(original)
        strategy["start_date"] = START
        equity, trades, signals = lab.simulate_strategy(strategy, histories)
        metrics = lab.v3.perf_metrics(equity)
        rows.append({
            "id": strategy["id"],
            "label": strategy["label"],
            "total_return": metrics["total_return"],
            "cagr": metrics["cagr"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe": metrics["sharpe"],
            "avg_exposure": metrics["avg_exposure"],
            "trades": len(trades),
            "yearly": json.dumps(metrics.get("yearly", {}), ensure_ascii=False),
            "data_errors": len(errors),
        })

    v2_path = Path(os.environ.get("V2_RESULT_PATH", str(ROOT / "a_stock_daily_workflow/etf_rotation/backtests/v2_t0_2015_2021/dual_sleeve_v2_metrics_latest.json")))
    v2_payload = json.loads(v2_path.read_text(encoding="utf-8"))
    for row in v2_payload.get("rows", []):
        rows.append({
            "id": "v2_t0_month_start3",
            "label": "V2 T0 双袖轮动｜月初第3交易日",
            "total_return": row["total_return"],
            "cagr": row["cagr"],
            "max_drawdown": row["max_drawdown"],
            "sharpe": row["sharpe"],
            "avg_exposure": row["avg_exposure"],
            "trades": row["trades"],
            "yearly": row["yearly"],
            "data_errors": len(v2_payload.get("errors", {})),
        })

    rows.sort(key=lambda row: row["total_return"], reverse=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "historical_strategy_metrics.csv", rows)
    (OUT_DIR / "historical_strategy_metrics.json").write_text(json.dumps({"rows": rows, "errors": errors, "start": START, "end": END, "data_start": DATA_START}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# {PERIOD_SLUG} 历史策略回测对照（T0）",
        "",
        f"- 行情预热：{DATA_START} 至 {END}",
        f"- 实际统计：{START} 至 {END}",
        "- 统一使用 T0；未运行 T1。",
        "- 成本、ETF池和复权口径沿用各策略原脚本，策略之间不完全同构；结果用于历史敏感性比较。",
        "",
        "> 重要：不同策略脚本的 ETF 池、调仓频率和过滤器可能不同，排名不能等同于严格同口径的公平竞赛。",
        "",
        "## 结果排名",
        "",
        "| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 成交记录 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(rows, 1):
        lines.append(f"| {i} | {row['label']} | {pct(row['total_return'])} | {pct(row['cagr'])} | {pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {pct(row['avg_exposure'])} | {row['trades']} |")

    lines.extend(["", "## 年度表现", "", "| 策略 | 年度收益 |", "|---|---|"])
    for row in rows:
        lines.append(f"| {row['label']} | {row['yearly']} |")

    lines.extend(["", "## 数据质量", ""])
    if errors:
        lines.append(f"- 策略实验室数据加载错误：{', '.join(sorted(errors))}")
    else:
        lines.append("- 策略实验室未报告数据加载错误。")
    if v2_payload.get("errors"):
        lines.append(f"- V2 T0 数据加载错误：{', '.join(sorted(v2_payload['errors']))}")
    lines.extend(["", "## 产物", "", f"- 指标 CSV：`{OUT_DIR / 'historical_strategy_metrics.csv'}`", f"- 指标 JSON：`{OUT_DIR / 'historical_strategy_metrics.json'}`", ""])
    report = "\n".join(lines)
    report_path = OUT_DIR / "historical_strategy_summary.md"
    report_path.write_text(report, encoding="utf-8")
    NOTE_DIR.mkdir(parents=True, exist_ok=True)
    note_path = NOTE_DIR / f"历史策略T0回测对照_{PERIOD_SLUG}.md"
    note_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {report_path}")
    print(f"wrote {note_path}")


if __name__ == "__main__":
    main()
