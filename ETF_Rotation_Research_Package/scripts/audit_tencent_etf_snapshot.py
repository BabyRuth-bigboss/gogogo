#!/usr/bin/env python3
"""Write an auditable inventory of the cached Tencent ETF/index snapshot."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
START = os.environ.setdefault("V2_DATA_START_DATE", "2014-07-01")
END = os.environ.setdefault("V2_DATA_END_DATE", "2026-07-10")
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/data_snapshot_audit_2014_2026"
NOTE_PATH = Path("/Users/yansenz/Documents/note/ETF腾讯行情缓存审计_2014-07至2026-07.md")
DEFENSIVE_ETFS = [
    {"code": "511010", "theme": "防守", "label": "国债ETF"},
    {"code": "511360", "theme": "防守", "label": "短融ETF"},
    {"code": "518880", "theme": "防守", "label": "黄金ETF"},
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_module("snapshot_audit_base", ROOT / "scripts/backtest_etf_dynamic_pool_5y.py")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    for etf in DEFENSIVE_ETFS:
        if etf["code"] not in {item["code"] for item in base.live.ETF_UNIVERSE}:
            base.live.ETF_UNIVERSE.append(etf)
    base.KLINE_LIMIT = 1800
    histories, errors = base.load_history()
    if errors:
        raise RuntimeError(errors)
    labels = {item["code"]: item["label"] for item in base.live.ETF_UNIVERSE + base.live.MARKET_WATCH}
    labels.update({key: value["label"] for key, value in base.BENCHMARK_INDEXES.items()})
    sec_by_code = {item["code"]: base.tencent_sec(item["code"]) for item in base.live.ETF_UNIVERSE + base.live.MARKET_WATCH}
    sec_by_code.update({key: value["sec"] for key, value in base.BENCHMARK_INDEXES.items()})
    rows: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    for code, history in sorted(histories.items()):
        sec = sec_by_code[code]
        cache_path = base.CACHE_DIR / f"{sec}_{START}_{END}_{base.KLINE_LIMIT}.json"
        if not cache_path.exists():
            raise RuntimeError(f"missing cache: {cache_path}")
        raw = cache_path.read_bytes()
        source_rows = json.loads(raw.decode("utf-8"))
        dates = [str(row["date"]) for row in history]
        positive = sum(1 for row in source_rows if float(row.get("open", 0)) > 0 and float(row.get("close", 0)) > 0)
        split_events = sorted({str(row.get("split_events", "")) for row in history if row.get("split_events")})
        item = {
            "code": code,
            "label": labels.get(code, code),
            "tencent_sec": sec,
            "cache_path": str(cache_path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "raw_rows": len(source_rows),
            "adjusted_rows": len(history),
            "start": dates[0] if dates else "",
            "end": dates[-1] if dates else "",
            "unique_dates": len(set(dates)),
            "strict_date_order": dates == sorted(dates) and len(dates) == len(set(dates)),
            "invalid_open_or_close_rows": len(source_rows) - positive,
            "eligible_after_252_days": dates[252] if len(dates) > 252 else "",
            "split_adjusted": bool(split_events),
            "split_events": ";".join(split_events),
        }
        rows.append(item)
        files[code] = {key: item[key] for key in ("label", "tencent_sec", "cache_path", "sha256", "raw_rows", "start", "end")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "tencent_snapshot_audit_latest.csv", rows)
    manifest = {
        "requested_window": {"start": START, "end": END},
        "cache_root": str(base.CACHE_DIR),
        "endpoint": "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        "query_template": "param={tencent_sec},day,{start},{end},{limit},",
        "raw_fields": ["date", "open", "close", "high", "low", "volume"],
        "derived_fields": ["amount_yi", "raw_open", "raw_close", "raw_high", "raw_low", "split_adjust_factor", "split_events"],
        "handling": {
            "long_window": "Requests longer than approximately 2200 calendar days are fetched in two windows, date-deduplicated, then adjusted once.",
            "split_adjustment": "Large ETF split/reverse-split discontinuities are made price-continuous. This is not dividend total-return adjustment.",
            "eligibility": "An ETF must have at least 252 observations before scoring.",
            "execution": "Signals use prior close; trades use next trading-day open; baseline one-way cost is 0.105%.",
        },
        "files": files,
    }
    (OUT_DIR / "tencent_snapshot_manifest_latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    invalid = [row for row in rows if not row["strict_date_order"] or row["invalid_open_or_close_rows"]]
    report = [
        "# 腾讯ETF历史行情缓存审计", "",
        f"- 缓存区间：{START} 至 {END}；共 {len(rows)} 组ETF/指数历史。",
        "- 接口：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`，请求参数格式：`{tencent_sec},day,{start},{end},{limit},`。",
        "- 原始字段：日期、开盘、收盘、最高、最低、成交量；缓存保留接口原始日线JSON。",
        "- 回测使用：对ETF大比例拆分/合并做价格连续化调整；不等同于分红再投资复权。",
        "- 核查结果：日期排序/重复日期/开收盘正值异常共 %d 组。" % len(invalid),
        "", "| 代码 | 标的 | 行数 | 起始 | 截止 | 252日可用起点 | SHA-256前16位 |", "|---|---|---:|---|---|---|---|",
    ]
    for row in rows:
        report.append(f"| {row['code']} | {row['label']} | {row['adjusted_rows']} | {row['start']} | {row['end']} | {row['eligible_after_252_days']} | {row['sha256'][:16]} |")
    report.extend(["", f"- 完整审计表：`{OUT_DIR / 'tencent_snapshot_audit_latest.csv'}`", f"- 完整清单：`{OUT_DIR / 'tencent_snapshot_manifest_latest.json'}`", ""])
    output = "\n".join(report)
    (OUT_DIR / "tencent_snapshot_audit_latest.md").write_text(output, encoding="utf-8")
    NOTE_PATH.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
