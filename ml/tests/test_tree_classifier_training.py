import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from model_features import FEATURE_CONTRACT_VERSION, TREE_FEATURE_COLS
from train_tree_classifiers import build_classifier_bundle, validate_classifier_bundle


def test_classifier_bundle_is_volume_free_and_survives_roundtrip(tmp_path):
    x = np.vstack([
        np.zeros(len(TREE_FEATURE_COLS)),
        np.ones(len(TREE_FEATURE_COLS)),
        np.full(len(TREE_FEATURE_COLS), 2.0),
    ])
    y = np.array([0, 1, 2])
    model = RandomForestClassifier(n_estimators=2, random_state=42).fit(x, y)
    bundle = build_classifier_bundle(
        model=model,
        label="RF",
        params={"n_estimators": 2},
        encoders={},
    )

    path = tmp_path / "rf_cls.pkl"
    joblib.dump(bundle, path)
    loaded = joblib.load(path)

    validate_classifier_bundle(loaded)
    assert tuple(loaded["feature_cols"]) == TREE_FEATURE_COLS
    assert not any("volume" in name.lower() for name in loaded["feature_cols"])
    assert loaded["model"].predict(x).shape == (3,)
    assert loaded["feature_contract_version"] == FEATURE_CONTRACT_VERSION
