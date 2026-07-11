from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import factor_report, ic_summary
from .backtest import CostModel, performance, run_rank_backtest
from .data import fetch_akshare, load_csv, save_bars
from .factors import add_forward_return, compute_factors


def main() -> None:
    parser = argparse.ArgumentParser(prog="quant-factor")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--symbols", nargs="+", required=True)
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True)
    fetch.add_argument("--asset", choices=["etf", "stock"], default="etf")
    fetch.add_argument("--adjust", choices=["", "qfq", "hfq"], default="qfq")
    fetch.add_argument("--output", required=True)
    run = sub.add_parser("research")
    run.add_argument("--input", required=True)
    run.add_argument("--factor", default="momentum_20")
    run.add_argument("--horizon", type=int, default=5)
    run.add_argument("--top-n", type=int, default=3)
    run.add_argument("--frequency", choices=["weekly", "monthly"], default="weekly")
    run.add_argument("--asset", choices=["etf", "stock"], default="etf")
    run.add_argument("--output-dir", default="output/factor_lab")
    args = parser.parse_args()
    if args.command == "fetch":
        save_bars(fetch_akshare(args.symbols, args.start, args.end, args.asset, args.adjust), args.output)
        return
    bars = load_csv(args.input)
    factors = compute_factors(bars)
    enriched = add_forward_return(factors, bars, args.horizon)
    target = f"forward_return_{args.horizon}"
    ic, quantiles = factor_report(enriched, args.factor, target)
    stamp = 0.0 if args.asset == "etf" else 0.0005
    curve, weights = run_rank_backtest(bars, factors, args.factor, args.top_n, args.frequency,
                                       CostModel(stamp_duty_sell=stamp))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ic.to_csv(out / "daily_ic.csv")
    quantiles.to_csv(out / "quantile_returns.csv")
    curve.to_csv(out / "equity_curve.csv")
    weights.to_csv(out / "weights.csv")
    summary = {"factor": args.factor, "ic": ic_summary(ic), "portfolio": performance(curve)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

