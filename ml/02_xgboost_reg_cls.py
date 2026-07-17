"""
任务 2: XGBoost 回归 + 分类
============================
- TimeSeriesSplit 分割 (禁止随机切分!) — 输入已按全局 date 排序
- 调参: train 内 TSCV；终训: train+val；评估: test
- XGBoost Regressor -> 预测 7 天后价格 (log 空间)
- XGBoost Classifier -> 预测涨/平/跌 三分类

运行: python 02_xgboost_reg_cls.py
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV
from xgboost import XGBRegressor, XGBClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    load_train_val_test, make_regression_target, make_classification_target,
    eval_regression, eval_classification, save_json, print_tscv_fold_dates,
)


def _merge_train_val(train_df, val_df):
    return pd.concat([train_df, val_df], ignore_index=True)


def train_xgboost_regression(train_df, val_df, test_df):
    """XGBoost 回归: 预测 7 天后价格"""
    print("\n  [回归] 准备数据...")
    X_cv, y_cv, dates_cv, _, _ = make_regression_target(train_df)
    X_fit, y_fit, _, _, _ = make_regression_target(_merge_train_val(train_df, val_df))
    X_test, y_test, _, _, prices_test = make_regression_target(test_df)

    print(f"  [回归] CV-train: {X_cv.shape}  Final-fit: {X_fit.shape}  Test: {X_test.shape}")

    tscv = TimeSeriesSplit(n_splits=5)
    print("  [回归] TimeSeriesSplit fold 日期范围:")
    print_tscv_fold_dates(dates_cv, tscv)

    param_grid = {
        "max_depth": [4, 6, 8],
        "n_estimators": [100, 200],
        "learning_rate": [0.05, 0.1],
    }

    print("  [回归] GridSearchCV 调参 (TimeSeriesSplit=5 on train)...")
    t0 = time.time()
    model = XGBRegressor(random_state=42, n_jobs=-1, tree_method="hist")
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="neg_root_mean_squared_error",
                      n_jobs=-1, verbose=0, refit=True)
    gs.fit(X_cv, y_cv)
    search_time = time.time() - t0
    print(f"  [回归] 最佳参数: {gs.best_params_}  (search {search_time:.1f}s)")

    print("  [回归] 用 train+val 重训最佳参数...")
    t1 = time.time()
    best = XGBRegressor(random_state=42, n_jobs=-1, tree_method="hist", **gs.best_params_)
    best.fit(X_fit, y_fit)
    train_time = search_time + (time.time() - t1)

    y_pred_log = best.predict(X_test)
    metrics = eval_regression(y_test, y_pred_log, prices_test)
    print(f"  [回归] RMSE={metrics['rmse']:.2f}  MAE={metrics['mae']:.2f}  "
          f"MAPE={metrics['mape']:.2f}%  R2={metrics['r2']:.4f}")

    return best, metrics, train_time


def train_xgboost_classification(train_df, val_df, test_df):
    """XGBoost 分类: 涨/平/跌"""
    print("\n  [分类] 准备数据...")
    X_cv, y_cv, dates_cv, _, _ = make_classification_target(train_df)
    X_fit, y_fit, _, _, _ = make_classification_target(_merge_train_val(train_df, val_df))
    X_test, y_test, _, _, _ = make_classification_target(test_df)

    unique, counts = np.unique(y_cv, return_counts=True)
    print(f"  [分类] CV-train: {X_cv.shape}  Final-fit: {X_fit.shape}  Test: {X_test.shape}")
    print(f"  [分类] 标签分布: {dict(zip(unique.tolist(), counts.tolist()))}")

    tscv = TimeSeriesSplit(n_splits=5)
    print("  [分类] TimeSeriesSplit fold 日期范围:")
    print_tscv_fold_dates(dates_cv, tscv)

    param_grid = {
        "max_depth": [4, 6],
        "n_estimators": [100, 200],
        "learning_rate": [0.05, 0.1],
    }

    print("  [分类] GridSearchCV 调参...")
    t0 = time.time()
    model = XGBClassifier(random_state=42, n_jobs=-1, tree_method="hist",
                          eval_metric="mlogloss")
    gs = GridSearchCV(model, param_grid, cv=tscv, scoring="f1_weighted",
                      n_jobs=-1, verbose=0, refit=True)
    gs.fit(X_cv, y_cv)
    search_time = time.time() - t0
    print(f"  [分类] 最佳参数: {gs.best_params_}  (search {search_time:.1f}s)")

    print("  [分类] 用 train+val 重训最佳参数...")
    t1 = time.time()
    best = XGBClassifier(random_state=42, n_jobs=-1, tree_method="hist",
                         eval_metric="mlogloss", **gs.best_params_)
    best.fit(X_fit, y_fit)
    train_time = search_time + (time.time() - t1)

    y_pred = best.predict(X_test)
    y_proba = best.predict_proba(X_test)
    metrics = eval_classification(y_test, y_pred, y_proba)
    if metrics["auc"] is not None:
        print(f"  [分类] Accuracy={metrics['accuracy']:.4f}  F1={metrics['f1']:.4f}  "
              f"AUC={metrics['auc']:.4f}")
    else:
        print(f"  [分类] Accuracy={metrics['accuracy']:.4f}  F1={metrics['f1']:.4f}")

    return best, metrics, train_time


def main():
    print("=" * 70)
    print("任务 2: XGBoost 回归 + 分类")
    print("=" * 70)

    print("\n[1/3] 加载数据 (全量面板特征 + train 编码)...")
    train_df, val_df, test_df = load_train_val_test()
    print(f"  Train: {len(train_df)}  Val: {len(val_df)}  Test: {len(test_df)}")
    print(f"  Train dates: {train_df['date'].min().date()} ~ {train_df['date'].max().date()}")
    print(f"  Val   dates: {val_df['date'].min().date()} ~ {val_df['date'].max().date()}")
    print(f"  Test  dates: {test_df['date'].min().date()} ~ {test_df['date'].max().date()}")

    print("\n[2/3] XGBoost 回归 (价格预测)...")
    reg_model, reg_metrics, reg_time = train_xgboost_regression(train_df, val_df, test_df)

    print("\n[3/3] XGBoost 分类 (涨/平/跌)...")
    cls_model, cls_metrics, cls_time = train_xgboost_classification(train_df, val_df, test_df)

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
