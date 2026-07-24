import numpy as np

import model_loader


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
