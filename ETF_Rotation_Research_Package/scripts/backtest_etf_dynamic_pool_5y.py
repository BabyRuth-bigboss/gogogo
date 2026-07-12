#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import os
import statistics
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "scripts/etf_momentum_rotation.py"
LAB_SCRIPT = ROOT / "scripts/backtest_etf_strategy_lab.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_cost_10bp_slippage"
CACHE_DIR = Path(os.environ.get("V2_CACHE_DIR", str(ROOT / "a_stock_daily_workflow/etf_rotation/cache/tencent_history")))

INITIAL_CASH = 1_000_000.0
KLINE_LIMIT = 1800
BACKTEST_YEARS = 5
COMMISSION_RATE = 0.00005
SLIPPAGE_RATE = 0.00100
FEE_RATE = COMMISSION_RATE + SLIPPAGE_RATE
FETCH_RETRIES = 4
DATA_START_DATE = os.environ.get("V2_DATA_START_DATE", "")
DATA_END_DATE = os.environ.get("V2_DATA_END_DATE", "")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live = load_module("etf_dynamic_5y_live", LIVE_SCRIPT)
lab = load_module("etf_dynamic_5y_lab", LAB_SCRIPT)
v3 = lab.v3


def fallback_portfolio_equity(
    cash: float,
    positions: dict[str, float],
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    date: str,
    field: str,
) -> float:
    equity = cash
    for code, shares in positions.items():
        price = v3.price_at(histories, indexes, code, date, field)
        if price is None:
            rows = histories.get(code, [])
            dates = [str(row["date"]) for row in rows]
            prev_idx = bisect_right(dates, date) - 1
            if prev_idx >= 0:
                price = fnum(rows[prev_idx].get("close"))
        if price is not None and price > 0:
            equity += shares * price
    return equity


# Keep trading strict through v3.price_at, but value suspended/missing-position
# days with the latest available close so drawdown is not polluted by missing rows.
v3.portfolio_equity = fallback_portfolio_equity


STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "rs612_top2_month_start_3",
        "label": "RS612 Top2｜月初第3日",
        "model": "rs612",
        "top_n": 2,
        "filter": "etf_ma200_ret12_pos",
        "weighting": "equal",
        "schedule": "month_start_3",
        "fee_rate": FEE_RATE,
    },
    {
        "id": "core_satellite_50_50_month_start",
        "label": "核心卫星50/50｜无闸门/月初",
        "model": "core_satellite",
        "top_n": 2,
        "filter": "etf_ma120_ret6_pos",
        "weighting": "core_satellite",
        "schedule": "month_start",
        "fee_rate": FEE_RATE,
    },
]


BENCHMARK_INDEXES = {
    "IDX_HS300": {"sec": "sh000300", "label": "沪深300指数"},
    "IDX_CHINEXT": {"sec": "sz399006", "label": "创业板指"},
    "IDX_SCI50": {"sec": "sh000688", "label": "科创50指数"},
}


def tencent_sec(code: str) -> str:
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None, "-"):
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def split_adjust_prices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Tencent raw ETF history can contain large unit splits/reverse-splits that
    # qfqday would normally smooth, but qfqday is too short for this 5-year test.
    # Detect only extreme overnight jumps and adjust earlier prices to a continuous
    # total-return-like series. Keep amount_yi raw for liquidity filtering.
    if len(rows) < 2:
        return rows
    factors = [1.0 for _ in rows]
    factor = 1.0
    split_events: list[str] = []
    for i in range(len(rows) - 2, -1, -1):
        prev_close = fnum(rows[i].get("raw_close"))
        next_open = fnum(rows[i + 1].get("raw_open"))
        if prev_close > 0 and next_open > 0:
            ratio = next_open / prev_close
            if ratio < 0.55 or ratio > 1.85:
                factor *= ratio
                split_events.append(f"{rows[i]['date']}->{rows[i + 1]['date']}:{ratio:.4f}")
        factors[i] = factor
    out: list[dict[str, Any]] = []
    for row, adj in zip(rows, factors):
        new_row = dict(row)
        for field in ("open", "high", "low", "close"):
            new_row[field] = fnum(row.get(f"raw_{field}")) * adj
        new_row["split_adjust_factor"] = adj
        new_row["split_events"] = ";".join(reversed(split_events))
        out.append(new_row)
    return out


def _fetch_tencent_window(sec: str, start: str, end: str, limit: int) -> list[dict[str, Any]]:
    params = urlencode({"param": f"{sec},day,{start},{end},{limit},"})
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + params
    req = Request(url, headers={"User-Agent": live.UA, "Referer": "https://gu.qq.com/"})
    with urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    raw_data = payload.get("data", {}).get(sec, {})
    raw = raw_data.get("day") or raw_data.get("qfqday") or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if len(item) < 6:
            continue
        close = fnum(item[2])
        open_price = fnum(item[1])
        high = fnum(item[3])
        low = fnum(item[4])
        volume = fnum(item[5])
        rows.append(
            {
                "date": item[0],
                "open": open_price,
                "close": close,
                "high": high,
                "low": low,
                "raw_open": open_price,
                "raw_close": close,
                "raw_high": high,
                "raw_low": low,
                "volume": volume,
                "amount_yi": close * volume * 100 / 100_000_000 if close > 0 and volume > 0 else 0.0,
            }
        )
    return rows


def fetch_tencent_adjusted_day(sec: str, limit: int = KLINE_LIMIT) -> list[dict[str, Any]]:
    # Tencent caps a single response near 2000 rows. Split long historical
    # windows, merge and then apply one continuous split adjustment.
    cache_key = "_".join([sec, DATA_START_DATE or "latest", DATA_END_DATE or "latest", str(limit)])
    cache_path = CACHE_DIR / (cache_key.replace("/", "_") + ".json")
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if isinstance(cached, list) and cached:
            return split_adjust_prices(cached)
    if DATA_START_DATE and DATA_END_DATE:
        start = dt.date.fromisoformat(DATA_START_DATE)
        end = dt.date.fromisoformat(DATA_END_DATE)
        if (end - start).days > 2200:
            windows = [(DATA_START_DATE, "2020-12-31"), ("2021-01-01", DATA_END_DATE)]
        else:
            windows = [(DATA_START_DATE, DATA_END_DATE)]
    else:
        windows = [(DATA_START_DATE, DATA_END_DATE)]
    rows: list[dict[str, Any]] = []
    for start, end in windows:
        rows.extend(_fetch_tencent_window(sec, start, end, min(limit, 2000)))
    unique = {row["date"]: row for row in rows}
    merged = [unique[key] for key in sorted(unique)]
    if merged:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(cache_path)
    return split_adjust_prices(merged)


def fetch_one(item: dict[str, str]) -> tuple[str, list[dict[str, Any]], str | None]:
    code = item["code"]
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            rows = fetch_tencent_adjusted_day(tencent_sec(code))
            if not rows:
                raise RuntimeError("Tencent returned no daily rows")
            return code, rows, None
        except Exception as exc:
            last_error = exc
            if attempt + 1 < FETCH_RETRIES:
                time.sleep(0.5 * (2**attempt))
    return code, [], str(last_error)


def fetch_index_one(key: str, sec: str) -> tuple[str, list[dict[str, Any]], str | None]:
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            rows = fetch_tencent_adjusted_day(sec)
            if not rows:
                raise RuntimeError("Tencent returned no daily rows")
            return key, rows, None
        except Exception as exc:
            last_error = exc
            if attempt + 1 < FETCH_RETRIES:
                time.sleep(0.5 * (2**attempt))
    return key, [], str(last_error)


def load_history() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    data: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    items = live.ETF_UNIVERSE + live.MARKET_WATCH
    # Tencent occasionally closes concurrent TLS connections with EOF. Keep
    # concurrency moderate and retry individual symbols with backoff above.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_one, item) for item in items]
        futures += [pool.submit(fetch_index_one, key, item["sec"]) for key, item in BENCHMARK_INDEXES.items()]
        for future in as_completed(futures):
            code, rows, err = future.result()
            if err:
                errors[code] = err
            else:
                data[code] = rows
    return data, errors


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


def fmt_y(value: dict[str, float]) -> str:
    return "; ".join(f"{year}:{fmt_pct(ret)}" for year, ret in sorted(value.items()))


def coverage_rows(histories: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    labels = {item["code"]: item["label"] for item in live.ETF_UNIVERSE + live.MARKET_WATCH}
    themes = {item["code"]: item["theme"] for item in live.ETF_UNIVERSE + live.MARKET_WATCH}
    rows: list[dict[str, Any]] = []
    for code in [item["code"] for item in live.ETF_UNIVERSE]:
        hist = histories.get(code, [])
        rows.append(
            {
                "code": code,
                "label": labels.get(code, code),
                "theme": themes.get(code, ""),
                "rows": len(hist),
                "start": hist[0]["date"] if hist else "",
                "end": hist[-1]["date"] if hist else "",
                "eligible_from": hist[252]["date"] if len(hist) > 252 else "",
                "has_5y_raw_history": bool(len(hist) >= 1200),
            }
        )
    return sorted(rows, key=lambda row: (str(row["start"]), str(row["code"])))


def benchmark_metrics(histories: dict[str, list[dict[str, Any]]], dates: list[str]) -> dict[str, dict[str, Any]]:
    return {
        "沪深300": v3.perf_metrics(v3.benchmark_series(histories, "IDX_HS300", dates)),
        "创业板指": v3.perf_metrics(v3.benchmark_series(histories, "IDX_CHINEXT", dates)),
        "科创50": v3.perf_metrics(v3.benchmark_series(histories, "IDX_SCI50", dates)),
    }


def month_selected_counts(signals: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for row in signals:
        if not row.get("rebalance_day"):
            continue
        selected = str(row.get("selected", "") or "")
        for label in [part for part in selected.split(";") if part]:
            out[label] += 1
    return dict(sorted(out.items(), key=lambda item: item[1], reverse=True))


def latest_positions(equity: list[dict[str, Any]], histories: dict[str, list[dict[str, Any]]]) -> str:
    if not equity:
        return ""
    latest = equity[-1]
    codes = [code for code in str(latest.get("positions", "")).split(";") if code]
    labels = {item["code"]: item["label"] for item in live.ETF_UNIVERSE + live.MARKET_WATCH}
    return ";".join(f"{labels.get(code, code)}({code})" for code in codes)


def build_summary(
    results: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    errors: dict[str, str],
    benchmarks: dict[str, dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# ETF 5年真实动态池回测")
    lines.append("")
    lines.append(f"- 生成日期：{dt.date.today().isoformat()}")
    lines.append(f"- 回测口径：真实ETF动态池，ETF必须已有252个交易日历史才允许参与打分。")
    lines.append(f"- 标的池：当前ETF池 {len(live.ETF_UNIVERSE)} 只；不是固定幸存者全程可买。")
    lines.append(f"- 数据源：腾讯日线 raw day，最多 {KLINE_LIMIT} 根；对ETF份额拆分/合并做连续化调整。")
    lines.append(f"- 成本：佣金万0.5 + 滑点0.10%，单边合计 {FEE_RATE*100:.3f}%。")
    lines.append("- 信号：调仓日前一交易日收盘后计算；成交：下一交易日开盘成交。")
    lines.append("- 停牌/缺当天K线处理：持仓估值用上一可得收盘价兜底；交易仍要求成交日有开盘价。")
    lines.append("- 注意：拆分调整只修正大比例份额拆分/合并，不等同于完整分红复权；红利类收益仍可能被低估。")
    lines.append("")

    lines.append("## 核心结果")
    lines.append("")
    lines.append("| 策略 | 区间 | 总收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 交易笔数 | 当前持仓 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for row in results:
        metrics = row["metrics"]
        lines.append(
            f"| {row['label']} | {metrics['start']}~{metrics['end']} | "
            f"{fmt_pct(metrics['total_return'])} | {fmt_pct(metrics['cagr'])} | {fmt_pct(metrics['max_drawdown'])} | "
            f"{metrics['sharpe']:.2f} | {fmt_pct(metrics['avg_exposure'])} | {row['trade_count']} | {row['latest_positions']} |"
        )

    lines.append("")
    lines.append("## 年度收益")
    lines.append("")
    lines.append("| 策略 | 年度表现 |")
    lines.append("|---|---|")
    for row in results:
        lines.append(f"| {row['label']} | {fmt_y(row['metrics']['yearly'])} |")

    lines.append("")
    lines.append("## 基准")
    lines.append("")
    lines.append("| 基准 | 总收益 | 年化 | 最大回撤 | Sharpe |")
    lines.append("|---|---:|---:|---:|---:|")
    for label, metrics in benchmarks.items():
        lines.append(
            f"| {label} | {fmt_pct(metrics['total_return'])} | {fmt_pct(metrics['cagr'])} | "
            f"{fmt_pct(metrics['max_drawdown'])} | {metrics['sharpe']:.2f} |"
        )

    lines.append("")
    lines.append("## 入选频率Top10")
    lines.append("")
    for row in results:
        top = list(row["selected_counts"].items())[:10]
        lines.append(f"### {row['label']}")
        if not top:
            lines.append("- 无")
        else:
            lines.append("| 标的 | 调仓入选次数 |")
            lines.append("|---|---:|")
            for label, count in top:
                lines.append(f"| {label} | {count} |")
        lines.append("")

    lines.append("## 动态池覆盖审计")
    lines.append("")
    enough = sum(1 for row in coverage if row["has_5y_raw_history"])
    lines.append(f"- 5年左右原始历史充足（>=1200根）：{enough}/{len(coverage)} 只。")
    lines.append("- 历史不足的ETF不会被删除，只是在其上市并积累252个交易日以后才进入候选池。")
    lines.append("")
    lines.append("| 代码 | 名称 | 行业 | K线起点 | K线终点 | 可参与打分起点 | 行数 |")
    lines.append("|---|---|---|---|---|---|---:|")
    for row in coverage:
        lines.append(
            f"| {row['code']} | {row['label']} | {row['theme']} | {row['start']} | {row['end']} | "
            f"{row['eligible_from']} | {row['rows']} |"
        )

    if errors:
        lines.append("")
        lines.append("## 数据错误")
        for code, err in errors.items():
            lines.append(f"- {code}: {err}")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    histories, errors = load_history()
    dates = v3.common_calendar(histories)
    end_date = dates[-1]
    start_date = (dt.date.fromisoformat(end_date) - dt.timedelta(days=int(365.25 * BACKTEST_YEARS))).isoformat()

    results: list[dict[str, Any]] = []
    bench_cache: dict[str, dict[str, Any]] | None = None
    for config in STRATEGIES:
        strategy = dict(config)
        strategy["start_date"] = start_date
        equity, trades, signals = lab.simulate_strategy(strategy, histories)
        metrics = v3.perf_metrics(equity)
        bench_cache = benchmark_metrics(histories, [row["date"] for row in equity])
        prefix = OUT_DIR / strategy["id"]
        write_csv(prefix.with_name(prefix.name + "_equity_latest.csv"), equity)
        write_csv(prefix.with_name(prefix.name + "_trades_latest.csv"), trades)
        write_csv(prefix.with_name(prefix.name + "_signals_latest.csv"), signals)
        prefix.with_name(prefix.name + "_metrics_latest.json").write_text(
            json.dumps({"metrics": metrics, "benchmarks": bench_cache, "config": strategy}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(
            {
                "id": strategy["id"],
                "label": strategy["label"],
                "metrics": metrics,
                "trade_count": len(trades),
                "latest_positions": latest_positions(equity, histories),
                "selected_counts": month_selected_counts(signals),
            }
        )

    coverage = coverage_rows(histories)
    write_csv(OUT_DIR / "dynamic_pool_5y_coverage_latest.csv", coverage)
    write_csv(
        OUT_DIR / "dynamic_pool_5y_metrics_latest.csv",
        [
            {
                "id": row["id"],
                "label": row["label"],
                **{k: v for k, v in row["metrics"].items() if k != "yearly"},
                "yearly": json.dumps(row["metrics"].get("yearly", {}), ensure_ascii=False),
                "trade_count": row["trade_count"],
                "latest_positions": row["latest_positions"],
            }
            for row in results
        ],
    )
    summary = build_summary(results, coverage, errors, bench_cache or {})
    summary_path = OUT_DIR / "dynamic_pool_5y_summary_latest.md"
    json_path = OUT_DIR / "dynamic_pool_5y_metrics_latest.json"
    summary_path.write_text(summary, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "results": results,
                "benchmarks": bench_cache,
                "coverage": coverage,
                "errors": errors,
                "data_note": "Tencent raw day with automatic split/reverse-split price continuity adjustment; dynamic ETF pool with 252 trading-day eligibility gate.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
