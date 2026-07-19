"""
SHAP 分析: XGBoost 分类模型特征重要性
========================================
对 xgb_cls.pkl 三分类 (跌/平/涨) 进行 SHAP 分析
"""
import json, sys
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_PATH = Path("d:/桌面/NUS SWS/socProject/ml/models/xgb_cls.pkl")
OUT_DIR = Path("../data/backtest")
sys.path.insert(0, str(Path("d:/桌面/NUS SWS/socProject/ml").resolve()))

from utils import load_train_val_test, make_classification_target  # noqa: E402

FEATURE_NAMES = [
    "log_price", "MA_7", "MA_30", "MA_90",
    "Return_1d", "Return_7d", "Volatility_30",
    "RSI_14", "MACD", "Volume_MA_7",
    "MA_30_dev", "BB_position", "Volume_Change_Ratio",
    "is_stattrak", "is_floor_price",
    "days_to_next_major", "days_since_last_major", "is_major_active",
    "steam_ccu", "days_since_cs2_announce",
    "weapon_type_enc", "rarity_enc", "wear_enc",
]
CLASS_NAMES = {0: "跌 (die)", 1: "平 (ping)", 2: "涨 (zhang)"}


def main():
    print("=" * 60)
    print("  SHAP 分析 — XGBoost 分类 (涨/平/跌)")
    print("=" * 60)

    # 加载
    print("\n[1/4] 加载模型...")
    bundle = joblib.load(MODEL_PATH)
    model = bundle["model"]
    print(f"  模型: XGBClassifier (max_depth={bundle['params']['max_depth']}, "
          f"n_estimators={bundle['params']['n_estimators']})")

    # 数据
    print("\n[2/4] 加载 val 数据...")
    _, val_df, _ = load_train_val_test()
    X_val, y_val, dates, skins, _ = make_classification_target(val_df)
    print(f"  Val: {X_val.shape}, 标签: {dict(zip(*np.unique(y_val, return_counts=True)))}")

    np.random.seed(42)
    n_sample = min(2000, X_val.shape[0])
    idx = np.random.choice(X_val.shape[0], n_sample, replace=False)
    X_sample = X_val[idx]
    print(f"  SHAP 采样: {n_sample}")

    # SHAP
    print("\n[3/4] 计算 SHAP 值...")
    import shap
    explainer = shap.TreeExplainer(model)
    # 多分类: XGBoost 3.x 返回 (N, F, C) 3D array; 旧版返 list of arrays
    shap_raw = explainer.shap_values(X_sample)
    if isinstance(shap_raw, list):
        # 旧版: list of (N, F) per class
        shap_values = [np.array(sv) for sv in shap_raw]  # list of (N,F)
    elif shap_raw.ndim == 3:
        # 新版: (N, F, C) → list of (N,F) per class
        shap_values = [shap_raw[:, :, c] for c in range(shap_raw.shape[2])]
    else:
        shap_values = [shap_raw]

    print(f"  SHAP: {len(shap_values)} classes × {shap_values[0].shape}")

    # --- 各类别 Top 特征 ---
    for ci, sv in enumerate(shap_values):
        mean_shap = np.abs(sv).mean(axis=0)
        ranked = sorted(zip(FEATURE_NAMES, mean_shap), key=lambda x: x[1], reverse=True)
        label = CLASS_NAMES.get(ci, f"class_{ci}")
        print(f"\n  [{label}] Top 5:")
        for i, (name, val) in enumerate(ranked[:5]):
            print(f"    {i+1}. {name:<25s} {val:.6f}")
        if ci > 2:
            break  # 只打印前3个类别

    # --- 全部类别汇总 (mean across classes) ---
    all_sv = np.stack(shap_values, axis=0)  # (C, N, F)
    mean_abs_all = np.abs(all_sv).mean(axis=(0, 1))  # mean across samples & classes
    ranked_all = sorted(zip(FEATURE_NAMES, mean_abs_all), key=lambda x: x[1], reverse=True)

    # --- 可视化 ---
    print("\n[4/4] 生成图表...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 涨类 (class 2) 最重要 — 这是我们关心的
    sv_class2 = shap_values[2]
    plt.figure(figsize=(12, 8))
    shap.summary_plot(sv_class2, X_sample, feature_names=FEATURE_NAMES,
                      show=False, max_display=20)
    plt.title("SHAP — XGBoost Classifier (class: 涨 zhang)", fontsize=14)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "shap_cls_summary.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ {OUT_DIR / 'shap_cls_summary.png'}")

    # 条形图 (全类别平均)
    plt.figure(figsize=(10, 8))
    # 手动画 bar chart for all-class average
    names_top = [n for n, _ in ranked_all[:20]]
    vals_top = [v for _, v in ranked_all[:20]]
    plt.barh(range(len(names_top)), vals_top[::-1], color="#1f77b4")
    plt.yticks(range(len(names_top)), names_top[::-1])
    plt.xlabel("mean |SHAP value| (across 3 classes)")
    plt.title("XGBoost Classifier — Feature Importance (all classes avg)")
    plt.tight_layout()
    plt.savefig(OUT_DIR / "shap_cls_bar.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✅ {OUT_DIR / 'shap_cls_bar.png'}")

    # JSON
    shap_export = {
        "model": "XGBoost Classifier",
        "classes": ["die", "ping", "zhang"],
        "n_features": len(FEATURE_NAMES),
        "n_samples": n_sample,
        "feature_importance_all_classes": [
            {"rank": i + 1, "feature": name, "mean_abs_shap": round(float(val), 6)}
            for i, (name, val) in enumerate(ranked_all)
        ],
        "top3_all": [ranked_all[0][0], ranked_all[1][0], ranked_all[2][0]],
    }
    with open(OUT_DIR / "shap_cls_results.json", "w", encoding="utf-8") as f:
        json.dump(shap_export, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {OUT_DIR / 'shap_cls_results.json'}")

    print("\n" + "=" * 60)
    print(f"  SHAP 分类分析完成! Top 3 (全类别): {shap_export['top3_all']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
