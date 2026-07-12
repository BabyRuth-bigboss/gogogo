# Codex + Antigravity 自动化

## 分工

- Antigravity：盘后获取行情、保存快照、执行 T0/T1 回测、生成原始 CSV。
- Codex：读取运行卡片和原始 CSV，检查信号、成交、指标和差异，生成结论。
- Git：保存策略、配置、快照清单和报告；不把密钥写入仓库。

## 交接协议

自动化脚本为每次运行创建唯一目录。Agent 只能写入自己的目录：Antigravity 写 `antigravity/`，Codex 写 `codex/`，行情写 `snapshot/`。`run_card.json` 是状态源，`handoff.md` 是人工可读的交接记录。

## 安全边界

- 默认不自动下单，只生成研究和模拟盘结果。
- 两个 Agent 必须读取同一快照。
- 任一 Agent 失败，流程停止，不生成“完成”结论。
- 任何真实交易动作必须人工确认。
