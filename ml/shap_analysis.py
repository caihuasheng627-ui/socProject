"""
SHAP 分析: XGBoost 回归模型特征重要性
======================================
team_tasks.md 第 7 步: 对 XGBoost 回归模型做 SHAP 特征重要性
分类模型不存在 (xgb_cls.pkl 未训练), 本次只做回归

输出:
  - ../data/backtest/shap_summary.png   (蜂群图)
  - ../data/backtest/shap_bar.png       (条形图)
  - ../data/backtest/shap_results.json  (SHAP 值汇总)
"""
import argparse
import json
import sys
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_PATH = BASE_DIR / "models" / "xgb_reg.pkl"
OUT_DIR = BASE_DIR / "outputs"

from tree_features import (  # noqa: E402
    assert_held_out,
    load_tree_split,
    regression_arrays,
)
from model_features import TREE_FEATURE_COLS  # noqa: E402

# Keep plotting labels aligned exactly with the tree model input order.
FEATURE_NAMES = TREE_FEATURE_COLS


def main(model_path=MODEL_PATH, split="test", allow_in_sample=False):
    print("=" * 60)
    print("  SHAP 分析 — XGBoost 回归")
    print("=" * 60)

    # ---------- 第 1 步: 加载模型 ----------
    print("\n[1/4] 加载 XGBoost 模型...")
    bundle = joblib.load(model_path)
    model = bundle["model"]
    print(f"  模型: XGBRegressor (max_depth={bundle['params']['max_depth']}, "
          f"n_estimators={bundle['params']['n_estimators']})")
    print(f"  训练 split: {bundle['fit_split']}")

    # ---------- 第 2 步: 加载数据 (val set 用于解释) ----------
    fit_split = bundle.get("fit_split", "unknown")
    assert_held_out(split, fit_split, allow_in_sample)
    print(f"\n[2/4] 加载 {split} 数据 (SHAP 用)...")
    split_df = load_tree_split(DATA_DIR, split)
    X_val, y_val, dates, skins, prices = regression_arrays(split_df)
    print(f"  {split} samples: {X_val.shape[0]}, Features: {X_val.shape[1]}")

    # 随机采样 2000 条加速 SHAP (全量 3.5 万条太慢)
    np.random.seed(42)
    n_sample = min(2000, X_val.shape[0])
    idx = np.random.choice(X_val.shape[0], n_sample, replace=False)
    X_sample = X_val[idx]
    print(f"  SHAP 采样: {n_sample} 条")

    # ---------- 第 3 步: SHAP TreeExplainer ----------
    print("\n[3/4] 计算 SHAP 值...")
    import shap
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)
    print(f"  SHAP values shape: {shap_values.shape}")

    # --- 特征重要性 (mean |SHAP|) ---
    mean_shap = np.abs(shap_values).mean(axis=0)
    ranked = sorted(
        zip(FEATURE_NAMES, mean_shap),
        key=lambda x: x[1], reverse=True
    )

    print("\n  Top 10 特征 (mean |SHAP|):")
    for i, (name, val) in enumerate(ranked[:10]):
        print(f"    {i+1:>2}. {name:<25s} {val:.6f}")

    # ---------- 第 4 步: 可视化 + 导出 ----------
    print("\n[4/4] 生成图表...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- 蜂群图 ---
    plt.figure(figsize=(12, 8))
    shap.summary_plot(shap_values, X_sample, feature_names=FEATURE_NAMES,
                      show=False, max_display=20)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "shap_summary.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ {OUT_DIR / 'shap_summary.png'}")

    # --- 条形图 (mean |SHAP|) ---
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, X_sample, feature_names=FEATURE_NAMES,
                      plot_type="bar", show=False, max_display=20)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "shap_bar.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ {OUT_DIR / 'shap_bar.png'}")

    # --- JSON 导出 (前端模型实验室用) ---
    feature_importance = [
        {"rank": i + 1, "feature": name, "mean_abs_shap": round(float(val), 6)}
        for i, (name, val) in enumerate(ranked)
    ]
    shap_export = {
        "model": "XGBoost Regression",
        "model_fit_split": fit_split,
        "explanation_split": split,
        "n_features": len(FEATURE_NAMES),
        "n_samples": n_sample,
        "feature_importance": feature_importance,
        "top3_features": [ranked[0][0], ranked[1][0], ranked[2][0]],
    }
    with open(OUT_DIR / "shap_results.json", "w", encoding="utf-8") as f:
        json.dump(shap_export, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {OUT_DIR / 'shap_results.json'}")

    # 同步写入 shap_features.json（兼容旧 API / 前端回退）
    compat_rows = [
        {"feature": r["feature"], "importance": r["mean_abs_shap"]}
        for r in feature_importance
    ]
    shap_features_path = OUT_DIR / "shap_features.json"
    shap_features = {}
    if shap_features_path.exists():
        try:
            shap_features = json.loads(shap_features_path.read_text(encoding="utf-8"))
        except Exception:
            shap_features = {}
    if not isinstance(shap_features, dict):
        shap_features = {}
    shap_features["xgboost"] = compat_rows
    with open(shap_features_path, "w", encoding="utf-8") as f:
        json.dump(shap_features, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {shap_features_path} (xgboost synced)")

    # ---------- 总结 ----------
    print("\n" + "=" * 60)
    print("  SHAP 分析完成!")
    print(f"  Top 3 特征: {shap_export['top3_features']}")
    print("  ⚠️ XGBoost 分类模型不存在 (xgb_cls.pkl), 未做分类 SHAP")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--split", choices=("val", "test"), default="test")
    parser.add_argument("--allow-in-sample", action="store_true")
    args = parser.parse_args()
    main(args.model_path, args.split, args.allow_in_sample)
