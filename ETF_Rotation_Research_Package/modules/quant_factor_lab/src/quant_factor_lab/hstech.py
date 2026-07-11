from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class HkCostModel:
    commission: float = 0.0001       # user-specified: 1 bp each side
    stamp_duty: float = 0.001        # Hong Kong stock duty, each side
    levies_and_fees: float = 0.000105
    slippage: float = 0.0015         # conservative for a mixed-liquidity basket

    @property
    def one_way(self) -> float:
        return self.commission + self.stamp_duty + self.levies_and_fees + self.slippage


@dataclass(frozen=True)
class RegimeConfig:
    top_n: int = 4
    long_ma: int = 160
    stock_ma: int = 100
    breadth_gate: float = 0.50
    max_market_drawdown: float = 0.20
    max_market_volatility: float = 0.48
    sideways_exposure: float = 0.50
    min_score_change: float = 0.10


@dataclass(frozen=True)
class TacticalConfig:
    top_n: int = 2
    fast_ma: int = 10
    slow_ma: int = 40
    stock_ma: int = 20
    breadth_ma: int = 50
    breadth_gate: float = 0.40
    max_market_drawdown: float = 0.15
    max_market_volatility: float = 0.60
    score_mode: str = "momentum"
    min_score_change: float = 0.10


def yahoo_symbol(code: str) -> str:
    return f"{int(code):04d}.HK"


def fetch_yahoo_hk(codes: Iterable[str], start: str, end: str, benchmark: str = "3032.HK") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("install quant-factor-lab[data] for Yahoo downloads") from exc
    tickers = [yahoo_symbol(code) for code in codes] + [benchmark]
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False,
                      group_by="column", threads=True)
    if raw.empty:
        raise RuntimeError("Yahoo returned no Hong Kong price data")
    pieces: list[pd.DataFrame] = []
    for ticker in tickers:
        try:
            part = pd.DataFrame({
                "date": raw.index,
                "symbol": "BENCHMARK" if ticker == benchmark else ticker.split(".")[0].zfill(4),
                "open": raw[("Open", ticker)].to_numpy(),
                "high": raw[("High", ticker)].to_numpy(),
                "low": raw[("Low", ticker)].to_numpy(),
                "close": raw[("Close", ticker)].to_numpy(),
                "volume": raw[("Volume", ticker)].to_numpy(),
            }).dropna(subset=["close"])
        except KeyError:
            continue
        if not part.empty:
            pieces.append(part)
    if not pieces:
        raise RuntimeError("Yahoo response contained no usable tickers")
    return pd.concat(pieces, ignore_index=True).sort_values(["date", "symbol"])


def save_hk_bars(frame: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)


def load_hk_bars(path: str | Path) -> pd.DataFrame:
    data = pd.read_csv(path, dtype={"symbol": str})
    data["date"] = pd.to_datetime(data["date"])
    data["symbol"] = data["symbol"].where(data["symbol"].eq("BENCHMARK"), data["symbol"].str.zfill(4))
    if data.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate Hong Kong bars")
    return data.sort_values(["date", "symbol"])


def _last_bar_of_week(index: pd.DatetimeIndex) -> pd.Series:
    period = pd.Series(index.to_period("W"), index=index)
    return period.ne(period.shift(-1))


def _cross_section_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)


def build_features(bars: pd.DataFrame, eligible: Iterable[str], config: RegimeConfig) -> dict[str, pd.DataFrame | pd.Series]:
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    universe = [str(code).zfill(4) for code in eligible if str(code).zfill(4) in close.columns]
    stocks = close[universe]
    benchmark = close["BENCHMARK"].dropna()
    close = close.loc[benchmark.index.min():]
    stocks = stocks.reindex(close.index)
    benchmark = benchmark.reindex(close.index).ffill()

    ret20, ret60, ret120 = stocks.pct_change(20), stocks.pct_change(60), stocks.pct_change(120)
    vol60 = stocks.pct_change().rolling(60).std() * np.sqrt(252)
    score = (0.25 * _cross_section_rank(ret20) + 0.40 * _cross_section_rank(ret60)
             + 0.35 * _cross_section_rank(ret120) + 0.10 * _cross_section_rank(-vol60)) / 1.10
    stock_trend = stocks > stocks.rolling(config.stock_ma).mean()
    score = score.where(stock_trend)

    breadth = stock_trend.sum(axis=1) / stock_trend.notna().sum(axis=1).replace(0, np.nan)
    bench_ma_long = benchmark.rolling(config.long_ma).mean()
    bench_ma_short = benchmark.rolling(max(60, config.long_ma // 2)).mean()
    bench_mom20 = benchmark.pct_change(20)
    bench_vol63 = benchmark.pct_change().rolling(63).std() * np.sqrt(252)
    bench_dd120 = benchmark / benchmark.rolling(120).max() - 1
    stress = (bench_vol63 > config.max_market_volatility) | (bench_dd120 < -config.max_market_drawdown)
    bull = ((benchmark > bench_ma_long) & (bench_mom20 > 0) &
            (breadth >= config.breadth_gate) & ~stress)
    sideways = ((benchmark > bench_ma_short) &
                (breadth >= max(0.25, config.breadth_gate - 0.15)) & ~stress & ~bull)
    regime = pd.Series("bear", index=close.index, name="regime")
    regime.loc[sideways.fillna(False)] = "sideways"
    regime.loc[bull.fillna(False)] = "bull"
    exposure = regime.map({"bull": 1.0, "sideways": config.sideways_exposure, "bear": 0.0})
    return {"close": stocks, "benchmark": benchmark, "score": score, "breadth": breadth,
            "regime": regime, "exposure": exposure, "benchmark_drawdown_120": bench_dd120,
            "benchmark_volatility_63": bench_vol63}


def run_hstech_strategy(bars: pd.DataFrame, eligible: Iterable[str], config: RegimeConfig,
                        costs: HkCostModel = HkCostModel()) -> dict[str, pd.DataFrame | pd.Series | dict]:
    f = build_features(bars, eligible, config)
    close = f["close"]
    score = f["score"]
    exposure = f["exposure"]
    signal_dates = _last_bar_of_week(close.index)
    target = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    previous = pd.Series(0.0, index=close.columns)
    for date in close.index[signal_dates]:
        candidates = score.loc[date].dropna().nlargest(config.top_n)
        proposed = pd.Series(0.0, index=close.columns)
        if len(candidates) and exposure.loc[date] > 0:
            proposed.loc[candidates.index] = float(exposure.loc[date]) / len(candidates)
        if float((proposed - previous).abs().sum()) >= config.min_score_change:
            previous = proposed
        target.loc[date] = previous
    signal_weights = target.ffill().fillna(0.0)
    weights = signal_weights.shift(1).fillna(0.0)
    asset_returns = close.pct_change().shift(-1).fillna(0.0)
    gross_contrib = weights * asset_returns
    gross = gross_contrib.sum(axis=1)
    delta = weights.diff().fillna(weights)
    turnover = delta.abs().sum(axis=1)
    transaction_cost = turnover * costs.one_way
    net = gross - transaction_cost
    result = pd.DataFrame({"gross_return": gross, "cost": transaction_cost,
                           "net_return": net, "turnover": turnover,
                           "regime": f["regime"], "breadth": f["breadth"]})
    valid = f["benchmark"].rolling(config.long_ma).mean().notna()
    result = result.loc[valid]
    weights = weights.reindex(result.index)
    gross_contrib = gross_contrib.reindex(result.index)
    result["equity"] = (1 + result["net_return"]).cumprod()
    benchmark_return = f["benchmark"].pct_change().shift(-1).reindex(result.index).fillna(0.0)
    result["benchmark_return"] = benchmark_return
    result["benchmark_equity"] = (1 + benchmark_return).cumprod()
    trades = delta.reindex(result.index).stack().rename("weight_change").reset_index()
    trades = trades[trades["weight_change"].abs() > 1e-12]
    return {"daily": result, "weights": weights, "contribution": gross_contrib,
            "trades": trades, "features": f, "config": asdict(config), "costs": asdict(costs)}


def run_tactical_strategy(bars: pd.DataFrame, eligible: Iterable[str], config: TacticalConfig,
                          costs: HkCostModel = HkCostModel()) -> dict[str, pd.DataFrame | pd.Series | dict]:
    close_all = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    universe = [str(code).zfill(4) for code in eligible if str(code).zfill(4) in close_all.columns]
    benchmark = close_all["BENCHMARK"].dropna()
    stocks = close_all[universe].reindex(benchmark.index)
    ret10, ret20, ret60 = stocks.pct_change(10), stocks.pct_change(20), stocks.pct_change(60)
    vol20 = stocks.pct_change().rolling(20).std() * np.sqrt(252)
    breakout60 = stocks / stocks.rolling(60).max().shift(1) - 1
    if config.score_mode == "momentum":
        score = (0.30 * _cross_section_rank(ret10) + 0.45 * _cross_section_rank(ret20)
                 + 0.25 * _cross_section_rank(ret60) + 0.10 * _cross_section_rank(-vol20)) / 1.10
    elif config.score_mode == "breakout":
        score = (0.40 * _cross_section_rank(breakout60) + 0.35 * _cross_section_rank(ret20)
                 + 0.25 * _cross_section_rank(ret60) + 0.10 * _cross_section_rank(-vol20)) / 1.10
    else:
        raise ValueError("score_mode must be momentum or breakout")
    stock_trend = stocks > stocks.rolling(config.stock_ma).mean()
    score = score.where(stock_trend & ret20.gt(0))
    breadth_trend = stocks > stocks.rolling(config.breadth_ma).mean()
    breadth = breadth_trend.sum(axis=1) / breadth_trend.notna().sum(axis=1).replace(0, np.nan)
    fast = benchmark.rolling(config.fast_ma).mean()
    slow = benchmark.rolling(config.slow_ma).mean()
    market_vol = benchmark.pct_change().rolling(20).std() * np.sqrt(252)
    market_dd = benchmark / benchmark.rolling(60).max() - 1
    risk_on = ((benchmark > fast) & (fast > slow) & benchmark.pct_change(10).gt(0)
               & breadth.ge(config.breadth_gate) & market_vol.le(config.max_market_volatility)
               & market_dd.gt(-config.max_market_drawdown))
    regime = pd.Series(np.where(risk_on.fillna(False), "bull", "bear"), index=stocks.index, name="regime")

    signal_dates = _last_bar_of_week(stocks.index)
    target = pd.DataFrame(np.nan, index=stocks.index, columns=stocks.columns)
    previous = pd.Series(0.0, index=stocks.columns)
    for date in stocks.index[signal_dates]:
        candidates = score.loc[date].dropna().nlargest(config.top_n)
        proposed = pd.Series(0.0, index=stocks.columns)
        if risk_on.loc[date] and len(candidates):
            proposed.loc[candidates.index] = 1.0 / len(candidates)
        if float((proposed - previous).abs().sum()) >= config.min_score_change:
            previous = proposed
        target.loc[date] = previous
    weights = target.ffill().fillna(0.0).shift(1).fillna(0.0)
    asset_returns = stocks.pct_change().shift(-1).fillna(0.0)
    contribution = weights * asset_returns
    delta = weights.diff().fillna(weights)
    turnover = delta.abs().sum(axis=1)
    daily = pd.DataFrame({"gross_return": contribution.sum(axis=1),
                          "cost": turnover * costs.one_way, "turnover": turnover,
                          "regime": regime, "breadth": breadth})
    daily["net_return"] = daily["gross_return"] - daily["cost"]
    valid = slow.notna()
    daily = daily.loc[valid]
    weights, contribution = weights.reindex(daily.index), contribution.reindex(daily.index)
    daily["equity"] = (1 + daily["net_return"]).cumprod()
    daily["benchmark_return"] = benchmark.pct_change().shift(-1).reindex(daily.index).fillna(0.0)
    daily["benchmark_equity"] = (1 + daily["benchmark_return"]).cumprod()
    trades = delta.reindex(daily.index).stack().rename("weight_change").reset_index()
    trades = trades[trades["weight_change"].abs() > 1e-12]
    return {"daily": daily, "weights": weights, "contribution": contribution,
            "trades": trades, "features": {"score": score, "regime": regime, "breadth": breadth},
            "config": asdict(config), "costs": asdict(costs)}


def metrics(daily: pd.DataFrame) -> dict[str, float]:
    if daily.empty:
        return {key: float("nan") for key in ("annualized_return", "max_drawdown", "sharpe")}
    r = daily["net_return"].fillna(0.0)
    years = len(r) / 252
    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    annual = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan")
    vol = float(r.std() * np.sqrt(252))
    dd = equity / equity.cummax() - 1
    return {"total_return": total, "annualized_return": annual, "annualized_volatility": vol,
            "sharpe": annual / vol if vol else float("nan"), "max_drawdown": float(dd.min()),
            "annual_turnover": float(daily["turnover"].sum() / years),
            "trade_days": int((daily["turnover"] > 0).sum()),
            "cash_ratio": float((daily.get("regime") == "bear").mean()),
            "total_cost": float(daily["cost"].sum())}


def split_metrics(daily: pd.DataFrame, split_date: str = "2024-01-01") -> dict[str, dict[str, float]]:
    split = pd.Timestamp(split_date)
    return {"train": metrics(daily.loc[daily.index < split]),
            "test": metrics(daily.loc[daily.index >= split]), "full": metrics(daily)}


def parameter_grid() -> list[RegimeConfig]:
    return [RegimeConfig(top_n=top_n, long_ma=long_ma, breadth_gate=breadth,
                         sideways_exposure=sideways)
            for top_n in (3, 4, 5)
            for long_ma in (120, 160, 200)
            for breadth in (0.40, 0.50, 0.60)
            for sideways in (0.0, 0.5)]


def tactical_grid() -> list[TacticalConfig]:
    return [TacticalConfig(top_n=top_n, fast_ma=fast, slow_ma=slow,
                           breadth_gate=breadth, score_mode=mode)
            for top_n in (1, 2, 3)
            for fast, slow in ((10, 30), (10, 40), (20, 60))
            for breadth in (0.35, 0.45, 0.55)
            for mode in ("momentum", "breakout")]


def yearly_returns(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.groupby(daily.index.year).agg(
        strategy=("net_return", lambda x: (1 + x).prod() - 1),
        benchmark=("benchmark_return", lambda x: (1 + x).prod() - 1),
        turnover=("turnover", "sum"), cost=("cost", "sum"))


def focus_attribution(run: dict, focus: Iterable[str]) -> pd.DataFrame:
    contribution = run["contribution"]
    weights = run["weights"]
    rows = []
    for raw in focus:
        symbol = str(raw).zfill(4)
        if symbol not in contribution:
            rows.append({"symbol": symbol, "gross_contribution": np.nan, "days_held": 0,
                         "average_weight_when_held": np.nan})
            continue
        held = weights[symbol] > 0
        rows.append({"symbol": symbol, "gross_contribution": float(contribution[symbol].sum()),
                     "days_held": int(held.sum()),
                     "average_weight_when_held": float(weights.loc[held, symbol].mean()) if held.any() else 0.0})
    return pd.DataFrame(rows).set_index("symbol")


def observation_report(bars: pd.DataFrame, focus: Iterable[str]) -> pd.DataFrame:
    close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    rows = []
    for raw in focus:
        symbol = str(raw).zfill(4)
        series = close[symbol].dropna() if symbol in close else pd.Series(dtype=float)
        if series.empty:
            rows.append({"symbol": symbol, "first_date": None, "last_date": None})
            continue
        returns = series.pct_change().dropna()
        equity = series / series.iloc[0]
        years = max(len(series) / 252, 1 / 252)
        ma20 = series.rolling(20).mean().iloc[-1]
        ma60 = series.rolling(60).mean().iloc[-1]
        rows.append({"symbol": symbol, "first_date": series.index[0].date(),
                     "last_date": series.index[-1].date(),
                     "annualized_return_since_available": float(equity.iloc[-1] ** (1 / years) - 1),
                     "annualized_volatility": float(returns.std() * np.sqrt(252)),
                     "max_drawdown": float((equity / equity.cummax() - 1).min()),
                     "momentum_20": float(series.pct_change(20).iloc[-1]),
                     "momentum_60": float(series.pct_change(60).iloc[-1]),
                     "above_ma20": bool(series.iloc[-1] > ma20),
                     "above_ma60": bool(series.iloc[-1] > ma60)})
    return pd.DataFrame(rows).set_index("symbol")


def drawdown_episodes(daily: pd.DataFrame) -> pd.DataFrame:
    equity = (1 + daily["net_return"]).cumprod()
    drawdown = equity / equity.cummax() - 1
    underwater = drawdown < 0
    groups = underwater.ne(underwater.shift()).cumsum()
    rows = []
    for _, episode in drawdown[underwater].groupby(groups[underwater]):
        trough = episode.idxmin()
        rows.append({"start": episode.index[0], "trough": trough, "end": episode.index[-1],
                     "max_drawdown": float(episode.min()), "underwater_days": int(len(episode))})
    return pd.DataFrame(rows).sort_values("max_drawdown").reset_index(drop=True) if rows else pd.DataFrame()
