"""
CSVest — 模型加载 + 推理(组员 3 主线第 3 步)
=====================================================
复用组员 1 的 LSTM-C/D/GRU 模型产物,实现 Hybrid 路由实时推理:
  默认(与 ml/models/lstm_hybrid_route.json 对齐):
    low  → LSTM-C
    mid/high → LSTM-D

推理管线(与 ml/make_predictions.py 对齐):
  1. 读 train+val+test 全量面板 → build_features
  2. 取该物品最近 60 天窗口 (LOOKBACK=60, 含决策日)
  3. x_scaler.transform → 模型 predict → y_scaler.inverse_transform → expm1 还原 USD 价
  4. Hybrid: 读 lstm_hybrid_route.json 路由(缺失则用默认 low→C)

降级:
  - TensorFlow / 模型文件缺失 → Hybrid 退化为"近 7 日趋势外推" Mock
  - 树模型 → 读 ml/preds/pred_*_{test,val}.csv 或旧 pred_*.csv
  - 未知物品: LSTM-C 使用 __UNK__(若 item_map 含该键)

缓存:面板 DataFrame 进程级单例(启动算一次);predictions 表缓存由 main.py 调用层处理。
"""
from __future__ import annotations

import os
import sys
import threading
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import DATA_DIR, MODEL_DIR, PRED_DIR, FORECAST_HORIZON, LOOKBACK, ML_DIR

# 静音 TF 日志
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
warnings.filterwarnings("ignore")

# 让 import 能找到 ml/ 下的 feature_engineering / train_lstm_c
sys.path.insert(0, str(ML_DIR))


# ============================================================
# 面板特征缓存(进程级单例)
# ============================================================
_PANEL_LOCK = threading.Lock()
_PANEL_CACHE: pd.DataFrame | None = None
_PANEL_FEATURE_COLS: list[str] | None = None
_RAW_PRICE_CACHE: pd.DataFrame | None = None


def _load_panel() -> tuple[pd.DataFrame, list[str]]:
    """读三份 CSV → build_features → 缓存。返回 (面板, FEATURE_COLS)。"""
    global _PANEL_CACHE, _PANEL_FEATURE_COLS
    if _PANEL_CACHE is not None:
        return _PANEL_CACHE, _PANEL_FEATURE_COLS  # type: ignore[return-value]
    with _PANEL_LOCK:
        if _PANEL_CACHE is not None:
            return _PANEL_CACHE, _PANEL_FEATURE_COLS  # type: ignore[return-value]
        from feature_engineering import build_features
        from train_lstm_c import FEATURE_COLS

        frames = []
        for split in ("train", "val", "test"):
            p = DATA_DIR / f"{split}.csv"
            if p.exists():
                df = pd.read_csv(p, parse_dates=["date"])
                df["_split"] = split
                frames.append(df)
        panel = pd.concat(frames, ignore_index=True)
        panel = build_features(panel, drop_na_target=False)
        _PANEL_CACHE = panel
        _PANEL_FEATURE_COLS = list(FEATURE_COLS)
        print(f"[model_loader] 面板就绪: {len(panel):,} 行, {len(FEATURE_COLS)} 特征")
        return panel, list(FEATURE_COLS)


def _skin_window(market_hash_name: str) -> tuple[np.ndarray, float, str] | None:
    """取该物品最近 LOOKBACK 天特征窗口 + 决策日当前价 + 决策日日期。"""
    panel, feat_cols = _load_panel()
    g = panel[panel["market_hash_name"] == market_hash_name].sort_values("date")
    if len(g) < LOOKBACK + 1:
        return None
    feat = g[feat_cols].values.astype(np.float32)
    price = g["price"].values
    dates = g["date"].values
    # v4 契约(forecast_contract.build_sequence_windows):窗口含决策日
    # X = features[i-LOOKBACK+1 : i+1], current = price[i], 预测第 7 个后续观测
    i = len(g) - 1
    X = feat[i - LOOKBACK + 1:i + 1][None, ...]    # (1, 60, F) 含决策日
    cur_price = float(price[i])
    cur_date = str(pd.Timestamp(dates[i]).strftime("%Y-%m-%d"))
    # 窗口内 NaN 用 0 填(滚动特征早期可能有 NaN,训练时已 fillna)
    X = np.nan_to_num(X, nan=0.0)
    return X, cur_price, cur_date


# ============================================================
# 模型加载(懒加载,失败降级)
# ============================================================
class ModelLoader:
    _instance: "ModelLoader | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.tf_available = False
        self.models: dict[str, Any] = {}
        self.scalers: dict[str, Any] = {}
        self.item_map: dict[str, int] = {}
        self.group_map: dict[str, str] = {}
        self.hybrid_route: dict[str, str] = {"low": "LSTM-C", "mid": "LSTM-D", "high": "LSTM-D"}
        self.gru_items: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            from tensorflow import keras  # noqa: F401
            import pickle
            self.tf_available = True
        except Exception as e:
            print(f"[model_loader] WARN TensorFlow unavailable, Hybrid mock: {e}")
            return

        try:
            from tensorflow import keras
            import pickle

            def _pkl(name):
                with open(MODEL_DIR / name, "rb") as f:
                    return pickle.load(f)

            # LSTM-C
            if (MODEL_DIR / "lstm_c.keras").exists():
                self.models["lstm_c"] = keras.models.load_model(MODEL_DIR / "lstm_c.keras")
                sc = _pkl("lstm_c_scaler.pkl")
                self.scalers["lstm_c"] = sc
                self.item_map = _pkl("lstm_c_item_map.pkl")

            # LSTM-D ×3
            for grp in ("low", "mid", "high"):
                p = MODEL_DIR / f"lstm_d_{grp}.keras"
                if p.exists():
                    self.models[f"lstm_d_{grp}"] = keras.models.load_model(p)
            if (MODEL_DIR / "lstm_d_scalers.pkl").exists():
                self.scalers["lstm_d"] = _pkl("lstm_d_scalers.pkl")
            if (MODEL_DIR / "lstm_d_group_map.pkl").exists():
                gm = _pkl("lstm_d_group_map.pkl")
                # gm 结构: {"item_group": {name: "low"/"mid"/"high"}, ...}
                self.group_map = gm.get("item_group", gm) if isinstance(gm, dict) else {}

            # Hybrid 路由(val 冻结);缺失则默认 low→C mid/high→D
            route_path = MODEL_DIR / "lstm_hybrid_route.json"
            if route_path.exists():
                import json
                meta = json.loads(route_path.read_text(encoding="utf-8"))
                route = meta.get("route") or {}
                if isinstance(route, dict) and route:
                    self.hybrid_route = {
                        "low": route.get("low", "LSTM-C"),
                        "mid": route.get("mid", "LSTM-D"),
                        "high": route.get("high", "LSTM-D"),
                    }

            # GRU
            if (MODEL_DIR / "gru.keras").exists():
                self.models["gru"] = keras.models.load_model(MODEL_DIR / "gru.keras")
                self.scalers["gru"] = _pkl("gru_scaler.pkl")
                self.gru_items = set(_pkl("gru_items.pkl"))

            print(f"[model_loader] OK models loaded: {list(self.models.keys())} | "
                  f"item_map={len(self.item_map)} group_map={len(self.group_map)} "
                  f"gru_items={len(self.gru_items)} route={self.hybrid_route}")
        except Exception as e:
            print(f"[model_loader] WARN model load failed, mock fallback: {e}")
            self.tf_available = False

    # ---------- 工具 ----------
    @staticmethod
    def _scale_X(X: np.ndarray, scaler) -> np.ndarray:
        n, t, f = X.shape
        return scaler.transform(X.reshape(-1, f)).reshape(n, t, f)

    @staticmethod
    def _to_price(pred_log_2d: np.ndarray, y_scaler) -> float:
        inv = y_scaler.inverse_transform(pred_log_2d.reshape(-1, 1)).ravel()
        return float(np.expm1(inv[0]))

    # ---------- 单模型推理 ----------
    def _predict_lstm_c(self, X: np.ndarray, name: str) -> float | None:
        if "lstm_c" not in self.models:
            return None
        item_id = self.item_map.get(name)
        if item_id is None:
            item_id = self.item_map.get("__UNK__")
        if item_id is None:
            return None
        sc = self.scalers["lstm_c"]
        Xi = np.array([[item_id]], dtype=np.int32)
        p = self.models["lstm_c"].predict(
            [self._scale_X(X, sc["x_scaler"]), Xi], verbose=0, batch_size=1
        ).ravel()
        return self._to_price(p, sc["y_scaler"])

    def _predict_lstm_d(self, X: np.ndarray, name: str) -> float | None:
        grp = self.group_map.get(name)
        key = f"lstm_d_{grp}"
        if key not in self.models or grp is None:
            return None
        sc = self.scalers["lstm_d"].get(grp) if isinstance(self.scalers.get("lstm_d"), dict) else None
        if sc is None:
            return None
        p = self.models[key].predict(self._scale_X(X, sc["x_scaler"]), verbose=0, batch_size=1).ravel()
        return self._to_price(p, sc["y_scaler"])

    def _predict_gru(self, X: np.ndarray, name: str) -> float | None:
        if "gru" not in self.models or name not in self.gru_items:
            return None
        sc = self.scalers["gru"]
        p = self.models["gru"].predict(self._scale_X(X, sc["x_scaler"]), verbose=0, batch_size=1).ravel()
        return self._to_price(p, sc["y_scaler"])

    # ---------- Hybrid 主入口 ----------
    def predict_hybrid(self, market_hash_name: str) -> dict | None:
        """
        返回 {current_price, predicted_price(7天), model, date, change_pct} 或 None。
        Hybrid 路由默认: low→C, mid/high→D(可被 lstm_hybrid_route.json 覆盖)。
        """
        # Do not import the TensorFlow training module before checking the
        # fallback. Otherwise a machine without TensorFlow gets a 500 instead
        # of the advertised trend-based mock prediction.
        if not self.tf_available:
            return self._mock_trend(market_hash_name)

        win = _skin_window(market_hash_name)
        if win is None:
            return None
        X, cur_price, cur_date = win

        grp = self.group_map.get(market_hash_name, "mid")
        prefer = self.hybrid_route.get(grp, "LSTM-D")
        if prefer == "LSTM-C":
            pred = self._predict_lstm_c(X, market_hash_name)
            model_tag = "LSTM-C"
        else:
            pred = self._predict_lstm_d(X, market_hash_name)
            model_tag = "LSTM-D"
        # 兜底:C/D 任一失败换另一个
        if pred is None:
            pred = self._predict_lstm_c(X, market_hash_name) or self._predict_lstm_d(X, market_hash_name)
            model_tag = "LSTM-Hybrid(fallback)"
        if pred is None:
            return self._mock_trend(market_hash_name, cur_price, cur_date)

        pred = max(pred, 0.01)
        change = round((pred - cur_price) / cur_price * 100, 2)
        return {
            "current_price": round(cur_price, 2),
            "predicted_price": round(pred, 2),
            "model": model_tag,
            "date": cur_date,
            "change_pct": change,
            "confidence": self._confidence(model_tag),
            "price_tier": grp,
            "route": prefer,
        }

    def predict_gru_for(self, market_hash_name: str) -> dict | None:
        if not self.tf_available or market_hash_name not in self.gru_items:
            return None
        win = _skin_window(market_hash_name)
        if win is None:
            return None
        X, cur_price, cur_date = win
        pred = self._predict_gru(X, market_hash_name)
        if pred is None:
            return None
        pred = max(pred, 0.01)
        return {
            "current_price": round(cur_price, 2),
            "predicted_price": round(pred, 2),
            "model": "GRU",
            "date": cur_date,
            "change_pct": round((pred - cur_price) / cur_price * 100, 2),
            "confidence": self._confidence("GRU"),
        }

    # ---------- 树模型(读 pred CSV,回测同口径)----------
    _tree_cache: dict[str, pd.DataFrame] = {}

    def _resolve_pred_csv(self, csv_name: str) -> Path | None:
        """优先 v4 契约的 *_test.csv,再回退旧文件名。"""
        stem = csv_name[:-4] if csv_name.endswith(".csv") else csv_name
        for candidate in (
            PRED_DIR / f"{stem}_test.csv",
            PRED_DIR / csv_name,
            PRED_DIR / f"{stem}_val.csv",
        ):
            if candidate.exists():
                return candidate
        return None

    def _tree_pred(self, csv_name: str, model_name: str, mtype: str,
                   market_hash_name: str) -> dict | None:
        path = self._resolve_pred_csv(csv_name)
        if path is None:
            return None
        cache_key = path.name
        if cache_key not in self._tree_cache:
            try:
                self._tree_cache[cache_key] = pd.read_csv(path, parse_dates=["date"])
            except Exception:
                return None
        df = self._tree_cache[cache_key]
        sub = df[df["market_hash_name"] == market_hash_name]
        if sub.empty:
            return None
        row = sub.sort_values("date").iloc[-1]
        cur = float(row["current_price"])
        pred = max(float(row["predicted_price"]), 0.01)
        return {
            "current_price": round(cur, 2),
            "predicted_price": round(pred, 2),
            "model": model_name,
            "type": mtype,
            "date": str(pd.Timestamp(row["date"]).strftime("%Y-%m-%d")),
            "change_pct": round((pred - cur) / cur * 100, 2) if cur else 0.0,
            "confidence": self._confidence(model_name),
        }

    def predict_all_models(self, market_hash_name: str, horizon: int = 7) -> list[dict]:
        """返回多模型预测列表(供 /api/predict)。

        优先读同一决策日的 test 预测 CSV,避免 live LSTM 与旧 val 树模型混比;
        CSV 缺失时 LSTM/GRU 再回退实时推理。
        """
        results: list[dict] = []
        # 统一决策日: Hybrid / C / D / GRU / 树 都优先 test CSV
        for csv_name, model_name, mtype in [
            ("pred_lstm_hybrid.csv", "LSTM", "DL"),
            ("pred_gru.csv", "GRU", "DL"),
            ("pred_arima.csv", "ARIMA", "统计"),
            ("pred_xgboost.csv", "XGBoost", "ML"),
            ("pred_lightgbm.csv", "LightGBM", "ML"),
            ("pred_rf.csv", "RandomForest", "ML"),
        ]:
            r = self._tree_pred(csv_name, model_name, mtype, market_hash_name)
            if r:
                # Hybrid 文件对外仍标 LSTM(部署主力)
                if model_name == "LSTM":
                    r["model"] = "LSTM"
                    r["confidence"] = self._confidence("LSTM")
                results.append(r)

        have = {r["model"] for r in results}
        # CSV 无 Hybrid 时: live Hybrid 兜底
        if "LSTM" not in have:
            h = self.predict_hybrid(market_hash_name)
            if h:
                results.insert(0, {**h, "model": "LSTM", "type": "DL"})
        if "GRU" not in have:
            g = self.predict_gru_for(market_hash_name)
            if g:
                results.append({**g, "type": "DL"})

        # horizon=30 时把 7 天预测外推(标注)
        if horizon == 30:
            for r in results:
                cur = r["current_price"]
                ch7 = (r["predicted_price"] - cur) / cur if cur else 0
                p30 = max(cur * (1 + ch7 * 3.5), 0.01)
                r["predicted_price_7d"] = r["predicted_price"]
                r["predicted_price"] = round(p30, 2)
                r["change_pct"] = round((p30 - cur) / cur * 100, 2) if cur else 0.0
                r["horizon_note"] = "30天由7天外推"
        return results

    # ---------- 内部辅助 ----------
    def _confidence(self, model_name: str) -> float:
        """按各模型历史 MAPE 反推置信度(MAPE 越低置信越高)。"""
        mape = {
            "LSTM-C": 5.56, "LSTM-D": 4.39, "LSTM": 4.5, "LSTM-Hybrid(fallback)": 4.5,
            "GRU": 11.02, "XGBoost": 7.5, "LightGBM": 12.67,
            "RandomForest": 9.0, "ARIMA": 18.17,
        }.get(model_name, 10.0)
        return round(max(35.0, min(95.0, 100.0 - mape * 2.2)), 1)

    def _mock_trend(
        self,
        market_hash_name: str,
        cur_price: float | None = None,
        cur_date: str | None = None,
    ) -> dict | None:
        """TF 不可用时的趋势外推(近 7 日收益率 ×7)。"""
        global _RAW_PRICE_CACHE
        if _RAW_PRICE_CACHE is None:
            frames = []
            for split in ("train", "val", "test"):
                path = DATA_DIR / f"{split}.csv"
                if path.exists():
                    frames.append(
                        pd.read_csv(
                            path,
                            usecols=["date", "market_hash_name", "price"],
                            parse_dates=["date"],
                        )
                    )
            if not frames:
                return None
            _RAW_PRICE_CACHE = pd.concat(frames, ignore_index=True)
        g = _RAW_PRICE_CACHE[
            _RAW_PRICE_CACHE["market_hash_name"] == market_hash_name
        ].sort_values("date")
        if g.empty:
            return None
        cur_price = float(cur_price if cur_price is not None else g["price"].iloc[-1])
        cur_date = cur_date or str(pd.Timestamp(g["date"].iloc[-1]).strftime("%Y-%m-%d"))
        if len(g) < 8:
            return {"current_price": round(cur_price, 2), "predicted_price": round(cur_price, 2),
                    "model": "Mock(趋势)", "date": cur_date, "change_pct": 0.0, "confidence": 40.0}
        ret7 = (g["price"].iloc[-1] - g["price"].iloc[-8]) / g["price"].iloc[-8]
        if abs(ret7) < 0.0001 and len(g) >= 31:
            ret30 = (g["price"].iloc[-1] - g["price"].iloc[-31]) / g["price"].iloc[-31]
            ret = ret30 if abs(ret30) > 0.0001 else ret7
        else:
            ret = ret7
        pred = max(cur_price * (1 + ret), 0.01)
        return {"current_price": round(cur_price, 2), "predicted_price": round(pred, 2),
                "model": "Mock(趋势)", "date": cur_date,
                "change_pct": round((pred - cur_price) / cur_price * 100, 2), "confidence": 45.0}


def get_loader() -> ModelLoader:
    return ModelLoader()
