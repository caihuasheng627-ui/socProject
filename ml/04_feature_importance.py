"""
任务 4: 特征重要性分析
======================
对 XGBoost 和 LightGBM 提取 feature_importances_,
输出前端 SHAP 格式的 JSON (8 个核心特征)。

运行: python 04_feature_importance.py
"""

import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import (
    load_and_prepare, make_regression_target, FEATURE_COLS,
    SHAP_FEATURE_NAMES, save_json, OUTPUT_DIR,
)

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def extract_importance(model, feature_names, model_name):
    """提取特征重要性并归一化"""
    importances = model.feature_importances_
    imp_dict = dict(zip(feature_names, importances))

    result = []
    for fname in SHAP_FEATURE_NAMES:
        idx = FEATURE_COLS.index(fname) if fname in FEATURE_COLS else None
        val = float(importances[idx]) if idx is not None else 0.0
        result.append({"feature": fname, "importance": val})

    total = sum(r["importance"] for r in result) or 1.0
    for r in result:
        r["importance"] = round(r["importance"] / total, 4)
    result.sort(key=lambda x: x["importance"], reverse=True)
    return result


def plot_importance(importances, model_name, filename):
    """画水平柱状图"""
    fig, ax = plt.subplots(figsize=(8, 5))
    features = [r["feature"] for r in importances]
    values = [r["importance"] for r in importances]
    colors = ["#ff6b00" if v == max(values) else "#378ADD" for v in values]
    y_pos = range(len(features))
    ax.barh(y_pos, values, color=colors, edgecolor="white", height=0.6)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(features, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlabel("Feature Importance", fontsize=12)
    ax.set_title(f"{model_name} -- Feature Importance", fontsize=14, fontweight="bold")
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#444")
    ax.xaxis.label.set_color("white")
    ax.title.set_color("white")
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, filename)
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close()
    print(f"  -> 图表已保存: {path}")


def main():
    print("=" * 70)
    print("任务 4: 特征重要性分析")
    print("=" * 70)

    print("\n[1/4] 加载数据并训练 XGBoost...")
    from xgboost import XGBRegressor
    train_df = load_and_prepare("train")
    X_train, y_train, _, _, _ = make_regression_target(train_df)
    xgb_model = XGBRegressor(max_depth=6, n_estimators=200, learning_rate=0.1,
                             random_state=42, n_jobs=-1, tree_method="hist")
    xgb_model.fit(X_train, y_train)

    print("[2/4] 训练 LightGBM...")
    from lightgbm import LGBMRegressor
    lgb_model = LGBMRegressor(max_depth=6, n_estimators=200, learning_rate=0.1,
                              random_state=42, n_jobs=-1, verbose=-1)
    lgb_model.fit(X_train, y_train)

    print("[3/4] 提取特征重要性...")
    xgb_imp = extract_importance(xgb_model, FEATURE_COLS, "XGBoost")
    lgb_imp = extract_importance(lgb_model, FEATURE_COLS, "LightGBM")

    print("\n  XGBoost 特征重要性:")
    for r in xgb_imp:
        print(f"    {r['feature']:25s}  {r['importance']:.4f}")
    print("\n  LightGBM 特征重要性:")
    for r in lgb_imp:
        print(f"    {r['feature']:25s}  {r['importance']:.4f}")

    print("\n[4/4] 画图 + 保存 JSON...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    plot_importance(xgb_imp, "XGBoost", "shap_xgboost.png")
    plot_importance(lgb_imp, "LightGBM", "shap_lightgbm.png")

    avg_imp = []
    for i, fname in enumerate(SHAP_FEATURE_NAMES):
        x_val = next(r["importance"] for r in xgb_imp if r["feature"] == fname)
        l_val = next(r["importance"] for r in lgb_imp if r["feature"] == fname)
        avg_imp.append({"feature": fname, "importance": round((x_val + l_val) / 2, 4)})
    avg_imp.sort(key=lambda x: x["importance"], reverse=True)

    output = {
        "xgboost": xgb_imp,
        "lightgbm": lgb_imp,
        "average": avg_imp,
    }
    save_json(output, "shap_features.json")
    return output


if __name__ == "__main__":
    main()
