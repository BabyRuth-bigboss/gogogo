#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import shutil
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "a_stock_daily_workflow/etf_rotation"
UA = "Mozilla/5.0"
KLINE_LIMIT = 100
MIN_AMOUNT_YI = 0.10


ETF_UNIVERSE: list[dict[str, str]] = [
    {"code": "512480", "theme": "科技", "label": "半导体"},
    {"code": "159995", "theme": "科技", "label": "芯片"},
    {"code": "159516", "theme": "科技", "label": "半导体设备"},
    {"code": "588200", "theme": "科技", "label": "科创芯片"},
    {"code": "515880", "theme": "科技", "label": "通信"},
    {"code": "159819", "theme": "科技", "label": "人工智能"},
    {"code": "159869", "theme": "科技", "label": "游戏"},
    {"code": "512980", "theme": "科技", "label": "传媒"},
    {"code": "515230", "theme": "科技", "label": "软件"},
    {"code": "512880", "theme": "金融", "label": "证券"},
    {"code": "512800", "theme": "金融", "label": "银行"},
    {"code": "512070", "theme": "金融", "label": "证券保险"},
    {"code": "159940", "theme": "金融", "label": "金融地产"},
    {"code": "159928", "theme": "消费", "label": "消费"},
    {"code": "515650", "theme": "消费", "label": "消费50"},
    {"code": "516600", "theme": "消费", "label": "消费服务"},
    {"code": "512170", "theme": "医药", "label": "医疗"},
    {"code": "512010", "theme": "医药", "label": "医药"},
    {"code": "159992", "theme": "医药", "label": "创新药"},
    {"code": "512400", "theme": "周期", "label": "有色金属"},
    {"code": "516780", "theme": "周期", "label": "稀土"},
    {"code": "159870", "theme": "周期", "label": "化工"},
    {"code": "159930", "theme": "周期", "label": "能源"},
    {"code": "515790", "theme": "新能源", "label": "光伏"},
    {"code": "516160", "theme": "新能源", "label": "新能源"},
    {"code": "515700", "theme": "新能源", "label": "新能源车"},
    {"code": "159755", "theme": "新能源", "label": "电池"},
    {"code": "560580", "theme": "新能源", "label": "电力"},
    {"code": "512660", "theme": "制造", "label": "军工"},
    {"code": "159770", "theme": "制造", "label": "机器人"},
    {"code": "159667", "theme": "制造", "label": "工业母机"},
    {"code": "512200", "theme": "地产基建", "label": "房地产"},
    {"code": "159745", "theme": "地产基建", "label": "建材"},
    {"code": "513120", "theme": "港股行业", "label": "港股创新药"},
    {"code": "513090", "theme": "港股行业", "label": "香港证券"},
    {"code": "513100", "theme": "海外", "label": "纳指"},
    {"code": "513500", "theme": "海外", "label": "标普500"},
    {"code": "513050", "theme": "海外", "label": "中概互联"},
    {"code": "513130", "theme": "海外", "label": "恒生科技"},
    {"code": "510880", "theme": "红利质量", "label": "红利"},
    {"code": "159399", "theme": "红利质量", "label": "现金流"},
]


MARKET_WATCH: list[dict[str, str]] = [
    {"code": "510300", "theme": "市场观察", "label": "沪深300"},
    {"code": "159915", "theme": "市场观察", "label": "创业板"},
    {"code": "588000", "theme": "市场观察", "label": "科创50"},
]


@dataclass
class EtfRow:
    code: str
    label: str
    theme: str
    name: str
    date: str
    close: float
    amount_yi: float
    ret10: float
    ret20: float
    ret60: float
    weighted_momentum: float
    mdd20: float
    ma20: float
    ma60: float
    up_days20: int
    premium_pct: float | None
    nav_change_pct: float | None
    momentum_score: float = 0.0
    premium_score: float = 70.0
    drawdown_score: float = 0.0
    rs_score: float = 0.0
    trend_score: float = 0.0
    gap_score: float = 0.0
    final_score: float = 0.0
    rank: int = 0
    position_pct: int = 0
    position_note: str = ""
    eligible: bool = False
    status: str = "WATCH"
    reasons: list[str] = field(default_factory=list)
    clear_reasons: list[str] = field(default_factory=list)
    error: str = ""


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


def market_prefix(code: str) -> str:
    return "1" if code.startswith(("5", "6", "9")) else "0"


def tencent_sec(code: str) -> str:
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def read_url(url: str, timeout: int = 12) -> str:
    req = Request(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_tencent_klines(code: str, limit: int = KLINE_LIMIT) -> list[dict[str, float | str]]:
    sec = tencent_sec(code)
    params = urlencode({"param": f"{sec},day,,,{limit},qfq"})
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + params
    req = Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    with urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload.get("data", {}).get(sec, {})
    raw = data.get("qfqday") or data.get("day") or []
    rows: list[dict[str, float | str]] = []
    for item in raw:
        if len(item) < 6:
            continue
        rows.append(
            {
                "date": item[0],
                "open": fnum(item[1]),
                "close": fnum(item[2]),
                "high": fnum(item[3]),
                "low": fnum(item[4]),
                "volume": fnum(item[5]),
            }
        )
    return rows[-limit:]


def fetch_eastmoney_quote(code: str) -> dict[str, Any]:
    params = urlencode(
        {
            "secid": f"{market_prefix(code)}.{code}",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f169,f170,f171,f116,f117",
        }
    )
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + params
    try:
        data = json.loads(read_url(url, timeout=10)).get("data") or {}
        return data
    except Exception:
        return {}


def fetch_fund_estimate(code: str) -> dict[str, Any]:
    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time() * 1000)}"
    try:
        text = read_url(url, timeout=10)
        match = re.search(r"jsonpgz\((.*)\);?", text)
        if not match:
            return {}
        return json.loads(match.group(1))
    except Exception:
        return {}


def simple_ma(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return statistics.mean(values[-window:])


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst * 100


def percentile_scores(values: list[float], higher_is_better: bool = True) -> list[float]:
    n = len(values)
    if n <= 1:
        return [100.0] * n
    ordered = sorted((value, idx) for idx, value in enumerate(values))
    ranks = [0.0] * n
    for rank, (_, idx) in enumerate(ordered):
        score = 100 * rank / (n - 1)
        ranks[idx] = score if higher_is_better else 100 - score
    return ranks


def premium_score(premium_pct: float | None) -> float:
    if premium_pct is None:
        return 70.0
    if premium_pct <= 0:
        return 100.0
    if premium_pct <= 2:
        return 100 - premium_pct * 7.5
    if premium_pct <= 5:
        return max(0.0, 85 - (premium_pct - 2) * (85 / 3))
    return 0.0


def drawdown_score(mdd20: float) -> float:
    if mdd20 >= -5:
        return 100.0
    if mdd20 <= -15:
        return 0.0
    return (mdd20 + 15) / 10 * 100


def trend_score(row: EtfRow) -> float:
    score = 20.0
    if row.close >= row.ma20:
        score += 25
    if row.ma20 >= row.ma60:
        score += 25
    if row.ret20 > 0:
        score += 20
    if row.up_days20 >= 12:
        score += 10
    return min(score, 100.0)


def gap_score_from_pct(gap_pct: float) -> float:
    if gap_pct >= 10:
        return 100.0
    if gap_pct <= 0:
        return 25.0
    return 25.0 + gap_pct / 10 * 75


def build_row(item: dict[str, str]) -> EtfRow:
    code = item["code"]
    label = item["label"]
    theme = item["theme"]
    try:
        klines = fetch_tencent_klines(code)
        if len(klines) < 61:
            raise RuntimeError(f"K线不足: {len(klines)}")
        quote = fetch_eastmoney_quote(code)
        estimate = fetch_fund_estimate(code)
        closes = [fnum(row["close"]) for row in klines]
        date = str(klines[-1]["date"])
        close = fnum(quote.get("f43")) or closes[-1]
        latest_volume = fnum(klines[-1].get("volume"))
        quoted_amount_yi = fnum(quote.get("f48")) / 100_000_000
        fallback_amount_yi = close * latest_volume * 100 / 100_000_000 if latest_volume > 0 else 0.0
        ret10 = (closes[-1] / closes[-11] - 1) * 100 if closes[-11] else 0.0
        ret20 = (closes[-1] / closes[-21] - 1) * 100 if closes[-21] else 0.0
        ret60 = (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else 0.0
        weighted_momentum = ret10 * 0.30 + ret20 * 0.70
        up_days20 = sum(1 for i in range(len(closes) - 20, len(closes)) if closes[i] > closes[i - 1])
        nav_estimate = fnum(estimate.get("gsz"))
        premium_pct = (close / nav_estimate - 1) * 100 if nav_estimate > 0 else None
        nav_change_pct = fnum(estimate.get("gszzl"), math.nan)
        if math.isnan(nav_change_pct):
            nav_change_pct = None
        return EtfRow(
            code=code,
            label=label,
            theme=theme,
            name=str(quote.get("f58") or estimate.get("name") or label),
            date=date,
            close=close,
            amount_yi=quoted_amount_yi or fallback_amount_yi,
            ret10=ret10,
            ret20=ret20,
            ret60=ret60,
            weighted_momentum=weighted_momentum,
            mdd20=max_drawdown(closes[-20:]),
            ma20=simple_ma(closes, 20),
            ma60=simple_ma(closes, 60),
            up_days20=up_days20,
            premium_pct=premium_pct,
            nav_change_pct=nav_change_pct,
        )
    except Exception as exc:
        return EtfRow(
            code=code,
            label=label,
            theme=theme,
            name=label,
            date="",
            close=0.0,
            amount_yi=0.0,
            ret10=0.0,
            ret20=0.0,
            ret60=0.0,
            weighted_momentum=0.0,
            mdd20=0.0,
            ma20=0.0,
            ma60=0.0,
            up_days20=0,
            premium_pct=None,
            nav_change_pct=None,
            error=str(exc),
        )


def score_rows(rows: list[EtfRow]) -> list[EtfRow]:
    valid = [row for row in rows if not row.error]
    momentum_scores = percentile_scores([row.weighted_momentum for row in valid])
    rs_scores = percentile_scores([row.ret20 for row in valid])
    for row, mom_score, rs_score in zip(valid, momentum_scores, rs_scores):
        row.momentum_score = mom_score
        row.rs_score = rs_score
        row.premium_score = premium_score(row.premium_pct)
        row.drawdown_score = drawdown_score(row.mdd20)
        row.trend_score = trend_score(row)
    by_momentum = sorted(valid, key=lambda item: item.weighted_momentum, reverse=True)
    for idx, row in enumerate(by_momentum):
        next_momentum = by_momentum[idx + 1].weighted_momentum if idx + 1 < len(by_momentum) else row.weighted_momentum
        row.gap_score = gap_score_from_pct(max(0.0, row.weighted_momentum - next_momentum))
    for row in valid:
        row.final_score = (
            row.momentum_score * 0.35
            + row.premium_score * 0.15
            + row.drawdown_score * 0.15
            + row.rs_score * 0.25
            + row.gap_score * 0.10
        )
        if row.premium_pct is not None and row.premium_pct > 5:
            row.reasons.append(f"溢价{row.premium_pct:.2f}%>5%")
            row.clear_reasons.append("溢价否决")
        if row.mdd20 <= -15:
            row.reasons.append(f"20日最大回撤{abs(row.mdd20):.1f}%>15%")
        if row.mdd20 <= -12:
            row.clear_reasons.append(f"20日回撤{abs(row.mdd20):.1f}%>12%")
        if row.amount_yi < MIN_AMOUNT_YI:
            row.reasons.append(f"成交额{row.amount_yi:.2f}亿偏低")
        if row.weighted_momentum <= 0:
            row.reasons.append("动量为负")
        if row.ret20 <= 0:
            row.reasons.append("20日动量为负")
        if row.close < row.ma20:
            row.reasons.append("未站上MA20")
        if row.close < row.ma20 and row.ret10 < 0:
            row.clear_reasons.append("跌破MA20且10日动量转负")
        hard_blocked = any(
            [
                row.premium_pct is not None and row.premium_pct > 5,
                row.mdd20 <= -15,
                row.amount_yi < MIN_AMOUNT_YI,
                bool(row.clear_reasons),
            ]
        )
        row.eligible = (not hard_blocked) and row.weighted_momentum > 0 and row.ret20 > 0 and row.close >= row.ma20
        row.status = "PASS" if row.eligible else ("CLEAR" if row.clear_reasons else "WATCH")
    ranked = sorted(valid, key=lambda item: item.final_score, reverse=True)
    for idx, row in enumerate(ranked, start=1):
        row.rank = idx
    return ranked + [row for row in rows if row.error]


def assign_positions(ranked: list[EtfRow]) -> tuple[list[EtfRow], str]:
    used_groups: set[str] = set()
    group_counts: dict[str, int] = {}
    eligible: list[EtfRow] = []
    for row in ranked:
        row.position_pct = 0
        row.position_note = ""
        if not row.eligible:
            continue
        group = rotation_group(row)
        group_counts[group] = group_counts.get(group, 0) + 1
        if group in used_groups:
            row.position_note = f"同类第{group_counts[group]}未入选"
            continue
        used_groups.add(group)
        eligible.append(row)
    if not eligible:
        return ranked, "无可开仓标的，保持现金。"
    top = eligible[0]
    second = eligible[1] if len(eligible) > 1 else None
    third = eligible[2] if len(eligible) > 2 else None
    gap = top.final_score - second.final_score if second else top.final_score

    if top.final_score >= 90 and gap >= 10:
        top.position_pct = 60
        if second and second.final_score >= 75:
            second.position_pct = 20
        note = f"第一名领先{gap:.1f}分，信号集中，核心仓给第一名。"
    elif top.final_score >= 85:
        top.position_pct = 50
        if second and second.final_score >= 75:
            second.position_pct = 25
        if third and gap < 8 and third.final_score >= 72:
            third.position_pct = 10
        note = f"第一名{top.final_score:.1f}分，趋势有效但不极端集中。"
    elif top.final_score >= 75:
        top.position_pct = 40
        if second and second.final_score >= 70:
            second.position_pct = 20
        note = f"第一名{top.final_score:.1f}分，轻仓参与，保留较高现金。"
    elif top.final_score >= 70:
        top.position_pct = 20
        note = f"第一名只有{top.final_score:.1f}分，仅观察/试仓。"
    else:
        return ranked, f"第一名只有{top.final_score:.1f}分，不开新仓。"

    for row in ranked:
        if row.position_pct <= 0:
            continue
        caps: list[str] = []
        cap = row.position_pct
        if row.ret20 >= 30:
            cap = min(cap, 45)
            caps.append("20日动量过热，衰减锁仓")
        if row.mdd20 <= -10:
            cap = min(cap, 45)
            caps.append("回撤接近警戒")
        if row.premium_pct is not None and row.premium_pct >= 2:
            cap = min(cap, 40)
            caps.append("溢价偏高")
        if cap < row.position_pct:
            row.position_pct = cap
            row.reasons.append("；".join(caps))
        row.position_note = f"{row.position_pct}%"
    return ranked, note


def rotation_group(row: EtfRow) -> str:
    text = row.name + row.label
    groups = [
        ("半导体芯片", ["半导体", "芯片"]),
        ("证券券商", ["证券", "券商"]),
        ("医药创新药", ["创新药", "医药", "医疗", "生物科技", "器械"]),
        ("新能源车电池", ["新能源车", "电池"]),
        ("光伏新能源", ["光伏", "新能源"]),
        ("化工", ["化工"]),
        ("军工国防", ["军工", "国防"]),
        ("机器人母机", ["机器人", "工业母机"]),
        ("农业养殖", ["农业", "养殖"]),
    ]
    for group, keys in groups:
        if any(key in text for key in keys):
            return group
    return row.label


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:+.2f}%"


def fmt_date(value: str) -> str:
    return value.replace("-", ".") if value else dt.date.today().isoformat().replace("-", ".")


def selected_rows(rows: list[EtfRow]) -> list[EtfRow]:
    return [row for row in rows if row.position_pct > 0]


def row_short_name(row: EtfRow) -> str:
    if row.label:
        return row.label
    return row.name.replace("ETF", "").replace("国泰", "").replace("华夏", "").replace("易方达", "")


def display_group_name(row: EtfRow) -> str:
    return row_short_name(row)


def market_mode(market_rows: list[EtfRow]) -> tuple[str, list[str]]:
    by_label = {row.label: row for row in market_rows if not row.error}
    hs300 = by_label.get("沪深300")
    cyb = by_label.get("创业板")
    k50 = by_label.get("科创50")
    notes: list[str] = []
    if cyb:
        notes.append(f"创业板20日{fmt_pct(cyb.ret20)}")
    if k50:
        notes.append(f"科创50 20日{fmt_pct(k50.ret20)}")
    if hs300:
        notes.append(f"沪深300 20日{fmt_pct(hs300.ret20)}")
    if hs300 and cyb and hs300.close >= hs300.ma20 and cyb.ret20 >= 5:
        return "牛市模式", notes
    if hs300 and hs300.close >= hs300.ma60 and (cyb is None or cyb.ret20 > -5):
        return "震荡市", notes
    return "防守模式", notes


def infer_title(valid: list[EtfRow], market_label: str) -> tuple[str, str]:
    selected = selected_rows(valid)
    clear_rows = [row for row in valid if row.clear_reasons]
    if clear_rows:
        title = f"{display_group_name(clear_rows[0])}触发清仓"
        if len(selected) >= 2:
            subtitle = f"{row_short_name(selected[0])}+{row_short_name(selected[1])}接棒"
        elif selected:
            subtitle = f"{row_short_name(selected[0])}接棒"
        else:
            subtitle = "保留现金，等待新主线"
        return title, subtitle
    if len(selected) >= 2:
        return f"{row_short_name(selected[0])}+{row_short_name(selected[1])}双核驱动", market_label
    if selected and selected[0].final_score >= 95:
        return f"{row_short_name(selected[0])}评分{selected[0].final_score:.1f}", "但有风险信号要注意"
    eligible_count = sum(1 for row in valid if row.eligible)
    if eligible_count >= 4:
        return "候选池扩张", f"{eligible_count}只达标，注意同类去重"
    return "行业ETF轮动信号", market_label


def position_text(row: EtfRow) -> str:
    if row.position_pct > 0:
        return f"{row.position_pct}%"
    if row.position_note:
        return row.position_note
    if row.clear_reasons:
        return "否决"
    if row.eligible:
        return "入选未分配"
    return "观察"


def build_report(ranked: list[EtfRow], allocation_note: str, market_rows: list[EtfRow]) -> str:
    valid = [row for row in ranked if not row.error]
    latest_date = max((row.date for row in valid if row.date), default=dt.date.today().isoformat())
    eligible_count = sum(1 for row in valid if row.eligible)
    selected = selected_rows(valid)
    top_all = valid[:13]
    top = selected[0] if selected else (valid[0] if valid else None)
    second = selected[1] if len(selected) > 1 else None
    gap = top.final_score - second.final_score if top and second else None
    cash_pct = max(0, 100 - sum(row.position_pct for row in valid))
    clear_rows = [row for row in valid if row.clear_reasons][:10]
    premium_denies = [row for row in valid if row.premium_pct is not None and row.premium_pct > 5]
    overheat_rows = [row for row in selected if row.ret20 >= 30 or any("衰减" in reason for reason in row.reasons)]
    market_label, market_notes = market_mode(market_rows)
    title, subtitle = infer_title(valid, market_label)

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(subtitle)
    lines.append("")
    lines.append(f"{fmt_date(latest_date)}  双动量V4 · {market_label}")
    lines.append("")

    lines.append("## 今日评分")
    if top:
        gap_text = f"{gap:.1f}分" if gap is not None else "无第二名"
        lines.append(
            f"- {row_short_name(top)}：评分 {top.final_score:.1f}，仓位 {top.position_pct}%"
            if top.position_pct > 0
            else f"- 第一候选：{row_short_name(top)}，评分 {top.final_score:.1f}，暂不分配仓位"
        )
        lines.append(
            f"  10日{fmt_pct(top.ret10)} · 20日{fmt_pct(top.ret20)} · "
            f"溢价{fmt_pct(top.premium_pct)} · 回撤{top.mdd20:.1f}%"
        )
        lines.append(
            f"  五维：动量{top.momentum_score:.0f} + 溢价{top.premium_score:.0f} + "
            f"回撤{top.drawdown_score:.0f} + 强度{top.rs_score:.0f} + 差距{top.gap_score:.0f}"
        )
        lines.append(f"- 动量差距：{gap_text}。{allocation_note}")
        if selected[1:]:
            lines.append("- 其余仓位：" + "；".join(f"{row_short_name(row)} {row.final_score:.1f}分 -> {row.position_pct}%" for row in selected[1:]))
        lines.append(f"- 总仓位 {100 - cash_pct}%，现金 {cash_pct}%。")
    else:
        lines.append("- 数据不足，今日不产生信号。")
    lines.append("")

    lines.append("## 排名与仓位")
    lines.append("| # | 标的 | 评分 | 仓位/原因 | 证据 |")
    lines.append("|---:|---|---:|---|---|")
    for row in top_all:
        evidence = (
            f"10日{fmt_pct(row.ret10)}；20日{fmt_pct(row.ret20)}；"
            f"溢价{fmt_pct(row.premium_pct)}；回撤{row.mdd20:.1f}%"
        )
        note_parts = []
        if row.clear_reasons:
            note_parts.extend(row.clear_reasons[:1])
        if row.reasons and row.position_pct == 0:
            note_parts.extend(row.reasons[:1])
        if row.position_pct > 0 and row.reasons:
            note_parts.extend([reason for reason in row.reasons if "衰减" in reason or "警戒" in reason][:1])
        if note_parts:
            evidence += "；" + "、".join(note_parts[:2])
        lines.append(f"| {row.rank} | {row.name}({row.code}) | {row.final_score:.1f} | {position_text(row)} | {evidence} |")

    lines.append("")
    lines.append("## 今日观察")
    lines.append(f"- 候选池 {eligible_count} 只；同类 ETF 只取最强，避免半导体/芯片/科创芯片重复占仓。")
    if market_notes:
        lines.append("- 市场环境：" + "；".join(market_notes))
    if top:
        lines.append(f"- {row_short_name(top)}评分{top.final_score:.1f}，{row_short_name(second) if second else '第二名'}差距{gap if gap is not None else 0:.1f}分。")
    if overheat_rows:
        lines.append("- 动量衰减：" + "；".join(f"{row_short_name(row)}20日{fmt_pct(row.ret20)}，仓位压到{row.position_pct}%" for row in overheat_rows))
    if clear_rows:
        lines.append("- 清仓接棒：" + "；".join(f"{row_short_name(row)}{'、'.join(row.clear_reasons[:1])}" for row in clear_rows[:3]))
    lines.append("")

    lines.append("## 风险信号")
    if clear_rows or premium_denies or overheat_rows:
        if clear_rows:
            lines.append("- 清仓/否决：" + "；".join(f"{row.name}({row.code}) {'、'.join(row.clear_reasons[:2])}" for row in clear_rows[:5]))
        if premium_denies:
            lines.append("- 溢价否决：" + "；".join(f"{row.name}溢价{fmt_pct(row.premium_pct)}" for row in premium_denies[:5]))
        if overheat_rows:
            lines.append("- 移动止盈：动量过热的仓位只降不追，回落不破清仓线则继续观察。")
    else:
        lines.append("- 没有硬清仓信号；按仓位上限和次日执行纪律处理。")
    lines.append("")

    lines.append("## 明日计划")
    lines.append("- 收盘后再评分，次一交易日执行；不盘中追高。")
    lines.append("- 单只最高60%，最多3只；同主题重复ETF只留最强。")
    lines.append("- 20日回撤超过12%先撤，溢价超过5%直接否决，20日动量超过30%进入衰减锁仓。")
    lines.append("- 这是研究和模拟盘模型，不构成投资建议。")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, ranked: list[EtfRow]) -> None:
    fields = [
        "rank",
        "code",
        "name",
        "theme",
        "date",
        "close",
        "final_score",
        "position_pct",
        "position_note",
        "eligible",
        "status",
        "ret10",
        "ret20",
        "ret60",
        "weighted_momentum",
        "premium_pct",
        "mdd20",
        "amount_yi",
        "momentum_score",
        "premium_score",
        "drawdown_score",
        "rs_score",
        "trend_score",
        "gap_score",
        "reasons",
        "clear_reasons",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in ranked:
            writer.writerow(
                {
                    "rank": row.rank,
                    "code": row.code,
                    "name": row.name,
                    "theme": row.theme,
                    "date": row.date,
                    "close": f"{row.close:.4f}",
                    "final_score": f"{row.final_score:.2f}",
                    "position_pct": row.position_pct,
                    "position_note": row.position_note,
                    "eligible": row.eligible,
                    "status": row.status,
                    "ret10": f"{row.ret10:.4f}",
                    "ret20": f"{row.ret20:.4f}",
                    "ret60": f"{row.ret60:.4f}",
                    "weighted_momentum": f"{row.weighted_momentum:.4f}",
                    "premium_pct": "" if row.premium_pct is None else f"{row.premium_pct:.4f}",
                    "mdd20": f"{row.mdd20:.4f}",
                    "amount_yi": f"{row.amount_yi:.4f}",
                    "momentum_score": f"{row.momentum_score:.2f}",
                    "premium_score": f"{row.premium_score:.2f}",
                    "drawdown_score": f"{row.drawdown_score:.2f}",
                    "rs_score": f"{row.rs_score:.2f}",
                    "trend_score": f"{row.trend_score:.2f}",
                    "gap_score": f"{row.gap_score:.2f}",
                    "reasons": "；".join(row.reasons),
                    "clear_reasons": "；".join(row.clear_reasons),
                    "error": row.error,
                }
            )


def write_model_doc(path: Path) -> None:
    path.write_text(
        f"""# ETF动量轮动模型

## 假设

行业 ETF 的短周期趋势具有延续性。用 10日/20日动量识别正在加速的主线，再用溢价、回撤、相对强度、动量差距过滤追高和趋势破坏。

## ETF池

固定 {len(ETF_UNIVERSE)} 只场内 ETF，覆盖科技、金融、消费、医药、周期、新能源、制造、地产基建、港股行业、海外、红利质量等方向。货币 ETF、债券 ETF、纯宽基 ETF 不进入池子；同名或明显同类重复 ETF 已删除。

沪深300、创业板、科创50只用于市场模式观察，不进入交易排名。

## 五维评分

1. 动量得分：10日动量 x 30% + 20日动量 x 70%，做池内百分位。
2. 溢价率：现价相对实时估算净值，超过 5% 直接否决。
3. 最大回撤：20日最大回撤超过 15% 不新开；超过 12% 作为持仓清仓警戒。
4. 相对强度：20日涨幅在全池排名，选冠军，不选陪跑。
5. 动量差距：第一名和第二名差距决定仓位集中度。

## 仓位规则

- 第一名 >= 90 且领先第二名 >= 10 分：第一名 60%，第二名最多 20%。
- 第一名 >= 85：第一名 50%，第二名最多 25%，差距很小时第三名最多 10%。
- 第一名 >= 75：第一名 40%，第二名最多 20%。
- 第一名 70-75：只允许 20% 试仓。
- 第一名 < 70：不开新仓。
- 20日动量超过 30%：进入衰减锁仓，目标仓位不高于 45%。
- 同主题重复 ETF 只保留最强的一只，其余标注“同类未入选”。

## 风控规则

- 单只最高 60%，最多 3 只 ETF。
- 溢价超过 5%、20日回撤超过 15%、成交额低于 0.1 亿，不新开。
- 已持仓 ETF 跌破 MA20 且 10日动量转负，或 20日回撤超过 12%，次日退出。
- 每日收盘后评分，次一交易日执行，避免盘中追高。
""",
        encoding="utf-8",
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[EtfRow] = []
    market_rows: list[EtfRow] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(build_row, item) for item in ETF_UNIVERSE]
        for future in as_completed(futures):
            rows.append(future.result())
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(build_row, item) for item in MARKET_WATCH]
        for future in as_completed(futures):
            market_rows.append(future.result())
    ranked = score_rows(rows)
    ranked, allocation_note = assign_positions(ranked)
    data_date = max((row.date for row in ranked if row.date), default=dt.date.today().isoformat())
    stamp = data_date.replace("-", "")
    report = build_report(ranked, allocation_note, market_rows)
    report_path = OUT_DIR / f"etf_rotation_signal_{data_date}.md"
    csv_path = OUT_DIR / f"etf_rotation_scores_{data_date}.csv"
    report_path.write_text(report, encoding="utf-8")
    write_csv(csv_path, ranked)
    write_model_doc(OUT_DIR / "etf_momentum_rotation_model.md")
    shutil.copyfile(report_path, OUT_DIR / "etf_rotation_signal_latest.md")
    shutil.copyfile(csv_path, OUT_DIR / "etf_rotation_scores_latest.csv")
    print(f"wrote {report_path}")
    print(f"wrote {csv_path}")
    print(f"stamp {stamp}")


if __name__ == "__main__":
    main()
