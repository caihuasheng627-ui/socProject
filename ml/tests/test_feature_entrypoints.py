import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]  # ml/


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_feature_entrypoints_follow_canonical_prediction_specification():
    frame = pd.DataFrame({
        "market_hash_name": ["A"] * 8,
        "date": pd.date_range("2026-01-01", periods=8),
        "price": np.arange(8, dtype=float) + 1,
        "daily_volume": 10,
        "steam_ccu": 1_000_000,
        "weapon_type": "Rifle",
        "rarity": "Rare",
        "wear": "Factory New",
        "is_stattrak": 0,
        "is_floor_price": 0,
        "days_to_next_major": 0,
        "days_since_last_major": 0,
        "is_major_active": 0,
        "days_since_cs2_announce": 0,
    })
    for index, path in enumerate((
        ROOT / "data" / "feature_engineering.py",
        ROOT / "data" / "code" / "feature_engineering.py",
    )):
        module = load_module(path, f"legacy_feature_engineering_{index}")
        output = module.build_features(frame, drop_na_target=False)
        assert {"Target", "TargetDate", "TargetPrice"}.issubset(output.columns)
