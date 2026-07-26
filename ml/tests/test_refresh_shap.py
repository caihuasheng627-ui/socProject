import numpy as np
import pytest
import importlib.util
from pathlib import Path

from refresh_shap import normalized_features
from model_features import TREE_FEATURE_COLS


def test_normalized_features_uses_tree_contract_order_and_sums_to_one():
    rows = normalized_features(np.arange(1, len(TREE_FEATURE_COLS) + 1, dtype=float))

    assert len(rows) == len(TREE_FEATURE_COLS)
    assert rows[0]["importance"] > rows[-1]["importance"]
    assert sum(row["importance"] for row in rows) == pytest.approx(1.0)


def test_normalized_features_rejects_wrong_shape():
    with pytest.raises(ValueError, match="invalid SHAP importance vector"):
        normalized_features(np.ones(8))


def test_legacy_feature_importance_entry_delegates_to_current_shap(monkeypatch):
    entry = Path(__file__).resolve().parents[1] / "04_feature_importance.py"
    spec = importlib.util.spec_from_file_location("legacy_feature_importance", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    called = {}

    def fake_refresh(split="test"):
        called["split"] = split
        return {"contractVersion": "tree-shap-v2"}

    monkeypatch.setattr(module, "refresh", fake_refresh, raising=False)

    assert module.main() == {"contractVersion": "tree-shap-v2"}
    assert called == {"split": "test"}
