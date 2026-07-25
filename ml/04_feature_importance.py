"""Backward-compatible entry point for held-out tree SHAP analysis.

Run ``python 04_feature_importance.py`` as before. The implementation now
delegates to the canonical SHAP refresher and no longer trains temporary
models or exports ``feature_importances_`` under a SHAP label.
"""

from refresh_shap import refresh


def main():
    return refresh(split="test")


if __name__ == "__main__":
    result = main()
    print(f"wrote tree SHAP v2 for {', '.join(result['models'])}")
