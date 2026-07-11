from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

from quant_factor_lab.backtest import CostModel, run_rank_backtest
from quant_factor_lab.data import normalize_bars
from quant_factor_lab.factors import add_forward_return, compute_factors
from quant_factor_lab.cli import main


def sample_bars(days: int = 90, symbols: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for j in range(symbols):
        for i, date in enumerate(dates):
            close = 10 + j + i * (j + 1) * 0.01
            rows.append({"date": date, "symbol": f"{j + 1:06d}", "open": close,
                         "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1000 + i * (j + 1)})
    return normalize_bars(pd.DataFrame(rows))


class FactorLabTest(unittest.TestCase):
    def test_forward_return_uses_requested_horizon(self):
        bars = sample_bars()
        enriched = add_forward_return(compute_factors(bars), bars, 5)
        symbol = "000005"
        series = bars.xs(symbol, level="symbol")["close"]
        date = series.index[30]
        expected = series.iloc[35] / series.iloc[30] - 1
        self.assertAlmostEqual(enriched.loc[(date, symbol), "forward_return_5"], expected)

    def test_execution_is_lagged_and_cost_is_pessimistic(self):
        bars = sample_bars()
        factors = compute_factors(bars)
        free, weights = run_rank_backtest(bars, factors, "momentum_20", top_n=1,
                                          cost=CostModel(0, 0, 0))
        costly, _ = run_rank_backtest(bars, factors, "momentum_20", top_n=1,
                                      cost=CostModel(0.001, 0.001, 0.0005))
        first_score_date = factors["momentum_20"].dropna().index.get_level_values("date").min()
        self.assertEqual(float(weights.loc[first_score_date].sum()), 0.0)
        self.assertLessEqual(float(costly["equity"].iloc[-1]), float(free["equity"].iloc[-1]))
        changes = weights.ne(weights.shift()).any(axis=1)
        changed_dates = weights.index[changes & weights.index.to_series().gt(weights.index[0])]
        # Weekly signals form on Monday and become held weights on the next bar.
        self.assertTrue(all(date.weekday() == 1 for date in changed_dates))

    def test_duplicate_bar_rejected(self):
        raw = sample_bars().reset_index().iloc[:2]
        raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize_bars(raw)

    def test_cli_research_writes_complete_artifacts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bars.csv"
            output = root / "result"
            sample_bars().reset_index().to_csv(source, index=False)
            argv = ["quant-factor", "research", "--input", str(source), "--factor",
                    "momentum_20", "--top-n", "2", "--output-dir", str(output)]
            with patch("sys.argv", argv):
                main()
            self.assertEqual(
                {"daily_ic.csv", "quantile_returns.csv", "equity_curve.csv", "weights.csv", "summary.json"},
                {path.name for path in output.iterdir()},
            )


if __name__ == "__main__":
    unittest.main()
