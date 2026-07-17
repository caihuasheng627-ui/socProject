"""
任务 2: XGBoost 回归 + 分类
============================
- TimeSeriesSplit 分割 (禁止随机切分!)
- XGBoost Regressor -> 预测价格 (log空间)
- XGBoost Classifier -> 预测涨/平/跌 三分类
- GridSearchCV 调参

运行: python 02_xgboost_reg_cls.py
"""

import os
import sys
import time
import numpy as np
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from xgboost import XGBRegressor, XGBClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    load_and_prepare, make_regression_target, make_classification_target,
    eval_regression, eval_classification, save_json,
)


def train_xgboost_regression(train_df, test_df):
    """XGBoost 回归: 预测 7 天后价格"""
    print("\n  [回归] 准备数据...")
    X_train, y_train, _, _, prices_train = make_regression_target(train_df)
    X_test, y_test, _, _, prices_test = make_regression_target(test_df)

    print(f"  [回归] Train: {X_train.shape}  Test: {X_test.shape}")

    tscv = TimeSeriesSplit(n_splits=5)
    param_grid = {
        "max_depth": [4, 6, 8],
        "n_estimators": [100, 200],
        "learning_rate": [0.05, 0.1],
    }

    print("  [回归] GridSearchCV 调参 (TimeSeriesSplit=5)...")
    t0 = time.time()
    model = XGBRegressor(random_state=42, n_jobs=-1, tree_method="hist")
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="neg_root_mean_squared_error",
                      n_jobs=-1, verbose=0, refit=True)
    gs.fit(X_train, y_train)
    train_time = time.time() - t0

    print(f"  [回归] 最佳参数: {gs.best_params_}  ({train_time:.1f}s)")

    y_pred_log = gs.predict(X_test)
    metrics = eval_regression(y_test, y_pred_log, prices_test)
    print(f"  [回归] RMSE={metrics['rmse']:.2f}  MAE={metrics['mae']:.2f}  MAPE={metrics['mape']:.2f}%  R2={metrics['r2']:.4f}")

    return gs.best_estimator_, metrics, train_time


def train_xgboost_classification(train_df, test_df):
    """XGBoost 分类: 涨/平/跌"""
    print("\n  [分类] 准备数据...")
    X_train, y_train, _, _, _ = make_classification_target(train_df)
    X_test, y_test, _, _, _ = make_classification_target(test_df)

    unique, counts = np.unique(y_train, return_counts=True)
    print(f"  [分类] Train: {X_train.shape}  Test: {X_test.shape}")
    print(f"  [分类] 标签分布: {dict(zip(unique, counts))}")

    tscv = TimeSeriesSplit(n_splits=5)
    param_grid = {
        "max_depth": [4, 6],
        "n_estimators": [100, 200],
        "learning_rate": [0.05, 0.1],
    }

    print("  [分类] GridSearchCV 调参...")
    t0 = time.time()
    model = XGBClassifier(random_state=42, n_jobs=-1, tree_method="hist",
                          use_label_encoder=False, eval_metric="mlogloss")
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="f1_weighted",
                      n_jobs=-1, verbose=0, refit=True)
    gs.fit(X_train, y_train)
    train_time = time.time() - t0

    print(f"  [分类] 最佳参数: {gs.best_params_}  ({train_time:.1f}s)")

    y_pred = gs.predict(X_test)
    y_proba = gs.predict_proba(X_test)
    metrics = eval_classification(y_test, y_pred, y_proba)
    print(f"  [分类] Accuracy={metrics['accuracy']:.4f}  F1={metrics['f1']:.4f}  AUC={metrics['auc']:.4f}" if metrics['auc'] else
          f"  [分类] Accuracy={metrics['accuracy']:.4f}  F1={metrics['f1']:.4f}")

    return gs.best_estimator_, metrics, train_time


def main():
    print("=" * 70)
    print("任务 2: XGBoost 回归 + 分类")
    print("=" * 70)

    print("\n[1/3] 加载数据 (train + test)...")
    train_df = load_and_prepare("train")
    test_df = load_and_prepare("test")
    print(f"  Train: {len(train_df)} rows  Test: {len(test_df)} rows")

    print("\n[2/3] XGBoost 回归 (价格预测)...")
    reg_model, reg_metrics, reg_time = train_xgboost_regression(train_df, test_df)

    print("\n[3/3] XGBoost 分类 (涨/平/跌)...")
    cls_model, cls_metrics, cls_time = train_xgboost_classification(train_df, test_df)

    total_time = reg_time + cls_time
    summary = {
        "regression": {
            "model": "XGBoost",
            "type": "ML 主力",
            "rmse": reg_metrics["rmse"],
            "mae": reg_metrics["mae"],
            "mape": reg_metrics["mape"],
            "r2": reg_metrics["r2"],
            "accuracy": None,
            "auc": None,
            "speed": "快",
            "interpretability": 2,
            "train_time_s": round(total_time, 1),
        },
        "classification": {
            "model": "XGBoost",
            "type": "ML 主力",
            "rmse": None,
            "mae": None,
            "mape": None,
            "r2": None,
            "accuracy": cls_metrics["accuracy"],
            "f1": cls_metrics["f1"],
            "auc": cls_metrics["auc"],
            "speed": "快",
            "interpretability": 2,
        },
    }
    print(f"\n  XGBoost 总训练时间: {total_time:.1f}s")
    save_json(summary, "xgboost_results.json")
    return summary, reg_model, cls_model


if __name__ == "__main__":
    main()
