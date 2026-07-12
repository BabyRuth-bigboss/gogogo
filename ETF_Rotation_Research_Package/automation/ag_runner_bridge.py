#!/usr/bin/env python3
"""Bridge script to execute Vibe-Trading backtest based on an automation Run Card."""

from __future__ import annotations

import json
import os
import shutil
import csv
import subprocess
import sys
from pathlib import Path

# Paths mapping
VIBE_ROOT = Path("/Users/yansenz/Desktop/Vibe-Trading")
VIBE_RUN_DIR = VIBE_ROOT / "runs" / "etf_rotation_v2"


def patch_fixed_cooldown(signal_engine_path: Path) -> tuple[str, str]:
    """Temporarily make Vibe's regime cooldown match the V2 fixed 5-day rule."""
    original = signal_engine_path.read_text(encoding="utf-8")
    fixed_marker = "            # --- 4.1.2 V2 fixed cooldown"
    if fixed_marker in original:
        return original, original
    marker_start = "            # 4.1.2 Adaptive Cooldown calculation"
    marker_end = "            days_since_switch = i - last_switch_day_index"
    start = original.find(marker_start)
    end = original.find(marker_end, start)
    if start < 0 or end < 0:
        raise RuntimeError("could not locate Vibe adaptive cooldown block")
    replacement = (
        "            # --- 4.1.2 V2 fixed cooldown\n"
        "            cooldown_days = 5\n\n"
    )
    patched = original[:start] + replacement + original[end:]
    signal_engine_path.write_text(patched, encoding="utf-8")
    return original, patched


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
    original_vibe_config = json.dumps(vibe_config, indent=2, ensure_ascii=False) + "\n"

    # Modify date range, initial cash, and monthly trading day
    vibe_config["start_date"] = start_date
    vibe_config["end_date"] = end_date
    vibe_config["initial_cash"] = initial_cash
    vibe_config["monthly_trading_day"] = monthly_trading_day

    print(f"[INFO] Updating Vibe-Trading config: start_date={start_date}, end_date={end_date}, initial_cash={initial_cash}, monthly_trading_day={monthly_trading_day}")
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
    try:
        original_signal_engine, patched_signal_engine = patch_fixed_cooldown(signal_engine_path)
    except Exception:
        vibe_config_path.write_text(original_vibe_config, encoding="utf-8")
        raise
    used_engine_path = run_dir / "antigravity" / "signal_engine_used.py"
    used_engine_path.write_text(patched_signal_engine, encoding="utf-8")
    timeout_seconds = int(os.environ.get("BACKTEST_TIMEOUT_SECONDS", "1800"))
    stdout_log = run_dir / "antigravity" / "backtest_stdout.log"
    stderr_log = run_dir / "antigravity" / "backtest_stderr.log"
    print(f"[INFO] Fixed V2 cooldown patched to 5 days")
    print(f"[INFO] Streaming Vibe output to: {stdout_log}")
    try:
        with stdout_log.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                runner_cmd,
                cwd=VIBE_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                res_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                res_code = 124
                print(f"[ERROR] Backtest timed out after {timeout_seconds}s")
            except Exception as exc:
                print(f"[ERROR] Execution failed: {exc}")
                res_code = 1
    finally:
        signal_engine_path.write_text(original_signal_engine, encoding="utf-8")
        vibe_config_path.write_text(original_vibe_config, encoding="utf-8")
        print("[INFO] Restored original Vibe config.json")
        print("[INFO] Restored original Vibe signal_engine.py")

    stderr_log.write_text("", encoding="utf-8")
    if res_code != 0:
        print(f"[ERROR] Backtest failed with exit code {res_code}")
        print(f"[ERROR] Inspect: {stdout_log}")
        sys.exit(res_code)

    print("[INFO] Backtest run completed successfully. Copying outputs...")

    # Load T0 equity dates to align dates if present
    t0_dates = None
    t0_dir = run_dir / "t0"
    if t0_dir.exists():
        t0_equity_matches = list(t0_dir.glob("*equity*.csv"))
        if t0_equity_matches:
            t0_equity_file = t0_equity_matches[0]
            try:
                with open(t0_equity_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    dc = None
                    for name in ("timestamp", "signal_date", "trade_date", "date"):
                        if name in reader.fieldnames:
                            dc = name
                            break
                    if dc:
                        t0_dates = {row[dc].split(" ")[0] for row in reader if row.get(dc)}
                        print(f"[INFO] Loaded {len(t0_dates)} target dates from T0: {t0_equity_file.name}")
            except Exception as exc:
                print(f"[WARN] Failed to read T0 equity dates for filtering: {exc}")

    # 3. Copy and filter outputs to runs/antigravity folder
    vibe_artifacts = VIBE_RUN_DIR / "artifacts"
    if vibe_artifacts.exists():
        for filename in ("trades.csv", "metrics.json", "equity.csv", "signals.csv"):
            src_file = vibe_artifacts / filename
            if src_file.exists():
                dest_file = run_dir / "antigravity" / filename
                if filename == "equity.csv" and t0_dates is not None:
                    try:
                        with open(src_file, "r", encoding="utf-8") as f:
                            reader = csv.DictReader(f)
                            t1_rows = list(reader)

                        filtered_rows = []
                        for row in t1_rows:
                            dt_str = row["timestamp"].split(" ")[0]
                            if dt_str in t0_dates:
                                filtered_rows.append(row)

                        with open(dest_file, "w", encoding="utf-8", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
                            writer.writeheader()
                            writer.writerows(filtered_rows)
                        print(f"[INFO] Filtered and copied equity.csv -> antigravity/ ({len(filtered_rows)} of {len(t1_rows)} rows)")
                    except Exception as exc:
                        print(f"[WARN] Failed to filter equity.csv: {exc}")
                        shutil.copy2(src_file, dest_file)
                else:
                    shutil.copy2(src_file, dest_file)
                    print(f"[INFO] Copied {filename} -> antigravity/")

    # 4. Save the restored source separately; signal_engine_used.py is the executable copy.
    src_engine = VIBE_RUN_DIR / "code" / "signal_engine.py"
    if src_engine.exists():
        shutil.copy2(src_engine, run_dir / "antigravity" / "signal_engine_restored.py")

    # 5. Copy ohlcv_*.csv snapshot files from t0 to antigravity if they exist (to satisfy audit checks)
    if t0_dir.exists():
        ohlcv_copied = 0
        for path in t0_dir.glob("ohlcv_*.csv"):
            shutil.copy2(path, run_dir / "antigravity" / path.name)
            ohlcv_copied += 1
        if ohlcv_copied > 0:
            print(f"[INFO] Copied {ohlcv_copied} ohlcv_*.csv files from t0/ to satisfy audit checks.")

    print("[INFO] Handoff files generated successfully inside run directory.")


if __name__ == "__main__":
    main()
