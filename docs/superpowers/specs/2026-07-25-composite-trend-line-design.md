# Composite Trend Line Design

## Goal

Replace the P10/P50/P90 band with one expressive composite trend line that connects naturally to the seven-day exact forecast.

## Display Algorithm

- Base value: `0.15 * P10 + 0.70 * P50 + 0.15 * P90`.
- The trend starts at the exact D7 endpoint, then displays model days D8-D30.
- The D7 difference between models decays over D8-D11.
- A deterministic dual-cycle wave fades in over four points, with about 1.8% maximum combined amplitude.
- No random values are used, so rerenders remain stable.

## Presentation

Only one green composite trend series is rendered. Quantile lines and probability shading are removed. The API continues returning all three quantiles for evaluation.

## Validation

Source-contract tests protect weights, handoff, fade-in and single-series rendering. JavaScript syntax and existing API tests must pass.
