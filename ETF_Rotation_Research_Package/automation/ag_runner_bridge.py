#!/usr/bin/env python3
"""Bridge script to execute Vibe-Trading backtest based on an automation Run Card."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Paths mapping
VIBE_ROOT = Path("/Users/yansenz/Desktop/Vibe-Trading")
VIBE_RUN_DIR = VIBE_ROOT / "runs" / "etf_rotation_v2"


def patch_rebalance_day(signal_engine_path: Path, trading_day: int) -> str:
    """Temporarily change the monthly trading-day index and return original text."""
    original = signal_engine_path.read_text(encoding="utf-8")
    old = 'group["date"].iloc[2]'
    new = f'group["date"].iloc[{trading_day - 1}]'
    if original.count(old) != 1:
        raise RuntimeError(f"expected one monthly rebalance expression in {signal_engine_path}")
    signal_engine_path.write_text(original.replace(old, new), encoding="utf-8")
    print(f"[INFO] Monthly rebalance day patched: {trading_day}th trading day")
    return original


def main():
    run_card_env = os.environ.get("RUN_CARD")
    run_dir_env = os.environ.get("RUN_DIR")
    if not run_card_env or not run_dir_env:
        print("[ERROR] Environment variables RUN_CARD or RUN_DIR not set.")
        sys.exit(1)

    run_card_path = Path(run_card_env).resolve()
    run_dir = Path(run_dir_env).resolve()

    if not run_card_path.exists():
        print(f"[ERROR] Run card file not found at: {run_card_path}")
        sys.exit(1)

    print(f"[INFO] Reading Run Card from: {run_card_path}")
    with open(run_card_path, "r", encoding="utf-8") as f:
        card = json.load(f)

    start_date = card.get("start_date")
    end_date = card.get("end_date")
    initial_cash = card.get("initial_cash", 1000000.0)
    monthly_trading_day = int(card.get("monthly_trading_day", 3))
    if not 1 <= monthly_trading_day <= 23:
        print(f"[ERROR] Invalid monthly_trading_day: {monthly_trading_day}")
        sys.exit(1)

    # 1. Update Vibe-Trading config.json dynamically
    vibe_config_path = VIBE_RUN_DIR / "config.json"
    if not vibe_config_path.exists():
        print(f"[ERROR] Vibe config file not found at: {vibe_config_path}")
        sys.exit(1)

    with open(vibe_config_path, "r", encoding="utf-8") as f:
        vibe_config = json.load(f)
    original_vibe_config = json.dumps(vibe_config, indent=2, ensure_ascii=False)

    # Modify date range and initial cash
    vibe_config["start_date"] = start_date
    vibe_config["end_date"] = end_date
    vibe_config["initial_cash"] = initial_cash

    print(f"[INFO] Updating Vibe-Trading config: start_date={start_date}, end_date={end_date}, initial_cash={initial_cash}")
    with open(vibe_config_path, "w", encoding="utf-8") as f:
        json.dump(vibe_config, f, indent=2, ensure_ascii=False)

    # 2. Run Vibe-Trading backtester runner
    venv_python = VIBE_ROOT / ".venv" / "bin" / "python"
    runner_cmd = [
        str(venv_python),
        "-m",
        "backtest.runner",
        str(VIBE_RUN_DIR)
    ]
    print(f"[INFO] Executing Vibe-Trading Backtest: {' '.join(runner_cmd)}")
    
    signal_engine_path = VIBE_RUN_DIR / "code" / "signal_engine.py"
    original_signal_engine = patch_rebalance_day(signal_engine_path, monthly_trading_day)
    try:
        res = subprocess.run(runner_cmd, cwd=VIBE_ROOT, capture_output=True, text=True)
    finally:
        signal_engine_path.write_text(original_signal_engine, encoding="utf-8")
        vibe_config_path.write_text(original_vibe_config + "\n", encoding="utf-8")
        print("[INFO] Restored original Vibe config.json")
        print("[INFO] Restored original Vibe signal_engine.py")
    
    # Save terminal outputs
    (run_dir / "antigravity" / "backtest_stdout.log").write_text(res.stdout, encoding="utf-8")
    (run_dir / "antigravity" / "backtest_stderr.log").write_text(res.stderr, encoding="utf-8")

    if res.returncode != 0:
        print("[ERROR] Backtest run failed. Stderr output:")
        print(res.stderr)
        sys.exit(res.returncode)

    print("[INFO] Backtest run completed successfully. Copying outputs...")

    # 3. Copy outputs to runs/antigravity folder
    vibe_artifacts = VIBE_RUN_DIR / "artifacts"
    if vibe_artifacts.exists():
        for filename in ("trades.csv", "metrics.json", "equity.csv", "signals.csv"):
            src_file = vibe_artifacts / filename
            if src_file.exists():
                shutil.copy2(src_file, run_dir / "antigravity" / filename)
                print(f"[INFO] Copied {filename} -> antigravity/")

    # 4. Save a copy of signal_engine.py used for reference
    src_engine = VIBE_RUN_DIR / "code" / "signal_engine.py"
    if src_engine.exists():
        shutil.copy2(src_engine, run_dir / "antigravity" / "signal_engine.py")

    print("[INFO] Handoff files generated successfully inside run directory.")


if __name__ == "__main__":
    main()
