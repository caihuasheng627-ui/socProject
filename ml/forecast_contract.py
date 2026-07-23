"""Shared forecast and prediction-file contract.

Supports both single-step (legacy, horizon=7) and multi-step (horizon=7 daily)
prediction targets.
"""

from pathlib import Path

import numpy as np
import pandas as pd


HORIZON_STEPS = 7
UNKNOWN_ITEM = "__UNK__"
PREDICTION_COLUMNS = [
    "split",
    "date",
    "target_date",
    "market_hash_name",
    "current_price",
    "actual_future_price",
    "predicted_price",
    "horizon_steps",
]

# Multi-step prediction columns (7 daily predictions)
PREDICTION_COLUMNS_SEQ = [
    "split",
    "date",
    "target_date",
    "market_hash_name",
    "current_price",
    "actual_future_price",  # day-7 actual (kept for backward compat)
]
# Append per-day columns: actual_future_price_d1..d7, predicted_price_d1..d7
for _d in range(1, HORIZON_STEPS + 1):
    PREDICTION_COLUMNS_SEQ.append(f"actual_future_price_d{_d}")
for _d in range(1, HORIZON_STEPS + 1):
    PREDICTION_COLUMNS_SEQ.append(f"predicted_price_d{_d}")
PREDICTION_COLUMNS_SEQ.append("horizon_steps")


def add_grouped_targets(df: pd.DataFrame, horizon_steps: int = HORIZON_STEPS) -> pd.DataFrame:
    """Add the seventh-later-observation target within each item (legacy single-step)."""
    if horizon_steps <= 0:
        raise ValueError("horizon_steps must be positive")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    groups = out.groupby("market_hash_name", sort=False)
    out["Target"] = groups["log_price"].shift(-horizon_steps)
    out["TargetPrice"] = groups["price"].shift(-horizon_steps)
    out["TargetDate"] = groups["date"].shift(-horizon_steps)
    if "_split" in out.columns:
        out["_target_split"] = groups["_split"].shift(-horizon_steps)
    return out


def add_grouped_targets_multi(
    df: pd.DataFrame, horizon_steps: int = HORIZON_STEPS
) -> pd.DataFrame:
    """Add 1..horizon_steps daily targets within each item.

    Produces columns: Target_1..Target_N (log_price), TargetPrice_1..TargetPrice_N,
    TargetDate_1..TargetDate_N.  Also keeps legacy Target/TargetPrice for backward compat
    (pointing to day N).
    """
    if horizon_steps <= 0:
        raise ValueError("horizon_steps must be positive")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    groups = out.groupby("market_hash_name", sort=False)

    for i in range(1, horizon_steps + 1):
        out[f"Target_{i}"] = groups["log_price"].shift(-i)
        out[f"TargetPrice_{i}"] = groups["price"].shift(-i)
        out[f"TargetDate_{i}"] = groups["date"].shift(-i)

    # Legacy aliases pointing to the last day
    out["Target"] = out[f"Target_{horizon_steps}"]
    out["TargetPrice"] = out[f"TargetPrice_{horizon_steps}"]
    out["TargetDate"] = out[f"TargetDate_{horizon_steps}"]
    if "_split" in out.columns:
        out["_target_split"] = groups["_split"].shift(-horizon_steps)
        # Multi-step: also track the split of day 1 (closest) for filtering
        out["_target_split_d1"] = groups["_split"].shift(-1)

    return out


def build_sequence_windows(
    df: pd.DataFrame,
    feature_cols: list[str],
    lookback: int,
    sample_split: str | None = None,
):
    """Build decision-inclusive per-item windows and canonical metadata (legacy single-step)."""
    x_values, y_values, metadata = [], [], []

    for name, group in df.groupby("market_hash_name", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        features = group[feature_cols].to_numpy(dtype=np.float32)

        for decision_idx in range(lookback - 1, len(group)):
            row = group.iloc[decision_idx]
            if sample_split is not None:
                if row.get("_split") != sample_split or row.get("_target_split") != sample_split:
                    continue
            if pd.isna(row.get("Target")) or pd.isna(row.get("TargetPrice")):
                continue

            x_values.append(features[decision_idx - lookback + 1 : decision_idx + 1])
            y_values.append(float(row["Target"]))
            metadata.append({
                "split": row.get("_split", sample_split),
                "date": pd.Timestamp(row["date"]),
                "target_date": pd.Timestamp(row["TargetDate"]),
                "market_hash_name": name,
                "current_price": float(row["price"]),
                "actual_future_price": float(row["TargetPrice"]),
                "horizon_steps": HORIZON_STEPS,
            })

    x = np.asarray(x_values, dtype=np.float32)
    y = np.asarray(y_values, dtype=np.float32)
    meta = pd.DataFrame(metadata)
    return x, y, meta


def build_sequence_windows_multi(
    df: pd.DataFrame,
    feature_cols: list[str],
    lookback: int,
    horizon: int,
    sample_split: str | None = None,
):
    """Build decision-inclusive per-item windows with multi-step targets.

    Returns y of shape (n_samples, horizon) where y[:, i] is the log_price
    i+1 days ahead.  Only keeps rows where ALL horizon target days are
    non-NaN and (when sample_split is set) in the requested split.
    """
    x_values, y_values, metadata = [], [], []

    for name, group in df.groupby("market_hash_name", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        features = group[feature_cols].to_numpy(dtype=np.float32)

        for decision_idx in range(lookback - 1, len(group)):
            row = group.iloc[decision_idx]

            # Must be in the right split
            if sample_split is not None:
                if row.get("_split") != sample_split:
                    continue
                # All target days must be in the same split
                target_splits_ok = True
                for i in range(1, horizon + 1):
                    ts_col = f"_target_split_d{i}" if i == 1 else None
                    # Use the legacy _target_split for day N consistency;
                    # for simplicity we only check day 1 and day N split.
                    pass
                # Check the furthest target day's split
                if row.get("_target_split") != sample_split:
                    continue
                # Also check day 1 split if available
                if pd.notna(row.get("_target_split_d1")) and row.get("_target_split_d1") != sample_split:
                    continue

            # All target days must be non-NaN
            targets = []
            all_valid = True
            for i in range(1, horizon + 1):
                t_val = row.get(f"Target_{i}")
                tp_val = row.get(f"TargetPrice_{i}")
                if pd.isna(t_val) or pd.isna(tp_val):
                    all_valid = False
                    break
                targets.append(float(t_val))

            if not all_valid:
                continue

            x_values.append(features[decision_idx - lookback + 1 : decision_idx + 1])

            y_values.append(targets)

            # Build metadata row with per-day actual prices
            meta_row = {
                "split": row.get("_split", sample_split),
                "date": pd.Timestamp(row["date"]),
                "target_date": pd.Timestamp(row[f"TargetDate_{horizon}"]),
                "market_hash_name": name,
                "current_price": float(row["price"]),
                "actual_future_price": float(row[f"TargetPrice_{horizon}"]),
                "horizon_steps": horizon,
            }
            for i in range(1, horizon + 1):
                meta_row[f"actual_future_price_d{i}"] = float(row[f"TargetPrice_{i}"])
            metadata.append(meta_row)

    x = np.asarray(x_values, dtype=np.float32)
    y = np.asarray(y_values, dtype=np.float32)  # (n_samples, horizon)
    meta = pd.DataFrame(metadata)
    return x, y, meta


def load_feature_panel(data_dir: str | Path) -> pd.DataFrame:
    """Load all chronological splits and calculate features once."""
    from feature_engineering import build_features

    data_dir = Path(data_dir)
    frames = []
    for split in ("train", "val", "test"):
        frame = pd.read_csv(data_dir / f"{split}.csv", parse_dates=["date"])
        frame["_split"] = split
        frames.append(frame)
    panel = pd.concat(frames, ignore_index=True)
    return build_features(panel, drop_na_target=False)


def build_training_item_map(train_df: pd.DataFrame) -> dict[str, int]:
    """Build item IDs from training identities and one unknown bucket."""
    names = sorted(train_df["market_hash_name"].dropna().unique())
    item_map = {name: idx for idx, name in enumerate(names)}
    item_map[UNKNOWN_ITEM] = len(item_map)
    return item_map


def encode_item_ids(names: pd.Series, item_map: dict[str, int]) -> np.ndarray:
    """Map unseen item names to the explicit unknown ID."""
    unknown_id = item_map[UNKNOWN_ITEM]
    return names.map(lambda name: item_map.get(name, unknown_id)).to_numpy(dtype=np.int32)


def decode_log_price_predictions(
    scaled_predictions, y_scaler, minimum_price: float = 0.01
) -> np.ndarray:
    """Invert scaled log prices with a train-derived non-negative price floor."""
    if minimum_price <= 0:
        raise ValueError("minimum_price must be positive")
    scaled = np.asarray(scaled_predictions, dtype=float).reshape(-1, 1)
    log_prices = y_scaler.inverse_transform(scaled).ravel()
    return np.expm1(np.maximum(log_prices, np.log1p(minimum_price)))


def assign_price_groups(train_df: pd.DataFrame):
    """Derive price boundaries and fixed known-item groups from train only."""
    medians = train_df.groupby("market_hash_name")["price"].median()
    q1, q2 = (float(value) for value in medians.quantile([0.55, 0.87]))

    def price_group(price: float) -> str:
        if price <= q1:
            return "low"
        if price <= q2:
            return "mid"
        return "high"

    return (q1, q2), {name: price_group(float(price)) for name, price in medians.items()}


def route_price_group(
    item_name: str,
    current_price: float,
    known_groups: dict[str, str],
    boundaries: tuple[float, float],
) -> str:
    """Route known items by train group and unseen items by current price."""
    if item_name in known_groups:
        return known_groups[item_name]
    q1, q2 = boundaries
    if current_price <= q1:
        return "low"
    if current_price <= q2:
        return "mid"
    return "high"


def decode_log_price_predictions_multi(
    scaled_predictions, y_scaler, minimum_price: float = 0.01
) -> np.ndarray:
    """Invert scaled multi-step log prices.

    scaled_predictions: (n_samples, horizon) — scaled log_price per day
    y_scaler: StandardScaler fit on (n_samples, horizon)
    Returns: (n_samples, horizon) real USD prices
    """
    if minimum_price <= 0:
        raise ValueError("minimum_price must be positive")
    scaled = np.asarray(scaled_predictions, dtype=float)
    if scaled.ndim == 1:
        scaled = scaled.reshape(1, -1)
    log_prices = y_scaler.inverse_transform(scaled)
    return np.expm1(np.maximum(log_prices, np.log1p(minimum_price)))


def validate_prediction_frame(df: pd.DataFrame, source: str | Path = "prediction frame") -> pd.DataFrame:
    """Validate and normalize a canonical prediction DataFrame."""
    missing = [column for column in PREDICTION_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"{source}: missing prediction columns: {missing}")

    out = df[PREDICTION_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"])
    out["target_date"] = pd.to_datetime(out["target_date"])

    splits = set(out["split"].dropna().astype(str))
    if len(splits) != 1 or not splits.issubset({"val", "test"}):
        raise ValueError(f"{source}: prediction split must be exactly one of val/test, got {sorted(splits)}")

    horizons = set(pd.to_numeric(out["horizon_steps"], errors="coerce").dropna().astype(int))
    if horizons != {HORIZON_STEPS}:
        raise ValueError(f"{source}: horizon_steps must be {HORIZON_STEPS}, got {sorted(horizons)}")

    if out.duplicated(["market_hash_name", "date"]).any():
        raise ValueError(f"{source}: duplicate item/date prediction rows")

    numeric = ["current_price", "actual_future_price", "predicted_price"]
    values = out[numeric].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError(f"{source}: prices must be finite")
    if (values <= 0).any().any():
        raise ValueError(f"{source}: prices must be positive")
    out[numeric] = values
    return out.sort_values(["date", "market_hash_name"]).reset_index(drop=True)


def validate_prediction_frame_seq(
    df: pd.DataFrame, source: str | Path = "prediction frame"
) -> pd.DataFrame:
    """Validate and normalize a multi-step (7-day) prediction DataFrame."""
    required = ["split", "date", "target_date", "market_hash_name",
                "current_price", "actual_future_price", "horizon_steps"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{source}: missing required columns: {missing}")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["target_date"] = pd.to_datetime(out["target_date"])

    splits = set(out["split"].dropna().astype(str))
    if len(splits) != 1 or not splits.issubset({"val", "test"}):
        raise ValueError(f"{source}: prediction split must be exactly one of val/test, got {sorted(splits)}")

    horizons = set(pd.to_numeric(out["horizon_steps"], errors="coerce").dropna().astype(int))
    if horizons != {HORIZON_STEPS}:
        raise ValueError(f"{source}: horizon_steps must be {HORIZON_STEPS}")

    if out.duplicated(["market_hash_name", "date"]).any():
        raise ValueError(f"{source}: duplicate item/date prediction rows")

    # Validate per-day price columns
    for i in range(1, HORIZON_STEPS + 1):
        for prefix in ("actual_future_price", "predicted_price"):
            col = f"{prefix}_d{i}"
            if col not in out.columns:
                raise ValueError(f"{source}: missing column {col}")
            vals = pd.to_numeric(out[col], errors="coerce")
            if not np.isfinite(vals.to_numpy()).all():
                raise ValueError(f"{source}: {col} must be finite")
            if (vals <= 0).any():
                raise ValueError(f"{source}: {col} must be positive")
            out[col] = vals

    return out.sort_values(["date", "market_hash_name"]).reset_index(drop=True)
