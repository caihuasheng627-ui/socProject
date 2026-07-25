# Unconfirmed Price Shock Anchor Design

## Decision

When the newest non-ultra price is at least 35% away from both the immediately preceding observation and the median of the preceding 14 valid observations, and the new level is not confirmed by two earlier observations, the forecast uses the preceding 14-observation median as its anchor. The latest price remains unchanged in `currentPrice` and in the historical K-line.

The detector is deliberately disabled for the existing `$1000+` ultra tier. That tier already has a dedicated price-level rebase intended to preserve high-value knife predictions around their current market level.

## Data Flow

`price_history` -> `forecast_anchor_context` -> seven-day calibration and recent-trend statistics -> 30-day handoff -> API `dailyPrices`/`trend30d`.

The API returns `forecastAnchorPrice`, `marketChange`, and `UNCONFIRMED_PRICE_SHOCK` in calibration metadata. The frontend uses the forecast anchor only for forecast-path scaling; actual market price and K-line data remain authoritative.

## Safety

No database prices are rewritten. Stable series and confirmed level changes use the existing live-price path. A cache contract suffix invalidates paths produced before this detector existed. Existing ±30% bounds still apply after shock anchoring.

## Verification

Tests cover stable data, one-point positive and negative jumps, confirmation by consecutive observations, ultra-tier precedence, cache invalidation, 7/30-day continuity, and the two reported饰品. Full backend and ML test suites must pass before handoff.
