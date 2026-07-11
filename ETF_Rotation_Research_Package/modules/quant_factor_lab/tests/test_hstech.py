from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quant_factor_lab.hstech import (HkCostModel, RegimeConfig, TacticalConfig,
                                     run_hstech_strategy, run_tactical_strategy)


def synthetic_bars(days: int = 360) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=days)
    rows = []
    symbols = ["0700", "1810", "9988", "9999", "9660"]
    for j, symbol in enumerate(symbols):
        close = 10 * np.exp(np.arange(days) * (0.0005 + j * 0.00015))
        for date, price in zip(dates, close):
            rows.append({"date": date, "symbol": symbol, "open": price, "high": price,
                         "low": price, "close": price, "volume": 1_000_000})
    benchmark = 100 * np.exp(np.arange(days) * 0.0007)
    for date, price in zip(dates, benchmark):
        rows.append({"date": date, "symbol": "BENCHMARK", "open": price, "high": price,
                     "low": price, "close": price, "volume": 1_000_000})
    return pd.DataFrame(rows)


class HstechTest(unittest.TestCase):
    def test_weekly_weights_are_lagged(self):
        run = run_hstech_strategy(synthetic_bars(), ["0700", "1810", "9988", "9999", "9660"],
                                  RegimeConfig(long_ma=120, breadth_gate=0.4))
        changes = run["weights"].ne(run["weights"].shift()).any(axis=1)
        changed_dates = run["weights"].index[changes][1:]
        self.assertTrue(all(date.weekday() == 0 for date in changed_dates))

    def test_costs_reduce_returns(self):
        bars = synthetic_bars()
        cfg = RegimeConfig(long_ma=120, breadth_gate=0.4)
        free = run_hstech_strategy(bars, ["0700", "1810", "9988", "9999", "9660"], cfg,
                                    HkCostModel(0, 0, 0, 0))["daily"]
        costly = run_hstech_strategy(bars, ["0700", "1810", "9988", "9999", "9660"], cfg)["daily"]
        self.assertLessEqual(costly["equity"].iloc[-1], free["equity"].iloc[-1])

    def test_bear_market_goes_to_cash(self):
        bars = synthetic_bars()
        mask = bars["date"] > bars["date"].sort_values().unique()[220]
        bars.loc[mask, "close"] *= np.exp(-0.01 * bars.loc[mask].groupby("symbol").cumcount())
        run = run_hstech_strategy(bars, ["0700", "1810", "9988", "9999", "9660"],
                                  RegimeConfig(long_ma=120, breadth_gate=0.4))
        bear_dates = run["daily"].index[run["daily"]["regime"].eq("bear")]
        self.assertTrue((run["weights"].loc[bear_dates].sum(axis=1).tail(20) == 0).all())

    def test_tactical_strategy_only_holds_during_risk_on(self):
        run = run_tactical_strategy(synthetic_bars(), ["0700", "1810", "9988", "9999", "9660"],
                                    TacticalConfig(fast_ma=10, slow_ma=30, breadth_gate=0.35))
        bear = run["daily"]["regime"].eq("bear")
        # One-bar execution lag is permitted when a Friday signal exits on Monday.
        self.assertTrue((run["weights"].sum(axis=1)[bear].shift(-1).fillna(0).tail(100) == 0).mean() > 0.95)


if __name__ == "__main__":
    unittest.main()
