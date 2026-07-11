#!/usr/bin/env python3
"""
强弱市判断引擎 V2 —— MA斜率 + 信号防抖 + 港股指数

改进点（相对 V1 的 generic_market_gate_bad）：
1. 不只看 price < MA，还看 MA 本身的方向（斜率）
   — 价格跌破上升的 MA = 假弱市（回调买入机会）
   — 价格跌破下降的 MA = 真弱市（趋势转空）
2. 引入死叉确认（MA50 < MA120）作为辅助信号
3. 信号防抖：切换需连续 N 天确认 + 冷却期
4. 指数池扩展到 A 股 + 港股共 6 个指数
5. 输出三态（强市 / 中性 / 弱市）而非二元

用法：
    from regime_engine_v2 import RegimeEngine, BENCHMARK_INDEXES_V2

    engine = RegimeEngine(
        indices=BENCHMARK_INDEXES_V2,
        ma_days=120,
        slope_lookback=20,
        slope_threshold=-0.005,
        weak_ratio=0.40,
        confirm_days=3,
        recovery_confirm_days=2,
        cooldown_days=10,
    )
    bad, note = engine.classify(signal_date, histories, indexes)
"""

from __future__ import annotations

from typing import Any

# ── 基础工具（从现有代码复用） ──────────────────────────
def _fnum(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _ma(values: list[float], window: int) -> float:
    if window <= 0 or len(values) < window:
        return 0.0
    return sum(values[-window:]) / window


# ── 扩展后的基准指数池 ──────────────────────────────────
# sec 格式为腾讯 K 线 API 参数（sh=沪, sz=深, hk=港）
# 如果某个指数的 sec 在数据源中不可用，该指数会被静默跳过
BENCHMARK_INDEXES_V2: dict[str, dict[str, str]] = {
    # A 股三大指数（原有）
    "IDX_HS300":   {"sec": "sh000300", "label": "沪深300"},
    "IDX_CHINEXT": {"sec": "sz399006", "label": "创业板"},
    "IDX_SCI50":   {"sec": "sh000688", "label": "科创50"},
    # A 股扩展
    "IDX_CSI500":  {"sec": "sh000905", "label": "中证500"},
    # 港股指数（如果数据源不支持，会被自动跳过）
    # 恒生指数 — 腾讯 API 可能用 hkHSI；若不支持请替换为其他数据源
    "IDX_HSI":     {"sec": "hkHSI",    "label": "恒生指数"},
    # 恒生科技 — 如果指数不可用，可使用 ETF 513180 的价格序列替代
    "IDX_HSTECH":  {"sec": "hkHSTECH", "label": "恒生科技"},
}

# 兼容旧版的三指数池
BENCHMARK_INDEXES_V1: dict[str, dict[str, str]] = {
    "IDX_HS300":   {"sec": "sh000300", "label": "沪深300"},
    "IDX_CHINEXT": {"sec": "sz399006", "label": "创业板"},
    "IDX_SCI50":   {"sec": "sh000688", "label": "科创50"},
}


class RegimeEngine:
    """强弱市判断引擎（MA斜率 + 防抖 + 多指数）"""

    def __init__(
        self,
        indices: dict[str, dict[str, str]] | None = None,
        ma_days: int = 120,
        slope_lookback: int = 20,
        slope_threshold: float = -0.005,
        weak_ratio: float = 0.40,
        confirm_days: int = 3,
        recovery_confirm_days: int = 2,
        cooldown_days: int = 10,
    ):
        """
        Args:
            indices: 指数池 {code: {sec, label}}，默认用 V2（含港股）
            ma_days: 主均线周期（120=半年线, 200=年线）
            slope_lookback: MA 斜率计算窗口（交易日）
            slope_threshold: MA 斜率阈值。MA 在 slope_lookback 天内
                            涨幅低于此值即视为"MA 走平/下行"
                            设为 -0.005 = -0.5% 即 MA 在 20 天内基本没涨
            weak_ratio: 弱市触发比例。有效指数中 ≥ 此比例显示真弱势 → 弱市
            confirm_days: 进入弱市需连续确认天数
            recovery_confirm_days: 恢复强市需连续确认天数（通常更短，不错过反弹）
            cooldown_days: 切换后冷却天数
        """
        self.indices = indices or BENCHMARK_INDEXES_V2
        self.ma_days = ma_days
        self.slope_lookback = slope_lookback
        self.slope_threshold = slope_threshold
        self.weak_ratio = weak_ratio
        self.confirm_days = confirm_days
        self.recovery_confirm_days = recovery_confirm_days
        self.cooldown_days = cooldown_days

        # 状态机
        self._prev_regime: int = 0        # -1=弱, 0=中性, 1=强
        self._weak_streak: int = 0        # 连续弱市计数
        self._strong_streak: int = 0      # 连续强市计数
        self._last_switch_day: int = -999 # 上次切换的绝对日期索引

        # 缓存
        self._cache: dict[tuple, tuple[int, str]] = {}

    # ── 单指数分析 ──────────────────────────────────────
    def _index_state(
        self,
        code: str,
        signal_date: str,
        histories: dict[str, list[dict[str, Any]]],
        indexes: dict[str, dict[str, int]],
    ) -> dict[str, Any] | None:
        """返回单个指数的趋势状态，数据不足返回 None"""
        idx = indexes.get(code, {}).get(signal_date)
        if idx is None or idx < self.ma_days:
            return None  # 连基础 MA 都不够

        rows = histories[code][: idx + 1]
        closes = [_fnum(r["close"]) for r in rows]
        if len(closes) <= self.ma_days or min(closes[-self.ma_days:]) <= 0:
            return None

        # 斜率数据是否充足
        slope_ready = len(closes) >= self.ma_days + self.slope_lookback

        # 基础 MA 计算
        ma = _ma(closes, self.ma_days)
        ma50 = _ma(closes, 50)
        ma200 = _ma(closes, 200) if len(closes) >= 200 else ma

        # 当前价格
        price = closes[-1]

        # ── MA 斜率：MA 线本身的 20 日 ROC ──
        # 如果斜率数据不足，标记为 slope_ready=False，回退到纯价格判断
        if slope_ready:
            ma_before = _ma(closes[: -self.slope_lookback], self.ma_days)
            ma_slope = (ma - ma_before) / ma_before if ma_before > 0 else 0.0
        else:
            ma_slope = 0.0

        # ── 价格相对 MA 的位置 ──
        price_below_ma = price < ma
        price_below_ma50 = price < ma50 if ma50 > 0 else False
        price_below_ma200 = price < ma200 if ma200 > 0 else False

        # ── MA 排列（多头/空头/交错） ──
        if ma50 > 0 and ma200 > 0:
            death_cross = ma50 < ma    # MA50 跌破主 MA
            deep_death = ma50 < ma200  # MA50 跌破 MA200
            golden_cross = ma50 > ma and ma50 > ma200
        else:
            death_cross = False
            deep_death = False
            golden_cross = False

        # ── Ma 方向 ──
        if slope_ready:
            ma_declining = ma_slope < self.slope_threshold  # MA 走平或下行
            ma_rising = ma_slope > abs(self.slope_threshold)  # MA 明确上行
        else:
            # 回退模式：斜率数据不足，仅用价格位置判断
            ma_declining = price_below_ma  # 保守：低于 MA 就算弱
            ma_rising = not price_below_ma

        # ── 真弱势 vs 假弱势 ──
        if slope_ready:
            # 真弱势 = 价格在 MA 下方 AND (MA正在下行 OR 死叉确认)
            # 假弱势 = 价格在 MA 下方 BUT MA 仍在上升（回调买入机会）
            true_weak = price_below_ma and (ma_declining or death_cross)
            false_weak = price_below_ma and not true_weak
        else:
            # 回退模式：无法区分真假，价格在 MA 下方都算弱
            true_weak = price_below_ma
            false_weak = False

        # ── 真强势 vs 假强势 ──
        if slope_ready:
            true_strong = (not price_below_ma) and ma_rising
            false_strong = (not price_below_ma) and not true_strong
        else:
            true_strong = not price_below_ma
            false_strong = False

        return {
            "code": code,
            "label": self.indices.get(code, {}).get("label", code),
            "price": price,
            "ma": ma,
            "ma50": ma50,
            "ma_slope": ma_slope,
            "price_below_ma": price_below_ma,
            "price_below_ma50": price_below_ma50,
            "price_below_ma200": price_below_ma200,
            "ma_declining": ma_declining,
            "ma_rising": ma_rising,
            "death_cross": death_cross,
            "deep_death": deep_death,
            "golden_cross": golden_cross,
            "true_weak": true_weak,
            "false_weak": false_weak,
            "true_strong": true_strong,
            "false_strong": false_strong,
        }

    # ── 综合判断 ────────────────────────────────────────
    def classify(
        self,
        signal_date: str,
        histories: dict[str, list[dict[str, Any]]],
        indexes: dict[str, dict[str, int]],
        day_index: int = 0,
    ) -> tuple[bool, str]:
        """
        返回 (market_bad, note)

        market_bad=True  → 弱市，切换到防御 sleeve
        market_bad=False → 强市，使用进攻 sleeve

        note 包含诊断信息。
        """
        cache_key = (
            self.ma_days,
            self.slope_lookback,
            self.slope_threshold,
            self.weak_ratio,
            signal_date,
        )
        if cache_key in self._cache:
            cached_regime, cached_note = self._cache[cache_key]
            raw_bad = cached_regime == -1
            # 防抖逻辑仍需执行
            return self._apply_debounce(raw_bad, cached_note, day_index)

        # ── 第一步：分析每个指数 ──
        states: list[dict[str, Any]] = []
        for code in self.indices:
            state = self._index_state(code, signal_date, histories, indexes)
            if state is not None:
                states.append(state)

        valid_count = len(states)
        if valid_count == 0:
            self._cache[cache_key] = (0, "no_valid_index")
            return self._apply_debounce(False, "no_valid_index", day_index)

        # ── 第二步：统计各维度信号 ──
        true_weak_count = sum(1 for s in states if s["true_weak"])
        false_weak_count = sum(1 for s in states if s["false_weak"])
        ma_declining_count = sum(1 for s in states if s["ma_declining"])
        death_cross_count = sum(1 for s in states if s["death_cross"])
        true_strong_count = sum(1 for s in states if s["true_strong"])

        weak_ratio_actual = true_weak_count / valid_count
        very_weak = true_weak_count / valid_count >= 0.50  # 过半真弱 → 高度确信

        # ── 第三步：判断原始信号 ──
        raw_bad = weak_ratio_actual >= self.weak_ratio

        # ── 第四步：构建诊断笔记 ──
        weak_labels = [s["label"] for s in states if s["true_weak"]]
        declining_labels = [s["label"] for s in states if s["ma_declining"]]
        false_weak_labels = [s["label"] for s in states if s["false_weak"]]
        strong_labels = [s["label"] for s in states if s["true_strong"]]

        parts = [f"true_weak={true_weak_count}/{valid_count}({weak_ratio_actual:.0%})"]
        if weak_labels:
            parts.append(f"弱:{','.join(weak_labels)}")
        if declining_labels:
            parts.append(f"MA↓:{','.join(declining_labels)}")
        if death_cross_count > 0:
            parts.append(f"死叉:{death_cross_count}")
        if false_weak_labels:
            parts.append(f"假弱(忽略):{','.join(false_weak_labels)}")
        if strong_labels:
            parts.append(f"强:{','.join(strong_labels)}")
        if very_weak:
            parts.append("⚠高确信弱市")

        raw_note = f"MA{self.ma_days}|{'|'.join(parts)}"

        self._cache[cache_key] = (-1 if raw_bad else 1, raw_note)
        return self._apply_debounce(raw_bad, raw_note, day_index)

    # ── 防抖状态机 ──────────────────────────────────────
    def _apply_debounce(
        self,
        raw_bad: bool,
        raw_note: str,
        day_index: int,
    ) -> tuple[bool, str]:
        """信号防抖 + 冷却期"""

        # 冷却期检查
        days_since_switch = day_index - self._last_switch_day if day_index > 0 else 999
        in_cooldown = days_since_switch < self.cooldown_days

        if in_cooldown and self._last_switch_day > 0:
            # 冷却期内，维持上一个 regime
            note = f"{raw_note}|冷却中({days_since_switch}/{self.cooldown_days}d)→维持{'弱' if self._prev_regime == -1 else '强'}"
            return self._prev_regime == -1, note

        if raw_bad:
            # 潜在弱市信号
            self._weak_streak += 1
            self._strong_streak = 0

            if self._weak_streak >= self.confirm_days:
                # 连续 N 天确认 → 正式切换
                self._weak_streak = 0
                if self._prev_regime != -1:
                    self._last_switch_day = day_index
                self._prev_regime = -1
                note = f"{raw_note}|确认弱市(连续{self.confirm_days}d)"
                return True, note
            else:
                # 等待确认
                if self._prev_regime == -1:
                    # 已经是弱市，维持
                    return True, raw_note
                else:
                    note = f"{raw_note}|待确认弱市({self._weak_streak}/{self.confirm_days}d)"
                    return self._prev_regime == -1, note
        else:
            # 潜在强市信号
            self._strong_streak += 1
            self._weak_streak = 0

            if self._strong_streak >= self.recovery_confirm_days:
                # 连续 N 天确认恢复 → 正式切换
                self._strong_streak = 0
                if self._prev_regime != 1:
                    self._last_switch_day = day_index
                self._prev_regime = 1
                note = f"{raw_note}|确认强市(连续{self.recovery_confirm_days}d)"
                return False, note
            else:
                if self._prev_regime == 1:
                    return False, raw_note
                else:
                    note = f"{raw_note}|待确认强市({self._strong_streak}/{self.recovery_confirm_days}d)"
                    return self._prev_regime != 1, note

    def reset(self) -> None:
        """重置状态机（每次回测开始时调用）"""
        self._prev_regime = 1  # 默认从强市开始
        self._weak_streak = 0
        self._strong_streak = 0
        self._last_switch_day = -999
        self._cache.clear()


# ── 快捷函数：兼容旧接口 ────────────────────────────────
def create_engine_v2_safe(
    ma_days: int = 120,
    slope_lookback: int = 20,
    slope_threshold: float = -0.005,
    weak_ratio: float = 0.40,
    confirm_days: int = 3,
    recovery_confirm_days: int = 2,
    cooldown_days: int = 10,
    use_hk: bool = True,
) -> RegimeEngine:
    """
    创建一个预配置的引擎。

    推荐两套预设：
    - preset="safe":   确认3天, 冷却10天, recovery确认2天 (更稳)
    - preset="agile":  确认2天, 冷却5天,  recovery确认1天 (更灵敏)
    """
    indices = BENCHMARK_INDEXES_V2 if use_hk else BENCHMARK_INDEXES_V1
    return RegimeEngine(
        indices=indices,
        ma_days=ma_days,
        slope_lookback=slope_lookback,
        slope_threshold=slope_threshold,
        weak_ratio=weak_ratio,
        confirm_days=confirm_days,
        recovery_confirm_days=recovery_confirm_days,
        cooldown_days=cooldown_days,
    )


# ── 诊断用：批量输出所有指数的趋势状态 ──────────────────
def diagnose_indices(
    engine: RegimeEngine,
    signal_date: str,
    histories: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    """输出每个指数的详细状态，方便调参"""
    results = []
    for code in engine.indices:
        state = engine._index_state(code, signal_date, histories, indexes)
        if state:
            results.append(state)
        else:
            results.append({"code": code, "label": engine.indices[code]["label"], "error": "data_unavailable"})
    return results


if __name__ == "__main__":
    print("RegimeEngine V2 — 强弱市判断引擎已就绪")
    print(f"  指数池: {list(BENCHMARK_INDEXES_V2.keys())}")
    print("  改进: MA斜率 + 防抖 + 死叉确认 + 港股指数")
