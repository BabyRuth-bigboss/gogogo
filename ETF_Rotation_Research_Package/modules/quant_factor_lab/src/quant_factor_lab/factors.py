from __future__ import annotations

import numpy as np
import pandas as pd


def compute_factors(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute trailing-only factors; positive score always means preferred."""
    g = bars.groupby(level="symbol", group_keys=False)
    close = bars["close"]
    volume = bars["volume"]
    out = pd.DataFrame(index=bars.index)
    out["momentum_20"] = g["close"].pct_change(20)
    out["momentum_60"] = g["close"].pct_change(60)
    out["reversal_5"] = -g["close"].pct_change(5)
    daily_return = g["close"].pct_change()
    out["low_vol_20"] = -daily_return.groupby(level="symbol").rolling(20).std().droplevel(0) * np.sqrt(252)
    avg_volume = volume.groupby(level="symbol").rolling(20).mean().droplevel(0)
    out["volume_ratio_20"] = volume / avg_volume - 1
    return out.replace([np.inf, -np.inf], np.nan)


def add_forward_return(factors: pd.DataFrame, bars: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    result = factors.copy()
    result[f"forward_return_{horizon}"] = bars.groupby(level="symbol")["close"].shift(-horizon) / bars["close"] - 1
    return result

