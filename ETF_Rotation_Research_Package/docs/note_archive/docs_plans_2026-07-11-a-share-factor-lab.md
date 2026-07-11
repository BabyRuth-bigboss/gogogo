---
tags:
  - design
  - etf
  - quant/backtest
  - quant/factor
---

# A Share Factor Lab Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Build a safe, reproducible A-share/ETF daily factor research and portfolio backtest MVP.

**Architecture:** A pandas-based modular monolith separates data normalization, factor computation, factor diagnostics, portfolio simulation, and CLI orchestration. Live execution is intentionally excluded and represented only as a future adapter boundary.

**Tech Stack:** Python 3.10+, pandas, numpy, optional AKShare, unittest.

---

### Task 1: Package and data contract
- Create package metadata, example config, and normalized OHLCV loader.
- Test: malformed and valid synthetic frames.

### Task 2: Factor and diagnostics layer
- Add lag-safe price/volume factors, forward returns, daily IC, and quantile returns.
- Test: deterministic monotonic synthetic series.

### Task 3: Portfolio backtest
- Add scheduled cross-sectional ranking, one-period execution lag, turnover costs, and metrics.
- Test: cost and lag invariants.

### Task 4: CLI and documentation
- Add fetch/research commands, CSV outputs, and explicit limitations.
- Test: run the full pipeline on generated local data.

