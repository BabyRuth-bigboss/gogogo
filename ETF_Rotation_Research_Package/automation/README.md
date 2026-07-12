# 双 Agent ETF 回测自动化

这套自动化采用文件交接模式：Antigravity 负责获取数据和执行回测，Codex 负责审计信号、比较结果和生成结论。

## 快速开始

```bash
cd ETF_Rotation_Research_Package
export ANTIGRAVITY_CMD='/path/to/antigravity --run-card "$RUN_CARD"'
export CODEX_CMD='/path/to/codex --prompt-file "$CODEX_PROMPT"'

python3 automation/dual_agent_workflow.py \
  --strategy docs/strategy_specs/v2_dual_sleeve.md \
  --start-date 2021-07-12 \
  --end-date 2026-07-10 \
  --mode backtest
```

脚本会创建：

```text
automation/runs/YYYYMMDD_HHMMSS/
├── run_card.json
├── handoff.md
├── codex_prompt.md
├── snapshot/
├── antigravity/
├── codex/
└── comparison/
```

如果没有设置 Agent 命令，脚本仍会创建完整交接目录并退出，方便在 Antigravity 或 Codex 中手动执行。

## 日常盘后

```bash
python3 automation/dual_agent_workflow.py \
  --strategy docs/strategy_specs/v2_dual_sleeve.md \
  --start-date $(date +%F) \
  --end-date $(date +%F) \
  --mode daily
```

## 月度调仓

```bash
python3 automation/dual_agent_workflow.py \
  --strategy docs/strategy_specs/v2_dual_sleeve.md \
  --start-date 2026-08-03 \
  --end-date 2026-08-03 \
  --mode monthly
```

Agent 命令必须遵守：只读 `run_card.json`，把输出写入自己的目录，不覆盖快照，不自行重新下载另一份行情。

## 创建一个策略变更任务

例如 V2 改为每月第 5 个交易日：

```bash
./automation/run_v2_month_start5.sh
```

任务配置保存在 `automation/tasks/v2_month_start5.json`。桥接脚本只在本次运行期间临时修改 Vibe 的调仓索引，完成后恢复原始 `signal_engine.py`。
