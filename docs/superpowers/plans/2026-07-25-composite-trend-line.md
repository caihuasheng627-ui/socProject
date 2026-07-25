# Composite Trend Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a single weighted, visibly undulating 30-day trend line with a natural D7 handoff.

**Architecture:** Keep API quantiles unchanged. Derive a deterministic display-only path in `app.js`, then feed one ECharts series.

**Tech Stack:** Vue 3 CDN, ECharts, pytest source-contract tests, Node syntax checks.

---

### Task 1: Protect the contract

**Files:** `backend/tests/test_prediction_api_contract.py`

- [ ] Assert weights, deterministic wave, D7 anchor and absence of band series.
- [ ] Run the focused test and confirm it fails.

### Task 2: Implement the path

**Files:** `app.js`

- [ ] Add weighting, D7 alignment, four-point fade-in and dual-cycle wave.
- [ ] Replace the quantile/band series with one composite series.
- [ ] Run focused tests and JavaScript syntax checks.

### Task 3: Verify regressions

**Files:** `backend/tests/test_prediction_api_contract.py`, `backend/tests/test_prediction_service.py`

- [ ] Run contract tests, `node --check app.js`, and `git diff --check`.
