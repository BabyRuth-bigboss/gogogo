from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    commission: float = 0.0003
    slippage: float = 0.0005
    stamp_duty_sell: float = 0.0  # ETF=0; set current stock rate explicitly.


def _rebalance_mask(dates: pd.DatetimeIndex, frequency: str) -> pd.Series:
    periods = pd.Series(dates.to_period("W" if frequency == "weekly" else "M"), index=dates)
    return periods.ne(periods.shift(1))


def run_rank_backtest(bars: pd.DataFrame, factors: pd.DataFrame, factor: str, top_n: int = 3,
                      frequency: str = "weekly", cost: CostModel = CostModel()) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("frequency must be weekly or monthly")
    closes = bars["close"].unstack("symbol").sort_index()
    scores = factors[factor].unstack("symbol").reindex(closes.index)
    rebalance = _rebalance_mask(closes.index, frequency)
    target = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    for date in closes.index[rebalance]:
        valid = scores.loc[date].dropna().nlargest(top_n).index
        target.loc[date] = 0.0
        if len(valid):
            target.loc[date, valid] = 1.0 / len(valid)
    signal_weights = target.ffill().fillna(0.0)
    # Signal after close T is executed at close T+1, so it earns from T+1 to T+2.
    held_weights = signal_weights.shift(1).fillna(0.0)
    asset_returns = closes.pct_change().shift(-1).fillna(0.0)
    gross = (held_weights * asset_returns).sum(axis=1)
    delta = held_weights.diff().fillna(held_weights)
    buys = delta.clip(lower=0).sum(axis=1)
    sells = -delta.clip(upper=0).sum(axis=1)
    costs = (buys + sells) * (cost.commission + cost.slippage) + sells * cost.stamp_duty_sell
    net = gross - costs
    result = pd.DataFrame({"gross_return": gross, "cost": costs, "net_return": net,
                           "turnover": buys + sells})
    result["equity"] = (1 + result["net_return"]).cumprod()
    return result, held_weights


def performance(result: pd.DataFrame) -> dict[str, float]:
    r = result["net_return"]
    years = max(len(r) / 252, 1 / 252)
    total = float(result["equity"].iloc[-1] - 1)
    annual = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    vol = float(r.std(ddof=1) * np.sqrt(252))
    drawdown = result["equity"] / result["equity"].cummax() - 1
    return {"total_return": total, "annualized_return": annual, "annualized_volatility": vol,
            "sharpe_0rf": annual / vol if vol else float("nan"),
            "max_drawdown": float(drawdown.min()), "total_cost": float(result["cost"].sum()),
            "annual_turnover": float(result["turnover"].sum() / years)}
