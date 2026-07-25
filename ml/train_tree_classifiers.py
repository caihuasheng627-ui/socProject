"""Train and persist the three volume-free tree classification baselines."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from xgboost import XGBClassifier

from make_predictions_trees import load_tree_splits
from model_features import (
    FEATURE_CONTRACT_VERSION,
    TREE_FEATURE_COLS,
    assert_volume_free_feature_contract,
)
from tree_features import classification_arrays
from artifact_io import save_joblib_artifact_atomic


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
OUTPUT_PATH = BASE_DIR / "outputs" / "tree_classification_results.json"

MODEL_SPECS = {
    "xgboost": {
        "label": "XGBoost",
        "path": "xgb_cls.pkl",
        "params": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
        "factory": lambda: XGBClassifier(
            max_depth=6,
            n_estimators=200,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            tree_method="hist",
            eval_metric="mlogloss",
        ),
    },
    "lightgbm": {
        "label": "LightGBM",
        "path": "lightgbm_cls.pkl",
        "params": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
        "factory": lambda: LGBMClassifier(
            max_depth=6,
            n_estimators=200,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        ),
    },
    "rf": {
        "label": "Random Forest",
        "path": "rf_cls.pkl",
        "params": {"max_depth": 12, "n_estimators": 100},
        "factory": lambda: RandomForestClassifier(
            max_depth=12, n_estimators=100, random_state=42, n_jobs=-1
        ),
    },
}


def build_classifier_bundle(model, label, params, encoders):
    return {
        "model": model,
        "name": label,
        "params": dict(params),
        "feature_cols": TREE_FEATURE_COLS,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "encoders": encoders,
        "categorical_encoding_fit_split": "train",
        "fit_split": "train+val",
        "predict_split": "test",
        "horizon_steps": 7,
        "class_threshold": 0.02,
    }


def validate_classifier_bundle(bundle, *, x_probe=None) -> None:
    required = {
        "model", "name", "params", "feature_cols", "encoders",
        "fit_split", "predict_split", "horizon_steps", "class_threshold",
        "feature_contract_version",
    }
    missing = sorted(required - set(bundle))
    if missing:
        raise ValueError(f"classifier bundle missing fields: {missing}")
    columns = assert_volume_free_feature_contract(bundle["feature_cols"])
    if columns != TREE_FEATURE_COLS:
        raise ValueError("classifier feature contract does not match TREE_FEATURE_COLS")
    if bundle["feature_contract_version"] != FEATURE_CONTRACT_VERSION:
        raise ValueError("classifier feature contract version is invalid")
    if bundle["fit_split"] != "train+val" or bundle["predict_split"] != "test":
        raise ValueError("classifier bundle split metadata is invalid")
    if bundle["horizon_steps"] != 7:
        raise ValueError("classifier horizon_steps must be 7")
    if x_probe is not None:
        prediction = bundle["model"].predict(x_probe)
        probabilities = bundle["model"].predict_proba(x_probe)
        if not np.isfinite(np.asarray(prediction, dtype=float)).all():
            raise ValueError("classifier reload prediction is non-finite")
        if not np.isfinite(np.asarray(probabilities, dtype=float)).all():
            raise ValueError("classifier reload probabilities are non-finite")


def classification_metrics(y_true, y_pred, probabilities) -> dict:
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1_weighted": float(f1_score(y_true, y_pred, average="weighted")),
        "auc_ovr_weighted": None,
        "rows": int(len(y_true)),
    }
    try:
        metrics["auc_ovr_weighted"] = float(
            roc_auc_score(
                y_true,
                probabilities,
                multi_class="ovr",
                average="weighted",
                labels=np.arange(probabilities.shape[1]),
            )
        )
    except ValueError:
        pass
    return metrics


def main() -> dict:
    splits, encoders = load_tree_splits()
    fit_frame = pd.concat([splits["train"], splits["val"]], ignore_index=True)
    x_fit, y_fit, *_ = classification_arrays(fit_frame)
    x_test, y_test, *_ = classification_arrays(splits["test"])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "feature_count": len(TREE_FEATURE_COLS),
        "fit_split": "train+val",
        "predict_split": "test",
        "models": {},
    }
    for key, spec in MODEL_SPECS.items():
        started = time.time()
        model = spec["factory"]()
        model.fit(x_fit, y_fit)
        prediction = model.predict(x_test)
        probabilities = model.predict_proba(x_test)
        metrics = classification_metrics(y_test, prediction, probabilities)
        metrics["training_seconds"] = round(time.time() - started, 3)

        bundle = build_classifier_bundle(
            model, spec["label"], spec["params"], encoders
        )
        validate_classifier_bundle(bundle, x_probe=x_test[: min(2, len(x_test))])
        artifact_path = MODEL_DIR / spec["path"]
        save_joblib_artifact_atomic(
            bundle,
            artifact_path,
            validator=lambda loaded: validate_classifier_bundle(
                loaded, x_probe=x_test[: min(2, len(x_test))]
            ),
        )
        results["models"][spec["label"]] = metrics
        print(
            f"{spec['label']}: accuracy={metrics['accuracy']:.4f}, "
            f"F1={metrics['f1_weighted']:.4f}, saved={artifact_path.name}",
            flush=True,
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    main()
