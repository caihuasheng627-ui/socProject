"""
make_predictions_trees: ARIMA / XGBoost / LightGBM / RF 预测导出 CSV
====================================================================
给 backtest.py 生产与组员 1 相同格式的输入:

  date, market_hash_name, current_price, predicted_price

约定:
  - date = 决策日
  - current_price = 决策日真实价 (USD)
  - predicted_price = 决策日起 7 天后预测价 (USD, log1p 训练 → expm1 还原)
  - 默认导出 split=val (与 preds/pred_lstm_c.csv 对齐); --split test 可改
  - 树模型: 预测 val 时用 train 终训; 预测 test 时用 train+val 终训 (防泄漏)
  - ARIMA: 代表性 5 件 walk-forward (与 01_arima_baseline 同口径)
  - 顺带把树模型 .pkl 存到 models/ 供组员 3 加载

运行:
  python make_predictions_trees.py                  # 四模型, val
  python make_predictions_trees.py --gpu            # XGB/LGB 尽量走 CUDA (RF/ARIMA 仍 CPU)
  python make_predictions_trees.py --split test     # 主表 A 同口径用 test
  python make_predictions_trees.py --models xgb,lgb # 只跑指定模型
  python make_predictions_trees.py --skip-arima     # 跳过 ARIMA (较慢)

GPU 说明:
  - XGBoost: 官方 pip 包支持 device=cuda (需本机 NVIDIA + CUDA)
  - LightGBM: pip 默认多为 CPU 版; GPU 失败会自动回退 CPU
  - RandomForest / ARIMA: 无 GPU 路径, 想加速请 --skip-arima 或少跑 rf

输出:
  preds/pred_arima.csv
  preds/pred_xgboost.csv
  preds/pred_lightgbm.csv
  preds/pred_rf.csv
  models/xgb_reg.pkl  lightgbm_reg.pkl  rf_reg.pkl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor

warnings.filterwarnings("ignore")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (  # noqa: E402
    DATA_DIR,
    OUTPUT_DIR,
    load_and_prepare,
    load_train_val_test,
    make_regression_target,
    select_representative_skins,
    eval_regression,
)

BASE = Path(__file__).resolve().parent
PRED_DIR = BASE / "preds"
MODEL_DIR = BASE / "models"
HORIZON = 7

# 与 02/03 搜参网格中部一致的默认超参 (跳过 GridSearch, 导出可几分钟内跑完)
DEFAULT_PARAMS = {
    "xgb": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
    "lgb": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
    "rf": {"max_depth": 12, "n_estimators": 100},
}

MODEL_ALIASES = {
    "arima": "arima",
    "xgb": "xgb",
    "xgboost": "xgb",
    "lgb": "lgb",
    "lightgbm": "lgb",
    "rf": "rf",
    "randomforest": "rf",
    "random_forest": "rf",
}


def log(msg: str = "") -> None:
    print(msg, flush=True)


def detect_cuda() -> bool:
    """探测本机是否有可用 CUDA (优先 torch, 其次 nvidia-smi)。"""
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            log(f"  CUDA: torch 可见 → {name}")
            return True
    except Exception:
        pass

    try:
        import subprocess

        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            log(f"  CUDA: nvidia-smi 可见 → {r.stdout.strip().splitlines()[0]}")
            return True
    except Exception:
        pass

    log("  CUDA: 未检测到可用 GPU")
    return False


def xgb_device_kwargs(use_gpu: bool) -> dict:
    """XGBoost 2.x 用 device=cuda; 旧版回退 tree_method=gpu_hist。"""
    if not use_gpu:
        return {"tree_method": "hist"}
    # 新 API
    try:
        import xgboost as xgb

        ver = tuple(int(x) for x in xgb.__version__.split(".")[:2])
        if ver >= (2, 0):
            return {"tree_method": "hist", "device": "cuda"}
    except Exception:
        pass
    return {"tree_method": "gpu_hist"}


def lgb_device_kwargs(use_gpu: bool) -> dict:
    if not use_gpu:
        return {}
    # LightGBM 新版 device=gpu; 旧版 device_type=gpu
    return {"device": "gpu"}


def _merge_train_val(train_df, val_df):
    return pd.concat([train_df, val_df], ignore_index=True)


def export_csv(df: pd.DataFrame, path: Path) -> None:
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    out = df[["date", "market_hash_name", "current_price", "predicted_price"]].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["current_price"] = out["current_price"].astype(float)
    out["predicted_price"] = np.maximum(out["predicted_price"].astype(float), 0.01)
    out.to_csv(path, index=False)
    log(f"  ✅ {path.relative_to(BASE)}  ({len(out):,} 行, {out['market_hash_name'].nunique()} 件)")


def _fit_frame(train_df, val_df, split: str):
    """预测 split 时, 终训集不包含该 split。"""
    if split == "val":
        return train_df
    if split == "test":
        return _merge_train_val(train_df, val_df)
    raise ValueError(f"不支持的 split: {split}")


def _predict_frame(train_df, val_df, test_df, split: str):
    if split == "val":
        return val_df
    if split == "test":
        return test_df
    raise ValueError(f"不支持的 split: {split}")


def train_and_export_tree(
    name: str,
    model_cls,
    params: dict,
    train_df,
    val_df,
    test_df,
    split: str,
    out_csv: Path,
    out_pkl: Path,
    fit_kwargs: dict | None = None,
    cpu_fallback_kwargs: dict | None = None,
):
    log(f"\n{'=' * 60}")
    log(f"{name}: 终训 → 预测 {split} → 导出 CSV")
    log(f"{'=' * 60}")

    fit_df = _fit_frame(train_df, val_df, split)
    pred_df = _predict_frame(train_df, val_df, test_df, split)

    X_fit, y_fit, _, _, _ = make_regression_target(fit_df)
    X_pred, y_true_log, dates, skins, prices = make_regression_target(pred_df)
    fit_kwargs = dict(fit_kwargs or {})
    log(f"  fit={X_fit.shape}  predict={X_pred.shape}  params={params}")
    log(f"  device kwargs: {fit_kwargs or '(cpu default)'}")

    t0 = time.time()
    model = model_cls(random_state=42, n_jobs=-1, **params, **fit_kwargs)
    try:
        model.fit(X_fit, y_fit)
    except Exception as e:
        if not cpu_fallback_kwargs:
            raise
        log(f"  ⚠ GPU/设备训练失败, 回退 CPU: {e}")
        fit_kwargs = dict(cpu_fallback_kwargs)
        model = model_cls(random_state=42, n_jobs=-1, **params, **fit_kwargs)
        model.fit(X_fit, y_fit)
    log(f"  训练完成 ({time.time() - t0:.1f}s)")

    y_pred_log = model.predict(X_pred)
    y_pred_price = np.maximum(np.expm1(y_pred_log), 0.01)
    metrics = eval_regression(y_true_log, y_pred_log, prices)
    log(
        f"  [{split}] RMSE={metrics['rmse']:.2f}  MAE={metrics['mae']:.2f}  "
        f"MAPE={metrics['mape']:.2f}%  R2={metrics['r2']:.4f}"
    )

    out = pd.DataFrame(
        {
            "date": dates,
            "market_hash_name": skins,
            "current_price": prices,
            "predicted_price": y_pred_price,
        }
    )
    export_csv(out, out_csv)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "name": name,
            "params": params,
            "fit_kwargs": fit_kwargs,
            "fit_split": "train" if split == "val" else "train+val",
            "predict_split": split,
            "horizon": HORIZON,
        },
        out_pkl,
    )
    log(f"  ✅ {out_pkl.relative_to(BASE)}")
    return metrics


def _load_raw_split(split: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(DATA_DIR, f"{split}.csv"), parse_dates=["date"])


def _arima_skins() -> list[str]:
    """优先用已跑过的 arima_results.json 里的 5 件, 否则按价格分位重选。"""
    path = os.path.join(OUTPUT_DIR, "arima_results.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        skins = [r["skin"] for r in data.get("per_skin", []) if r.get("skin")]
        if skins:
            return skins
    train_feat = load_and_prepare("train")
    return select_representative_skins(train_feat, n=5)


def run_arima_export(split: str, eval_step: int, out_csv: Path) -> dict | None:
    """
    Walk-forward 导出 ARIMA 预测行。
    在指定 split 段每个评估日 t: 用截至 t 的历史 → 预测 t+7 价。
    """
    from pmdarima import auto_arima
    from sklearn.metrics import (
        mean_absolute_error,
        mean_squared_error,
        r2_score,
    )

    log(f"\n{'=' * 60}")
    log(f"ARIMA: walk-forward 预测 {split} (step={eval_step})")
    log(f"{'=' * 60}")

    train_raw = _load_raw_split("train")
    val_raw = _load_raw_split("val")
    test_raw = _load_raw_split("test")
    skins = _arima_skins()
    log(f"  饰品 ({len(skins)}): {skins}")

    all_rows = []
    per_skin = []

    for skin_name in skins:
        parts = []
        for split_name, df in [("train", train_raw), ("val", val_raw), ("test", test_raw)]:
            sub = df[df["market_hash_name"] == skin_name][["date", "price"]].copy()
            if len(sub) == 0:
                continue
            sub["_split"] = split_name
            parts.append(sub)

        if not parts:
            log(f"  !! 跳过(无数据): {skin_name}")
            continue

        series = pd.concat(parts, ignore_index=True).sort_values("date").reset_index(drop=True)
        prices = series["price"].values.astype(float)
        dates = series["date"].values
        splits = series["_split"].values

        eval_idx = np.where(splits == split)[0]
        # 终训历史至少 30 天; t+7 须仍在序列内
        max_t = len(prices) - HORIZON - 1
        eval_starts = [i for i in eval_idx if i <= max_t and i >= 30]
        if not eval_starts:
            log(f"  !! 跳过(评估点不足): {skin_name}")
            continue
        eval_starts = eval_starts[:: max(1, eval_step)]

        try:
            hist0 = prices[: eval_starts[0] + 1]
            model = auto_arima(
                hist0,
                seasonal=False,
                stepwise=True,
                suppress_warnings=True,
                max_p=5,
                max_q=5,
                max_d=2,
                error_action="ignore",
                trace=False,
            )
            order = model.order
            last_end = eval_starts[0]
            y_true_list, y_pred_list = [], []

            for t in eval_starts:
                if t > last_end:
                    new_obs = prices[last_end + 1 : t + 1]
                    if len(new_obs) > 0:
                        try:
                            model.update(new_obs)
                        except Exception:
                            model = auto_arima(
                                prices[: t + 1],
                                seasonal=False,
                                stepwise=True,
                                suppress_warnings=True,
                                max_p=5,
                                max_q=5,
                                max_d=2,
                                error_action="ignore",
                                trace=False,
                                start_p=order[0],
                                start_q=order[2],
                                d=order[1],
                            )
                    last_end = t

                fc = model.predict(n_periods=HORIZON)
                pred = float(np.maximum(fc[-1], 0.01))
                true = float(prices[t + HORIZON])
                y_pred_list.append(pred)
                y_true_list.append(true)
                all_rows.append(
                    {
                        "date": dates[t],
                        "market_hash_name": skin_name,
                        "current_price": float(prices[t]),
                        "predicted_price": pred,
                    }
                )

            y_true = np.array(y_true_list)
            y_pred = np.array(y_pred_list)
            rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
            mae = float(mean_absolute_error(y_true, y_pred))
            mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 0.01))) * 100)
            r2 = float(r2_score(y_true, y_pred)) if len(y_true) > 2 else 0.0
            log(
                f"  {skin_name[:45]:45s}  RMSE={rmse:10.2f}  MAE={mae:10.2f}  "
                f"MAPE={mape:6.2f}%  n={len(y_true)}  order={order}"
            )
            per_skin.append(
                {
                    "skin": skin_name,
                    "rmse": rmse,
                    "mae": mae,
                    "mape": mape,
                    "r2": r2,
                    "order": list(order),
                    "n_eval": len(y_true),
                }
            )
        except Exception as e:
            log(f"  {skin_name[:45]:45s}  FAILED: {e}")

    if not all_rows:
        log("  !! ARIMA 没有产出任何预测行")
        return None

    export_csv(pd.DataFrame(all_rows), out_csv)
    summary = {
        "rmse": float(np.mean([r["rmse"] for r in per_skin])),
        "mae": float(np.mean([r["mae"] for r in per_skin])),
        "mape": float(np.mean([r["mape"] for r in per_skin])),
        "r2": float(np.mean([r["r2"] for r in per_skin])),
        "n_rows": len(all_rows),
        "per_skin": per_skin,
    }
    log(
        f"  ARIMA 平均: RMSE={summary['rmse']:.2f}  MAE={summary['mae']:.2f}  "
        f"MAPE={summary['mape']:.2f}%  R2={summary['r2']:.4f}"
    )
    return summary


def parse_models(raw: str | None, skip_arima: bool) -> list[str]:
    if raw:
        models = []
        for tok in raw.split(","):
            key = tok.strip().lower()
            if not key:
                continue
            if key not in MODEL_ALIASES:
                raise SystemExit(f"未知模型: {tok}  (可选: arima,xgb,lgb,rf)")
            models.append(MODEL_ALIASES[key])
        # 去重保序
        seen = set()
        ordered = []
        for m in models:
            if m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered

    models = ["xgb", "lgb", "rf"]
    if not skip_arima:
        models = ["arima"] + models
    return models


def main():
    parser = argparse.ArgumentParser(description="导出四模型预测 CSV (回测用)")
    parser.add_argument(
        "--split",
        choices=["val", "test"],
        default="val",
        help="导出哪个 split 的预测 (默认 val, 与 pred_lstm_c.csv 对齐)",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="逗号分隔: arima,xgb,lgb,rf (默认全跑)",
    )
    parser.add_argument("--skip-arima", action="store_true", help="跳过 ARIMA")
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="XGBoost/LightGBM 尽量用 CUDA (RF/ARIMA 仍 CPU; 失败自动回退)",
    )
    parser.add_argument(
        "--arima-step",
        type=int,
        default=1,
        help="ARIMA 评估步长 (默认 1=逐日; 01 脚本用 7 加速)",
    )
    args = parser.parse_args()
    models = parse_models(args.models, args.skip_arima)

    log("=" * 60)
    log("组员 2 — 四模型预测导出")
    log(f"  split={args.split}  models={models}  gpu={args.gpu}")
    log("=" * 60)

    use_gpu = False
    if args.gpu:
        log("\n[GPU] 探测 CUDA...")
        use_gpu = detect_cuda()
        if not use_gpu:
            log("  → 将全部使用 CPU (加 --gpu 但无可用 CUDA)")
        else:
            log("  → XGBoost/LightGBM 将尝试 GPU; RF/ARIMA 仍为 CPU")

    need_trees = any(m in models for m in ("xgb", "lgb", "rf"))
    train_df = val_df = test_df = None
    if need_trees:
        log("\n[数据] 加载 train/val/test 面板特征...")
        train_df, val_df, test_df = load_train_val_test()
        log(f"  Train={len(train_df)}  Val={len(val_df)}  Test={len(test_df)}")

    results = {}

    if "arima" in models:
        results["ARIMA"] = run_arima_export(
            args.split, args.arima_step, PRED_DIR / "pred_arima.csv"
        )

    if "xgb" in models:
        xgb_kw = xgb_device_kwargs(use_gpu)
        results["XGBoost"] = train_and_export_tree(
            "XGBoost",
            XGBRegressor,
            DEFAULT_PARAMS["xgb"],
            train_df,
            val_df,
            test_df,
            args.split,
            PRED_DIR / "pred_xgboost.csv",
            MODEL_DIR / "xgb_reg.pkl",
            fit_kwargs=xgb_kw,
            cpu_fallback_kwargs={"tree_method": "hist"} if use_gpu else None,
        )

    if "lgb" in models:
        lgb_kw = {"verbose": -1, **lgb_device_kwargs(use_gpu)}
        results["LightGBM"] = train_and_export_tree(
            "LightGBM",
            LGBMRegressor,
            DEFAULT_PARAMS["lgb"],
            train_df,
            val_df,
            test_df,
            args.split,
            PRED_DIR / "pred_lightgbm.csv",
            MODEL_DIR / "lightgbm_reg.pkl",
            fit_kwargs=lgb_kw,
            cpu_fallback_kwargs={"verbose": -1} if use_gpu else None,
        )

    if "rf" in models:
        if use_gpu:
            log("\n  (提示) RandomForest 无 sklearn GPU 路径, 仍用 CPU")
        results["RandomForest"] = train_and_export_tree(
            "RandomForest",
            RandomForestRegressor,
            DEFAULT_PARAMS["rf"],
            train_df,
            val_df,
            test_df,
            args.split,
            PRED_DIR / "pred_rf.csv",
            MODEL_DIR / "rf_reg.pkl",
            fit_kwargs={},
        )

    log("\n" + "=" * 60)
    log("导出完成 — 交给组员 1 跑回测:")
    log("  python backtest.py \\")
    log("    XGBoost=preds/pred_xgboost.csv \\")
    log("    LightGBM=preds/pred_lightgbm.csv \\")
    log("    RF=preds/pred_rf.csv \\")
    log("    ARIMA=preds/pred_arima.csv")
    log("=" * 60)

    # 简要汇总写到 outputs, 方便核对
    summary_path = Path(OUTPUT_DIR) / "tree_pred_export_summary.json"
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    serializable = {}
    for k, v in results.items():
        if v is None:
            serializable[k] = None
        else:
            serializable[k] = {
                key: val
                for key, val in v.items()
                if key in ("rmse", "mae", "mape", "r2", "n_rows")
            }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {"split": args.split, "models": models, "metrics": serializable},
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"  汇总: {summary_path}")


if __name__ == "__main__":
    main()
