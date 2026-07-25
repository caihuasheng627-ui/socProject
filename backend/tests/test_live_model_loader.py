import numpy as np

import model_loader
from model_features import FEATURE_CONTRACT_VERSION, SEQUENCE_FEATURE_COLS


def make_loader():
    loader = object.__new__(model_loader.ModelLoader)
    loader.tf_available = True
    loader.item_map = {"__UNK__": 0}
    loader.group_map = {}
    loader.hybrid_route = {
        "low": "LSTM-C",
        "mid": "LSTM-D",
        "high": "LSTM-D",
    }
    loader.models = {}
    loader.scalers = {}
    loader._predict_lstm_c = lambda X, name: [101.0] * 7
    loader._predict_lstm_d = lambda X, name: None
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
