#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from quant_factor_lab.hstech import (HkCostModel, drawdown_episodes, fetch_yahoo_hk, focus_attribution,
                                     load_hk_bars, metrics, parameter_grid,
                                     observation_report,
                                     run_hstech_strategy, run_tactical_strategy,
                                     save_hk_bars, split_metrics, tactical_grid, yearly_returns)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="config/hstech_constituents_20260608.csv")
    parser.add_argument("--data", default="data/hstech_daily.csv")
    parser.add_argument("--output", default="output/hstech_weekly")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2026-07-11")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--split-date", default="2024-01-01")
    args = parser.parse_args()
    universe = pd.read_csv(args.universe, dtype={"symbol": str})
    data_path = Path(args.data)
    if args.refresh or not data_path.exists():
        bars = fetch_yahoo_hk(universe["symbol"], args.start, args.end)
        save_hk_bars(bars, data_path)
    bars = load_hk_bars(data_path)
    eligible = universe.loc[universe["eligible"].eq(1), "symbol"].tolist()
    focus = universe.loc[universe["focus"].eq(1), "symbol"].tolist()
    rows = []
    runs = {}
    experiments = [("slow_regime", c, run_hstech_strategy) for c in parameter_grid()]
    experiments += [("tactical", c, run_tactical_strategy) for c in tactical_grid()]
    for i, (family, config, runner) in enumerate(experiments):
        run = runner(bars, eligible, config)
        runs[i] = run
        split = split_metrics(run["daily"], args.split_date)
        rows.append({"run_id": i, "family": family, **run["config"],
                     **{f"train_{k}": v for k, v in split["train"].items()},
                     **{f"test_{k}": v for k, v in split["test"].items()},
                     **{f"full_{k}": v for k, v in split["full"].items()}})
    grid = pd.DataFrame(rows)
    # Selection is based on training robustness only; test metrics are never used here.
    eligible_grid = grid[grid["train_trade_days"] >= 8].copy()
    eligible_grid["selection_score"] = (eligible_grid["train_sharpe"].clip(-2, 3)
                                         + 2 * eligible_grid["train_annualized_return"]
                                         + eligible_grid["train_max_drawdown"])
    grid = grid.merge(eligible_grid[["run_id", "selection_score"]], on="run_id", how="left")
    best_id = int(eligible_grid.sort_values("selection_score", ascending=False).iloc[0]["run_id"])
    best = runs[best_id]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    grid.sort_values("selection_score", ascending=False).to_csv(out / "grid_results.csv", index=False)
    best["daily"].to_csv(out / "best_daily.csv")
    best["weights"].to_csv(out / "best_weights.csv")
    best["trades"].to_csv(out / "best_trades.csv", index=False)
    yearly_returns(best["daily"]).to_csv(out / "yearly_returns.csv")
    focus_attribution(best, focus).to_csv(out / "focus_attribution.csv")
    observation_report(bars, focus).to_csv(out / "focus_observation.csv")
    drawdown_episodes(best["daily"]).to_csv(out / "drawdown_episodes.csv", index=False)
    benchmark_daily = pd.DataFrame({"net_return": best["daily"]["benchmark_return"],
                                    "turnover": 0.0, "cost": 0.0,
                                    "regime": "invested"}, index=best["daily"].index)
    summary = {"best_run_id": best_id, "selection_basis": "train_only",
               "split_date": args.split_date, "config": best["config"],
               "costs": best["costs"], "metrics": split_metrics(best["daily"], args.split_date),
               "benchmark_full": metrics(benchmark_daily)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
