import json
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.modules.pop("config", None)

import model_loader
from model_features import FEATURE_CONTRACT_VERSION, SEQUENCE_FEATURE_COLS


def make_loader():
    loader = object.__new__(model_loader.ModelLoader)
    loader.tf_available = True
    loader.item_map = {"__UNK__": 0}
    loader.group_map = {}
    loader.group_boundaries = (20.0, 100.0)
    loader.hybrid_v2_adapter = {"contractVersion": "hybrid-v2-volume-free-v1"}
    loader.hybrid_route = {
        "low": "LSTM-C",
        "mid": "LSTM-D",
        "high": "LSTM-D",
    }
    loader.models = {}
    loader.scalers = {}
    loader._predict_lstm_c = lambda X, name: [101.0] * 7
    loader._predict_lstm_d = lambda X, name: None
    loader._predict_lstm_d_for_group = lambda X, group: [99.0] * 7
    return loader


class IdentityScaler:
    n_features_in_ = 1

    def transform(self, values):
        return values

    def inverse_transform(self, values):
        return values


def test_live_lstm_uses_database_window_only(monkeypatch):
    X = np.zeros((1, 60, 15), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    monkeypatch.setattr(
        model_loader,
        "_skin_window",
        lambda name: (_ for _ in ()).throw(AssertionError("offline panel used")),
    )
    loader = make_loader()

    result = loader.predict_live_lstm("new item")

    assert result["date"] == "2026-07-22"
    assert result["current_price"] == 100.0
    assert result["predicted_price"] == 101.0
    assert result["daily_prices"] == [101.0] * 7
    assert result["model"] == "LSTM-C(__UNK__)"


def test_live_lstm_does_not_use_mock_when_tensorflow_is_unavailable(monkeypatch):
    X = np.zeros((1, 60, 15), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    loader = make_loader()
    loader.tf_available = False

    assert loader.predict_live_lstm("new item") is None


def test_live_ensemble_returns_c_and_d_for_unknown_online_item(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 55.0, "2026-07-22"),
    )
    loader = make_loader()
    loader._predict_lstm_c = lambda X, name: [60.0] * 7
    loader._predict_lstm_d_for_group = lambda X, group: [52.0] * 7

    result = loader.predict_live_ensemble("unknown item")

    assert result["model"] == "Hybrid-V2"
    assert result["price_tier"] == "mid"
    assert result["lstm_c_prices"] == [60.0] * 7
    assert result["lstm_d_prices"] == [52.0] * 7
    assert result["adapter"]["contractVersion"] == "hybrid-v2-volume-free-v1"


def test_live_ensemble_uses_high_d_model_but_ultra_adapter_for_expensive_item(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 1800.0, "2026-07-22"),
    )
    loader = make_loader()
    seen_groups = []
    loader._predict_lstm_d_for_group = lambda X, group: seen_groups.append(group) or [900.0] * 7

    result = loader.predict_live_ensemble("expensive knife")

    assert seen_groups == ["high"]
    assert result["model_group"] == "high"
    assert result["price_tier"] == "ultra"


def test_live_model_version_changes_with_artifact_metadata(monkeypatch):
    class Artifact:
        name = "lstm_c.keras"

        def __init__(self, size):
            self.size = size

        def stat(self):
            return type("Stat", (), {"st_size": self.size, "st_mtime_ns": 1})()

    class ArtifactDir:
        def __init__(self, artifact):
            self.artifact = artifact

        def glob(self, pattern):
            return [self.artifact]

    artifact = Artifact(2)
    monkeypatch.setattr(model_loader, "MODEL_DIR", ArtifactDir(artifact))
    loader = make_loader()

    version_v1 = loader.live_model_version()
    artifact.size = 11
    version_v2 = loader.live_model_version()

    assert version_v1.startswith("lstm-live-")
    assert version_v2.startswith("lstm-live-")
    assert version_v1 != version_v2


def test_hybrid_v2_adapter_hot_reloads_when_artifact_changes(monkeypatch, tmp_path):
    valid_weights = {
        "global": {
            str(day): {"c": 0.25, "d": 0.35, "recent": 0.40, "bias": 0.0}
            for day in range(1, 8)
        }
    }
    artifact = tmp_path / "hybrid_v2_adapter.json"
    artifact.write_text(
        json.dumps({
            "contractVersion": "hybrid-v2-volume-free-v1",
            "selectionSplit": "train",
            "horizonSteps": 7,
            "accepted": True,
            "weights": valid_weights,
            "dataThrough": "2026-07-01",
        }),
        encoding="utf-8",
    )
    loader = make_loader()
    loader.hybrid_v2_adapter = {}
    loader._hybrid_v2_mtime_ns = None
    monkeypatch.setattr(model_loader, "MODEL_DIR", tmp_path)

    loader._refresh_hybrid_v2_adapter()
    assert loader.hybrid_v2_adapter["dataThrough"] == "2026-07-01"

    artifact.write_text(
        json.dumps({
            "contractVersion": "hybrid-v2-volume-free-v1",
            "selectionSplit": "train",
            "horizonSteps": 7,
            "accepted": True,
            "weights": valid_weights,
            "dataThrough": "2026-07-22",
        }),
        encoding="utf-8",
    )
    loader._hybrid_v2_mtime_ns = -1
    loader._refresh_hybrid_v2_adapter()
    assert loader.hybrid_v2_adapter["dataThrough"] == "2026-07-22"


def test_hybrid_v2_hot_reload_preserves_previous_adapter_when_new_file_is_malformed(
    monkeypatch, tmp_path
):
    previous = {
        "contractVersion": "hybrid-v2-volume-free-v1",
        "selectionSplit": "train",
        "horizonSteps": 7,
        "accepted": True,
        "weights": {
            "global": {
                str(day): {"c": 0.25, "d": 0.35, "recent": 0.40, "bias": 0.0}
                for day in range(1, 8)
            }
        },
        "dataThrough": "2026-07-01",
    }
    artifact = tmp_path / "hybrid_v2_adapter.json"
    artifact.write_text(
        json.dumps({
            "contractVersion": "hybrid-v2-volume-free-v1",
            "selectionSplit": "train",
            "horizonSteps": 7,
            "accepted": True,
            "weights": {"global": {"7": {"c": float("nan")}}},
            "dataThrough": "2026-07-22",
        }),
        encoding="utf-8",
    )
    loader = make_loader()
    loader.hybrid_v2_adapter = previous
    loader._hybrid_v2_mtime_ns = None
    monkeypatch.setattr(model_loader, "MODEL_DIR", tmp_path)

    loader._refresh_hybrid_v2_adapter()

    assert loader.hybrid_v2_adapter == previous
    assert loader._hybrid_v2_mtime_ns is None


def test_live_trend_uses_database_window_and_returns_ordered_quantiles(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    monkeypatch.setattr(
        model_loader,
        "_skin_window",
        lambda name: (_ for _ in ()).throw(AssertionError("offline panel used")),
    )
    raw = np.tile(np.log1p([110.0, 100.0, 105.0]), (30, 1))[None, ...]

    class TrendModel:
        def predict(self, values, **kwargs):
            assert values.shape == (1, 60, 13)
            return raw

    loader = make_loader()
    loader.models["seq2seq_30d"] = TrendModel()
    loader.scalers["seq2seq_30d"] = {
        "x_scaler": IdentityScaler(),
        "y_scaler": IdentityScaler(),
        "feature_cols": SEQUENCE_FEATURE_COLS,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
    }

    result = loader.predict_live_trend_30d("new item")

    assert result["date"] == "2026-07-22"
    assert result["current_price"] == 100.0
    assert result["model"] == "Keras-Seq2Seq-30D"
    assert result["horizon"] == 30
    assert result["p10"] == [100.0] * 30
    assert result["p50"] == [105.0] * 30
    assert result["p90"] == [110.0] * 30


def test_live_trend_returns_none_when_tensorflow_or_artifact_is_unavailable(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    loader = make_loader()

    assert loader.predict_live_trend_30d("new item") is None

    loader.tf_available = False
    assert loader.predict_live_trend_30d("new item") is None


def test_live_trend_clips_interval_edges_to_forty_percent_around_median(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    raw = np.tile(np.log1p([5.0, 100.0, 300.0]), (30, 1))[None, ...]

    class TrendModel:
        def predict(self, values, **kwargs):
            return raw

    loader = make_loader()
    loader.models["seq2seq_30d"] = TrendModel()
    loader.scalers["seq2seq_30d"] = {
        "x_scaler": IdentityScaler(),
        "y_scaler": IdentityScaler(),
        "feature_cols": SEQUENCE_FEATURE_COLS,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
    }

    result = loader.predict_live_trend_30d("new item")

    assert result["p10"] == [60.0] * 30
    assert result["p50"] == [100.0] * 30
    assert result["p90"] == [140.0] * 30


def test_live_trend_inverts_training_scaler_one_quantile_at_a_time(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )

    class ThirtyDayScaler:
        n_features_in_ = 30

        def __init__(self):
            self.shapes = []

        def inverse_transform(self, values):
            self.shapes.append(values.shape)
            return values

    class TrendModel:
        def predict(self, values, **kwargs):
            return np.tile(np.log1p([90.0, 100.0, 110.0]), (30, 1))[None, ...]

    y_scaler = ThirtyDayScaler()
    loader = make_loader()
    loader.models["seq2seq_30d"] = TrendModel()
    loader.scalers["seq2seq_30d"] = {
        "x_scaler": IdentityScaler(),
        "y_scaler": y_scaler,
        "feature_cols": SEQUENCE_FEATURE_COLS,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
    }

    result = loader.predict_live_trend_30d("new item")

    assert result["p50"] == [100.0] * 30
    assert y_scaler.shapes == [(1, 30), (1, 30), (1, 30)]


def test_live_trend_rejects_scaler_with_mismatched_feature_contract(monkeypatch):
    X = np.zeros((1, 60, 13), dtype=np.float32)
    monkeypatch.setattr(
        model_loader,
        "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )

    class TrendModel:
        def predict(self, values, **kwargs):
            raise AssertionError("mismatched artifact should not run")

    loader = make_loader()
    loader.models["seq2seq_30d"] = TrendModel()
    loader.scalers["seq2seq_30d"] = {
        "x_scaler": IdentityScaler(),
        "y_scaler": IdentityScaler(),
        "feature_cols": ["wrong_feature"] * 13,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
    }

    assert loader.predict_live_trend_30d("new item") is None
