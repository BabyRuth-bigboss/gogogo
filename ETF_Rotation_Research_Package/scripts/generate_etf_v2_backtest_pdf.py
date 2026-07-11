#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import statistics
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "a_stock_daily_workflow/etf_rotation/backtests/regime_dual_sleeve_5y_v2"
OUT_DIR = ROOT / "output/pdf"
OUT_PDF = OUT_DIR / "etf_regime_dual_sleeve_v2_backtest_report.pdf"

METRICS_CSV = DATA_DIR / "dual_sleeve_v2_metrics_latest.csv"
STRESS_CSV = DATA_DIR / "dual_sleeve_v2_stress_latest.csv"

FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT = "CNFont"

INK = colors.HexColor("#18212F")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#D9E0E8")
PANEL = colors.HexColor("#F6F8FB")
NAVY = colors.HexColor("#15243A")
BLUE = colors.HexColor("#2563EB")
TEAL = colors.HexColor("#0F766E")
GREEN = colors.HexColor("#15803D")
RED = colors.HexColor("#B42318")
ORANGE = colors.HexColor("#D97706")

LABELS = {
    "159516": "半导体设备",
    "588200": "科创芯片",
    "512400": "有色金属",
    "515880": "通信",
    "513100": "纳指",
    "513500": "标普500",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def top_paths() -> tuple[Path, Path, Path]:
    eq = Path(glob.glob(str(DATA_DIR / "top1_*_equity_latest.csv"))[0])
    trades = Path(glob.glob(str(DATA_DIR / "top1_*_trades_latest.csv"))[0])
    signals = Path(glob.glob(str(DATA_DIR / "top1_*_signals_latest.csv"))[0])
    return eq, trades, signals


def pct(value: float | str, signed: bool = True) -> str:
    number = float(value) * 100
    sign = "+" if signed and number >= 0 else ""
    return f"{sign}{number:.2f}%"


def text(c: canvas.Canvas, value: str, x: float, y: float, size: float = 9, color=INK, font: str = FONT) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, value)


def centered(c: canvas.Canvas, value: str, x: float, y: float, size: float = 9, color=INK, font: str = FONT) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(x, y, value)


def right(c: canvas.Canvas, value: str, x: float, y: float, size: float = 9, color=INK, font: str = FONT) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawRightString(x, y, value)


def wrap(value: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in value:
        current += char
        if len(current) >= max_chars or char == "\n":
            lines.append(current.rstrip("\n"))
            current = ""
    if current:
        lines.append(current)
    return lines or [""]


def wrapped_text(c: canvas.Canvas, value: str, x: float, y: float, max_chars: int, leading: float = 15, size: float = 9, color=INK) -> float:
    for line in wrap(value, max_chars):
        text(c, line, x, y, size, color)
        y -= leading
    return y


def card(c: canvas.Canvas, x: float, y: float, w: float, h: float, fill=PANEL, stroke=LINE, radius: float = 7) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=1)


def header(c: canvas.Canvas, page: int, subtitle: str, title: str = "ETF强弱市双袖轮动 V2") -> None:
    w, h = A4
    c.setFillColor(NAVY)
    c.rect(0, h - 76, w, 76, stroke=0, fill=1)
    title_font = "Helvetica-Bold" if title.isascii() else FONT
    text(c, title, 40, h - 34, 20, colors.HexColor("#FFFFFF"), title_font)
    text(c, subtitle, 40, h - 57, 9, colors.HexColor("#C7D2E1"))
    right(c, f"{page} / 4", w - 40, h - 55, 9, colors.HexColor("#C7D2E1"))


def footer(c: canvas.Canvas) -> None:
    text(c, "回测研究报告 | 数据截至2026-07-10 | 不构成投资建议", 40, 28, 7.5, MUTED)
    right(c, "Codex", A4[0] - 40, 28, 7.5, MUTED)


def metric_box(c: canvas.Canvas, label: str, value: str, x: float, y: float, w: float, accent) -> None:
    card(c, x, y, w, 62, colors.white, LINE)
    c.setFillColor(accent)
    c.roundRect(x, y, 4, 62, 2, stroke=0, fill=1)
    text(c, label, x + 14, y + 40, 8, MUTED)
    text(c, value, x + 14, y + 14, 17, accent)


def section_title(c: canvas.Canvas, title: str, x: float, y: float) -> None:
    c.setFillColor(BLUE)
    c.rect(x, y - 2, 4, 16, stroke=0, fill=1)
    text(c, title, x + 11, y, 13, INK)


def draw_table(c: canvas.Canvas, headers: list[str], rows: list[list[str]], x: float, y: float, widths: list[float], row_h: float = 27) -> float:
    total = sum(widths)
    c.setFillColor(colors.HexColor("#E9EEF5"))
    c.roundRect(x, y, total, row_h, 5, stroke=0, fill=1)
    cur = x
    for label, width in zip(headers, widths):
        centered(c, label, cur + width / 2, y + 9, 8, MUTED)
        cur += width
    for idx, row in enumerate(rows):
        yy = y - (idx + 1) * row_h
        c.setFillColor(colors.white if idx % 2 == 0 else colors.HexColor("#FAFBFD"))
        c.setStrokeColor(LINE)
        c.rect(x, yy, total, row_h, stroke=1, fill=1)
        cur = x
        for col, (value, width) in enumerate(zip(row, widths)):
            color = INK
            if value.startswith("+"):
                color = GREEN
            elif value.startswith("-") and value.endswith("%"):
                color = RED
            if col == 0:
                text(c, value, cur + 8, yy + 9, 8.2, color)
            else:
                centered(c, value, cur + width / 2, yy + 9, 8.2, color)
            cur += width
    return y - (len(rows) + 1) * row_h


def line_chart(c: canvas.Canvas, values: list[float], x: float, y: float, w: float, h: float, color, fill: bool = False) -> None:
    if len(values) < 2:
        return
    low, high = min(values), max(values)
    span = high - low or 1.0
    pts = [(x + i * w / (len(values) - 1), y + (value - low) / span * h) for i, value in enumerate(values)]
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    for j in range(5):
        gy = y + j * h / 4
        c.line(x, gy, x + w, gy)
    path = c.beginPath()
    path.moveTo(*pts[0])
    for px, py in pts[1:]:
        path.lineTo(px, py)
    if fill:
        path.lineTo(x + w, y)
        path.lineTo(x, y)
        path.close()
        c.setFillColor(colors.Color(color.red, color.green, color.blue, alpha=0.10))
        c.drawPath(path, stroke=0, fill=1)
        path = c.beginPath()
        path.moveTo(*pts[0])
        for px, py in pts[1:]:
            path.lineTo(px, py)
    c.setStrokeColor(color)
    c.setLineWidth(1.7)
    c.drawPath(path, stroke=1, fill=0)


def draw_page_one(c: canvas.Canvas, metrics: dict[str, str], equity: list[dict[str, str]], stress: list[dict[str, str]]) -> None:
    w, h = A4
    header(c, 1, "五年动态ETF池回测 - 核心结论")
    text(c, "研究结论", 40, h - 112, 9, BLUE)
    text(c, "V2显著提升仓位利用率与收益，但最大回撤仍略高于20%。", 40, h - 139, 16, INK)
    text(c, "100万元模拟本金期末为542.86万元；收益集中于2023、2025和2026年。", 40, h - 162, 9, MUTED)

    gap = 10
    box_w = (w - 80 - gap * 2) / 3
    metric_box(c, "总收益", pct(metrics["total_return"]), 40, h - 244, box_w, TEAL)
    metric_box(c, "年化收益", pct(metrics["cagr"]), 40 + box_w + gap, h - 244, box_w, BLUE)
    metric_box(c, "最大回撤", pct(metrics["max_drawdown"]), 40 + (box_w + gap) * 2, h - 244, box_w, RED)
    metric_box(c, "Sharpe", f"{float(metrics['sharpe']):.2f}", 40, h - 318, box_w, ORANGE)
    metric_box(c, "平均仓位", pct(metrics["avg_exposure"]), 40 + box_w + gap, h - 318, box_w, BLUE)
    metric_box(c, "交易次数", metrics["trades"], 40 + (box_w + gap) * 2, h - 318, box_w, TEAL)

    section_title(c, "当前模型状态", 40, h - 354)
    card(c, 40, h - 468, w - 80, 90, colors.white, LINE)
    text(c, "市场状态", 58, h - 405, 8, MUTED)
    text(c, "强市 - 进攻袖", 58, h - 432, 15, GREEN)
    text(c, "159516", 224, h - 405, 8, MUTED)
    text(c, "半导体设备  50%", 224, h - 432, 14, INK)
    text(c, "588200", 402, h - 405, 8, MUTED)
    text(c, "科创芯片  50%", 402, h - 432, 14, INK)

    section_title(c, "压力测试", 40, h - 506)
    matching = [row for row in stress if row["id"] == metrics["id"]]
    median_cagr = statistics.median(float(row["cagr"]) for row in matching)
    min_cagr = min(float(row["cagr"]) for row in matching)
    worst_dd = min(float(row["max_drawdown"]) for row in matching)
    table_rows = [["成本翻倍 + 不同起点", pct(median_cagr), pct(min_cagr), pct(worst_dd)]]
    draw_table(c, ["场景", "中位年化", "最低年化", "最差回撤"], table_rows, 40, h - 544, [190, 105, 105, 105], 30)

    final_equity = float(equity[-1]["equity"])
    card(c, 40, 92, w - 80, 92, colors.HexColor("#F1F7F5"), colors.HexColor("#B8D8CE"))
    text(c, "期末资产", 58, 158, 8, MUTED)
    text(c, f"{final_equity / 10000:,.2f} 万元", 58, 126, 22, TEAL)
    text(c, "评价", 280, 158, 8, MUTED)
    wrapped_text(c, "收益表现强，压力测试未散，但尚未经过严格滚动样本外验证。", 280, 137, 21, 16, 10, INK)
    footer(c)


def draw_page_two(c: canvas.Canvas) -> None:
    w, h = A4
    header(c, 2, "ETF强弱市双袖轮动 V2", "V2策略结构")
    section_title(c, "核心逻辑", 40, h - 114)
    text(c, "以六指数识别市场状态，强市追踪行业主线，弱市转向海外慢趋势。", 40, h - 140, 10, MUTED)

    card(c, 40, h - 310, 236, 136, colors.HexColor("#F2F7FF"), colors.HexColor("#BDD1F5"))
    centered(c, "强市 - 进攻袖", 158, h - 205, 14, BLUE)
    rules = [
        "6月动量60% + 12月动量40%",
        "流动性15% + 趋势质量10%",
        "收盘价 > MA200，12个月收益 > 0",
        "选择行业/主题ETF Top2，等权持有",
        "月初第3个交易日正常调仓",
    ]
    yy = h - 232
    for rule in rules:
        text(c, "- " + rule, 55, yy, 8.5, INK)
        yy -= 17

    card(c, 319, h - 310, 236, 136, colors.HexColor("#F1F8F5"), colors.HexColor("#B9D9CE"))
    centered(c, "弱市 - 防御袖", 437, h - 205, 14, TEAL)
    rules = [
        "候选资产：纳指ETF、标普500ETF",
        "评分：6月收益50% + 12月收益50%",
        "收盘价 > MA200，12个月收益 > 0",
        "选择Top2，满仓等权；无合格标的则现金",
        "确认恢复后立即切回进攻袖",
    ]
    yy = h - 232
    for rule in rules:
        text(c, "- " + rule, 334, yy, 8.5, INK)
        yy -= 17

    section_title(c, "V2强弱判断引擎", 40, h - 350)
    boxes = [
        ("指数池", "沪深300、创业板、科创50、中证500、恒生指数、恒生科技"),
        ("主趋势", "价格相对MA120，同时检查MA120近20日方向"),
        ("辅助确认", "MA50低于MA120作为弱势确认状态"),
        ("防抖", "弱市连续2天确认，切换后冷却5个交易日"),
        ("阈值", "有效指数中至少约一半真弱势，才进入防御袖"),
    ]
    y = h - 400
    for title, body in boxes:
        card(c, 40, y - 42, w - 80, 42, colors.white, LINE, 5)
        text(c, title, 55, y - 26, 9, BLUE)
        text(c, body, 130, y - 26, 8.5, INK)
        y -= 51

    card(c, 40, 72, w - 80, 62, colors.HexColor("#FFF8ED"), colors.HexColor("#F0CF9B"))
    text(c, "成交口径", 55, 112, 9, ORANGE)
    text(c, "前一交易日收盘生成信号，下一交易日开盘成交；单边成本=佣金万0.5+滑点0.10%。", 55, 88, 8.5, INK)
    footer(c)


def draw_page_three(c: canvas.Canvas, metrics: dict[str, str], equity: list[dict[str, str]]) -> None:
    w, h = A4
    header(c, 3, "ETF强弱市双袖轮动 V2", "V2绩效表现")
    section_title(c, "组合净值", 40, h - 114)
    values = [1.0] + [float(row["equity"]) / 1_000_000 for row in equity]
    line_chart(c, values, 55, h - 345, w - 110, 180, BLUE, True)
    text(c, "1.0", 42, h - 348, 7, MUTED)
    right(c, f"{values[-1]:.2f}", w - 42, h - 168, 7, MUTED)
    text(c, "2021-07", 55, h - 360, 7, MUTED)
    right(c, "2026-07", w - 55, h - 360, 7, MUTED)

    running_peak = values[0]
    drawdowns = []
    for value in values:
        running_peak = max(running_peak, value)
        drawdowns.append(value / running_peak - 1)
    section_title(c, "组合回撤", 40, h - 397)
    line_chart(c, drawdowns, 55, h - 520, w - 110, 82, RED, True)
    text(c, pct(min(drawdowns)), 42, h - 523, 7, RED)

    section_title(c, "年度收益", 40, h - 560)
    yearly = json.loads(metrics["yearly"])
    years = ["2021", "2022", "2023", "2024", "2025", "2026"]
    rows = [[year + ("*" if year in ("2021", "2026") else ""), pct(yearly.get(year, 0))] for year in years]
    draw_table(c, ["年份", "收益"], rows, 40, h - 596, [115, 130], 25)

    card(c, 318, 82, 237, 135, colors.HexColor("#FFF6F4"), colors.HexColor("#EAC0B8"))
    text(c, "收益集中度观察", 334, 190, 11, RED)
    notes = [
        "2023：+75.96%",
        "2025：+60.20%",
        "2026截至7月：+68.35%",
        "2022仍录得-7.29%，策略并非每年盈利。",
        "* 2021与2026均不是完整自然年。",
    ]
    yy = 166
    for note in notes:
        text(c, "- " + note, 334, yy, 8.2, INK)
        yy -= 18
    footer(c)


def draw_page_four(c: canvas.Canvas, metrics: dict[str, str], equity: list[dict[str, str]], signals: list[dict[str, str]]) -> None:
    w, h = A4
    header(c, 4, "风险复盘、当前持仓与使用边界", "V2 RISK REVIEW")
    section_title(c, "最大回撤复盘", 40, h - 114)
    peak = 1_000_000.0
    peak_date = "2021-07-12"
    max_dd = 0.0
    dd_start = peak_date
    dd_end = peak_date
    trough_equity = peak
    for row in equity:
        value = float(row["equity"])
        if value > peak:
            peak = value
            peak_date = row["date"]
        dd = value / peak - 1
        if dd < max_dd:
            max_dd = dd
            dd_start = peak_date
            dd_end = row["date"]
            trough_equity = value
    rows = [
        ["回撤起点", dd_start, "峰值", "372.65万元"],
        ["回撤低点", dd_end, "低点", f"{trough_equity / 10000:.2f}万元"],
        ["最大回撤", pct(max_dd), "当时状态", "强市 / 进攻袖"],
        ["主要暴露", "通信、有色金属", "随后切换", "半导体设备、有色金属"],
    ]
    draw_table(c, ["项目", "内容", "项目", "内容"], rows, 40, h - 154, [86, 145, 86, 188], 28)

    section_title(c, "当前状态", 40, h - 312)
    last = equity[-1]
    changes = sum(row.get("regime_changed") == "True" for row in signals)
    bad_days = sum(row.get("market_bad") == "True" for row in signals)
    state_rows = [
        ["数据日期", last["date"], "市场状态", "强市"],
        ["当前持仓", "半导体设备50%", "第二持仓", "科创芯片50%"],
        ["强弱切换", f"{changes}次/5年", "弱市交易日", f"{bad_days}天"],
        ["平均仓位", pct(metrics["avg_exposure"]), "交易次数", metrics["trades"]],
    ]
    draw_table(c, ["项目", "内容", "项目", "内容"], state_rows, 40, h - 350, [86, 145, 86, 188], 28)

    section_title(c, "使用边界", 40, h - 506)
    card(c, 40, 116, w - 80, 180, colors.HexColor("#FFFDF7"), colors.HexColor("#E7D6AA"))
    notes = [
        "1. 本结果是当前ETF名单上的5年样本内回测，仍存在幸存者偏差。",
        "2. 改变起点和成本属于敏感度测试，不等同于严格滚动样本外验证。",
        "3. 最大回撤略高于20%；强市内部的行业急跌仍可能绕过市场闸门。",
        "4. 腾讯ETF原始日线仅修正大比例份额拆分，不等同于完整分红复权。",
        "5. 实盘生成器尚需修正同日重复确认和非调仓日重新排名问题。",
        "6. 当前半导体暴露高度集中，实际执行应配合仓位上限与人工复核。",
    ]
    yy = 266
    for note in notes:
        wrapped_text(c, note, 56, yy, 48, 16, 8.5, INK)
        yy -= 25
    footer(c)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont(FONT, FONT_PATH, subfontIndex=0))

    metrics = read_csv(METRICS_CSV)[0]
    stress = read_csv(STRESS_CSV)
    equity_path, _, signals_path = top_paths()
    equity = read_csv(equity_path)
    signals = read_csv(signals_path)

    c = canvas.Canvas(str(OUT_PDF), pagesize=A4)
    c.setTitle("ETF Regime Dual Sleeve V2 Backtest Report")
    c.setAuthor("Codex")
    c.setSubject("ETF V2 strategy backtest, risk and current holdings")
    draw_page_one(c, metrics, equity, stress)
    c.showPage()
    draw_page_two(c)
    c.showPage()
    draw_page_three(c, metrics, equity)
    c.showPage()
    draw_page_four(c, metrics, equity, signals)
    c.save()
    print(OUT_PDF)


if __name__ == "__main__":
    main()
