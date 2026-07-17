"""
任务 3: LightGBM + Random Forest
=================================
用和 XGBoost 一样的数据、一样的特征, 换两个模型对比。
回归 + 分类, 记录同样指标。

运行: python 03_lightgbm_rf.py
"""

import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from lightgbm import LGBMRegressor, LGBMClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    load_and_prepare, make_regression_target, make_classification_target,
    eval_regression, eval_classification, save_json,
)


def _train_regression(model_cls, model_name, param_grid, train_df, test_df, **kwargs):
    X_train, y_train, _, _, _ = make_regression_target(train_df)
    X_test, y_test, _, _, prices_test = make_regression_target(test_df)
    tscv = TimeSeriesSplit(n_splits=5)

    print(f"\n  [{model_name} 回归] GridSearchCV...")
    t0 = time.time()
    model = model_cls(**kwargs)
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="neg_root_mean_squared_error",
                      n_jobs=1, verbose=1, refit=True)
    gs.fit(X_train, y_train)
    train_time = time.time() - t0
    print(f"  [{model_name} 回归] 最佳参数: {gs.best_params_}  ({train_time:.1f}s)")

    y_pred = gs.predict(X_test)
    metrics = eval_regression(y_test, y_pred, prices_test)
    print(f"  [{model_name} 回归] RMSE={metrics['rmse']:.2f}  MAE={metrics['mae']:.2f}  MAPE={metrics['mape']:.2f}%  R2={metrics['r2']:.4f}")
    return gs.best_estimator_, metrics, train_time


def _train_classification(model_cls, model_name, param_grid, train_df, test_df, **kwargs):
    X_train, y_train, _, _, _ = make_classification_target(train_df)
    X_test, y_test, _, _, _ = make_classification_target(test_df)
    tscv = TimeSeriesSplit(n_splits=5)

    print(f"\n  [{model_name} 分类] GridSearchCV...")
    t0 = time.time()
    model = model_cls(**kwargs)
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="f1_weighted",
                      n_jobs=1, verbose=1, refit=True)
    gs.fit(X_train, y_train)
    train_time = time.time() - t0
    print(f"  [{model_name} 分类] 最佳参数: {gs.best_params_}  ({train_time:.1f}s)")

    y_pred = gs.predict(X_test)
    y_proba = None
    if hasattr(gs, "predict_proba"):
        y_proba = gs.predict_proba(X_test)
    metrics = eval_classification(y_test, y_pred, y_proba)
    auc_str = f"  AUC={metrics['auc']:.4f}" if metrics['auc'] else ""
    print(f"  [{model_name} 分类] Accuracy={metrics['accuracy']:.4f}  F1={metrics['f1']:.4f}{auc_str}")
    return gs.best_estimator_, metrics, train_time


def main():
    print("=" * 70)
    print("任务 3: LightGBM + Random Forest")
    print("=" * 70)

    print("\n[1/5] 加载数据...")
    train_df = load_and_prepare("train")
    test_df = load_and_prepare("test")

    lgb_reg_params = {"max_depth": [4, 6, -1], "n_estimators": [100, 200], "learning_rate": [0.05, 0.1]}
    lgb_cls_params = {"max_depth": [4, 6, -1], "n_estimators": [100, 200], "learning_rate": [0.05, 0.1]}
    rf_reg_params  = {"max_depth": [8, 12, None], "n_estimators": [100, 200]}
    rf_cls_params  = {"max_depth": [8, 12, None], "n_estimators": [100, 200]}

    print("\n[2/5] LightGBM 回归...")
    lgb_reg_model, lgb_reg_metrics, lgb_reg_time = _train_regression(
        LGBMRegressor, "LightGBM", lgb_reg_params, train_df, test_df,
        random_state=42, n_jobs=-1, verbose=-1,
    )

    print("\n[3/5] LightGBM 分类...")
    lgb_cls_model, lgb_cls_metrics, lgb_cls_time = _train_classification(
        LGBMClassifier, "LightGBM", lgb_cls_params, train_df, test_df,
        random_state=42, n_jobs=-1, verbose=-1,
    )

    print("\n[4/5] Random Forest 回归...")
    rf_reg_model, rf_reg_metrics, rf_reg_time = _train_regression(
        RandomForestRegressor, "RandomForest", rf_reg_params, train_df, test_df,
        random_state=42, n_jobs=-1,
    )

    print("\n[5/5] Random Forest 分类...")
    rf_cls_model, rf_cls_metrics, rf_cls_time = _train_classification(
        RandomForestClassifier, "RandomForest", rf_cls_params, train_df, test_df,
        random_state=42, n_jobs=-1,
    )

    summary = {
        "lightgbm_regression": {
            "model": "LightGBM", "type": "ML 对比",
            "rmse": lgb_reg_metrics["rmse"], "mae": lgb_reg_metrics["mae"],
            "mape": lgb_reg_metrics["mape"], "r2": lgb_reg_metrics["r2"],
            "accuracy": None, "auc": None,
            "speed": "极快", "interpretability": 2,
        },
        "lightgbm_classification": {
            "model": "LightGBM", "type": "ML 对比",
            "rmse": None, "mae": None, "mape": None, "r2": None,
            "accuracy": lgb_cls_metrics["accuracy"], "f1": lgb_cls_metrics["f1"],
            "auc": lgb_cls_metrics["auc"],
            "speed": "极快", "interpretability": 2,
        },
        "randomforest_regression": {
            "model": "Random Forest", "type": "集成基线",
            "rmse": rf_reg_metrics["rmse"], "mae": rf_reg_metrics["mae"],
            "mape": rf_reg_metrics["mape"], "r2": rf_reg_metrics["r2"],
            "accuracy": None, "auc": None,
            "speed": "快", "interpretability": 2,
        },
        "randomforest_classification": {
            "model": "Random Forest", "type": "集成基线",
            "rmse": None, "mae": None, "mape": None, "r2": None,
            "accuracy": rf_cls_metrics["accuracy"], "f1": rf_cls_metrics["f1"],
            "auc": rf_cls_metrics["auc"],
            "speed": "快", "interpretability": 2,
        },
    }
    save_json(summary, "lightgbm_rf_results.json")
    return summary


if __name__ == "__main__":
    main()
