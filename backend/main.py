"""
SkinVision AI — FastAPI 主应用(组员 3 主线第 2 步)
==================================================
按 api-spec/openapi.yaml 实现全部端点 + 🆕 portfolio value_history / diagnose。

启动:
  cd backend && uvicorn main:app --reload --port 8000

降级:无 DeepSeek Key / 无 TF 时各端点仍可用(Mock/预录/规则)。
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

# Windows 控制台/管道默认可能是 ascii/GBK;强制 UTF-8,避免中文 prompt/日志 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import (
    PRED_CACHE_TTL_HOURS, OUTPUT_DIR, LLM_ENABLED, USE_BUFF_LIVE, ensure_dirs,
)
from database import (
    get_connection, resolve_skin, latest_price, change_pct, run_init, _utcnow,
    weapon_to_category,
)
from model_loader import get_loader
from auth import (
    get_current_user, get_current_user_optional, get_admin_user,
    register_user, authenticate_user, list_users,
)
import rag
import agent_debate
import portfolio_diagnose
import llm
import quotes as quotes_svc
import settings_store

# ---------- 启动初始化 ----------
ensure_dirs()
run_init()
_loader = get_loader()

app = FastAPI(title="SkinVision AI API", version="1.1.0",
              description="CS2 饰品 AI 智能分析平台后端(组员 3)")

app.add_middleware(
    CORSMiddleware,
    # 反射具体 Origin；勿用 allow_origins=["*"] + credentials（浏览器会拒）
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 请求体模型
# ============================================================
class PredictReq(BaseModel):
    skinId: str
    horizon: int = 7
    models: list[str] | None = None


class EntryRangeReq(BaseModel):
    skinId: str
    riskLevel: str = "moderate"


class ChatReq(BaseModel):
    message: str
    sessionId: str | None = None
    context: dict | None = None


class PortfolioReq(BaseModel):
    skinId: str
    buyPrice: float | None = None
    quantity: int = 1
    buyDate: str | None = None
    holdingType: str = "real"


class AlertReq(BaseModel):
    skinId: str
    type: str = "above"
    targetPrice: float
    note: str | None = None


class AuthReq(BaseModel):
    username: str
    password: str


class RagAskReq(BaseModel):
    query: str
    topK: int = 5


class AdminConfigReq(BaseModel):
    deepseekApiKey: str | None = None
    deepseekBaseUrl: str | None = None
    deepseekModel: str | None = None
    dashscopeApiKey: str | None = None
    dashscopeBaseUrl: str | None = None
    ragEmbedModel: str | None = None
    ragEmbedDim: int | None = None
    ragUseVector: bool | None = None


# ============================================================
# 辅助:skin 序列化
# ============================================================
def _skin_to_dict(conn, row) -> dict:
    cur, cur_date = latest_price(conn, row["id"])
    ch24 = change_pct(conn, row["id"], 1)
    ch7 = change_pct(conn, row["id"], 7)
    # 流动性:由近 7 日均成交量映射 0-100
    vol_row = conn.execute(
        "SELECT AVG(daily_volume) v FROM price_history WHERE skin_id=? "
        "AND date >= (SELECT MAX(date) FROM price_history WHERE skin_id=?)",
        (row["id"], row["id"]),
    ).fetchone()
    vol24 = int(vol_row["v"] or 0) if vol_row else 0
    liquidity = min(100, int(vol24 / 50))
    # 数据来源与新鲜度: BUFF 爬取(滚动实时更新) vs 训练 CSV(历史静态)
    src = (row["source"] or "csv") if "source" in row.keys() else "csv"
    is_live = False
    if cur_date:
        try:
            is_live = (pd.Timestamp.utcnow().tz_localize(None)
                       - pd.Timestamp(cur_date)).days <= 7
        except Exception:
            is_live = False
    return {
        "id": row["slug"],
        "name": row["market_hash_name"],
        # 按 weapon_type 重算,避免库内旧映射漏刀/手套
        "category": weapon_to_category(row["weapon_type"] or row["market_hash_name"] or "")
                    or row["category"],
        "wear": row["wear_full"] or row["wear"],
        # 与训练数据同口径: USD
        "price": round(cur, 2) if cur else None,
        "priceUsd": round(cur, 2) if cur else None,
        "change24h": ch24,
        "change7d": ch7,
        "volume24h": vol24,
        "liquidity": liquidity,
        "rarity": row["rarity_rank"],
        "image": "🎮",
        "source": "BUFF" if src == "buff" else "CSV",
        "priceDate": cur_date,
        "isLive": is_live,
        "weaponType": row["weapon_type"],
    }


# ============================================================
# P0:健康检查
# ============================================================
@app.get("/api/health")
def health():
    return health_check_payload()


# ============================================================
# P0:行情
# ============================================================
@app.get("/api/skins")
def list_skins(category: str | None = None, sort: str = "volume_desc",
               limit: int = Query(200, le=1000)):
    with get_connection() as conn:
        q = """SELECT s.* FROM skins s
               WHERE EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id)"""
        params: list[Any] = []
        if category:
            q += " AND s.category=?"
            params.append(category)
        rows = conn.execute(q, params).fetchall()
        items = [_skin_to_dict(conn, r) for r in rows]
    # 排序
    sort_map = {
        "price_desc": lambda x: -(x["priceUsd"] or 0),
        "price_asc": lambda x: x["priceUsd"] or 0,
        "change7d_desc": lambda x: -(x["change7d"] or 0),
        "change7d_asc": lambda x: x["change7d"] or 0,
        "volume_desc": lambda x: -(x["volume24h"] or 0),
    }
    items.sort(key=sort_map.get(sort, sort_map["volume_desc"]))
    items = items[:limit]
    return {"total": len(rows), "items": items}


@app.get("/api/skins/{skin_id}")
def get_skin(skin_id: str):
    with get_connection() as conn:
        row = resolve_skin(conn, skin_id)
        if not row:
            raise HTTPException(404, "skin not found")
        item = _skin_to_dict(conn, row)
        # 详情补充
        listings = conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE skin_id=?", (row["id"],)).fetchone()[0]
        item["listings"] = listings
        item["daysSinceRelease"] = listings
        return item


@app.get("/api/skins/{skin_id}/quotes")
def get_skin_quotes(
    skin_id: str,
    platforms: str | None = Query(
        None,
        description="逗号分隔平台(默认 skinport,waxpeer,marketcsgo,lootfarm,csgotrader)",
    ),
    live: bool | None = Query(
        None,
        description="强制实时拉取; 默认跟随 USE_BUFF_LIVE",
    ),
):
    """多平台实时/演示报价。USE_BUFF_LIVE=0 时返回基于库内价的跨平台演示价差。"""
    with get_connection() as conn:
        row = resolve_skin(conn, skin_id)
        if not row:
            raise HTTPException(404, "skin not found")
        base, _ = latest_price(conn, row["id"])
        name = row["market_hash_name"]
        slug = row["slug"]
    plat_list = [p.strip() for p in platforms.split(",")] if platforms else None
    payload = quotes_svc.get_skin_quotes(
        market_hash_name=name,
        base_price=base,
        platforms=plat_list,
        live=live,
    )
    payload["skinId"] = slug
    return payload


@app.get("/api/skins/{skin_id}/kline")
def get_kline(skin_id: str, days: int = 90, interval: str = "1d"):
    with get_connection() as conn:
        row = resolve_skin(conn, skin_id)
        if not row:
            raise HTTPException(404, "skin not found")
        rows = conn.execute(
            "SELECT date, price, daily_volume FROM price_history WHERE skin_id=? "
            "ORDER BY date DESC LIMIT ?", (row["id"], days)
        ).fetchall()
        rows = list(reversed(rows))
    if not rows:
        return {"skinId": skin_id, "interval": interval, "data": [], "ma7": [], "ma30": [], "volumes": []}
    prices = [r["price"] for r in rows]
    data, volumes = [], []
    prev = prices[0]
    for i, r in enumerate(rows):
        open_ = prev if i > 0 else r["price"]
        close = r["price"]
        high = max(open_, close) * 1.003
        low = min(open_, close) * 0.997
        ts = pd.Timestamp(r["date"])
        date_str = f"{ts.month}/{ts.day}"
        data.append({"date": date_str, "open": round(open_, 2), "close": round(close, 2),
                     "high": round(high, 2), "low": round(low, 2)})
        volumes.append({"date": date_str, "volume": int(r["daily_volume"] or 0),
                        "direction": 1 if close >= open_ else -1})
        prev = close
    closes = [d["close"] for d in data]
    ma7 = [round(sum(closes[max(0, i - 6):i + 1]) / (i - max(0, i - 6) + 1), 2) for i in range(len(closes))]
    ma30 = [round(sum(closes[max(0, i - 29):i + 1]) / (i - max(0, i - 29) + 1), 2) for i in range(len(closes))]
    return {"skinId": skin_id, "interval": interval, "data": data,
            "ma7": ma7, "ma30": ma30, "volumes": volumes}


# ============================================================
# P0:预测(走缓存)
# ============================================================
@app.post("/api/predict")
def predict(req: PredictReq):
    if req.horizon not in (7, 30):
        raise HTTPException(400, "horizon must be 7 or 30")
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        skin_id = skin["id"]
        name = skin["market_hash_name"]
        live_cur, _ = latest_price(conn, skin_id)

        # 缓存命中?
        exp = (_utcnow() - timedelta(hours=PRED_CACHE_TTL_HOURS)).isoformat()
        cached = conn.execute(
            "SELECT * FROM predictions WHERE skin_id=? AND horizon=? AND expires_at>?",
            (skin_id, req.horizon, exp),
        ).fetchall()
        # v5 上线前的缓存没有 daily_json；继续命中会让 LSTM 退回旧单点展示。
        # 发现整批缓存都不含逐日路径时主动失效并按新契约重算一次。
        if cached and not any(
            ("daily_json" in c.keys() and c["daily_json"]) for c in cached
        ):
            conn.execute(
                "DELETE FROM predictions WHERE skin_id=? AND horizon=?",
                (skin_id, req.horizon),
            )
            conn.commit()
            cached = []
        decision_cur = live_cur
        decision_date = None
        if cached:
            preds = []
            for c in cached:
                daily = None
                try:
                    if "daily_json" in c.keys() and c["daily_json"]:
                        daily = json.loads(c["daily_json"])
                except Exception:
                    daily = None
                preds.append({"model": c["model"], "type": c["type"],
                              "price": c["predicted_price"],
                              "priceUsd": c["predicted_price"],
                              "change": c["change_pct"], "confidence": c["confidence"],
                              "dailyPrices": daily})
            # 缓存里 current_price 是决策日 USD
            if cached[0]["current_price"]:
                decision_cur = float(cached[0]["current_price"])
        else:
            raw = _loader.predict_all_models(name, req.horizon)
            preds = []
            now_iso = _utcnow().isoformat()
            exp_iso = (_utcnow() + timedelta(hours=PRED_CACHE_TTL_HOURS)).isoformat()
            # 用各模型决策日 current_price 的中位数作统一基准(通常相同)
            raw_curs = [float(r["current_price"]) for r in raw if r.get("current_price")]
            if raw_curs:
                decision_cur = sorted(raw_curs)[len(raw_curs) // 2]
            for r in raw:
                if r.get("date"):
                    decision_date = r["date"]
                daily = r.get("daily_prices") or None
                preds.append({
                    "model": r["model"], "type": r.get("type", "ML"),
                    "price": r["predicted_price"],
                    "priceUsd": r["predicted_price"],
                    "change": r["change_pct"],
                    "confidence": r["confidence"],
                    "decisionDate": r.get("date"),
                    "dailyPrices": daily,
                })
                conn.execute(
                    """INSERT INTO predictions(skin_id, horizon, model, type, predicted_price,
                       current_price, change_pct, confidence, generated_at, expires_at, daily_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (skin_id, req.horizon, r["model"], r.get("type", "ML"),
                     r["predicted_price"], r["current_price"], r["change_pct"],
                     r["confidence"], now_iso, exp_iso,
                     json.dumps(daily) if daily else None),
                )
            conn.commit()

    # 过滤指定模型
    if req.models:
        ml = {m.lower() for m in req.models}
        preds = [p for p in preds if any(t in p["model"].lower() for t in ml)] or preds

    # 共识
    changes = [p["change"] for p in preds if p["change"] is not None]
    avg_chg = sum(changes) / len(changes) if changes else 0
    consensus_score = round(min(100, max(0, 50 + avg_chg * 8)), 1)
    level = ("very_high" if consensus_score >= 80 else "high" if consensus_score >= 65
             else "medium" if consensus_score >= 45 else "low")

    cur = decision_cur if decision_cur else live_cur
    return {
        "skinId": skin["slug"],
        "horizon": req.horizon,
        "currency": "USD",
        "currentPrice": round(cur, 2) if cur else None,
        "currentPriceUsd": round(cur, 2) if cur else None,
        "livePriceUsd": round(live_cur, 2) if live_cur else None,
        "decisionDate": decision_date,
        "predictions": preds,
        "consensus": {"score": consensus_score, "level": level},
        "entryRange": {"low": round((cur or 0) * 0.97, 2),
                       "high": round((cur or 0) * 0.99, 2)},
        # 7 天目标:按共识涨跌幅,而非写死 +5%
        "targetPrice": round((cur or 0) * (1 + avg_chg / 100), 2) if cur else None,
        "generatedAt": _utcnow().isoformat(),
    }


@app.post("/api/predict/entry-range")
def entry_range(req: EntryRangeReq):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        cur, _ = latest_price(conn, skin["id"])
    mult = {"conservative": (0.95, 0.98, 0.95, 1.03, 1.06),
            "moderate": (0.97, 0.99, 0.92, 1.05, 1.12),
            "aggressive": (0.98, 1.0, 0.88, 1.08, 1.20)}[req.riskLevel]
    return {k: round((cur or 0) * v, 2) for k, v in
            zip(["entryLow", "entryHigh", "stopLoss", "target7d", "target30d"], mult)}


# ============================================================
# P0:AI 对话(SSE 流式)
# ============================================================
@app.post("/api/chat")
async def chat(req: ChatReq):
    def gen():
        messages = [{"role": "user", "content": req.message}]
        for ch in llm.chat_stream(messages):
            yield f"data: {json.dumps({'chunk': ch}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': 'deepseek-chat' if LLM_ENABLED else 'mock'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ============================================================
# 🆕 认证:注册 / 登录
# ============================================================
@app.post("/api/register")
def api_register(req: AuthReq):
    return register_user(req.username, req.password)


@app.post("/api/login")
def api_login(req: AuthReq):
    return authenticate_user(req.username, req.password)


@app.get("/api/me")
def api_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


# ============================================================
# 管理员:用户列表 / API 配置 / 探针
# ============================================================
@app.get("/api/admin/users")
def admin_users(_: dict = Depends(get_admin_user)):
    return {"items": list_users()}


@app.get("/api/admin/config")
def admin_get_config(_: dict = Depends(get_admin_user)):
    return settings_store.public_config()


@app.put("/api/admin/config")
def admin_put_config(req: AdminConfigReq, _: dict = Depends(get_admin_user)):
    updates: dict[str, Any] = {}
    if req.deepseekApiKey is not None:
        updates["DEEPSEEK_API_KEY"] = req.deepseekApiKey
    if req.deepseekBaseUrl is not None:
        updates["DEEPSEEK_BASE_URL"] = req.deepseekBaseUrl
    if req.deepseekModel is not None:
        updates["DEEPSEEK_MODEL"] = req.deepseekModel
    if req.dashscopeApiKey is not None:
        updates["DASHSCOPE_API_KEY"] = req.dashscopeApiKey
    if req.dashscopeBaseUrl is not None:
        updates["DASHSCOPE_BASE_URL"] = req.dashscopeBaseUrl
    if req.ragEmbedModel is not None:
        updates["RAG_EMBED_MODEL"] = req.ragEmbedModel
    if req.ragEmbedDim is not None:
        updates["RAG_EMBED_DIM"] = str(req.ragEmbedDim)
    if req.ragUseVector is not None:
        updates["RAG_USE_VECTOR"] = "1" if req.ragUseVector else "0"
    try:
        settings_store.set_settings(updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "config": settings_store.public_config()}


@app.get("/api/admin/status")
def admin_status(_: dict = Depends(get_admin_user)):
    """聚合健康检查 + 配置态(不发外网探针)。"""
    health = health_check_payload()
    return {
        "health": health,
        "config": settings_store.public_config(),
        "rag": rag.vector_status(),
    }


@app.post("/api/admin/probe/llm")
def admin_probe_llm(_: dict = Depends(get_admin_user)):
    """探测 DeepSeek LLM 是否可用。"""
    import time
    t0 = time.time()
    try:
        # 探测前先同步运行时配置,避免管理页刚保存后读到旧 Key
        settings_store.apply_runtime_settings()
        from config import LLM_ENABLED as llm_on
        if not llm_on:
            return {"ok": False, "provider": "deepseek", "latencyMs": 0,
                    "error": "未配置 DEEPSEEK_API_KEY", "sample": ""}
        text = llm.chat_sync(
            [{"role": "user", "content": "请只回复两个字:正常"}],
            temperature=0.1,
            timeout=20.0,
        )
        ms = int((time.time() - t0) * 1000)
        sample = (text or "")[:160]
        # chat_sync 失败时不会抛异常,而是返回带 Mock/[error:] 的文本
        failed = (
            not text
            or "Mock" in text
            or "调用失败" in text
            or "[error:" in text
            or "401" in text
            or "Unauthorized" in text
        )
        if failed:
            return {"ok": False, "provider": "deepseek", "latencyMs": ms,
                    "error": "LLM 返回失败/Mock,请检查 API Key 是否有效", "sample": sample}
        return {"ok": True, "provider": "deepseek", "latencyMs": ms, "sample": sample}
    except Exception as e:
        return {"ok": False, "provider": "deepseek",
                "latencyMs": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"}


@app.post("/api/admin/probe/embed")
def admin_probe_embed(_: dict = Depends(get_admin_user)):
    """探测阿里云 DashScope Embedding 是否可用。"""
    import time
    t0 = time.time()
    try:
        from config import RAG_EMBED_ENABLED, RAG_EMBED_MODEL
        if not RAG_EMBED_ENABLED:
            return {"ok": False, "provider": "dashscope",
                    "latencyMs": 0, "error": "未配置 DASHSCOPE_API_KEY 或未启用向量检索"}
        model = (RAG_EMBED_MODEL or "").strip().lower()
        if "rerank" in model:
            return {
                "ok": False,
                "provider": "dashscope",
                "latencyMs": 0,
                "model": RAG_EMBED_MODEL,
                "error": (
                    f"模型「{RAG_EMBED_MODEL}」是重排序(Rerank)，不能用于向量检索。"
                    "请改成 text-embedding-v3 或 text-embedding-v4 后再测。"
                ),
            }
        vecs = rag._embed_texts(["CS2 饰品市场测试向量"])
        ms = int((time.time() - t0) * 1000)
        dim = int(vecs.shape[1]) if vecs.size else 0
        return {"ok": True, "provider": "dashscope", "latencyMs": ms, "dim": dim,
                "model": rag.vector_status().get("model")}
    except Exception as e:
        return {"ok": False, "provider": "dashscope",
                "latencyMs": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"}


def health_check_payload() -> dict:
    """抽出健康检查 payload,供 /api/health 与管理员 status 复用。"""
    with get_connection() as conn:
        n_skins = conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
        n_price = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        n_portfolio = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        n_news = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    from config import LLM_ENABLED as _llm_on
    models_status = {
        "lstm_hybrid": "ok" if _loader.tf_available else "mock",
        "gru": "ok" if _loader.tf_available else "mock",
        "trees": "ok",
        "deepseek": "ok" if _llm_on else "mock",
        "rag": rag.vector_status().get("mode", "keyword"),
    }
    status = "ok" if (_loader.tf_available and n_price > 0) else "degraded"
    return {
        "status": status,
        "dataSources": {"skins": n_skins, "price_history": n_price,
                        "portfolio": n_portfolio, "news": n_news,
                        "users": n_users, "buff_live": USE_BUFF_LIVE},
        "models": models_status,
        "rag": rag.vector_status(),
        "timestamp": _utcnow().isoformat(),
    }


# ============================================================
# 🆕 P0:Portfolio CRUD(需登录,按 user_id 隔离)
# ============================================================
@app.get("/api/portfolio")
def get_portfolio(current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.*, s.market_hash_name, s.slug, s.category
               FROM portfolio p JOIN skins s ON s.id=p.skin_id
               WHERE p.user_id=? ORDER BY p.id""",
            (current_user["id"],),
        ).fetchall()
        items = []
        for r in rows:
            cur, _ = latest_price(conn, r["skin_id"])
            mv = (cur or 0) * r["quantity"]
            buy = r["buy_price"]
            pnl = round((cur - buy) * r["quantity"], 2) if (buy and cur) else None
            pnl_pct = round((cur - buy) / buy * 100, 2) if (buy and cur) else None
            items.append({
                "id": r["id"], "skinId": r["slug"], "name": r["market_hash_name"],
                "holdingType": r["holding_type"], "buyPrice": buy,
                "quantity": r["quantity"], "buyDate": r["buy_date"],
                "currentPrice": round(cur, 2) if cur else None,
                "marketValue": round(mv, 2),
                "pnl": pnl, "pnlPct": pnl_pct,
            })
        total = round(sum(i["marketValue"] for i in items), 2)
        return {"total": total, "items": items}


@app.post("/api/portfolio")
def add_portfolio(req: PortfolioReq, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        cur = _utcnow().isoformat()
        c = conn.execute(
            """INSERT INTO portfolio(skin_id, holding_type, buy_price, buy_date, quantity, note, created_at, user_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (skin["id"], req.holdingType, req.buyPrice, req.buyDate,
             req.quantity, None, cur, current_user["id"]),
        )
        conn.commit()
        pid = c.lastrowid
    return {"id": pid, "skinId": skin["slug"], "holdingType": req.holdingType,
            "buyPrice": req.buyPrice, "quantity": req.quantity, "buyDate": req.buyDate}


@app.delete("/api/portfolio/{item_id}")
def delete_portfolio(item_id: int, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        r = conn.execute("DELETE FROM portfolio WHERE id=? AND user_id=?",
                         (item_id, current_user["id"]))
        conn.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "not found")
    return {"success": True}


# ============================================================
# 🆕 P1:Portfolio value_history(SQL 聚合,需登录)
# ============================================================
@app.get("/api/portfolio/value_history")
def portfolio_value_history(days: int = 90, current_user: dict = Depends(get_current_user_optional)):
    """portfolio JOIN price_history GROUP BY date → 总市值曲线。"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.date AS date, SUM(p.price * po.quantity) AS value
               FROM price_history p
               JOIN portfolio po ON po.skin_id = p.skin_id
               WHERE po.user_id=? AND p.date >= date((SELECT MAX(date) FROM price_history), ?)
               GROUP BY p.date ORDER BY p.date""",
            (current_user["id"], f"-{days} days"),
        ).fetchall()
    if not rows:
        # 兜底:用各持仓最新价 × 数量 单点
        return {"dates": [], "values": [], "total": 0}
    dates = [r["date"] for r in rows]
    values = [round(r["value"], 2) for r in rows]
    return {"dates": dates, "values": values, "total": values[-1] if values else 0}


# ============================================================
# 🆕 P1:组合诊断(需登录)
# ============================================================
@app.post("/api/portfolio/diagnose")
def diagnose_portfolio(current_user: dict = Depends(get_current_user_optional)):
    result = portfolio_diagnose.diagnose(user_id=current_user["id"])
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ============================================================
# P1:RAG 解释 / 新闻 / 日报
# ============================================================
@app.get("/api/explain/{skin_id}")
def explain(skin_id: str, days: int = 7):
    return rag.explain(skin_id, days)


@app.get("/api/news")
def get_news(limit: int = 20, sentiment: str | None = None, source: str | None = None,
             maxAgeDays: int | None = Query(default=60, ge=1, le=365)):
    """资讯列表。优先返回带原文链接的近期条目(可点击跳转)。"""
    with get_connection() as conn:
        q = "SELECT * FROM news WHERE 1=1"
        params: list[Any] = []
        # 用日期前缀比较,兼容带时区的 ISO 时间戳(避免 date() 解析失败把新稿滤掉)
        if maxAgeDays:
            q += " AND substr(IFNULL(published_at,''), 1, 10) >= date('now', ?)"
            params.append(f"-{int(maxAgeDays)} days")
        if sentiment:
            q += " AND sentiment=?"; params.append(sentiment)
        if source:
            q += " AND source=?"; params.append(source)
        # 有 url 的排前面,再按时间
        q += """ ORDER BY
                   CASE WHEN IFNULL(url,'') != '' THEN 0 ELSE 1 END,
                   substr(IFNULL(published_at,''), 1, 10) DESC,
                   id DESC
                 LIMIT ?"""
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        if not rows:
            q2 = """SELECT * FROM news WHERE 1=1"""
            p2: list[Any] = []
            if sentiment:
                q2 += " AND sentiment=?"; p2.append(sentiment)
            if source:
                q2 += " AND source=?"; p2.append(source)
            q2 += """ ORDER BY
                        CASE WHEN IFNULL(url,'') != '' THEN 0 ELSE 1 END,
                        id DESC LIMIT ?"""
            p2.append(limit)
            rows = conn.execute(q2, p2).fetchall()
    return [{"id": r["id"], "title": r["title"], "summary": r["summary"],
             "source": r["source"], "url": (r["url"] or None) or None,
             "time": r["published_at"],
             "sentiment": r["sentiment"], "impact": r["impact"],
             "relatedSkins": r["related_skins"].split(",") if r["related_skins"] else []}
            for r in rows]


@app.get("/api/daily-report")
def daily_report(date: str | None = None):
    # Expo 种子可提供文案兜底，但 metrics 必须与当前库一致；
    # aiSummary 若是旧的 Mock/调用失败文案，则现场刷新（LLM 可用则重生成）。
    import scheduler
    live_metrics = scheduler.market_metrics_from_db()

    from config import SEED_DIR
    seed = SEED_DIR / "seed_daily_report.json"
    if seed.exists():
        try:
            rep = json.loads(seed.read_text(encoding="utf-8"))
            rep["metrics"] = live_metrics
            if not rep.get("sources"):
                try:
                    rep["sources"] = rag.retrieve_daily_sources(limit=6)
                except Exception:
                    rep["sources"] = []
            if scheduler.summary_is_degraded(rep.get("aiSummary")):
                portfolio = rep.get("portfolio") or []
                portfolio_text = "无持仓" if not portfolio else "; ".join(
                    f"{p.get('name')} {p.get('quantity', 1)}件" for p in portfolio
                )
                rep["aiSummary"] = scheduler.refresh_ai_summary(
                    live_metrics,
                    portfolio_text=portfolio_text,
                    sources=rep.get("sources") or [],
                )
                try:
                    seed.write_text(
                        json.dumps(rep, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return rep
        except Exception:
            pass
    return scheduler.generate_daily_report()


@app.post("/api/rag/ask")
def rag_ask(req: RagAskReq):
    """RAG 问答: 检索知识库/资讯 → 生成带引用的答案。"""
    return rag.ask(req.query, top_k=req.topK)


# ============================================================
# P2:双 Agent 辩论(双模式)
# ============================================================
@app.post("/api/debate/{skin_id}")
def debate(skin_id: str, mode: str = "bull_bear", live: bool = False):
    return agent_debate.debate(skin_id, live=live, mode=mode)


# ============================================================
# 预警
# ============================================================
@app.get("/api/alerts")
def get_alerts(active: bool | None = None, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        q = """SELECT a.*, s.market_hash_name, s.slug FROM alerts a
               JOIN skins s ON s.id=a.skin_id WHERE a.user_id=?"""
        params: list[Any] = [current_user["id"]]
        if active is not None:
            q += " AND a.active=?"; params.append(int(active))
        rows = conn.execute(q + " ORDER BY a.id DESC", params).fetchall()
    out = []
    for r in rows:
        cur, _ = latest_price(get_connection(), r["skin_id"])  # 轻量;实际可批
        out.append({"id": r["id"], "skinId": r["slug"], "skinName": r["market_hash_name"],
                    "type": r["type"], "targetPrice": r["target_price"],
                    "currentPrice": round(cur, 2) if cur else None,
                    "active": bool(r["active"]), "triggered": bool(r["triggered"]),
                    "createdAt": r["created_at"]})
    return out


@app.post("/api/alerts", status_code=201)
def create_alert(req: AlertReq, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        c = conn.execute(
            "INSERT INTO alerts(skin_id, type, target_price, note, active, created_at, user_id) VALUES (?,?,?,?,1,?,?)",
            (skin["id"], req.type, req.targetPrice, req.note, _utcnow().isoformat(), current_user["id"]),
        )
        conn.commit()
        return {"id": c.lastrowid, "skinId": skin["slug"], "type": req.type,
                "targetPrice": req.targetPrice, "active": True, "triggered": False}


@app.delete("/api/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: int, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        r = conn.execute("DELETE FROM alerts WHERE id=? AND user_id=?",
                         (alert_id, current_user["id"]))
        conn.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "not found")


# ============================================================
# 模型对比 / 回测 / SHAP
# ============================================================
@app.get("/api/models/comparison")
def models_comparison():
    """模型实验室对比表。
    优先 fair-test（compare_results_test）+ backtest returnPct；
    分类优先用 model_comparison.json（含 AUC），避免被 direction 无 AUC 结果覆盖。
    """
    mc_path = OUTPUT_DIR / "model_comparison.json"
    cmp_path = OUTPUT_DIR / "compare_results_test.json"
    if not cmp_path.exists():
        cmp_path = OUTPUT_DIR / "compare_results.json"
    bt_path = OUTPUT_DIR / "backtest" / "backtest_results.json"
    if not bt_path.exists():
        bt_path = OUTPUT_DIR / "backtest_results.json"

    mc: dict[str, Any] = {}
    if mc_path.exists():
        try:
            mc = json.loads(mc_path.read_text(encoding="utf-8"))
        except Exception:
            mc = {}

    # backtest returnPct: 兼容 lstm_c / LSTM-C 两种键名
    ret_map: dict[str, float] = {}
    key_alias = {
        "lstm_c": "LSTM-C", "LSTM-C": "LSTM-C",
        "lstm_d": "LSTM-D", "LSTM-D": "LSTM-D",
        "hybrid": "Hybrid", "Hybrid": "Hybrid",
        "gru": "GRU", "GRU": "GRU",
        "rf": "Random Forest", "RF": "Random Forest", "Random Forest": "Random Forest",
        "lightgbm": "LightGBM", "LightGBM": "LightGBM",
        "xgboost": "XGBoost", "XGBoost": "XGBoost",
    }
    if bt_path.exists():
        try:
            bt = json.loads(bt_path.read_text(encoding="utf-8"))
            fee = bt.get("fee_0.0000") if isinstance(bt, dict) else None
            if isinstance(fee, dict):
                for k, blk in fee.items():
                    if not isinstance(blk, dict) or blk.get("returnPct") is None:
                        continue
                    rp = float(blk["returnPct"])
                    display = key_alias.get(k, k)
                    ret_map[k] = rp
                    ret_map[display] = rp
        except Exception:
            pass

    meta_by_name = {
        r.get("name"): r for r in (mc.get("regression") or []) if isinstance(r, dict)
    }

    regression: list[dict[str, Any]] = []
    horizon_steps = None
    # 1) fair-test compare_results_* 优先
    if cmp_path.exists():
        try:
            cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
            horizon_steps = cmp.get("horizon_steps") if isinstance(cmp, dict) else None
            models_blk = cmp.get("models") if isinstance(cmp, dict) else None
            if isinstance(models_blk, dict):
                for name, blk in models_blk.items():
                    if not isinstance(blk, dict):
                        continue
                    display = "Random Forest" if name == "RF" else name
                    meta = meta_by_name.get(display) or meta_by_name.get(name) or {}
                    is_dl = any(x in display.upper() for x in ("LSTM", "GRU"))
                    # v5 契约: Seq2Seq 多步模型带 per_day 逐日指标(day1..day7)
                    per_day_blk = blk.get("per_day")
                    per_day = None
                    if isinstance(per_day_blk, dict) and per_day_blk:
                        per_day = [
                            {"day": int(d), **(m if isinstance(m, dict) else {})}
                            for d, m in sorted(per_day_blk.items(), key=lambda kv: int(kv[0]))
                        ]
                    regression.append({
                        "name": display,
                        "type": meta.get("type") or (
                            "DL" if is_dl else "Route" if "Hybrid" in display else "ML"
                        ),
                        "typeKey": meta.get("typeKey"),
                        "course": meta.get("course") or "fair test",
                        "rmse": blk.get("rmse"),
                        "mae": blk.get("mae"),
                        "mape": blk.get("mape"),
                        "r2": blk.get("r2"),
                        "returnPct": (
                            ret_map.get(display)
                            or ret_map.get(name)
                            or meta.get("returnPct")
                        ),
                        "speed": meta.get("speed") or ("慢" if is_dl else "快"),
                        "interpretability": meta.get("interpretability"),
                        "perDay": per_day,
                    })
        except Exception:
            regression = []

    # 2) 回退 model_comparison.json
    if not regression and mc.get("regression"):
        regression = list(mc["regression"])

    # 分类：优先带 AUC 的 curated 表，勿被 direction（auc=null）覆盖
    classification: list[dict[str, Any]] = []
    if isinstance(mc.get("classification"), list) and mc["classification"]:
        classification = list(mc["classification"])
    elif cmp_path.exists():
        try:
            cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
            models_blk = cmp.get("models") if isinstance(cmp, dict) else None
            if isinstance(models_blk, dict):
                for name, blk in models_blk.items():
                    if not isinstance(blk, dict):
                        continue
                    d = blk.get("direction") or {}
                    if not d:
                        continue
                    display = "Random Forest" if name == "RF" else name
                    classification.append({
                        "name": display,
                        "type": "DL" if any(x in display.upper() for x in ("LSTM", "GRU")) else "ML",
                        "accuracy": d.get("accuracy"),
                        "auc": d.get("auc"),
                        "precision": d.get("precision"),
                        "recall": d.get("recall"),
                        "f1": d.get("f1"),
                        "returnPct": ret_map.get(display) or ret_map.get(name),
                    })
        except Exception:
            classification = []

    buy_hold = mc.get("buyAndHold") or {
        "name": "Buy & Hold", "type": "基准",
        "rmse": 0, "mae": 0, "mape": 0, "r2": 0,
        "returnPct": 0, "speed": "—", "course": "基准策略",
    }
    return {"regression": regression, "classification": classification,
            "buyAndHold": buy_hold,
            "horizonSteps": horizon_steps or mc.get("horizonSteps") or 7}


@app.get("/api/models/backtest")
def models_backtest(days: int = 60, skinId: str | None = None):
    p = OUTPUT_DIR / "backtest" / "backtest_curves.json"
    if not p.exists():
        p = OUTPUT_DIR / "backtest_curves.json"
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            # 原始结构:{fee_0.0000:{lstm_c:[{date,capital}], ...}, buy_hold:[{date,capital}]}
            fee = raw.get("fee_0.0000") if isinstance(raw, dict) else None
            buy_hold = raw.get("buy_hold") if isinstance(raw, dict) else None
            if fee and isinstance(fee, dict):
                # 兼容 snake_case(lstm_c) 与展示名(LSTM-C)；ml/backtest.py 写出后者
                label_aliases: dict[str, tuple[str, ...]] = {
                    "LSTM-C": ("lstm_c", "LSTM-C", "LSTM"),
                    "LSTM-D": ("lstm_d", "LSTM-D"),
                    "Hybrid": ("hybrid", "Hybrid"),
                    "GRU": ("gru", "GRU"),
                    "Random Forest": ("rf", "RF", "Random Forest"),
                    "LightGBM": ("lightgbm", "LightGBM"),
                    "XGBoost": ("xgboost", "XGBoost"),
                }
                # 图表主系列：策略模型 + Buy&Hold（避免一次塞太多线）
                prefer_labels = ("LSTM-C", "LSTM-D", "Hybrid", "Random Forest", "XGBoost")

                def _fee_series(label: str) -> list:
                    for alias in label_aliases.get(label, (label,)):
                        arr = fee.get(alias)
                        if arr:
                            return arr
                    return []

                anchor = next((_fee_series(lb) for lb in prefer_labels if _fee_series(lb)), None)
                if not anchor:
                    anchor = buy_hold or []
                dates = [pt.get("date", "") for pt in anchor]

                def _capitals(arr: list) -> list[float]:
                    return [round(float(pt.get("capital", 0) or 0), 2) for pt in arr]

                def _reindex(vals: list[float]) -> list[float]:
                    """统一成起始=100 的净值指数，避免 Buy&Hold 绝对资金撑破 Y 轴。"""
                    if not vals:
                        return vals
                    base = next((v for v in vals if v), 0.0) or 1.0
                    return [round(v / base * 100.0, 2) for v in vals]

                series_raw: dict[str, list[float]] = {}
                for label in prefer_labels:
                    arr = _fee_series(label)
                    if not arr:
                        continue
                    series_raw[label] = _capitals(arr)
                if buy_hold:
                    series_raw["Buy&Hold"] = _capitals(buy_hold)

                if days and days > 0 and len(dates) > days:
                    dates = dates[-days:]
                    series_raw = {k: v[-days:] for k, v in series_raw.items()}

                series = {k: _reindex(v) for k, v in series_raw.items()}
                return {"dates": dates, "series": series, "indexed": True}
            return raw
        except Exception:
            pass
    return {"dates": [], "series": {}}


@app.get("/api/models/shap")
def models_shap(model: str = "xgboost"):
    p = OUTPUT_DIR / "shap_features.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get(model, data.get("xgboost", []))


# ============================================================
# 启动事件:开定时任务
# ============================================================
@app.on_event("startup")
def _startup():
    try:
        import scheduler
        scheduler.start_scheduler()
    except Exception as e:
        print(f"[main] scheduler 启动失败(不影响主服务): {e}")


@app.on_event("shutdown")
def _shutdown():
    try:
        import scheduler
        scheduler.shutdown_scheduler()
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
