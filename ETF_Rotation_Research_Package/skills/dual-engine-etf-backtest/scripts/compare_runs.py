#!/usr/bin/env python3
"""Compare two saved ETF backtest runs without fetching or changing data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def find_csv(root: Path, kind: str) -> Path | None:
    preferred = root / f"{kind}.csv"
    if preferred.exists():
        return preferred
    matches = sorted(root.rglob(f"*{kind}*.csv"))
    return matches[0] if matches else None


def read_csv(path: Path | None) -> pd.DataFrame:
    return pd.read_csv(path) if path else pd.DataFrame()


def date_col(df: pd.DataFrame) -> str | None:
    for name in ("timestamp", "signal_date", "trade_date", "date"):
        if name in df.columns:
            return name
    return None


def normalise_code(value: Any) -> str:
    return str(value).split(".", 1)[0]


def normalise_events(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    dc = date_col(out)
    if dc:
        out["_date"] = pd.to_datetime(out[dc], errors="coerce").dt.strftime("%Y-%m-%d")
    source = "code" if "code" in out else "symbol" if "symbol" in out else None
    if source:
        out["_code"] = out[source].map(normalise_code)
    if "side" in out:
        out["_side"] = out["side"].astype(str).str.upper()
    return out


def equity_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "equity" not in df:
        return {}
    out = df.copy()
    dc = date_col(out)
    if dc:
        out["_date"] = pd.to_datetime(out[dc], errors="coerce")
        out = out.sort_values("_date").dropna(subset=["_date"])
    series = pd.to_numeric(out["equity"], errors="coerce").dropna()
    if len(series) < 2:
        return {"final_value": float(series.iloc[-1]) if len(series) else None}
    returns = series.pct_change().fillna(0.0)
    years = max((out["_date"].iloc[-1] - out["_date"].iloc[0]).days / 365.25, 1 / 365.25) if "_date" in out else 1.0
    return {"total_return": float(series.iloc[-1] / series.iloc[0] - 1), "cagr": float((series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1), "max_drawdown": float((series / series.cummax() - 1).min()), "sharpe": float(252**0.5 * returns.mean() / returns.std(ddof=0)) if returns.std(ddof=0) else None, "final_value": float(series.iloc[-1]), "rows": int(len(series))}


def snapshot_manifest(root: Path) -> dict[str, Any]:
    symbols = {}
    for path in sorted(root.rglob("ohlcv_*.csv")):
        df = pd.read_csv(path)
        dc = "trade_date" if "trade_date" in df else "date" if "date" in df else None
        code = path.stem.removeprefix("ohlcv_")
        symbols[normalise_code(code)] = {"rows": int(len(df)), "start": str(df[dc].iloc[0]) if dc and len(df) else None, "end": str(df[dc].iloc[-1]) if dc and len(df) else None, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"count": len(symbols), "symbols": symbols}


def compare_snapshots(a: Path, b: Path) -> dict[str, Any]:
    sa, sb = set(snapshot_manifest(a)["symbols"]), set(snapshot_manifest(b)["symbols"])
    return {"a_count": len(sa), "b_count": len(sb), "common": len(sa & sb), "a_only": sorted(sa - sb), "b_only": sorted(sb - sa)}


def compare_events(a: pd.DataFrame, b: pd.DataFrame) -> dict[str, Any]:
    aa, bb = normalise_events(a), normalise_events(b)
    if aa.empty or bb.empty or "_date" not in aa or "_date" not in bb:
        return {"available": False}
    extra = [x for x in ("targets", "selected", "market_bad", "price", "fill_price") if x in aa and x in bb]
    key = [x for x in ("_date", "_code", "_side", *extra) if x in aa and x in bb]
    left = set(map(tuple, aa[key].fillna("").astype(str).itertuples(index=False, name=None)))
    right = set(map(tuple, bb[key].fillna("").astype(str).itertuples(index=False, name=None)))
    return {"available": True, "a_rows": len(aa), "b_rows": len(bb), "same_keys": len(left & right), "a_only": len(left - right), "b_only": len(right - left), "first_a_only": sorted(left - right)[:1], "first_b_only": sorted(right - left)[:1]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a", required=True, help="T0 run directory")
    parser.add_argument("--b", required=True, help="T1 run directory")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()
    a, b = Path(args.a), Path(args.b)
    af = {kind: find_csv(a, kind) for kind in ("signals", "trades", "equity")}
    bf = {kind: find_csv(b, kind) for kind in ("signals", "trades", "equity")}
    result = {"a": str(a), "b": str(b), "metrics": {"a": equity_metrics(read_csv(af["equity"])), "b": equity_metrics(read_csv(bf["equity"]))}, "snapshots": compare_snapshots(a, b), "signals": compare_events(read_csv(af["signals"]), read_csv(bf["signals"])), "trades": compare_events(read_csv(af["trades"]), read_csv(bf["trades"]))}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "dual_comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    m, s = result["metrics"], result["snapshots"]
    lines = ["# 双引擎 ETF 回测对比", "", f"- T0: `{args.a}`", f"- T1: `{args.b}`", "", "## 指标", "", "| 指标 | T0 | T1 |", "|---|---:|---:|"]
    for key in ("total_return", "cagr", "max_drawdown", "sharpe", "final_value"):
        lines.append(f"| {key} | {m['a'].get(key)} | {m['b'].get(key)} |")
    lines += ["", "## 快照", "", f"- T0 标的数：{s['a_count']}", f"- T1 标的数：{s['b_count']}", f"- 共同标的：{s['common']}", f"- T0 独有：{', '.join(s['a_only']) or '无'}", f"- T1 独有：{', '.join(s['b_only']) or '无'}", "", "## 信号与成交", "", f"- 信号：{result['signals']}", f"- 成交：{result['trades']}", "", "结论必须结合快照、信号和成交差异判断，不能只依据最终收益。"]
    (out / "dual_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out / "dual_comparison.md")


if __name__ == "__main__":
    main()
