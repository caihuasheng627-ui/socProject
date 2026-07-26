from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from model_features import (
    FEATURE_CONTRACT_VERSION,
    FROZEN_GRU_ITEMS,
    SEQUENCE_FEATURE_COLS,
    TREE_FEATURE_COLS,
    assert_volume_free_feature_contract,
)
from tree_features import regression_arrays


ML_DIR = Path(__file__).resolve().parents[1]


def test_sequence_feature_contract_has_13_volume_free_columns():
    assert FEATURE_CONTRACT_VERSION == "volume-free-v1"
    assert len(SEQUENCE_FEATURE_COLS) == 13
    assert SEQUENCE_FEATURE_COLS == (
        "log_price",
        "MA_7",
        "MA_30",
        "MA_90",
        "Return_1d",
        "Return_7d",
        "Volatility_30",
        "RSI_14",
        "MACD",
        "is_floor_price",
        "is_stattrak",
        "is_major_active",
        "steam_ccu",
    )


def test_tree_feature_contract_has_21_volume_free_columns():
    assert len(TREE_FEATURE_COLS) == 21
    assert TREE_FEATURE_COLS == (
        "log_price",
        "MA_7",
        "MA_30",
        "MA_90",
        "Return_1d",
        "Return_7d",
        "Volatility_30",
        "RSI_14",
        "MACD",
        "MA_30_dev",
        "BB_position",
        "is_stattrak",
        "is_floor_price",
        "days_to_next_major",
        "days_since_last_major",
        "is_major_active",
        "steam_ccu",
        "days_since_cs2_announce",
        "weapon_type_enc",
        "rarity_enc",
        "wear_enc",
    )


@pytest.mark.parametrize("column", ["daily_volume", "Daily_Volume_Log", "VOLUME_MA_7"])
def test_volume_free_contract_rejects_volume_columns_case_insensitively(column):
    with pytest.raises(ValueError, match=column):
        assert_volume_free_feature_contract(("log_price", column))


def test_model_consumers_import_shared_feature_contract_without_copying_lists():
    expected_imports = {
        "train_lstm_c.py": "SEQUENCE_FEATURE_COLS",
        "train_lstm_d.py": "SEQUENCE_FEATURE_COLS",
        "train_gru.py": "SEQUENCE_FEATURE_COLS",
        "tree_features.py": "TREE_FEATURE_COLS",
        "utils.py": "TREE_FEATURE_COLS",
        "shap_analysis.py": "TREE_FEATURE_COLS",
        "shap_cls_analysis.py": "TREE_FEATURE_COLS",
    }
    removed_columns = {
        '"volume_ma_log"',
        '"daily_volume_log"',
        '"Volume_MA_7"',
        '"Volume_Change_Ratio"',
    }

    for filename, constant in expected_imports.items():
        source = (ML_DIR / filename).read_text(encoding="utf-8")
        assert f"from model_features import" in source, filename
        assert constant in source, filename
        assert all(column not in source for column in removed_columns), filename


def test_gru_items_are_frozen_to_existing_training_artifact():
    assert FROZEN_GRU_ITEMS == (
        "CZ75-Auto | Circaetus (Battle-Scarred)",
        "R8 Revolver | Amber Fade (Factory New)",
        "Danger Zone Case",
        "R8 Revolver | Amber Fade (Minimal Wear)",
        "Dreams & Nightmares Case",
        "CZ75-Auto | Framework (Battle-Scarred)",
        "Glock-18 | Blue Fissure (Factory New)",
        "FAMAS | CaliCamo (Factory New)",
        "CZ75-Auto | Army Sheen (Factory New)",
        "AK-47 | Safari Mesh (Minimal Wear)",
    )


@pytest.mark.parametrize(
    "filename",
    ["train_lstm_c.py", "train_lstm_d.py", "train_gru.py"],
)
def test_sequence_trainers_checkpoint_best_model_before_post_training_evaluation(filename):
    source = (ML_DIR / filename).read_text(encoding="utf-8")
    checkpoint_position = source.index("keras.callbacks.ModelCheckpoint(")
    fit_position = source.index("model.fit(")
    promote_position = source.index("promote_keras_checkpoint(")

    assert checkpoint_position < fit_position < promote_position
    assert "save_best_only=True" in source[checkpoint_position:fit_position]
    assert "save_weights_only=False" in source[checkpoint_position:fit_position]
    assert "checkpoint_path" in source[checkpoint_position:fit_position]
    assert ".best-" in source[:checkpoint_position]
    assert "model.save(" not in source[promote_position:]
    assert "feature_contract_version" in source


def test_pandas_consumers_convert_immutable_contract_to_list():
    tree_source = (ML_DIR / "make_predictions_trees.py").read_text(encoding="utf-8")
    backend_source = (ML_DIR.parent / "backend" / "model_loader.py").read_text(encoding="utf-8")
    assert "fit_frame[list(FEATURE_COLS)]" in tree_source
    assert "prediction_source[list(FEATURE_COLS)]" in tree_source
    assert "g[list(FEATURE_COLS)]" in backend_source


@pytest.mark.parametrize("filename", ["train_lstm_c.py", "train_gru.py"])
def test_single_model_trainers_save_preprocessors_before_evaluation(filename):
    source = (ML_DIR / filename).read_text(encoding="utf-8")
    assert source.index("model.fit(") < source.index("save_pickle_atomic(") < source.index("model.predict(")


def test_lstm_d_persists_group_map_and_each_completed_group_scaler_early():
    source = (ML_DIR / "train_lstm_d.py").read_text(encoding="utf-8")
    train_group_source = source[source.index("def train_group("):source.index("def main():")]
    main_source = source[source.index("def main():"):]

    assert (
        train_group_source.index("model.fit(")
        < train_group_source.index("save_pickle_atomic(scalers")
        < train_group_source.index("model.predict(")
    )
    assert main_source.index("save_pickle_atomic(group_bundle") < main_source.index(
        "for gname in GROUP_NAMES:"
    )


def test_tree_array_builder_converts_immutable_contract_for_pandas():
    frame = pd.DataFrame({column: [1.0] for column in TREE_FEATURE_COLS})
    frame["Target"] = 1.0
    frame["date"] = pd.Timestamp("2026-01-01")
    frame["market_hash_name"] = "A"
    frame["price"] = 1.0

    features, *_ = regression_arrays(frame)

    assert features.shape == (1, len(TREE_FEATURE_COLS))


@pytest.mark.parametrize("filename", ["train_lstm_c.py", "train_lstm_d.py", "train_gru.py"])
def test_sequence_trainers_convert_immutable_contract_for_window_builder(filename):
    source = (ML_DIR / filename).read_text(encoding="utf-8")
    assert "list(FEATURE_COLS), LOOKBACK" in source
