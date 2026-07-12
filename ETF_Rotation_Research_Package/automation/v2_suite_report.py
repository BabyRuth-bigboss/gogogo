#!/usr/bin/env python3
"""Create a durable Markdown summary for a V2 model-suite run."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


NOTE_DIR = Path("/Users/yansenz/Documents/note")


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: v2_suite_report.py RUN_DIR")
    run_dir = Path(sys.argv[1]).resolve()
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    metrics_path = run_dir / "t0" / "dual_sleeve_v2_metrics_latest.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = sorted(payload.get("rows", []), key=lambda row: row.get("total_return", -999), reverse=True)
    errors = payload.get("errors", {})
    if not rows:
        raise SystemExit(f"no T0 model results found in {metrics_path}")

    lines = [
        "# V2 ETF 强弱市双袖轮动：15模型批量回测",
        "",
        f"- 运行 ID：`{card['run_id']}`",
        f"- 回测区间：{card['start_date']} 至 {card['end_date']}",
        f"- 正常调仓：每月第 {card.get('monthly_trading_day', 5)} 个交易日",
        f"- 模型数：{len(rows)}",
        "- T0：项目 V2 引擎批量回测；T1：Antigravity/Vibe 基线审计",
        "- 口径：信号使用前一交易日收盘，下一交易日开盘执行；T0 成本按项目默认单边约 0.105%",
        "",
        "> 本报告用于模型筛选和审计留档，不代表两套引擎已完成公平等价验证。请先阅读双引擎审计报告中的快照、信号和成本限制。",
        "",
        "## 模型排名",
        "",
        "| 排名 | 模型 ID | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 成交记录 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"| {index} | `{row['id']}` | {pct(row['total_return'])} | {pct(row['cagr'])} | "
            f"{pct(row['max_drawdown'])} | {row['sharpe']:.2f} | {pct(row['avg_exposure'])} | {row['trades']} |"
        )

    best = rows[0]
    safe = [row for row in rows if row.get("max_drawdown", -1) >= -0.20]
    lines.extend([
        "",
        "## 初步结论",
        "",
        f"- 收益最高：`{best['id']}`，总收益 {pct(best['total_return'])}，年化 {pct(best['cagr'])}，最大回撤 {pct(best['max_drawdown'])}，Sharpe {best['sharpe']:.2f}。",
        f"- 全期回撤不超过 20% 的模型：{len(safe)} 个。",
        "- 本轮关闭了压力测试，只用于快速比较 15 个 V2 参数模型；选出候选后再单独运行成本翻倍、不同起点和共享快照压力测试。",
        "",
        "## 数据质量",
        "",
    ])
    if errors:
        lines.append(f"- **存在数据加载错误：{', '.join(sorted(errors))}。** 本轮排名只能作为诊断结果，不能直接作为最终策略结论；应补齐数据后重跑。")
    else:
        lines.append("- 本轮 T0 未报告数据加载错误。")
    lines.extend([
        "- 双引擎审计仍需以 `codex/dual_comparison.md` 为准；若没有共享行情快照、manifest 和哈希，不能把 T0/T1 的收益差异归因给引擎。",
        "",
        "## 产物",
        "",
        f"- 运行卡：`{run_dir / 'run_card.json'}`",
        f"- T0 指标：`{run_dir / 't0' / 'dual_sleeve_v2_metrics_latest.json'}`",
        f"- T0 模型清单：`{run_dir / 't0' / 'v2_model_manifest.json'}`",
        f"- 双引擎审计：`{run_dir / 'codex' / 'dual_comparison.md'}`",
        f"- Antigravity 基线：`{run_dir / 'antigravity'}`",
        "",
    ])

    report = "\n".join(lines)
    NOTE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dated = NOTE_DIR / f"ETF_V2_15_MODEL_BACKTEST_{stamp}.md"
    latest = NOTE_DIR / "ETF_V2_15_MODEL_BACKTEST_LATEST.md"
    run_copy = run_dir / "v2_15_model_summary.md"
    for path in (dated, latest, run_copy):
        path.write_text(report, encoding="utf-8")
    print(dated)
    print(latest)


if __name__ == "__main__":
    main()
