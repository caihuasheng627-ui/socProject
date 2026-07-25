"""Train the leakage-free Hybrid V2 convex fusion adapter."""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from hybrid_v2_contract import CONTRACT_VERSION, validate_hybrid_v2_adapter
from hybrid_v2_transform import (
    ULTRA_PRICE_THRESHOLD,
    rebase_price_matrix,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR.parent / "backend" / "data" / "skinvision.db"
DEFAULT_MODEL_DIR = BASE_DIR / "models"
DEFAULT_ARTIFACT_PATH = DEFAULT_MODEL_DIR / "hybrid_v2_adapter.json"
DEFAULT_REPORT_PATH = BASE_DIR / "outputs" / "hybrid_v2_results.json"
REQUIRED_COLUMNS = {
    "decision_date",
    "target_date",
    "price_tier",
    "horizon",
    "c_return",
    "d_return",
    "recent_return",
    "target_return",
}


def _price_tier(current_price: float, boundaries: tuple[float, float]) -> str:
    if current_price <= boundaries[0]:
        return "low"
    if current_price <= boundaries[1]:
        return "mid"
    return "high"


def _recent_daily_return(prices: np.ndarray) -> float:
    values = np.asarray(prices, dtype=float)
    returns = np.diff(np.log(np.maximum(values[-30:], 0.01)))
    if len(returns) == 0:
        return 0.0
    recent = returns[-14:]
    median = float(np.median(recent))
    mad = max(0.005, float(np.median(np.abs(recent - median))) * 1.4826)
    clipped = np.clip(recent, median - 3.0 * mad, median + 3.0 * mad)
    weights = np.arange(1, len(clipped) + 1, dtype=float)
    return float(np.clip(np.average(clipped, weights=weights), -0.04, 0.04))


def build_recent_windows(
    panel: pd.DataFrame,
    *,
    feature_cols: tuple[str, ...] | list[str],
    boundaries: tuple[float, float],
    known_groups: dict[str, str] | None = None,
    lookback: int = 60,
    horizon: int = 7,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Build grouped decision windows whose features end before every target."""
    if lookback <= 0 or horizon <= 0:
        raise ValueError("lookback and horizon must be positive")
    windows = []
    targets = []
    metadata = []
    ordered = panel.sort_values(["market_hash_name", "date"])
    for name, group in ordered.groupby("market_hash_name", sort=False):
        group = group.reset_index(drop=True)
        if len(group) < lookback + horizon:
            continue
        features = group[list(feature_cols)].to_numpy(dtype=np.float32)
        prices = group["price"].to_numpy(dtype=float)
        dates = pd.to_datetime(group["date"]).to_numpy()
        for decision in range(lookback - 1, len(group) - horizon):
            current = float(prices[decision])
            model_group = (known_groups or {}).get(
                str(name), _price_tier(current, boundaries)
            )
            windows.append(features[decision - lookback + 1:decision + 1])
            targets.append(prices[decision + 1:decision + horizon + 1])
            metadata.append(
                {
                    "market_hash_name": str(name),
                    "decision_date": pd.Timestamp(dates[decision]),
                    "target_dates": [
                        pd.Timestamp(value)
                        for value in dates[decision + 1:decision + horizon + 1]
                    ],
                    "current_price": current,
                    "model_group": model_group,
                    "price_tier": (
                        "ultra" if current >= ULTRA_PRICE_THRESHOLD else model_group
                    ),
                    "recent_daily_return": _recent_daily_return(
                        prices[max(0, decision - 59):decision + 1]
                    ),
                    "recent_prices": prices[
                        max(0, decision - 59):decision + 1
                    ].astype(float).tolist(),
                }
            )
    if not windows:
        raise ValueError("no recent Hybrid V2 windows were generated")
    return (
        np.asarray(windows, dtype=np.float32),
        np.asarray(targets, dtype=np.float64),
        pd.DataFrame(metadata),
    )


def assemble_adapter_samples(
    metadata: pd.DataFrame,
    *,
    c_prices: np.ndarray,
    d_prices: np.ndarray,
    target_prices: np.ndarray,
) -> pd.DataFrame:
    """Convert base-model price paths into current-anchored training returns."""
    c_values = np.asarray(c_prices, dtype=float)
    d_values = np.asarray(d_prices, dtype=float)
    targets = np.asarray(target_prices, dtype=float)
    if c_values.shape != d_values.shape or c_values.shape != targets.shape:
        raise ValueError("C, D, and target arrays must have identical shapes")
    if c_values.ndim != 2 or len(metadata) != c_values.shape[0]:
        raise ValueError("prediction arrays must align one-to-one with metadata")
    if not np.isfinite(c_values).all() or not np.isfinite(d_values).all():
        raise ValueError("base predictions must be finite")

    anchors = metadata["current_price"].to_numpy(dtype=float)
    c_values = rebase_price_matrix(anchors, c_values)
    d_values = rebase_price_matrix(anchors, d_values)

    rows = []
    for row_index, row in enumerate(metadata.itertuples(index=False)):
        anchor = float(row.current_price)
        for horizon in range(1, c_values.shape[1] + 1):
            column = horizon - 1
            rows.append(
                {
                    "decision_date": pd.Timestamp(row.decision_date),
                    "target_date": pd.Timestamp(row.target_dates[column]),
                    "price_tier": str(row.price_tier),
                    "horizon": horizon,
                    "c_return": math.log(max(c_values[row_index, column], 0.01) / anchor),
                    "d_return": math.log(max(d_values[row_index, column], 0.01) / anchor),
                    "recent_return": float(row.recent_daily_return) * horizon,
                    "target_return": math.log(max(targets[row_index, column], 0.01) / anchor),
                }
            )
    return pd.DataFrame(rows)


def chronological_split(
    samples: pd.DataFrame,
    *,
    train_fraction: float = 0.70,
    val_fraction: float = 0.15,
) -> dict[str, pd.DataFrame]:
    """Split all items by global decision date without temporal overlap."""
    missing = REQUIRED_COLUMNS.difference(samples.columns)
    if missing:
        raise ValueError(f"hybrid samples missing columns: {sorted(missing)}")
    if not 0 < train_fraction < 1 or not 0 < val_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + val_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test split")

    frame = samples.copy()
    frame["decision_date"] = pd.to_datetime(frame["decision_date"])
    frame["target_date"] = pd.to_datetime(frame["target_date"])
    dates = np.asarray(sorted(frame["decision_date"].dropna().unique()))
    if len(dates) < 3:
        raise ValueError("at least three distinct decision dates are required")
    train_count = max(1, min(len(dates) - 2, int(len(dates) * train_fraction)))
    val_count = max(1, min(len(dates) - train_count - 1, int(len(dates) * val_fraction)))
    train_dates = set(dates[:train_count])
    val_dates = set(dates[train_count:train_count + val_count])
    test_dates = set(dates[train_count + val_count:])
    val_start = pd.Timestamp(min(val_dates))
    test_start = pd.Timestamp(min(test_dates))
    return {
        "train": frame[
            frame["decision_date"].isin(train_dates) & (frame["target_date"] < val_start)
        ].reset_index(drop=True),
        "val": frame[
            frame["decision_date"].isin(val_dates) & (frame["target_date"] < test_start)
        ].reset_index(drop=True),
        "test": frame[frame["decision_date"].isin(test_dates)].reset_index(drop=True),
    }


def _weight_grid(step: float):
    if not 0 < step <= 1:
        raise ValueError("grid_step must be in (0, 1]")
    divisions = int(round(1.0 / step))
    if not math.isclose(divisions * step, 1.0, abs_tol=1e-9):
        raise ValueError("grid_step must divide one exactly")
    for c_index in range(divisions + 1):
        for d_index in range(divisions - c_index + 1):
            c_weight = c_index / divisions
            d_weight = d_index / divisions
            recent_weight = 1.0 - c_weight - d_weight
            yield c_weight, d_weight, recent_weight


def _fit_one(frame: pd.DataFrame, grid_step: float) -> dict[str, float]:
    if frame.empty:
        return {"c": 0.4, "d": 0.5, "recent": 0.1, "bias": 0.0}
    c_values = frame["c_return"].to_numpy(dtype=float)
    d_values = frame["d_return"].to_numpy(dtype=float)
    recent_values = frame["recent_return"].to_numpy(dtype=float)
    targets = frame["target_return"].to_numpy(dtype=float)
    best = None
    for c_weight, d_weight, recent_weight in _weight_grid(grid_step):
        raw = c_weight * c_values + d_weight * d_values + recent_weight * recent_values
        bias = float(np.clip(np.median(targets - raw), -0.15, 0.15))
        errors = np.abs(targets - (raw + bias))
        score = (
            float(np.mean(errors)),
            float(np.percentile(errors, 95)),
            abs(bias),
            d_weight,
            recent_weight,
        )
        if best is None or score < best[0]:
            best = (score, c_weight, d_weight, recent_weight, bias)
    assert best is not None
    return {
        "c": float(best[1]),
        "d": float(best[2]),
        "recent": float(best[3]),
        "bias": float(best[4]),
    }


def _predict_return(row: Any, weights: dict[str, dict[str, dict[str, float]]]) -> float:
    tier_weights = weights.get(str(row.price_tier), weights["global"])
    chosen = tier_weights[str(int(row.horizon))]
    return float(
        chosen["c"] * row.c_return
        + chosen["d"] * row.d_return
        + chosen["recent"] * row.recent_return
        + chosen["bias"]
    )


def _return_metrics(
    frame: pd.DataFrame, predicted: np.ndarray
) -> dict[str, float | int]:
    truth = frame["target_return"].to_numpy(dtype=float)
    error = truth - predicted
    absolute = np.abs(error)
    return {
        "rows": int(len(frame)),
        "mae": float(np.mean(absolute)),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "p95AbsoluteLogError": float(np.percentile(absolute, 95)),
    }


def _metrics(frame: pd.DataFrame, weights: dict[str, Any]) -> dict[str, float | int]:
    if frame.empty:
        raise ValueError("adapter evaluation split is empty")
    predicted = np.asarray(
        [_predict_return(row, weights) for row in frame.itertuples(index=False)],
        dtype=float,
    )
    return _return_metrics(frame, predicted)


def fit_adapter(
    samples: pd.DataFrame,
    *,
    grid_step: float = 0.05,
    min_tier_rows: int = 210,
) -> dict[str, Any]:
    """Fit convex weights on train dates and evaluate untouched val/test dates."""
    splits = chronological_split(samples)
    train = splits["train"]
    weights: dict[str, dict[str, dict[str, float]]] = {"global": {}}
    for horizon in range(1, 8):
        weights["global"][str(horizon)] = _fit_one(
            train[train["horizon"] == horizon], grid_step
        )

    for tier in sorted(set(train["price_tier"].astype(str)) - {"global"}):
        tier_frame = train[train["price_tier"].astype(str) == tier]
        if len(tier_frame) < min_tier_rows:
            continue
        weights[tier] = {}
        for horizon in range(1, 8):
            horizon_frame = tier_frame[tier_frame["horizon"] == horizon]
            if len(horizon_frame) < max(10, min_tier_rows // 14):
                weights.pop(tier, None)
                break
            weights[tier][str(horizon)] = _fit_one(horizon_frame, grid_step)

    metrics = {name: _metrics(frame, weights) for name, frame in splits.items()}
    baselines = {
        "LSTM-C": {
            name: _return_metrics(frame, frame["c_return"].to_numpy(dtype=float))
            for name, frame in splits.items()
        },
        "LSTM-D": {
            name: _return_metrics(frame, frame["d_return"].to_numpy(dtype=float))
            for name, frame in splits.items()
        },
    }
    best_val_mae = min(
        baselines["LSTM-C"]["val"]["mae"],
        baselines["LSTM-D"]["val"]["mae"],
    )
    date_ranges = {
        name: {
            "start": frame["decision_date"].min().strftime("%Y-%m-%d"),
            "end": frame["decision_date"].max().strftime("%Y-%m-%d"),
        }
        for name, frame in splits.items()
    }
    return {
        "contractVersion": CONTRACT_VERSION,
        "featureContractVersion": "volume-free-v1",
        "selectionSplit": "train",
        "horizonSteps": 7,
        "fallbackTier": "global",
        "weights": weights,
        "metrics": metrics,
        "baselines": baselines,
        "accepted": bool(metrics["val"]["mae"] <= best_val_mae * 1.01),
        "dateRanges": date_ranges,
        "rows": int(len(samples)),
    }


def save_adapter_atomic(
    adapter: dict[str, Any], destination: Path, *, require_accepted: bool = True
) -> None:
    """Validate and atomically publish a reloadable JSON adapter."""
    validate_hybrid_v2_adapter(adapter, require_accepted=require_accepted)
    if set(adapter.get("metrics", {})) != {"train", "val", "test"}:
        raise ValueError("Hybrid V2 artifact requires train/val/test metrics")
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".json", dir=destination.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(adapter, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_recent_cs2_panel(db_path: Path, *, days: int = 180) -> pd.DataFrame:
    """Load a deduplicated rolling CS2 price panel from SQLite."""
    if days < 67:
        raise ValueError("Hybrid V2 requires at least 67 calendar days")
    with sqlite3.connect(db_path) as connection:
        maximum = connection.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
        if not maximum:
            raise ValueError("price_history is empty")
        query = """
            SELECT s.market_hash_name, s.weapon_type, s.rarity, s.wear,
                   s.is_stattrak, ph.date, AVG(ph.price) AS price
            FROM price_history ph
            JOIN skins s ON s.id = ph.skin_id
            WHERE ph.date >= date(?, ?)
            GROUP BY s.market_hash_name, ph.date
            ORDER BY s.market_hash_name, ph.date
        """
        frame = pd.read_sql_query(
            query, connection, params=(maximum, f"-{days - 1} days"), parse_dates=["date"]
        )
    if frame.empty:
        raise ValueError("rolling CS2 query returned no prices")
    frame["daily_volume"] = 0.0
    frame["is_floor_price"] = 0
    frame["days_to_next_major"] = 0
    frame["days_since_last_major"] = 0
    frame["is_major_active"] = 0
    frame["days_since_cs2_announce"] = 0
    frame["steam_ccu"] = 0.0
    frame["weapon_type"] = frame["weapon_type"].fillna("")
    frame["rarity"] = frame["rarity"].fillna("")
    frame["wear"] = frame["wear"].fillna("")
    frame["is_stattrak"] = frame["is_stattrak"].fillna(0).astype(int)
    from feature_engineering import build_features

    return build_features(frame, drop_na_target=False)


def _scaled_windows(windows: np.ndarray, scaler: Any) -> np.ndarray:
    samples, steps, features = windows.shape
    scaled = scaler.transform(windows.reshape(-1, features))
    return scaled.reshape(samples, steps, features)


def _decode_prices(values: np.ndarray, scaler: Any) -> np.ndarray:
    restored = scaler.inverse_transform(np.asarray(values, dtype=float))
    prices = np.expm1(restored)
    if not np.isfinite(prices).all():
        raise ValueError("base model produced non-finite decoded prices")
    return np.maximum(prices, 0.01)


def load_lstm_d_routing(model_dir: Path) -> tuple[tuple[float, float], dict[str, str]]:
    with (model_dir / "lstm_d_group_map.pkl").open("rb") as handle:
        payload = pickle.load(handle)
    boundaries = tuple(float(value) for value in payload["boundaries"])
    if len(boundaries) != 2:
        raise ValueError("LSTM-D boundaries must contain two values")
    return boundaries, dict(payload.get("item_group", {}))


def predict_base_paths(
    windows: np.ndarray,
    metadata: pd.DataFrame,
    *,
    model_dir: Path = DEFAULT_MODEL_DIR,
    batch_size: int = 512,
) -> tuple[np.ndarray, np.ndarray]:
    """Batch frozen LSTM-C/D predictions for recent CS2 decision windows."""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    from tensorflow import keras

    with (model_dir / "lstm_c_scaler.pkl").open("rb") as handle:
        c_scalers = pickle.load(handle)
    with (model_dir / "lstm_c_item_map.pkl").open("rb") as handle:
        item_map = pickle.load(handle)
    unknown_id = item_map.get("__UNK__")
    if unknown_id is None:
        raise ValueError("LSTM-C item map has no __UNK__ entry")
    item_ids = np.asarray(
        [item_map.get(name, unknown_id) for name in metadata["market_hash_name"]],
        dtype=np.int32,
    ).reshape(-1, 1)
    c_model = keras.models.load_model(model_dir / "lstm_c.keras")
    c_scaled = c_model.predict(
        [_scaled_windows(windows, c_scalers["x_scaler"]), item_ids],
        batch_size=batch_size,
        verbose=0,
    )
    c_prices = _decode_prices(c_scaled, c_scalers["y_scaler"])

    with (model_dir / "lstm_d_scalers.pkl").open("rb") as handle:
        d_scalers = pickle.load(handle)
    d_prices = np.full_like(c_prices, np.nan, dtype=float)
    route_column = "model_group" if "model_group" in metadata else "price_tier"
    routes = metadata[route_column].astype(str).to_numpy()
    for tier in ("low", "mid", "high"):
        mask = routes == tier
        if not mask.any():
            continue
        scaler = d_scalers[tier]
        model = keras.models.load_model(model_dir / f"lstm_d_{tier}.keras")
        predicted = model.predict(
            _scaled_windows(windows[mask], scaler["x_scaler"]),
            batch_size=batch_size,
            verbose=0,
        )
        d_prices[mask] = _decode_prices(predicted, scaler["y_scaler"])
    if not np.isfinite(d_prices).all():
        raise ValueError("LSTM-D did not cover every recent decision window")
    return c_prices, d_prices


def train_from_database(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    model_dir: Path = DEFAULT_MODEL_DIR,
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    days: int = 180,
    grid_step: float = 0.05,
    batch_size: int = 512,
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Fit, evaluate, and safely publish the current Hybrid V2 adapter."""
    from model_features import FEATURE_CONTRACT_VERSION, SEQUENCE_FEATURE_COLS

    boundaries, known_groups = load_lstm_d_routing(model_dir)
    panel = load_recent_cs2_panel(db_path, days=days)
    windows, targets, metadata = build_recent_windows(
        panel,
        feature_cols=SEQUENCE_FEATURE_COLS,
        boundaries=boundaries,
        known_groups=known_groups,
    )
    if sample_limit is not None:
        if sample_limit < 30:
            raise ValueError("sample_limit must be at least 30")
        windows = windows[:sample_limit]
        targets = targets[:sample_limit]
        metadata = metadata.iloc[:sample_limit].reset_index(drop=True)
    c_prices, d_prices = predict_base_paths(
        windows, metadata, model_dir=model_dir, batch_size=batch_size
    )
    samples = assemble_adapter_samples(
        metadata, c_prices=c_prices, d_prices=d_prices, target_prices=targets
    )
    adapter = fit_adapter(samples, grid_step=grid_step)
    adapter.update(
        {
            "featureContractVersion": FEATURE_CONTRACT_VERSION,
            "dataSource": "sqlite-price-history-rolling-180d",
            "dataThrough": metadata["decision_date"].max().strftime("%Y-%m-%d"),
            "items": int(metadata["market_hash_name"].nunique()),
            "decisionWindows": int(len(metadata)),
            "modelInputs": ["LSTM-C", "LSTM-D", "recent-price-trend"],
        }
    )
    save_adapter_atomic(adapter, report_path, require_accepted=False)
    if not adapter["accepted"]:
        raise RuntimeError(
            "Hybrid V2 validation gate failed; report saved and deployed artifact preserved"
        )
    save_adapter_atomic(adapter, artifact_path)
    return adapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--grid-step", type=float, default=0.05)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--sample-limit", type=int)
    args = parser.parse_args()
    adapter = train_from_database(
        db_path=args.db,
        model_dir=args.model_dir,
        artifact_path=args.artifact,
        report_path=args.report,
        days=args.days,
        grid_step=args.grid_step,
        batch_size=args.batch_size,
        sample_limit=args.sample_limit,
    )
    print(json.dumps(adapter, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
