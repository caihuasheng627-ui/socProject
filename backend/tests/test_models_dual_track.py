import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.modules.pop("config", None)

import main


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_model_comparison_returns_isolated_historical_and_online_tracks(monkeypatch, tmp_path):
    write_json(tmp_path / "compare_results_test.json", {
        "horizon_steps": 7,
        "models": {"Hybrid": {"rmse": 50, "mae": 7, "mape": 8, "r2": 0.9}},
    })
    write_json(tmp_path / "online_model_comparison.json", {
        "track": "online", "dateRange": {"start": "2026-06-28", "end": "2026-07-15"},
        "items": 665, "decisions": 9967, "modelVersion": "hybrid-v2-test",
        "models": {"Hybrid-V2-Calibrated": {
            "rmse": 27.7, "mae": 6.45, "mapePct": 6.88, "r2": 0.95,
        }},
    })
    write_json(tmp_path / "backtest_online" / "backtest_results.json", {
        "fee_0.0000": {
            "Hybrid-V2-Calibrated": {"returnPct": 3.63, "maxDrawdownPct": -0.38},
        },
    })
    write_json(tmp_path / "trend_30d_results_test.json", {
        "split": "test", "items": 155, "rows": 31757,
        "overall": {"rmse": 46.9, "mae": 7.68, "mape_pct": 57.9, "r2": 0.92},
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    result = main.models_comparison()

    assert result["tracks"]["historical"]["regression"][0]["name"] == "Hybrid"
    online = result["tracks"]["online"]
    assert online["regression"][0]["name"] == "Hybrid-V2-Calibrated"
    assert online["regression"][0]["returnPct"] == 3.63
    assert online["metadata"]["items"] == 665
    assert online["trend30"]["name"] == "Keras-Seq2Seq-30D"
    assert result["regression"] == result["tracks"]["historical"]["regression"]


def test_online_backtest_never_falls_back_to_historical_hybrid(monkeypatch, tmp_path):
    write_json(tmp_path / "backtest_online" / "backtest_curves.json", {
        "fee_0.0000": {
            "Hybrid-V2-Calibrated": [
                {"date": "2026-07-01", "capital": 10000},
                {"date": "2026-07-02", "capital": 10100},
            ]
        },
        "buy_hold": [
            {"date": "2026-07-01", "capital": 10000},
            {"date": "2026-07-02", "capital": 10050},
        ],
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    result = main.models_backtest(days=60, track="online")

    assert result["track"] == "online"
    assert "Hybrid-V2-Calibrated" in result["series"]
    assert "Hybrid" not in result["series"]



def test_models_shap_prefers_current_held_out_shap_contract(monkeypatch, tmp_path):
    write_json(tmp_path / "shap_features.json", {
        "contractVersion": "tree-shap-v2",
        "models": {
            "xgboost": {
                "modelFitSplit": "train+val",
                "explanationSplit": "test",
                "features": [
                    {"feature": "MA_90", "importance": 0.75},
                    {"feature": "log_price", "importance": 0.25},
                ],
            },
        },
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    result = main.models_shap("xgboost")

    assert result[0]["feature"] == "MA_90"
    assert abs(result[0]["importance"] - 0.75) < 1e-9
    assert abs(result[1]["importance"] - 0.25) < 1e-9


def test_models_shap_prefers_real_shap_results(monkeypatch, tmp_path):
    write_json(tmp_path / "shap_results.json", {
        "model": "XGBoost Regression",
        "feature_importance": [
            {"rank": 1, "feature": "MA_90", "mean_abs_shap": 0.867687},
            {"rank": 2, "feature": "log_price", "mean_abs_shap": 0.363003},
        ],
    })
    # 旧 mock 文件不应覆盖真实 SHAP
    write_json(tmp_path / "shap_features.json", {
        "xgboost": [{"feature": "steam_ccu", "importance": 0.99}],
        "lightgbm": [{"feature": "MACD", "importance": 0.5}],
        "average": [{"feature": "steam_ccu", "importance": 0.7}],
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    rows = main.models_shap("xgboost")

    assert rows[0]["feature"] == "MA_90"
    assert abs(rows[0]["importance"] - 0.867687) < 1e-9
    assert rows[0]["meanAbsShap"] == rows[0]["importance"]
    assert all("steam_ccu" != r["feature"] for r in rows)


def test_models_shap_adapts_regression_shap_result_before_legacy_fallback(monkeypatch, tmp_path):
    write_json(tmp_path / "shap_results.json", {
        "model": "XGBoost Regression",
        "model_fit_split": "train+val",
        "explanation_split": "test",
        "feature_importance": [
            {"rank": 1, "feature": "MA_90", "mean_abs_shap": 2.0},
            {"rank": 2, "feature": "log_price", "mean_abs_shap": 1.0},
        ],
    })
    write_json(tmp_path / "shap_features.json", {
        "xgboost": [{"feature": "stale", "importance": 1.0}],
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    result = main.models_shap("xgboost")

    assert result[0]["feature"] == "MA_90"
    assert abs(result[0]["importance"] - 2.0) < 1e-9
    assert abs(result[1]["importance"] - 1.0) < 1e-9
    assert all(r["feature"] != "stale" for r in result)


def test_models_shap_average_uses_real_artifacts_when_available(monkeypatch, tmp_path):
    write_json(tmp_path / "shap_results.json", {
        "feature_importance": [
            {"feature": "MA_90", "mean_abs_shap": 0.8},
            {"feature": "log_price", "mean_abs_shap": 0.4},
        ],
    })
    write_json(tmp_path / "shap_lightgbm_results.json", {
        "feature_importance": [
            {"feature": "MA_90", "mean_abs_shap": 0.2},
            {"feature": "MACD", "mean_abs_shap": 0.6},
        ],
    })
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    rows = main.models_shap("average")
    by_name = {r["feature"]: r["importance"] for r in rows}

    assert abs(by_name["MA_90"] - 0.5) < 1e-9
    assert abs(by_name["log_price"] - 0.4) < 1e-9
    assert abs(by_name["MACD"] - 0.6) < 1e-9


def test_frontend_labels_legacy_hybrid_as_historical_only():
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    markup = (root / "index.html").read_text(encoding="utf-8")
    messages = (root / "i18n.js").read_text(encoding="utf-8")

    assert "models.hybridHistoricalName" in markup
    assert "Hybrid V1（历史）" in messages
    assert "当前部署请查看“当前线上模型”中的 Hybrid V2" in messages

