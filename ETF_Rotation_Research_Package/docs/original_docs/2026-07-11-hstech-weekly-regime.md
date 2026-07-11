# HSTECH Weekly Regime Backtest Implementation Plan

**Goal:** Backtest low-turnover weekly rotation across current Hang Seng TECH constituents with cash risk-off regimes.

**Architecture:** Extend the existing pandas research package with a Yahoo-compatible Hong Kong data adapter, point-in-time-safe weekly signals, regime classification, parameter-grid experiments, and attribution reports. Keep the generic factor engine unchanged.

**Tech Stack:** Python, pandas, numpy, yfinance (optional data dependency), unittest.

---

### Step 1: Define universe and data adapter
- Output: official June 2026 constituent metadata and adjusted daily cache.
- Test: symbols, benchmark, duplicates and missing histories are reported.

### Step 2: Implement weekly regime rotation
- Output: market-state series, scores, lagged weights and HK Stock Connect costs.
- Test: no signal trades on the same bar; bear state is cash; fees reduce equity.

### Step 3: Cross-test a bounded strategy grid
- Output: train/test metrics for momentum windows, trend gates, breadth gates and Top-N.
- Test: ranking uses train only and reports out-of-sample separately.

### Step 4: Produce diagnostics
- Output: yearly returns, regime performance, focus-stock attribution, trades and drawdown episodes.
- Test: accounting identities reconcile portfolio return and contributions.

