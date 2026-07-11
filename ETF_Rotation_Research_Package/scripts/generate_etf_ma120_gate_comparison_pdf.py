#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output/pdf"
OUT_PDF = OUT_DIR / "etf_ma120_gate_rs612_vs_rs36.pdf"

RS612_CSV = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_market_gate_cost_10bp_slippage/market_gate_5y_metrics_latest.csv"
RS36_CSV = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/dynamic_pool_5y_rs36_ma_gate_cost_10bp_slippage/rs36_ma_gate_5y_metrics_latest.csv"


def read_strategy_row(path: Path, strategy_id: str) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("strategy_id") == strategy_id and row.get("gate_id") == "majority_below_ma120":
                row["yearly_json"] = json.loads(row.get("yearly") or "{}")
                return row
    raise RuntimeError(f"row not found: {strategy_id} in {path}")


def pct(value: str | float, signed: bool = True) -> str:
    number = float(value) * 100
    sign = "+" if signed and number >= 0 else ""
    return f"{sign}{number:.2f}%"


def num(value: str | float, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


def draw_text(c: canvas.Canvas, text: str, x: float, y: float, size: int = 10, color=colors.HexColor("#20242A"), font: str = "CNFont") -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, text)


def draw_center(c: canvas.Canvas, text: str, x: float, y: float, size: int = 10, color=colors.HexColor("#20242A"), font: str = "CNFont") -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(x, y, text)


def draw_right(c: canvas.Canvas, text: str, x: float, y: float, size: int = 10, color=colors.HexColor("#20242A"), font: str = "CNFont") -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawRightString(x, y, text)


def pill(c: canvas.Canvas, text: str, x: float, y: float, w: float, h: float, fill: str, color: str = "#FFFFFF") -> None:
    c.setFillColor(colors.HexColor(fill))
    c.roundRect(x, y, w, h, 6, stroke=0, fill=1)
    draw_center(c, text, x + w / 2, y + h / 2 - 4, 10, colors.HexColor(color))


def card(c: canvas.Canvas, x: float, y: float, w: float, h: float, fill: str = "#FFFFFF", stroke: str = "#D9DEE7") -> None:
    c.setFillColor(colors.HexColor(fill))
    c.setStrokeColor(colors.HexColor(stroke))
    c.roundRect(x, y, w, h, 8, stroke=1, fill=1)


def draw_metric_box(c: canvas.Canvas, label: str, value: str, x: float, y: float, w: float, color: str) -> None:
    card(c, x, y, w, 58, "#F8FAFC", "#DDE3EC")
    draw_text(c, label, x + 12, y + 37, 9, colors.HexColor("#697386"))
    draw_text(c, value, x + 12, y + 13, 17, colors.HexColor(color))


def draw_header(c: canvas.Canvas, page: int) -> None:
    w, h = A4
    c.setFillColor(colors.HexColor("#111827"))
    c.rect(0, h - 74, w, 74, stroke=0, fill=1)
    draw_text(c, "ETF轮动策略对比", 42, h - 33, 20, colors.white)
    draw_text(c, "RS612 Top2 vs RS36 Top3 - 2/3指数低于MA120空仓", 42, h - 56, 10, colors.HexColor("#C9D2E3"))
    draw_right(c, f"Page {page}", w - 42, h - 52, 9, colors.HexColor("#C9D2E3"))


def draw_comparison_table(c: canvas.Canvas, rows: list[dict[str, Any]], x: float, y: float, w: float) -> None:
    headers = ["策略", "总收益", "年化", "最大回撤", "Sharpe", "平均仓位", "交易"]
    col_widths = [142, 68, 62, 74, 52, 70, 43]
    row_h = 38
    c.setFillColor(colors.HexColor("#EEF2F7"))
    c.roundRect(x, y, w, 28, 6, stroke=0, fill=1)
    cur = x
    for header, cw in zip(headers, col_widths):
        draw_center(c, header, cur + cw / 2, y + 9, 9, colors.HexColor("#4B5563"))
        cur += cw
    for i, row in enumerate(rows):
        yy = y - (i + 1) * row_h
        c.setFillColor(colors.HexColor("#FFFFFF") if i == 0 else colors.HexColor("#FBFCFE"))
        c.setStrokeColor(colors.HexColor("#E5EAF2"))
        c.rect(x, yy, w, row_h, stroke=1, fill=1)
        vals = [
            row["short_name"],
            pct(row["total_return"]),
            pct(row["cagr"]),
            pct(row["max_drawdown"]),
            num(row["sharpe"]),
            pct(row["avg_exposure"]),
            str(row["trades"]),
        ]
        cur = x
        for j, (val, cw) in enumerate(zip(vals, col_widths)):
            color = "#0F766E" if j in (1, 2, 4) and i == 0 else "#20242A"
            if j == 3:
                color = "#B42318"
            if j == 0:
                draw_text(c, val, cur + 8, yy + 14, 9, colors.HexColor(color))
            else:
                draw_center(c, val, cur + cw / 2, yy + 14, 9, colors.HexColor(color))
            cur += cw


def draw_bar(c: canvas.Canvas, label: str, value: float, max_value: float, x: float, y: float, w: float, color: str, value_text: str) -> None:
    draw_text(c, label, x, y + 5, 9, colors.HexColor("#394150"))
    c.setFillColor(colors.HexColor("#E7ECF3"))
    c.roundRect(x + 88, y, w, 12, 6, stroke=0, fill=1)
    fill_w = max(1, min(w, w * value / max_value))
    c.setFillColor(colors.HexColor(color))
    c.roundRect(x + 88, y, fill_w, 12, 6, stroke=0, fill=1)
    draw_right(c, value_text, x + 88 + w + 56, y + 2, 9, colors.HexColor("#20242A"))


def draw_rule_block(c: canvas.Canvas, title: str, lines: list[str], x: float, y: float, w: float, h: float, accent: str) -> None:
    card(c, x, y, w, h, "#FFFFFF", "#D9DEE7")
    c.setFillColor(colors.HexColor(accent))
    c.roundRect(x + 14, y + h - 34, 92, 20, 5, stroke=0, fill=1)
    draw_center(c, title, x + 60, y + h - 29, 9, colors.white)
    text_y = y + h - 58
    for line in lines:
        draw_text(c, line, x + 18, text_y, 9, colors.HexColor("#303742"))
        text_y -= 18


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont("CNFont", "/System/Library/Fonts/STHeiti Medium.ttc", subfontIndex=0))
    rs612 = read_strategy_row(RS612_CSV, "rs612_top2_month_start_3")
    rs36 = read_strategy_row(RS36_CSV, "rs36_top3_month_start")
    rs612["short_name"] = "RS612 Top2"
    rs36["short_name"] = "RS36 Top3"
    rows = [rs612, rs36]

    c = canvas.Canvas(str(OUT_PDF), pagesize=A4)
    c.setTitle("ETF MA120 Gate Strategy Comparison")
    c.setAuthor("Codex")
    w, h = A4
    margin = 42

    draw_header(c, 1)
    pill(c, "结论", margin, h - 112, 54, 22, "#2563EB")
    draw_text(c, "RS612 + MA120闸门当前更适合作为主策略候选。", margin + 68, h - 106, 14, colors.HexColor("#111827"))
    draw_text(c, "它在同一成本和动态池口径下，收益更高、回撤更小、Sharpe更高，且交易次数更少。", margin, h - 131, 10, colors.HexColor("#4B5563"))

    draw_metric_box(c, "RS612 总收益", pct(rs612["total_return"]), margin, h - 210, 116, "#0F766E")
    draw_metric_box(c, "RS612 最大回撤", pct(rs612["max_drawdown"]), margin + 130, h - 210, 116, "#B42318")
    draw_metric_box(c, "RS612 Sharpe", num(rs612["sharpe"]), margin + 260, h - 210, 116, "#2563EB")
    draw_metric_box(c, "平均仓位", pct(rs612["avg_exposure"]), margin + 390, h - 210, 116, "#7C3AED")

    draw_text(c, "核心指标对比", margin, h - 258, 13, colors.HexColor("#111827"))
    draw_comparison_table(c, rows, margin, h - 296, w - margin * 2)

    draw_text(c, "风险收益拆解", margin, h - 435, 13, colors.HexColor("#111827"))
    max_ret = max(float(row["total_return"]) for row in rows)
    max_dd_abs = max(abs(float(row["max_drawdown"])) for row in rows)
    draw_bar(c, "RS612收益", float(rs612["total_return"]), max_ret, margin, h - 464, 270, "#0F766E", pct(rs612["total_return"]))
    draw_bar(c, "RS36收益", float(rs36["total_return"]), max_ret, margin, h - 489, 270, "#14B8A6", pct(rs36["total_return"]))
    draw_bar(c, "RS612回撤", abs(float(rs612["max_drawdown"])), max_dd_abs, margin, h - 524, 270, "#F97316", pct(rs612["max_drawdown"]))
    draw_bar(c, "RS36回撤", abs(float(rs36["max_drawdown"])), max_dd_abs, margin, h - 549, 270, "#EF4444", pct(rs36["max_drawdown"]))

    card(c, margin, 82, w - margin * 2, 102, "#F8FAFC", "#D9DEE7")
    draw_text(c, "当前持仓", margin + 16, 158, 12, colors.HexColor("#111827"))
    draw_text(c, f"RS612: {rs612['latest_positions']}", margin + 16, 134, 9, colors.HexColor("#303742"))
    draw_text(c, f"RS36: {rs36['latest_positions']}", margin + 16, 113, 9, colors.HexColor("#303742"))
    draw_text(c, "注意: 2026年收益为截至本次数据更新日的年内表现，不代表全年收益。", margin + 16, 92, 8, colors.HexColor("#6B7280"))

    c.showPage()

    draw_header(c, 2)
    draw_text(c, "策略规则", margin, h - 114, 14, colors.HexColor("#111827"))
    draw_rule_block(
        c,
        "RS612",
        [
            "调仓: 月初第3个交易日开盘。",
            "信号: 使用上一交易日收盘前数据。",
            "评分: 6个月收益50% + 12个月收益50%。",
            "ETF过滤: 收盘价 > MA200 且12个月收益 > 0。",
            "持仓: Top2等权。",
            "闸门: 2/3指数低于MA120则空仓。",
        ],
        margin,
        h - 300,
        246,
        158,
        "#2563EB",
    )
    draw_rule_block(
        c,
        "RS36",
        [
            "调仓: 月初第1个交易日开盘。",
            "信号: 使用上一交易日收盘前数据。",
            "评分: 3个月收益50% + 6个月收益50%。",
            "ETF过滤: 收盘价 > MA120 且6个月收益 > 0。",
            "持仓: Top3等权。",
            "闸门: 2/3指数低于MA120则空仓。",
        ],
        margin + 266,
        h - 300,
        246,
        158,
        "#0F766E",
    )

    draw_text(c, "年度收益", margin, h - 332, 14, colors.HexColor("#111827"))
    years = sorted(set(rs612["yearly_json"]) | set(rs36["yearly_json"]))
    table_x = margin
    table_y = h - 370
    col_w = [72, 112, 112]
    c.setFillColor(colors.HexColor("#EEF2F7"))
    c.roundRect(table_x, table_y, sum(col_w), 26, 6, stroke=0, fill=1)
    for idx, header in enumerate(["年份", "RS612", "RS36"]):
        draw_center(c, header, table_x + sum(col_w[:idx]) + col_w[idx] / 2, table_y + 8, 9, colors.HexColor("#4B5563"))
    for r, year in enumerate(years):
        yy = table_y - (r + 1) * 28
        c.setFillColor(colors.white if r % 2 == 0 else colors.HexColor("#FBFCFE"))
        c.setStrokeColor(colors.HexColor("#E5EAF2"))
        c.rect(table_x, yy, sum(col_w), 28, stroke=1, fill=1)
        draw_center(c, str(year), table_x + col_w[0] / 2, yy + 9, 9)
        v1 = rs612["yearly_json"].get(year, 0)
        v2 = rs36["yearly_json"].get(year, 0)
        draw_center(c, pct(v1), table_x + col_w[0] + col_w[1] / 2, yy + 9, 9, colors.HexColor("#0F766E") if v1 >= 0 else colors.HexColor("#B42318"))
        draw_center(c, pct(v2), table_x + col_w[0] + col_w[1] + col_w[2] / 2, yy + 9, 9, colors.HexColor("#0F766E") if v2 >= 0 else colors.HexColor("#B42318"))

    card(c, margin, 118, w - margin * 2, 124, "#FFFDF7", "#F4D9A6")
    draw_text(c, "复盘判断", margin + 16, 218, 13, colors.HexColor("#111827"))
    notes = [
        "1. RS612的慢趋势确认更严格，过去5年在MA120市场闸门下更稳。",
        "2. RS36更灵敏，能吃到快趋势，但更容易在震荡和假突破中回吐。",
        "3. 两者平均仓位都只有约35%-37%，收益主要来自少数强趋势年份和少数主线ETF。",
        "4. 当前共同暴露在半导体链，后续模拟盘要重点监控行业拥挤和指数闸门变化。",
    ]
    yy = 196
    for note in notes:
        draw_text(c, note, margin + 16, yy, 9, colors.HexColor("#303742"))
        yy -= 19

    draw_text(c, "数据口径: 5年真实ETF动态池，ETF满252个交易日才可入池；佣金万0.5 + 滑点0.10%；下一交易日开盘成交。", margin, 70, 8, colors.HexColor("#6B7280"))
    draw_text(c, "用途: 策略研究和模拟盘复盘，不构成投资建议。", margin, 55, 8, colors.HexColor("#6B7280"))

    c.save()
    print(OUT_PDF)


if __name__ == "__main__":
    main()
