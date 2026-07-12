#!/usr/bin/env python3
"""Run the packaged T0 engine for the current automation run card."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backtest_etf_regime_dual_sleeve_5y_v2.py"


def main() -> None:
    run_card = Path(os.environ["RUN_CARD"]).resolve()
    run_dir = Path(os.environ["RUN_DIR"]).resolve()
    card = json.loads(run_card.read_text(encoding="utf-8"))
    out_dir = run_dir / "t0"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("t0_month5", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.OUT_DIR = out_dir
    os.environ["V2_MONTHLY_TRADING_DAY"] = str(card.get("monthly_trading_day", 3))
    print(f"[INFO] Running T0 with monthly trading day {os.environ['V2_MONTHLY_TRADING_DAY']}", flush=True)
    module.main()


if __name__ == "__main__":
    main()
