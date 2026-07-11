from __future__ import annotations

import numpy as np
import pandas as pd


def factor_report(data: pd.DataFrame, factor: str, target: str, quantiles: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    sample = data[[factor, target]].dropna().copy()
    # Rank then use Pearson to avoid a hard scipy dependency.
    ic = sample.groupby(level="date").apply(
        lambda x: x[factor].rank().corr(x[target].rank())
    ).rename("rank_ic").dropna().to_frame()

    def bucket(x: pd.DataFrame) -> pd.DataFrame:
        if x[factor].nunique() < 2 or len(x) < quantiles:
            return pd.DataFrame(columns=["quantile", "return"])
        ranks = x[factor].rank(method="first")
        labels = pd.qcut(ranks, quantiles, labels=False, duplicates="drop") + 1
        return pd.DataFrame({"quantile": labels, "return": x[target].to_numpy()})

    pieces = []
    for date, group in sample.groupby(level="date"):
        part = bucket(group)
        part["date"] = date
        pieces.append(part)
    quantile = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=["quantile", "return", "date"])
    quantile_summary = quantile.groupby("quantile")["return"].agg(["mean", "std", "count"])
    if not quantile_summary.empty:
        quantile_summary["annualized_mean"] = quantile_summary["mean"] * 252 / max(1, int(target.rsplit("_", 1)[-1]))
    return ic, quantile_summary


def ic_summary(ic: pd.DataFrame) -> dict[str, float]:
    series = ic["rank_ic"].dropna()
    std = series.std(ddof=1)
    return {"ic_mean": float(series.mean()), "ic_std": float(std),
            "ic_ir": float(series.mean() / std) if std and np.isfinite(std) else float("nan"),
            "positive_ratio": float((series > 0).mean()), "observations": int(len(series))}
