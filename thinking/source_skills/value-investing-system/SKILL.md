---
name: value-investing-system
description: Value investing analysis and review system based on the user's distilled investment documents. Use when the user asks Codex to analyze stocks, funds, indices, sectors, portfolios, market cycles, valuation, buy/sell decisions, monthly investment reviews, or to summarize/distill value-investing materials into actionable checklists, memos, scoring tables, and operating rules.
---

# Value Investing System

## Core Use

Use this skill to turn investment ideas or materials into a structured value-investing workflow:

```text
good business + good price + good cycle position + good behavior discipline = sustainable compounding
```

Prefer Chinese output when the user writes in Chinese. Keep the tone practical, clear, and decision-oriented.

## Safety And Evidence

- Treat investment work as decision support, not personalized financial advice.
- If current prices, valuations, rates, earnings, news, laws, or market data are needed, verify with current sources or available finance/data tools before making claims.
- Distinguish facts, user-provided data, assumptions, and inferences.
- Avoid giving a simple "buy/sell" command unless the user explicitly asks for an opinion; even then, present it as a conditional thesis with risks and invalidation criteria.
- When source materials are supplied, preserve traceability: cite filenames, tables, or source links where practical.

## Workflow Selector

1. **Investment idea / stock / sector analysis**: read `references/framework.md`, then produce a thesis memo using quality, valuation, cycle, expectation gap, risk, and action plan.
2. **Portfolio or monthly review**: read `references/templates.md`, then use the monthly review and position review templates.
3. **Market cycle / macro water-level review**: read `references/framework.md`, especially the market season and macro dashboard sections.
4. **Buy/sell decision support**: read both `references/framework.md` and `references/templates.md`, then produce a buy or sell memo with explicit conditions.
5. **Distilling investment documents**: use the "three-layer extraction" pattern: philosophy, method, reusable rules. If the document is long, build source notes first.
6. **Creating reusable checklists or scorecards**: read `references/templates.md` and adapt the 100-point scoring table.

## Required Analysis Order

For any investment target, use this order unless the user requests otherwise:

1. Personal or portfolio context if provided: liquidity, time horizon, risk tolerance, existing exposure.
2. Market season: winter, spring, summer, or autumn.
3. Industry position: lifecycle, supply/demand, policy, competition.
4. Company or asset quality: business model, moat, ROE, cash flow, balance sheet, management.
5. Valuation: payback period first, then the appropriate valuation method for the asset type.
6. Expectation gap: what the market may be missing and how it could be verified.
7. Position sizing and execution: left-side, right-side, index DCA, core holding, or defensive allocation.
8. Invalidation and exit rules.

## Output Formats

Choose a compact format based on the user request:

- **Quick view**: conclusion, key reasons, main risks, next checks.
- **Full memo**: thesis, business quality, valuation, cycle, expected return drivers, risks, action plan.
- **Scorecard**: 100-point table plus one-sentence judgment.
- **Checklist**: buy-before checklist, macro dashboard, sell rules, monthly review.
- **Learning summary**: core philosophy, method framework, reusable principles.

## Key Operating Principles

- Do not confuse good company with good investment; price matters.
- Use ROE as a quality clue, not a standalone answer.
- Prefer cash flow over accounting profit when judging durability.
- Use macro to judge water level, not exact index points.
- Treat low valuation as opportunity only after checking whether the asset is a value trap.
- Separate left-side investing from right-side trading; right-side positions need explicit stop rules.
- Treat holding through volatility as valid only when the original business thesis remains intact.
- Protect against leverage, liquidity pressure, and emotion-driven forced selling.

