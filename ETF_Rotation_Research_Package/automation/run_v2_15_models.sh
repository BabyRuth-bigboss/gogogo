#!/bin/zsh
set -euo pipefail

ROOT="/Users/yansenz/Documents/project/gogogo/ETF_Rotation_Research_Package"
SUITE="$ROOT/automation/tasks/v2_15_models.json"
export ANTIGRAVITY_CMD="python3 $ROOT/automation/t0_runner_bridge.py && python3 $ROOT/automation/ag_runner_bridge.py"
export CODEX_CMD="$ROOT/automation/codex_runner_bridge.sh"

cd "$ROOT"
python3 automation/dual_agent_workflow.py \
  --strategy docs/strategy_specs/v2_month_start5.md \
  --start-date 2021-07-12 \
  --end-date 2026-07-10 \
  --mode backtest \
  --monthly-trading-day 5 \
  --model-suite-file "$SUITE"

RUN_DIR=$(ls -td automation/runs/*/ | head -1)
python3 automation/v2_suite_report.py "$ROOT/${RUN_DIR%/}"
