# Online Prediction and Steam Volume Design

## Scope

This change has three deliverables:

1. Repair `/api/predict` so online responses never reuse offline val/test prediction CSV files.
2. Backfill Steam daily sales volume into the existing 2026 `price_history` rows without changing BUFF prices.
3. Add an unattended daily Steam volume collector with persistent progress, retry, and explicit data-quality metadata.

Model retraining and frontend volume visualization are out of scope. The existing LSTM artifacts remain deployed until they are evaluated on modern held-out data. The frontend will only regain volume charts after backend coverage and API contracts are complete.

## Data Semantics

`price_history.price` remains the cleaned BUFF market price. `price_history.daily_volume` becomes a Steam liquidity feature. Mixing these sources is intentional for the current serving design and must remain visible in metadata.

Add the following nullable columns to `price_history`:

- `volume_source`: `steam_pricehistory` for exact historical backfill or `steam_24h` for anonymous rolling snapshots.
- `volume_observed_at`: UTC timestamp when Steam returned the value.
- `volume_window_hours`: `24` for rolling snapshots; null for exact historical buckets when the source response already supplies the bucket.
- `volume_status`: `complete`, `estimated_window`, `missing`, or `stale`.

Legacy zeroes with no `volume_source` are unknown, not proven zero sales. Migration converts them to null. A confirmed zero from Steam remains `0` with a non-null source and `complete` or `estimated_window` status.

No BUFF `sell_num` value may enter `daily_volume`; it is a listing count rather than executed sales.

## Initial Backfill

The one-time backfill uses Steam `market/pricehistory` with `STEAM_COOKIE` from the environment. Secrets are never logged or stored in SQLite.

For every skin with existing `price_history` rows:

1. Request Steam history by exact `market_hash_name` and app ID 730.
2. Parse Steam timestamps in English and normalize them to UTC dates.
3. Sum all intraday volume points by date.
4. Update only matching `(skin_id, date)` rows.
5. Preserve BUFF `price`, `raw_price`, and outlier fields.
6. Commit per item so the operation is resumable and idempotent.

The script records coverage and failures. Dates not proven by the response remain null rather than being guessed. The backfill command supports item limits, resume, retry, and a dry-run audit.

## Daily Unattended Collection

After backfill, daily updates use anonymous Steam `priceoverview.volume`. This avoids routine Cookie renewal but produces a rolling 24-hour count rather than an exact UTC calendar-day count.

The collector creates durable jobs in a new `steam_volume_jobs` table:

- `skin_id`
- `target_date`
- `status`: `pending`, `running`, `retry`, `complete`, or `missing`
- `attempt_count`
- `next_attempt_at`
- `last_error`
- `observed_at`
- unique `(skin_id, target_date)`

At 00:30 UTC it creates one job per active skin for the previous UTC date. A single rate-limited worker processes jobs at a configurable interval, defaults to three seconds, and uses exponential backoff for 429 and transient failures. Progress is stored after every item, so process restarts continue from the database.

Startup reconciliation creates yesterday's jobs if the scheduled trigger was missed. Anonymous `priceoverview` cannot reconstruct a window missed by more than 30 hours; such jobs become `missing` and the corresponding `daily_volume` remains null. The collector never copies a prior value forward.

Normal operation requires an always-on deployment host but no daily human action. A full host outage can still create missing observations; exact repair then requires the optional authenticated backfill command.

## Online Prediction Serving

Offline canonical CSV files remain available only to model comparison and backtest endpoints. `/api/predict` uses database-derived features exclusively.

Serving rules:

1. Build the latest 60-observation window from `price_history` for the requested skin.
2. Require the window decision date to equal the latest database price date.
3. Reject insufficient, stale, non-finite, or incompatible input instead of falling back to offline CSV.
4. Run the deployed live LSTM route only. Tree/ARIMA CSV predictions are not online predictions and are omitted.
5. Reject outputs with non-positive prices or a seven-observation move beyond the initial 30% circuit breaker.
6. Do not generate consensus, entry range, or target price when no valid live prediction exists.

The response adds:

- `status`: `available` or `unavailable`
- `reason`: stable machine-readable reason code when unavailable
- `decisionDate`
- `dataThrough`
- `currentPrice` and `livePriceUsd`, both anchored to the latest database price
- `modelVersion`
- `priceSource`
- `volumeSource`
- `volumeCoverage`
- `generatedAt`

The response never exposes `actual_future_price`.

## Prediction Cache

Prediction cache validity depends on data and model identity, not only elapsed time. Add cache fields for `decision_date`, `input_price`, `model_version`, `data_through`, and `generated_at`.

Cache lookup requires:

- `expires_at > now`
- matching latest decision date
- matching model version
- matching input price within numeric tolerance

Any price or volume update for a skin invalidates that skin's cached predictions. Legacy cache rows without the new metadata are deleted during migration. Cached responses return their original generation time rather than the current request time.

## API and Frontend Behavior

Update OpenAPI to document availability, provenance, and nullable prediction fields. Existing frontend prediction code may continue rendering valid predictions. When `status=unavailable`, the response contains an empty prediction list and null decision aids, allowing the current empty/failure path to avoid drawing a fabricated forecast.

Volume charts, liquidity sorting, and volume labels are not restored in this change. A later frontend change must require acceptable backend coverage and display `Steam 24h volume` rather than presenting the metric as BUFF volume.

## Error Handling and Observability

- Never print Steam or BUFF Cookie values.
- Classify authentication, rate-limit, not-found, parsing, and network failures separately.
- Record per-item failure state and retry time in `steam_volume_jobs`.
- Expose aggregate collection health through the existing health response or an admin data-quality response: completed, pending, retrying, missing, and stale counts.
- Treat null as unavailable and zero as a confirmed numeric observation.

## Testing

Automated tests must cover:

- Steam timestamp parsing and intraday volume summation.
- Historical backfill preserving all price and outlier columns.
- Null-versus-confirmed-zero semantics.
- Idempotent backfill and daily upsert.
- Persistent job resume after simulated restart.
- 429 backoff and terminal missing state.
- Cache expiration using the real current time.
- Cache invalidation after price or volume updates.
- `/api/predict` never reading canonical test/val CSV files.
- Database decision-date anchoring and stale/unavailable responses.
- Prediction circuit breaker behavior.
- OpenAPI contract fields.

## Acceptance Criteria

1. Existing backend price history retains its BUFF prices and gains auditable Steam volume where available.
2. A collector restart continues incomplete work without duplicating rows.
3. Daily operation needs no Cookie and no manual trigger while the deployment host is running.
4. Missed data is visible as null/missing, never copied or fabricated.
5. `/api/predict` returns only fresh database-anchored LSTM inference or an explicit unavailable response.
6. Offline model metrics and canonical CSV files remain unchanged and confined to evaluation endpoints.
7. No frontend volume visualization is reintroduced until a separate coverage review approves it.
