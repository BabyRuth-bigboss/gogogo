#!/usr/bin/env python3
"""Create and optionally execute a file-based Codex/Antigravity ETF workflow."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "automation" / "runs"


def render_prompt(card_path: Path, mode: str) -> str:
    return f"""使用 $dual-engine-etf-backtest 审计本次实验。\n\n运行卡片：{card_path}\n模式：{mode}\n\n要求：\n1. 读取 run_card.json、model_suite.json（如有）、t0/ 和 antigravity/；\n2. 检查 T0/T1 是否使用同一策略、时间、资产池和成本；批量模型结果按模型清单逐一核对；\n3. 运行：python3 skills/dual-engine-etf-backtest/scripts/compare_runs.py --a \"$RUN_DIR/t0\" --b \"$RUN_DIR/antigravity\" --out \"$RUN_DIR/codex\"；\n4. 对比收益、回撤、Sharpe、信号、成交和第一处分歧；\n5. 将结论写入 codex/dual_comparison.md；\n6. 如果行情快照不一致，明确标记为非公平比较；\n7. 明确区分已确认、强推断和无法确认，不能把 T0 15 模型排名误写成 T0/T1 引擎优劣结论。\n"""


def run_command(command: str, run_dir: Path) -> int:
    env = os.environ.copy()
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    env.update({"RUN_DIR": str(run_dir), "RUN_CARD": str(run_dir / "run_card.json"), "SNAPSHOT_DIR": str(run_dir / "snapshot"), "ANTIGRAVITY_DIR": str(run_dir / "antigravity"), "CODEX_DIR": str(run_dir / "codex"), "CODEX_PROMPT": str(run_dir / "codex_prompt.md")})
    if card.get("model_ids"):
        env["V2_MODEL_IDS"] = ",".join(card["model_ids"])
    if card.get("skip_stress"):
        env["V2_NO_STRESS"] = "1"
    return subprocess.run(command, shell=True, cwd=ROOT, env=env, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--mode", choices=("daily", "monthly", "backtest"), default="backtest")
    parser.add_argument("--monthly-trading-day", type=int, default=3)
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--model-suite-file", help="JSON file containing a V2 model suite")
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS / stamp
    for name in ("snapshot", "antigravity", "codex", "comparison"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    strategy_path = Path(args.strategy).resolve()
    suite = {}
    if args.model_suite_file:
        suite_path = Path(args.model_suite_file).resolve()
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        if not suite.get("models"):
            raise SystemExit("model suite must contain a non-empty models list")
    if not 1 <= args.monthly_trading_day <= 23:
        raise SystemExit("--monthly-trading-day must be between 1 and 23")
    card = {"run_id": stamp, "mode": args.mode, "strategy": str(strategy_path), "start_date": args.start_date, "end_date": args.end_date, "initial_cash": args.initial_cash, "monthly_trading_day": args.monthly_trading_day, "root": str(ROOT), "status": "prepared", "model_suite": suite.get("name"), "model_ids": suite.get("models", []), "skip_stress": bool(suite.get("skip_stress", False)), "rules": {"single_snapshot": True, "signal_uses_prior_close": True, "agent_outputs_are_separate": True}}
    (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "strategy_spec.md").write_text(strategy_path.read_text(encoding="utf-8"), encoding="utf-8")
    if args.model_suite_file:
        (run_dir / "model_suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "codex_prompt.md").write_text(render_prompt(run_dir / "run_card.json", args.mode), encoding="utf-8")
    (run_dir / "handoff.md").write_text("# Agent Handoff\n\n- status: prepared\n- next: Antigravity runs data and backtest, then Codex audits outputs.\n", encoding="utf-8")

    ag = os.environ.get("ANTIGRAVITY_CMD")
    if ag:
        card["status"] = "antigravity_running"
        (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        code = run_command(ag, run_dir)
        if code != 0:
            card["status"] = "antigravity_failed"
            (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(code)
    else:
        print("ANTIGRAVITY_CMD 未设置，已创建交接目录。")

    codex = os.environ.get("CODEX_CMD")
    if codex:
        card["status"] = "codex_running"
        (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        code = run_command(codex, run_dir)
        if code != 0:
            card["status"] = "codex_failed"
            (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(code)
    else:
        print("CODEX_CMD 未设置，请把 codex_prompt.md 交给 Codex。")

    card["status"] = "completed" if ag and codex else "handoff_ready"
    (run_dir / "run_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(run_dir)


if __name__ == "__main__":
    main()
