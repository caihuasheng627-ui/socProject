"""
任务 3: LightGBM + Random Forest
=================================
用和 XGBoost 一样的数据、一样的特征, 换两个模型对比。
调参: train 内 TSCV；终训: train+val；评估: test

运行: python 03_lightgbm_rf.py
  或: python -u 03_lightgbm_rf.py   # 强制无缓冲进度输出
"""

import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

# 实时打印进度（避免管道/后台运行时 stdout 缓冲）
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit, ParameterGrid
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from lightgbm import LGBMRegressor, LGBMClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    load_train_val_test, make_regression_target, make_classification_target,
    eval_regression, eval_classification, save_json, print_tscv_fold_dates,
)


def log(msg=""):
    print(msg, flush=True)


def _merge_train_val(train_df, val_df):
    return pd.concat([train_df, val_df], ignore_index=True)


def _count_grid(param_grid):
    n = 1
    for v in param_grid.values():
        n *= len(v)
    return n


def _grid_search_with_progress(model_cls, param_grid, X, y, tscv, scoring, model_name, task, **kwargs):
    """
    手动走 ParameterGrid + TSCV，每完成一折打印进度。
    scoring: "rmse" | "f1"
    """
    from sklearn.metrics import mean_squared_error, f1_score

    params_list = list(ParameterGrid(param_grid))
    n_params = len(params_list)
    n_splits = tscv.get_n_splits()
    total_fits = n_params * n_splits
    log(f"  [{model_name} {task}] 开始搜参: {n_params} 组参数 × {n_splits} 折 = {total_fits} 次拟合")

    best_score = None
    best_params = None
    done = 0
    t0 = time.time()

    for pi, params in enumerate(params_list, 1):
        fold_scores = []
        for fi, (tr_idx, va_idx) in enumerate(tscv.split(X), 1):
            model = model_cls(**{**kwargs, **params})
            model.fit(X[tr_idx], y[tr_idx])
            pred = model.predict(X[va_idx])
            if scoring == "rmse":
                score = -float(np.sqrt(mean_squared_error(y[va_idx], pred)))  # 越大越好
            else:
                score = float(f1_score(y[va_idx], pred, average="weighted"))
            fold_scores.append(score)
            done += 1
            elapsed = time.time() - t0
            eta = elapsed / done * (total_fits - done) if done else 0
            log(
                f"  [{model_name} {task}] "
                f"进度 {done}/{total_fits} "
                f"(参数 {pi}/{n_params}, fold {fi}/{n_splits}) "
                f"score={score:.4f}  "
                f"已用 {elapsed:.0f}s  ETA {eta:.0f}s"
            )

        mean_score = float(np.mean(fold_scores))
        log(f"  [{model_name} {task}] 参数组 {pi}/{n_params} {params}  "
            f"CV均值={mean_score:.4f}")
        if best_score is None or mean_score > best_score:
            best_score = mean_score
            best_params = params

    search_time = time.time() - t0
    log(f"  [{model_name} {task}] 搜参完成 最佳={best_params}  "
        f"CV={best_score:.4f}  ({search_time:.1f}s)")
    return best_params, best_score, search_time


def _train_regression(model_cls, model_name, param_grid, train_df, val_df, test_df, **kwargs):
    log(f"\n  [{model_name} 回归] 准备数据...")
    X_cv, y_cv, dates_cv, _, _ = make_regression_target(train_df)
    X_fit, y_fit, _, _, _ = make_regression_target(_merge_train_val(train_df, val_df))
    X_test, y_test, _, _, prices_test = make_regression_target(test_df)
    tscv = TimeSeriesSplit(n_splits=5)

    log(f"  [{model_name} 回归] CV={X_cv.shape}  Final={X_fit.shape}  Test={X_test.shape}")
    log(f"  [{model_name} 回归] TimeSeriesSplit fold 日期范围:")
    print_tscv_fold_dates(dates_cv, tscv)
    sys.stdout.flush()

    best_params, _, search_time = _grid_search_with_progress(
        model_cls, param_grid, X_cv, y_cv, tscv, "rmse", model_name, "回归", **kwargs
    )

    log(f"  [{model_name} 回归] 用 train+val 重训最佳参数...")
    t1 = time.time()
    best = model_cls(**{**kwargs, **best_params})
    best.fit(X_fit, y_fit)
    train_time = search_time + (time.time() - t1)
    log(f"  [{model_name} 回归] 重训完成 ({time.time() - t1:.1f}s)")

    y_pred = best.predict(X_test)
    metrics = eval_regression(y_test, y_pred, prices_test)
    log(f"  [{model_name} 回归] RMSE={metrics['rmse']:.2f}  MAE={metrics['mae']:.2f}  "
        f"MAPE={metrics['mape']:.2f}%  R2={metrics['r2']:.4f}")
    return best, metrics, train_time


def _train_classification(model_cls, model_name, param_grid, train_df, val_df, test_df, **kwargs):
    log(f"\n  [{model_name} 分类] 准备数据...")
    X_cv, y_cv, dates_cv, _, _ = make_classification_target(train_df)
    X_fit, y_fit, _, _, _ = make_classification_target(_merge_train_val(train_df, val_df))
    X_test, y_test, _, _, _ = make_classification_target(test_df)
    tscv = TimeSeriesSplit(n_splits=5)

    log(f"  [{model_name} 分类] CV={X_cv.shape}  Final={X_fit.shape}  Test={X_test.shape}")
    log(f"  [{model_name} 分类] TimeSeriesSplit fold 日期范围:")
    print_tscv_fold_dates(dates_cv, tscv)
    sys.stdout.flush()

    best_params, _, search_time = _grid_search_with_progress(
        model_cls, param_grid, X_cv, y_cv, tscv, "f1", model_name, "分类", **kwargs
    )

    log(f"  [{model_name} 分类] 用 train+val 重训最佳参数...")
    t1 = time.time()
    best = model_cls(**{**kwargs, **best_params})
    best.fit(X_fit, y_fit)
    train_time = search_time + (time.time() - t1)
    log(f"  [{model_name} 分类] 重训完成 ({time.time() - t1:.1f}s)")

    y_pred = best.predict(X_test)
    y_proba = best.predict_proba(X_test) if hasattr(best, "predict_proba") else None
    metrics = eval_classification(y_test, y_pred, y_proba)
    auc_str = f"  AUC={metrics['auc']:.4f}" if metrics["auc"] else ""
    log(f"  [{model_name} 分类] Accuracy={metrics['accuracy']:.4f}  "
        f"F1={metrics['f1']:.4f}{auc_str}")
    return best, metrics, train_time


def main():
    log("=" * 70)
    log("任务 3: LightGBM + Random Forest")
    log("=" * 70)

    log("\n[1/5] 加载数据 (全量面板特征 + train 编码)...")
    train_df, val_df, test_df = load_train_val_test()
    log(f"  Train: {len(train_df)}  Val: {len(val_df)}  Test: {len(test_df)}")

    # 缩小网格：去掉 RF max_depth=None / 过大 n_estimators，避免单机搜参过慢
    lgb_reg_params = {"max_depth": [4, 6], "n_estimators": [100, 200], "learning_rate": [0.05, 0.1]}
    lgb_cls_params = {"max_depth": [4, 6], "n_estimators": [100, 200], "learning_rate": [0.05, 0.1]}
    rf_reg_params = {"max_depth": [8, 12], "n_estimators": [100]}
    rf_cls_params = {"max_depth": [8, 12], "n_estimators": [100]}

    log(f"  网格规模: LGB回归={_count_grid(lgb_reg_params)*5}次拟合  "
        f"LGB分类={_count_grid(lgb_cls_params)*5}  "
        f"RF回归={_count_grid(rf_reg_params)*5}  "
        f"RF分类={_count_grid(rf_cls_params)*5}")

    log("\n[2/5] LightGBM 回归...")
    _, lgb_reg_metrics, _ = _train_regression(
        LGBMRegressor, "LightGBM", lgb_reg_params, train_df, val_df, test_df,
        random_state=42, n_jobs=-1, verbose=-1,
    )

    log("\n[3/5] LightGBM 分类...")
    _, lgb_cls_metrics, _ = _train_classification(
        LGBMClassifier, "LightGBM", lgb_cls_params, train_df, val_df, test_df,
        random_state=42, n_jobs=-1, verbose=-1,
    )

    log("\n[4/5] Random Forest 回归...")
    _, rf_reg_metrics, _ = _train_regression(
        RandomForestRegressor, "RandomForest", rf_reg_params, train_df, val_df, test_df,
        random_state=42, n_jobs=-1,
    )

    log("\n[5/5] Random Forest 分类...")
    _, rf_cls_metrics, _ = _train_classification(
        RandomForestClassifier, "RandomForest", rf_cls_params, train_df, val_df, test_df,
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
    log("\n任务 3 全部完成.")
    return summary


if __name__ == "__main__":
    main()
