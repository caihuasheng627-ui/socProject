# Post-Delivery Maintenance Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the complete forecast-contract repair as an offline maintenance package that will never be pushed with the current course delivery.

**Architecture:** Copy only the repaired source, tests, documentation, trained artifacts, canonical predictions, and evaluation outputs into a directory outside `socProject/`. Add a human-readable manifest and restoration guide, calculate SHA-256 hashes, then create and verify a ZIP archive.

**Tech Stack:** PowerShell file packaging, SHA-256, ZIP, Markdown, Python/pytest verification.

---

### Task 1: Create package metadata

**Files:**
- Create: `CSVest_post_delivery_maintenance_2026-07-20/README.md`
- Create: `CSVest_post_delivery_maintenance_2026-07-20/INTEGRATION_GUIDE.md`
- Create: `CSVest_post_delivery_maintenance_2026-07-20/MANIFEST.md`

- [ ] State that the package is post-delivery research and must not be pushed into the current project without a new review.
- [ ] Record the base repository commit `ef6a0c1`, prediction contract, final metrics, known backend incompatibilities, and adoption order.
- [ ] List included and deliberately excluded files.

### Task 2: Copy the maintenance payload

**Files:**
- Copy repaired Python files to `source/notebooks/` and compatibility wrappers to `source/data/`.
- Copy tests to `tests/`.
- Copy relevant design and handoff documents to `docs/`.
- Copy model artifacts to `artifacts/models/`.
- Copy canonical val/test predictions to `artifacts/preds/`.
- Copy comparison and backtest JSON to `artifacts/evaluation/`.

- [ ] Preserve relative filenames without copying raw datasets, caches, logs, secrets, or Git metadata.
- [ ] Confirm no path contains `socProject` inside the package payload.

### Task 3: Verify and archive

**Files:**
- Create: `CSVest_post_delivery_maintenance_2026-07-20/SHA256SUMS.txt`
- Create: `CSVest_post_delivery_maintenance_2026-07-20.zip`

- [ ] Run the source test suite and record `22 passed` in the package README.
- [ ] Generate one SHA-256 line for every packaged file except `SHA256SUMS.txt`.
- [ ] Create the ZIP from the package directory.
- [ ] Extract the ZIP to a temporary verification directory and compare file counts and hashes.
- [ ] Confirm `socProject/` is still clean and no push occurred.
