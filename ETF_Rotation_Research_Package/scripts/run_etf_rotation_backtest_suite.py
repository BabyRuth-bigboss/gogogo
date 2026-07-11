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
SCRIPTS_DIR = ROOT / "scripts"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v1 = load_module("etf_rotation_backtest_v1_suite", SCRIPTS_DIR / "backtest_etf_momentum_rotation_3y.py")
v2 = load_module("etf_rotation_backtest_v2_suite", SCRIPTS_DIR / "backtest_etf_momentum_rotation_3y_v2.py")
v3 = load_module("etf_rotation_backtest_v3_suite", SCRIPTS_DIR / "backtest_etf_momentum_rotation_3y_v3.py")
v4 = load_module("etf_rotation_backtest_v4_suite", SCRIPTS_DIR / "backtest_etf_momentum_rotation_3y_v4_variants.py")


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
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_base(
    module: Any,
    label: str,
    rule: str,
    histories: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
    filenames: dict[str, str],
) -> dict[str, Any]:
    equity_rows, trades, signal_rows = module.simulate(histories)
    dates = [row["date"] for row in equity_rows]
    benchmarks = {
        "hs300": module.perf_metrics(module.benchmark_series(histories, "IDX_HS300", dates)),
        "chinext": module.perf_metrics(module.benchmark_series(histories, "IDX_CHINEXT", dates)),
        "sci50": module.perf_metrics(module.benchmark_series(histories, "IDX_SCI50", dates)),
    }
    metrics = module.perf_metrics(equity_rows)
    summary = module.build_summary(
        metrics,
        benchmarks["hs300"],
        benchmarks["chinext"],
        benchmarks["sci50"],
        trades,
        signal_rows,
        errors,
    )

    summary_path = OUT_DIR / filenames["summary"]
    equity_path = OUT_DIR / filenames["equity"]
    trades_path = OUT_DIR / filenames["trades"]
    signals_path = OUT_DIR / filenames["signals"]
    metrics_path = OUT_DIR / filenames["metrics"]

    summary_path.write_text(summary, encoding="utf-8")
    module.write_csv(equity_path, equity_rows)
    module.write_csv(trades_path, trades)
    module.write_csv(signals_path, signal_rows)
    metrics_path.write_text(
        json.dumps(
            {
                "strategy": metrics,
                "hs300": benchmarks["hs300"],
                "chinext": benchmarks["chinext"],
                "sci50": benchmarks["sci50"],
                "data_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "variant": label,
        "rule": rule,
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "avg_exposure": metrics["avg_exposure"],
        "trade_days": len({trade["date"] for trade in trades}),
        "trades": len(trades),
        "buys": sum(1 for trade in trades if trade["side"] == "BUY"),
        "sells": sum(1 for trade in trades if trade["side"] == "SELL"),
        "start": metrics["start"],
        "end": metrics["end"],
        "summary_file": str(summary_path),
        "metrics_file": str(metrics_path),
    }


def run_v4_variants(
    histories: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
    v3_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    benchmark_cache: dict[str, dict[str, Any]] | None = None

    for config in v4.VARIANTS:
        equity_rows, trades, signal_rows = v4.simulate_variant(histories, config)
        aligned_dates = [row["date"] for row in equity_rows]
        benchmarks = {
            "hs300": v4.v3.perf_metrics(v4.v3.benchmark_series(histories, "IDX_HS300", aligned_dates)),
            "chinext": v4.v3.perf_metrics(v4.v3.benchmark_series(histories, "IDX_CHINEXT", aligned_dates)),
            "sci50": v4.v3.perf_metrics(v4.v3.benchmark_series(histories, "IDX_SCI50", aligned_dates)),
        }
        benchmark_cache = benchmarks
        metrics = v4.v3.perf_metrics(equity_rows)
        row = v4.metrics_row(config["label"], config, metrics, trades)
        row["start"] = metrics["start"]
        row["end"] = metrics["end"]
        results.append(row)

        prefix = OUT_DIR / f"etf_rotation_3y_{config['id']}"
        equity_path = prefix.with_name(prefix.name + "_equity_latest.csv")
        trades_path = prefix.with_name(prefix.name + "_trades_latest.csv")
        signals_path = prefix.with_name(prefix.name + "_signals_latest.csv")
        metrics_path = prefix.with_name(prefix.name + "_metrics_latest.json")
        summary_path = prefix.with_name(prefix.name + "_summary_latest.md")
        write_csv(equity_path, equity_rows)
        write_csv(trades_path, trades)
        write_csv(signals_path, signal_rows)
        metrics_path.write_text(
            json.dumps({"strategy": metrics, "benchmarks": benchmarks, "errors": errors, "config": config}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary_path.write_text(v4.build_variant_summary(row, benchmarks), encoding="utf-8")
        row["summary_file"] = str(summary_path)
        row["metrics_file"] = str(metrics_path)

    comparison = v4.build_comparison(results, v3_metrics)
    comparison_path = OUT_DIR / "etf_rotation_3y_v3_v4_variants_comparison.md"
    comparison_path.write_text(comparison, encoding="utf-8")
    (OUT_DIR / "etf_rotation_3y_v4_variants_metrics_latest.json").write_text(
        json.dumps({"variants": results, "v3": v3_metrics, "benchmarks": benchmark_cache}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


def build_overall_comparison(rows: list[dict[str, Any]], errors: dict[str, str]) -> str:
    by_drawdown = sorted(rows, key=lambda row: row["max_drawdown"], reverse=True)
    lines: list[str] = []
    lines.append("# ETF轮动策略3年回测汇总")
    lines.append("")
    lines.append(f"- 生成时间：{dt.date.today().isoformat()}")
    lines.append(f"- 当前ETF池：{len(v3.ETF_UNIVERSE)}只")
    lines.append("- 长期亏损剔除规则：2020-2025覆盖至少3年且年化收益 <= -10% 删除。")
    lines.append("- 已剔除：159883 医疗器械、159837 生物科技。")
    lines.append("- 成本假设：单边佣金0.03% + 滑点0.05%，ETF无印花税。")
    if errors:
        lines.append(f"- 数据拉取失败：{len(errors)}只，已在各策略中跳过。")
    lines.append("")
    lines.append("## 按最大回撤排序")
    lines.append("| 排名 | 策略 | 总收益 | 年化 | 最大回撤 | 平均仓位 | 交易笔数 | 规则 |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---|")
    for idx, row in enumerate(by_drawdown, 1):
        lines.append(
            f"| {idx} | {row['variant']} | {fmt_pct(row['total_return'])} | {fmt_pct(row['cagr'])} | "
            f"{fmt_pct(row['max_drawdown'])} | {fmt_pct(row['avg_exposure'])} | {row['trades']} | {row['rule']} |"
        )
    lines.append("")
    best = by_drawdown[0]
    lines.append("## 结论")
    lines.append(
        f"- 回撤最小的是 {best['variant']}，最大回撤 {fmt_pct(best['max_drawdown'])}，"
        f"总收益 {fmt_pct(best['total_return'])}，平均仓位 {fmt_pct(best['avg_exposure'])}。"
    )
    lines.append("- 从这轮看，减少调仓频率比继续加单个ETF过滤更能压低回撤；但月频版本仓位明显更低，收益也更依赖少数趋势段。")
    lines.append("- 这还是三年样本，下一步更该做成本翻倍、参数邻域和按年度拆分，确认不是某几个行情段刚好适配。")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = v3.load_history()

    rows: list[dict[str, Any]] = []
    rows.append(
        run_base(
            v1,
            "V1 日频纯图形/动量",
            "每日开盘按五维分数调仓",
            histories,
            errors,
            {
                "summary": "etf_rotation_3y_backtest_summary_latest.md",
                "equity": "etf_rotation_3y_equity_latest.csv",
                "trades": "etf_rotation_3y_trades_latest.csv",
                "signals": "etf_rotation_3y_signals_latest.csv",
                "metrics": "etf_rotation_3y_metrics_latest.json",
            },
        )
    )
    rows.append(
        run_base(
            v2,
            "V2 周频+环境+冷却",
            "周频调仓，strong/neutral开仓，weak降现金，卖出后冷却10日",
            histories,
            errors,
            {
                "summary": "etf_rotation_3y_v2_weekly_env_cooldown_summary_latest.md",
                "equity": "etf_rotation_3y_v2_weekly_env_cooldown_equity_latest.csv",
                "trades": "etf_rotation_3y_v2_weekly_env_cooldown_trades_latest.csv",
                "signals": "etf_rotation_3y_v2_weekly_env_cooldown_signals_latest.csv",
                "metrics": "etf_rotation_3y_v2_weekly_env_cooldown_metrics_latest.json",
            },
        )
    )
    v3_row = run_base(
        v3,
        "V3 strong开仓+弱市现金",
        "只允许strong开新仓，neutral只持有，weak强制现金，至少持有5日",
        histories,
        errors,
        {
            "summary": "etf_rotation_3y_v3_strong_only_cash_minhold_summary_latest.md",
            "equity": "etf_rotation_3y_v3_strong_only_cash_minhold_equity_latest.csv",
            "trades": "etf_rotation_3y_v3_strong_only_cash_minhold_trades_latest.csv",
            "signals": "etf_rotation_3y_v3_strong_only_cash_minhold_signals_latest.csv",
            "metrics": "etf_rotation_3y_v3_strong_only_cash_minhold_metrics_latest.json",
        },
    )
    rows.append(v3_row)

    v4_rows = run_v4_variants(histories, errors, json.loads(Path(v3_row["metrics_file"]).read_text(encoding="utf-8"))["strategy"])
    rows.extend(v4_rows)

    comparison = build_overall_comparison(rows, errors)
    comparison_path = OUT_DIR / "etf_rotation_3y_cleaned_universe_strategy_comparison_latest.md"
    comparison_json_path = OUT_DIR / "etf_rotation_3y_cleaned_universe_strategy_comparison_latest.json"
    comparison_csv_path = OUT_DIR / "etf_rotation_3y_cleaned_universe_strategy_comparison_latest.csv"
    comparison_path.write_text(comparison, encoding="utf-8")
    comparison_json_path.write_text(json.dumps({"strategies": rows, "data_errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(comparison_csv_path, rows)

    print(comparison)
    print(f"wrote {comparison_path}")
    print(f"wrote {comparison_json_path}")
    print(f"wrote {comparison_csv_path}")


if __name__ == "__main__":
    main()
