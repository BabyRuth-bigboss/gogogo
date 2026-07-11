#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIVE_SCRIPT = ROOT / "scripts/etf_momentum_rotation.py"
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/universe_audit"
START_DATE = "2020-01-01"
END_DATE = "2025-12-31"
MIN_COVERAGE_YEARS = 3.0
DELETE_CAGR_THRESHOLD = -0.10
KLINE_LIMIT = 1800


def load_live_module():
    spec = importlib.util.spec_from_file_location("etf_momentum_rotation_live", LIVE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LIVE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live = load_live_module()


def fnum(value: Any, default: float = 0.0) -> float:
    return live.fnum(value, default)


def fetch_audit_row(item: dict[str, str]) -> dict[str, Any]:
    code = item["code"]
    try:
        rows = live.fetch_tencent_klines(code, limit=KLINE_LIMIT)
        rows = [row for row in rows if START_DATE <= str(row.get("date")) <= END_DATE and fnum(row.get("close")) > 0]
        if len(rows) < 2:
            return {
                "code": code,
                "label": item["label"],
                "theme": item["theme"],
                "status": "INSUFFICIENT",
                "reason": "2020-2025无足够K线",
            }
        start = rows[0]
        end = rows[-1]
        start_date = str(start["date"])
        end_date = str(end["date"])
        start_close = fnum(start["close"])
        end_close = fnum(end["close"])
        days = (dt.date.fromisoformat(end_date) - dt.date.fromisoformat(start_date)).days
        years = days / 365.25 if days > 0 else 0.0
        total_return = end_close / start_close - 1
        cagr = (end_close / start_close) ** (1 / years) - 1 if years > 0 else 0.0
        status = "KEEP"
        reason = ""
        if years < MIN_COVERAGE_YEARS:
            status = "INSUFFICIENT"
            reason = f"覆盖{years:.2f}年<3年"
        elif cagr <= DELETE_CAGR_THRESHOLD:
            status = "DELETE"
            reason = f"年化{cagr*100:.2f}%<=-10%"
        return {
            "code": code,
            "label": item["label"],
            "theme": item["theme"],
            "status": status,
            "reason": reason,
            "start_date": start_date,
            "end_date": end_date,
            "start_close": f"{start_close:.4f}",
            "end_close": f"{end_close:.4f}",
            "years": f"{years:.2f}",
            "total_return": f"{total_return:.6f}",
            "cagr": f"{cagr:.6f}",
            "rows": len(rows),
        }
    except Exception as exc:
        return {
            "code": code,
            "label": item["label"],
            "theme": item["theme"],
            "status": "ERROR",
            "reason": str(exc),
        }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "code",
        "label",
        "theme",
        "status",
        "reason",
        "start_date",
        "end_date",
        "start_close",
        "end_close",
        "years",
        "total_return",
        "cagr",
        "rows",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def pct(value: str | float) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except Exception:
        return ""


def build_summary(rows: list[dict[str, Any]]) -> str:
    deletes = [row for row in rows if row["status"] == "DELETE"]
    insufficient = [row for row in rows if row["status"] == "INSUFFICIENT"]
    keep = [row for row in rows if row["status"] == "KEEP"]
    lines = ["# ETF长期收益审计 2020-2025", ""]
    lines.append(f"规则：覆盖至少{MIN_COVERAGE_YEARS:.0f}年，年化收益 <= {DELETE_CAGR_THRESHOLD*100:.0f}% 删除；历史不足只标注不删除。")
    lines.append("")
    lines.append(f"- 当前池：{len(rows)}只")
    lines.append(f"- 删除：{len(deletes)}只")
    lines.append(f"- 保留：{len(keep)}只")
    lines.append(f"- 历史不足：{len(insufficient)}只")
    lines.append("")
    lines.append("## 删除名单")
    if not deletes:
        lines.append("- 无")
    else:
        lines.append("| 代码 | 名称 | 区间 | 总收益 | 年化 | 原因 |")
        lines.append("|---|---|---|---:|---:|---|")
        for row in sorted(deletes, key=lambda item: float(item.get("cagr", 0))):
            lines.append(
                f"| {row['code']} | {row['label']} | {row.get('start_date','')}~{row.get('end_date','')} | "
                f"{pct(row.get('total_return', 0))} | {pct(row.get('cagr', 0))} | {row.get('reason','')} |"
            )
    lines.append("")
    lines.append("## 历史不足")
    if not insufficient:
        lines.append("- 无")
    else:
        lines.append("| 代码 | 名称 | 区间 | 年化 | 说明 |")
        lines.append("|---|---|---|---:|---|")
        for row in sorted(insufficient, key=lambda item: item.get("start_date", "")):
            lines.append(
                f"| {row['code']} | {row['label']} | {row.get('start_date','')}~{row.get('end_date','')} | "
                f"{pct(row.get('cagr', 0))} | {row.get('reason','')} |"
            )
    lines.append("")
    lines.append("## 收益排名")
    lines.append("| 代码 | 名称 | 区间 | 总收益 | 年化 | 状态 |")
    lines.append("|---|---|---|---:|---:|---|")
    sortable = [row for row in rows if row.get("cagr") not in (None, "")]
    for row in sorted(sortable, key=lambda item: float(item.get("cagr", 0))):
        lines.append(
            f"| {row['code']} | {row['label']} | {row.get('start_date','')}~{row.get('end_date','')} | "
            f"{pct(row.get('total_return', 0))} | {pct(row.get('cagr', 0))} | {row['status']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_audit_row, item) for item in live.ETF_UNIVERSE]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row.get("status", ""), row.get("code", "")))
    csv_path = OUT_DIR / "etf_long_term_returns_2020_2025_latest.csv"
    md_path = OUT_DIR / "etf_long_term_returns_2020_2025_latest.md"
    json_path = OUT_DIR / "etf_long_term_returns_2020_2025_latest.json"
    write_csv(csv_path, rows)
    md_path.write_text(build_summary(rows), encoding="utf-8")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {csv_path}")
    print(f"wrote {md_path}")


if __name__ == "__main__":
    main()
