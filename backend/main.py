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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

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
    PRED_CACHE_TTL_HOURS, PREDICTION_CIRCUIT_BREAKER_ENABLED,
    OUTPUT_DIR, LLM_ENABLED, USE_BUFF_LIVE, DEEPSEEK_MODEL,
    BULL_MODEL, BEAR_MODEL, JUDGE_MODEL, ensure_dirs,
)
from database import (
    get_connection, resolve_skin, latest_price, change_pct, run_init, _utcnow,
    weapon_to_category,
)
from model_loader import get_loader
from prediction_service import predict_for_skin
from auth import (
    get_current_user, get_current_user_optional, get_admin_user,
    register_user, authenticate_user, list_users,
)
import rag
import agent_debate
import portfolio_diagnose
from inventory_forecast import aggregate_inventory_forecast
import llm
import quotes as quotes_svc
import settings_store
from agents.orchestrator import AIOrchestrator, grounded_chat_system_prompt
from agents.session_service import AgentSessionService
from agents.session_store import SessionNotFoundError
from agents.schemas import UserProfile

# ---------- 启动初始化 ----------
ensure_dirs()
run_init()
import settings_store
settings_store.apply_runtime_settings()
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
    horizon: Literal[7] = 7
    models: list[str] | None = None


class EntryRangeReq(BaseModel):
    skinId: str
    riskLevel: str = "moderate"


class ChatReq(BaseModel):
    message: str
    sessionId: str | None = None
    context: dict | None = None
    locale: str = "zh-CN"
    history: list[dict] | None = None
    capabilities: dict[str, bool] | None = None


class PortfolioReq(BaseModel):
    skinId: str
    buyPrice: float | None = None
    quantity: int = 1
    buyDate: str | None = None
    holdingType: str = "sim"


class InventoryReq(BaseModel):
    """手动添加真实库存饰品(对应前端 addInventoryItem)。"""
    skinId: str
    acquirePrice: float | None = None
    quantity: int = 1
    acquireDate: str | None = None
    source: str = "manual"


class SteamImportReq(BaseModel):
    """Steam 库存导入:链接 + steamLoginSecure cookie(必填,Steam 已限制匿名访问)。"""
    steamUrl: str
    cookie: str | None = None


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


class AIOrchestratorReq(BaseModel):
    message: str
    action: Literal["auto", "recommend", "predict", "debate", "chat", "qa"] = "auto"
    skinId: str | None = None
    sessionId: str | None = None
    targetAgent: Literal["bull", "bear", "judge"] | None = None
    budget: float | None = None
    horizonDays: Literal[7, 30] = 7
    riskLevel: Literal["low", "medium", "high"] = "medium"
    history: list[dict[str, str]] | None = None
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    capabilities: dict[str, bool] | None = None


class AITranslationReq(BaseModel):
    content: Any
    targetLocale: Literal["zh-CN", "en-US"]


class AgentSessionCreateReq(BaseModel):
    skinId: str
    budget: float | None = None
    horizonDays: int = 7
    riskLevel: Literal["low", "medium", "high"] = "medium"
    rounds: int = 1
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


class AgentSessionMessageReq(BaseModel):
    message: str
    targetAgent: Literal["bull", "bear", "judge"]
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


class AgentSessionRoundReq(BaseModel):
    message: str
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


# ============================================================
# 辅助:skin 序列化
# ============================================================
def _skin_to_dict(conn, row) -> dict:
    cur, cur_date = latest_price(conn, row["id"])
    ch24 = change_pct(conn, row["id"], 1)
    ch7 = change_pct(conn, row["id"], 7)
    # BUFF 未提供真日成交量;旧 sell_num 代理已停用,不再伪造 volume/liquidity
    vol24 = None
    liquidity = None
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
        "rarityName": row["rarity"] or "",
        "image": "🎮",
        # Steam CDN 饰品主图 base URL(无尺寸后缀);前端拼 /360fx360f 显示,缺失回落 emoji
        "imageUrl": row["image_url"] or None,
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
               limit: int = Query(1000, le=2000)):
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
        "change24h_desc": lambda x: -(x["change24h"] or 0),
        "rarity_desc": lambda x: -(x["rarity"] or 0),
        # volume_desc 保留兼容旧前端,但已无真实成交量,回退到 7 日涨跌
        "volume_desc": lambda x: -(x["change7d"] or 0),
    }
    items.sort(key=sort_map.get(sort, sort_map["change7d_desc"]))
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
# P0:数据库实时预测
# ============================================================
@app.post("/api/predict")
def predict(req: PredictReq):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        return predict_for_skin(
            conn=conn,
            skin=skin,
            horizon=req.horizon,
            requested_models=req.models,
            loader=_loader,
            now=_utcnow(),
            ttl_hours=PRED_CACHE_TTL_HOURS,
            circuit_breaker_enabled=PREDICTION_CIRCUIT_BREAKER_ENABLED,
        )


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
        safe_history = [
            {"role": item.get("role", "user"), "content": str(item.get("content", ""))[:2000]}
            for item in (req.history or [])[-8:]
            if item.get("role") in {"user", "assistant"}
        ]
        messages = [*safe_history, {"role": "user", "content": req.message}]
        system_prompt = grounded_chat_system_prompt(
            req.locale,
            user_message=req.message,
            capabilities=req.capabilities,
        )
        for ch in llm.chat_stream(messages, system_prompt=system_prompt, max_tokens=900):
            yield f"data: {json.dumps({'chunk': ch}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': DEEPSEEK_MODEL if LLM_ENABLED else 'unavailable'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _llm_provider_label(base_url: str) -> str:
    u = (base_url or "").lower()
    if "deepseek.com" in u:
        return "DeepSeek"
    if any(x in u for x in ("dashscope", "maas.aliyuncs.com", "token-plan.")):
        return "Bailian"
    return "LLM"


def _attach_ai_runtime(result: dict) -> dict:
    """Attach LLM/agent/hybrid runtime metadata to an orchestrator result."""
    from config import DEEPSEEK_BASE_URL as llm_base

    execution = llm.get_execution_status()
    # Never label a configured API key as a successful provider call.
    agent_mode = "live" if execution["liveCalls"] else (
        "degraded" if execution["fallbackCalls"] else "configured"
    )
    agent_session = result.get("agentSession") or {}
    snapshot = agent_session.get("marketSnapshot") or {}
    hybrid = snapshot.get("hybrid_prediction") or snapshot.get("hybridPrediction") or {}
    hybrid_mode = "unavailable" if hybrid.get("degraded") else "live"
    if result.get("type") == "prediction":
        hybrid_mode = "live"
    result["runtime"] = {
        "llm": {
            **execution,
            "provider": _llm_provider_label(llm_base),
            "model": DEEPSEEK_MODEL,
            "baseUrl": llm_base,
        },
        "agents": {
            "mode": agent_mode,
            "bullModel": BULL_MODEL,
            "bearModel": BEAR_MODEL,
            "judgeModel": JUDGE_MODEL,
        },
        "hybrid": {"mode": hybrid_mode, "model": hybrid.get("model")},
    }
    return result


@app.post("/api/ai/orchestrate")
def orchestrate_ai(req: AIOrchestratorReq):
    llm.reset_execution_status()
    service = AIOrchestrator(
        prediction_loader=lambda skin_id, horizon: predict(
            PredictReq(skinId=skin_id, horizon=horizon)
        )
    )
    try:
        result = service.handle(
            req.message,
            action=req.action,
            skin_id=req.skinId,
            session_id=req.sessionId,
            target_agent=req.targetAgent,
            budget=req.budget,
            horizon_days=req.horizonDays,
            risk_level=req.riskLevel,
            history=req.history,
            locale=req.locale,
            capabilities=req.capabilities,
        )
        return _attach_ai_runtime(result)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/ai/debate/stream")
def ai_debate_stream(req: AIOrchestratorReq):
    """SSE variant of orchestrate for debate flows: pushes one event as each
    agent (Bull / Bear / Judge) finishes so the timeline renders live."""
    llm.reset_execution_status()
    service = AIOrchestrator(
        prediction_loader=lambda skin_id, horizon: predict(
            PredictReq(skinId=skin_id, horizon=horizon)
        )
    )

    def gen():
        try:
            for event in service.handle_debate_stream(
                req.message,
                action=req.action,
                skin_id=req.skinId,
                session_id=req.sessionId,
                budget=req.budget,
                horizon_days=req.horizonDays,
                risk_level=req.riskLevel,
                history=req.history,
                locale=req.locale,
            ):
                if event.get("event") == "done":
                    _attach_ai_runtime(event["payload"])
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:  # surface as an SSE error event, not a 500
            yield (
                "data: "
                + json.dumps(
                    {"event": "error", "message": str(exc)}, ensure_ascii=False
                )
                + "\n\n"
            )

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/ai/translate")
def translate_ai_content(req: AITranslationReq):
    """Translate existing chat/debate output without generating a mock reply."""
    try:
        return {"content": llm.translate_content(req.content, req.targetLocale)}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


# Legacy debate endpoint is intentionally retained for the original card ->
# debate flow.  It is fast by default (seeded/deterministic evidence mode); a
# caller must explicitly opt into provider-backed live execution.
@app.post("/api/debate/{skin_id}")
def debate(
    skin_id: str,
    mode: str = "bull_bear",
    live: bool = False,
    seed: bool = Query(
        default=True,
        description="When false, skip Expo seed replay and run structured debate "
        "with the same Hybrid-V2 price as /api/predict.",
    ),
    budget: float | None = Query(default=None, gt=0),
    riskLevel: Literal["low", "medium", "high"] = Query(default="medium"),
    horizon: Literal[7] = Query(default=7),
    rounds: int | None = Query(default=None, ge=1, le=5),
    locale: Literal["zh-CN", "en-US"] = Query(default="zh-CN"),
):
    return agent_debate.debate(
        skin_id,
        live=live,
        mode=mode,
        budget=budget,
        risk_level=riskLevel,
        horizon_days=horizon,
        rounds=rounds,
        locale=locale,
        use_seed=seed,
    )


@app.post("/api/agent/sessions")
def create_agent_session(req: AgentSessionCreateReq):
    try:
        profile = UserProfile(
            budget=req.budget,
            horizon_days=req.horizonDays,
            risk_level=req.riskLevel,
            locale=req.locale,
        )
        return AgentSessionService().create(
            req.skinId,
            user_profile=profile,
            rounds=req.rounds,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/agent/sessions/{session_id}")
def get_agent_session(session_id: str):
    try:
        return AgentSessionService().get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/agent/sessions/{session_id}/message")
def send_agent_session_message(session_id: str, req: AgentSessionMessageReq):
    try:
        return AgentSessionService().send_message(
            session_id,
            message=req.message,
            target_agent=req.targetAgent,
            locale=req.locale,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/agent/sessions/{session_id}/round")
def run_agent_session_round(session_id: str, req: AgentSessionRoundReq):
    try:
        return AgentSessionService().run_round(
            session_id, message=req.message, locale=req.locale
        )
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
        model = str(req.ragEmbedModel).strip()
        if "rerank" in model.lower():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"模型「{model}」是 Rerank，不能用于向量检索。"
                    "请改成 text-embedding-v3 或 text-embedding-v4。"
                ),
            )
        updates["RAG_EMBED_MODEL"] = model
    if req.ragEmbedDim is not None:
        updates["RAG_EMBED_DIM"] = str(req.ragEmbedDim)
    if req.ragUseVector is not None:
        updates["RAG_USE_VECTOR"] = "1" if req.ragUseVector else "0"
    try:
        settings_store.set_settings(updates)
        return {"ok": True, "config": settings_store.public_config()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"保存失败: {type(e).__name__}: {e}",
        )


@app.get("/api/admin/status")
def admin_status(_: dict = Depends(get_admin_user)):
    """聚合健康检查 + 配置态(不发外网探针)。"""
    health: dict[str, Any]
    try:
        health = health_check_payload()
    except Exception as e:
        health = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    try:
        rag_status = rag.vector_status()
    except Exception as e:
        rag_status = {"mode": "error", "error": f"{type(e).__name__}: {e}"}
    try:
        config = settings_store.public_config()
    except Exception as e:
        config = {"error": f"{type(e).__name__}: {e}"}
    return {
        "health": health,
        "config": config,
        "rag": rag_status,
    }


@app.post("/api/admin/probe/llm")
def admin_probe_llm(_: dict = Depends(get_admin_user)):
    """探测 DeepSeek LLM 是否可用。"""
    import time
    t0 = time.time()
    try:
        # 探测前先同步运行时配置,避免管理页刚保存后读到旧 Key
        settings_store.apply_runtime_settings()
        from config import (
            DEEPSEEK_BASE_URL as llm_base,
            DEEPSEEK_MODEL as llm_model,
            LLM_ENABLED as llm_on,
        )
        if not llm_on:
            return {"ok": False, "provider": "deepseek", "latencyMs": 0,
                    "model": llm_model, "baseUrl": llm_base,
                    "error": "未配置 DEEPSEEK_API_KEY", "sample": ""}
        text = llm.chat_sync(
            [{"role": "user", "content": "请只回复两个字:正常"}],
            temperature=0.1,
            timeout=20.0,
        )
        ms = int((time.time() - t0) * 1000)
        sample = (text or "")[:240]
        # 已配置 Key 时的失败文案不再含 Mock；仍兼容旧降级文案
        failed = (
            not text
            or "调用失败" in text
            or "Live LLM call failed" in text
            or "实时 LLM 调用失败" in text
            or "Mock" in text
            or "[error:" in text
            or "401" in text
            or "Unauthorized" in text
            or "HTTP 4" in text
            or "HTTP 5" in text
        )
        if failed:
            # 尽量抽出 error= 行给管理端直接看
            err_line = next(
                (ln for ln in sample.splitlines() if ln.startswith("error=")),
                "",
            )
            return {
                "ok": False,
                "provider": "deepseek",
                "latencyMs": ms,
                "model": llm_model,
                "baseUrl": llm_base,
                "error": err_line[6:] if err_line.startswith("error=") else (
                    "LLM 调用失败：请核对 Model 与 Base URL 是否匹配同一服务商"
                ),
                "sample": sample,
            }
        return {
            "ok": True,
            "provider": "deepseek",
            "latencyMs": ms,
            "model": llm_model,
            "baseUrl": llm_base,
            "sample": sample,
        }
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
    from config import DEEPSEEK_MODEL as _llm_model, LLM_ENABLED as _llm_on
    models_status = {
        "lstm_hybrid": "ok" if _loader.tf_available else "unavailable",
        "gru": "ok" if _loader.tf_available else "unavailable",
        "trees": "ok",
        "deepseek": "ok" if _llm_on else "unavailable",
        "llm": "ok" if _llm_on else "unavailable",
        "rag": rag.vector_status().get("mode", "keyword"),
    }
    status = "ok" if (_loader.tf_available and n_price > 0) else "degraded"
    return {
        "status": status,
        "dataSources": {"skins": n_skins, "price_history": n_price,
                        "portfolio": n_portfolio, "news": n_news,
                        "users": n_users, "buff_live": USE_BUFF_LIVE},
        "models": models_status,
        "llm": {
            "enabled": bool(_llm_on),
            "model": _llm_model if _llm_on else None,
        },
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
# 🆕 我的库存(holding_type='real' 的 portfolio 项)+ Steam 导入
# ============================================================
@app.get("/api/inventory")
def get_inventory(current_user: dict = Depends(get_current_user_optional)):
    """返回当前用户的真实库存(holding_type='real')。字段对齐前端 loadInventoryFromApi。"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.*, s.market_hash_name, s.slug
               FROM portfolio p JOIN skins s ON s.id=p.skin_id
               WHERE p.user_id=? AND p.holding_type='real' ORDER BY p.id""",
            (current_user["id"],),
        ).fetchall()
        items = []
        for r in rows:
            cur, _ = latest_price(conn, r["skin_id"])
            note = r["note"] or ""
            items.append({
                "id": r["id"], "skinId": r["slug"], "name": r["market_hash_name"],
                "acquirePrice": r["buy_price"], "quantity": r["quantity"],
                "acquireDate": r["buy_date"],
                "source": "steam" if "steam" in note.lower() else "manual",
                "currentPrice": round(cur, 2) if cur else None,
            })
        total = round(sum((i["currentPrice"] or 0) * i["quantity"] for i in items), 2)
    return {"total": total, "items": items}


@app.post("/api/inventory")
def add_inventory(req: InventoryReq, current_user: dict = Depends(get_current_user_optional)):
    """手动添加一件真实库存饰品。"""
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        cur = _utcnow().isoformat()
        c = conn.execute(
            """INSERT INTO portfolio(skin_id, holding_type, buy_price, buy_date, quantity, note, created_at, user_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (skin["id"], "real", req.acquirePrice, req.acquireDate,
             req.quantity, req.source, cur, current_user["id"]),
        )
        conn.commit()
        pid = c.lastrowid
    return {"id": pid, "skinId": skin["slug"], "acquirePrice": req.acquirePrice,
            "quantity": req.quantity, "acquireDate": req.acquireDate, "source": req.source}


@app.delete("/api/inventory/{item_id}")
def delete_inventory(item_id: int, current_user: dict = Depends(get_current_user_optional)):
    with get_connection() as conn:
        r = conn.execute(
            "DELETE FROM portfolio WHERE id=? AND user_id=? AND holding_type='real'",
            (item_id, current_user["id"]),
        )
        conn.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "not found")
    return {"success": True}


@app.post("/api/inventory/steam/import")
def import_steam_inventory(req: SteamImportReq, current_user: dict = Depends(get_current_user_optional)):
    """从 Steam 链接拉取 CS2 库存,映射到 skins 表,写入真实库存(holding_type='real')。"""
    import steam_inventory
    from config import STEAM_COOKIE

    steamid = steam_inventory.parse_steamid(req.steamUrl)
    if not steamid:
        raise HTTPException(400, "链接格式不对,需为 https://steamcommunity.com/profiles/<你的ID>/inventory")

    cookie = (req.cookie or STEAM_COOKIE or "").strip() or None
    if not cookie:
        # Steam 已限制匿名访问库存,cookie 现为必填(前端弹窗也会拦截空值)
        raise HTTPException(400, "请填写 steamLoginSecure Cookie,否则无法拉取 Steam 库存")

    try:
        items = steam_inventory.fetch_cs2_inventory(steamid, cookie=cookie)
    except steam_inventory.SteamPrivate:
        raise HTTPException(403, "库存私有或被拒,请在弹窗里填写 steamLoginSecure cookie")
    except steam_inventory.SteamNotFound:
        raise HTTPException(404, "账号不存在或无 CS2 库存")
    except steam_inventory.SteamRateLimited:
        raise HTTPException(429, "Steam 限流,请稍后再试或填写 cookie")
    except steam_inventory.SteamError as e:
        raise HTTPException(502, f"Steam 拉取失败: {e}")

    today = _utcnow().strftime("%Y-%m-%d")
    imported, skipped, unmatched = 0, 0, []
    with get_connection() as conn:
        # 预取该用户已有 real 持仓的 skin_id,去重
        existing = {r["skin_id"] for r in conn.execute(
            "SELECT skin_id FROM portfolio WHERE user_id=? AND holding_type='real'",
            (current_user["id"],)).fetchall()}
        # 按 market_hash_name 批量查 skins
        names = [it["market_hash_name"] for it in items]
        skin_map = {r["market_hash_name"]: r["id"] for r in conn.execute(
            "SELECT id, market_hash_name FROM skins WHERE market_hash_name IN (%s)"
            % ",".join("?" * len(names)), names).fetchall()} if names else {}

        now = _utcnow().isoformat()
        for it in items:
            name, qty = it["market_hash_name"], it["quantity"]
            skin_id = skin_map.get(name)
            if not skin_id:
                unmatched.append({"name": name, "qty": qty})
                continue
            if skin_id in existing:
                skipped += 1
                continue
            cur, _ = latest_price(conn, skin_id)
            conn.execute(
                """INSERT INTO portfolio(skin_id, holding_type, buy_price, buy_date, quantity, note, created_at, user_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (skin_id, "real", cur, today, qty, f"Steam导入 {today}", now, current_user["id"]),
            )
            existing.add(skin_id)
            imported += 1
        conn.commit()

    return {"imported": imported, "skipped": skipped,
            "unmatched": unmatched, "total_items": len(items), "steamid": steamid}


# ============================================================
# 🆕 P1:Portfolio value_history(SQL 聚合,需登录)
# ============================================================
@app.get("/api/portfolio/value_history")
def portfolio_value_history(days: int = 90, current_user: dict = Depends(get_current_user_optional)):
    """模拟持仓(holding_type=sim)总市值走势。无模拟仓时返回空曲线,不混入真实库存。"""
    empty = {"dates": [], "values": [], "predictedDates": [], "predictedValues": [], "total": 0}
    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM portfolio WHERE user_id=? AND holding_type='sim'",
            (current_user["id"],),
        ).fetchone()[0]
        if not n:
            return empty
        rows = conn.execute(
            """SELECT p.date AS date, SUM(p.price * po.quantity) AS value
               FROM price_history p
               JOIN portfolio po ON po.skin_id = p.skin_id
               WHERE po.user_id=? AND po.holding_type='sim'
                 AND p.date >= date((SELECT MAX(date) FROM price_history), ?)
               GROUP BY p.date ORDER BY p.date""",
            (current_user["id"], f"-{days} days"),
        ).fetchall()
    if not rows:
        return empty
    dates = [r["date"] for r in rows]
    values = [round(r["value"], 2) for r in rows]
    return {
        "dates": dates,
        "values": values,
        "predictedDates": [],
        "predictedValues": [],
        "total": values[-1] if values else 0,
    }


@app.get("/api/inventory/value_history")
def inventory_value_history(days: int = 90, current_user: dict = Depends(get_current_user_optional)):
    """真实库存(holding_type=real)总市值走势。无库存时返回空曲线,不伪造数据。"""
    empty = {
        "dates": [], "values": [], "predictedDates": [], "predictedValues": [],
        "predicted7Dates": [], "predicted7Values": [],
        "trend30Dates": [], "trend30Values": [], "forecastAnchorTotal": 0,
        "predictionCoverage": {"totalItems": 0, "predictedItems": 0, "trendItems": 0,
                               "itemRatio": 0.0, "valueRatio": 0.0},
        "modelVersion": None, "total": 0,
    }
    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM portfolio WHERE user_id=? AND holding_type='real'",
            (current_user["id"],),
        ).fetchone()[0]
        if not n:
            return empty
        rows = conn.execute(
            """SELECT p.date AS date, SUM(p.price * po.quantity) AS value
               FROM price_history p
               JOIN portfolio po ON po.skin_id = p.skin_id
               WHERE po.user_id=? AND po.holding_type='real'
                 AND p.date >= date((SELECT MAX(date) FROM price_history), ?)
               GROUP BY p.date ORDER BY p.date""",
            (current_user["id"], f"-{days} days"),
        ).fetchall()
        forecast = aggregate_inventory_forecast(
            conn,
            user_id=current_user["id"],
            loader=_loader,
            now=_utcnow(),
            ttl_hours=PRED_CACHE_TTL_HOURS,
            circuit_breaker_enabled=PREDICTION_CIRCUIT_BREAKER_ENABLED,
        )
    if not rows:
        return empty
    dates = [r["date"] for r in rows]
    values = [round(r["value"], 2) for r in rows]
    predicted_dates = forecast["predicted7Dates"]
    predicted_values = forecast["predicted7Values"]
    return {
        "dates": dates,
        "values": values,
        "predictedDates": predicted_dates,
        "predictedValues": predicted_values,
        **forecast,
        "total": values[-1] if values else 0,
    }


# ============================================================
# 🆕 P1:组合诊断(需登录)
# ============================================================
@app.post("/api/portfolio/diagnose")
def diagnose_portfolio(
    locale: Literal["zh-CN", "en-US"] = Query(default="zh-CN"),
    current_user: dict = Depends(get_current_user_optional),
):
    """只诊断模拟持仓(sim)。空仓返回 empty 标记,不抛 400。locale 控制 AI 总结语言。"""
    result = portfolio_diagnose.diagnose(
        user_id=current_user["id"], holding_type="sim", locale=locale,
    )
    if result.get("empty") or "error" in result:
        english = str(locale or "").lower().startswith("en")
        msg = result.get("error") or (
            "Paper portfolio is empty — add holdings first"
            if english
            else "模拟持仓为空，请先添加持仓"
        )
        return {
            "empty": True,
            "summary": msg,
            "aiSummary": msg,
            "totalItems": 0,
            "valueRange": None,
            "adjustments": [],
            "riskTopN": [],
            "locale": "en-US" if english else "zh-CN",
        }
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


@app.post("/api/news/fetch")
def fetch_news(aggressive: bool = Query(default=True)):
    """手动触发 RSS 抓取。默认 aggressive=强化力度(更多源条目+更长窗口)。"""
    import scheduler
    result = scheduler.fetch_rss_news(aggressive=aggressive)
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return {"ok": True, **result}

@app.get("/api/daily-report")
def daily_report(
    date: str | None = None,
    refresh: bool = False,
    locale: Literal["zh-CN", "en-US"] = Query(default="zh-CN"),
):
    # Expo 种子可提供文案兜底，但 metrics / 文案数字必须与当前库一致；
    # aiSummary 若过期、Mock、locale 不符、或数字与 live metrics 不一致，则现场刷新。
    # refresh=1 时强制重新生成整份日报（「重新生成」按钮）。
    import scheduler
    if refresh:
        return scheduler.generate_daily_report(locale=locale)

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
            need_summary_refresh = (
                scheduler.summary_is_degraded(rep.get("aiSummary"))
                or scheduler.summary_invents_current_events(rep.get("aiSummary"))
                or scheduler.summary_locale_mismatch(rep.get("aiSummary"), locale)
                or scheduler.summary_metrics_mismatch(rep.get("aiSummary"), live_metrics)
            )
            if need_summary_refresh:
                # Prefer live holdings from DB; fall back to empty (never keep stale seed portfolio)
                try:
                    with get_connection() as conn:
                        positions = conn.execute(
                            """SELECT s.market_hash_name, p.quantity
                               FROM portfolio p JOIN skins s ON s.id=p.skin_id
                               LIMIT 20"""
                        ).fetchall()
                    portfolio_text = (
                        ("No holdings" if str(locale).startswith("en") else "无持仓")
                        if not positions
                        else "; ".join(
                            f"{r['market_hash_name']} x{r['quantity']}" for r in positions
                        )
                    )
                    rep["portfolio"] = [
                        {"name": r["market_hash_name"], "quantity": r["quantity"]}
                        for r in positions
                    ]
                except Exception:
                    portfolio_text = "No holdings" if str(locale).startswith("en") else "无持仓"
                    rep["portfolio"] = []
                try:
                    rep["sources"] = rag.retrieve_daily_sources(limit=6)
                except Exception:
                    rep["sources"] = rep.get("sources") or []
                summary, provider = scheduler.refresh_ai_summary(
                    live_metrics,
                    portfolio_text=portfolio_text,
                    sources=rep.get("sources") or [],
                    locale=locale,
                )
                rep["aiSummary"] = summary
                rep["summaryProvider"] = provider
                rep["locale"] = locale
                rep["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rep["generatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    seed.write_text(
                        json.dumps(rep, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            else:
                rep["locale"] = locale
                rep.setdefault("summaryProvider", "seed")
            return rep
        except Exception:
            pass
    return scheduler.generate_daily_report(locale=locale)

@app.post("/api/rag/ask")
def rag_ask(req: RagAskReq):
    """RAG 问答: 检索知识库/资讯 → 生成带引用的答案。"""
    return rag.ask(req.query, top_k=req.topK)


# ============================================================
# P2:双 Agent 辩论(双模式)
# ============================================================
@app.post("/api/debate/{skin_id}")
def debate(skin_id: str, mode: str = "bull_bear", live: bool = False, seed: bool = True):
    return agent_debate.debate(skin_id, live=live, mode=mode, use_seed=seed)


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

def _read_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_shap_rows(items: Any) -> list[dict[str, Any]]:
    """统一 SHAP / 特征重要性条目为 {feature, importance, meanAbsShap}。"""
    if isinstance(items, dict):
        items = (
            items.get("feature_importance")
            or items.get("feature_importance_all_classes")
            or items.get("features")
            or []
        )
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        feature = row.get("feature") or row.get("name")
        if not feature:
            continue
        raw = row.get("mean_abs_shap", row.get("meanAbsShap", row.get("importance", row.get("value"))))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value < 0 or value != value:  # NaN
            continue
        out.append({
            "feature": str(feature),
            "importance": value,
            "meanAbsShap": value,
        })
    out.sort(key=lambda r: r["importance"], reverse=True)
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    return out


def _load_shap_artifact(model_key: str) -> list[dict[str, Any]]:
    """优先读真实 SHAP 产物 shap_results*.json，再回退旧版 shap_features.json。"""
    key = (model_key or "xgboost").strip().lower()
    aliases = {
        "xgboost": "xgboost",
        "xgb": "xgboost",
        "lightgbm": "lightgbm",
        "lgbm": "lightgbm",
        "lgb": "lightgbm",
        "average": "average",
        "avg": "average",
    }
    key = aliases.get(key, key)

    real_files = {
        "xgboost": [
            OUTPUT_DIR / "shap_results.json",
            OUTPUT_DIR / "shap_xgboost_results.json",
        ],
        "lightgbm": [
            OUTPUT_DIR / "shap_lightgbm_results.json",
            OUTPUT_DIR / "shap_lgbm_results.json",
        ],
    }

    def from_real(mkey: str) -> list[dict[str, Any]]:
        for path in real_files.get(mkey, []):
            payload = _read_json_file(path)
            rows = _normalize_shap_rows(payload)
            if rows:
                return rows
        return []

    if key in ("xgboost", "lightgbm"):
        rows = from_real(key)
        if rows:
            return rows

    if key == "average":
        buckets: dict[str, list[float]] = {}
        for mkey in ("xgboost", "lightgbm"):
            for row in from_real(mkey):
                buckets.setdefault(row["feature"], []).append(float(row["importance"]))
        if buckets:
            merged = [
                {"feature": feat, "importance": sum(vals) / len(vals)}
                for feat, vals in buckets.items()
            ]
            return _normalize_shap_rows(merged)

    legacy = _read_json_file(OUTPUT_DIR / "shap_features.json")
    if isinstance(legacy, dict):
        models = legacy.get("models")
        if isinstance(models, dict):
            block = models.get(key) or {}
            if key == "average" and not block:
                # average of model blocks when present
                buckets = {}
                for mkey in ("xgboost", "lightgbm"):
                    b = models.get(mkey) or {}
                    feats = b.get("features") if isinstance(b, dict) else None
                    for row in _normalize_shap_rows(feats or []):
                        buckets.setdefault(row["feature"], []).append(float(row["importance"]))
                if buckets:
                    merged = [
                        {"feature": feat, "importance": sum(vals) / len(vals)}
                        for feat, vals in buckets.items()
                    ]
                    return _normalize_shap_rows(merged)
            feats = block.get("features") if isinstance(block, dict) else None
            rows = _normalize_shap_rows(feats or [])
            if rows:
                return rows
        if key == "average":
            avg_rows = _normalize_shap_rows(legacy.get("average"))
            if avg_rows:
                return avg_rows
            buckets = {}
            for mkey in ("xgboost", "lightgbm"):
                for row in _normalize_shap_rows(legacy.get(mkey) or []):
                    buckets.setdefault(row["feature"], []).append(float(row["importance"]))
            if buckets:
                merged = [
                    {"feature": feat, "importance": sum(vals) / len(vals)}
                    for feat, vals in buckets.items()
                ]
                return _normalize_shap_rows(merged)
        rows = _normalize_shap_rows(legacy.get(key) or legacy.get("xgboost") or [])
        if rows:
            return rows

    return []


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
    key_alias = {
        "lstm_c": "LSTM-C", "LSTM-C": "LSTM-C",
        "lstm_d": "LSTM-D", "LSTM-D": "LSTM-D",
        "hybrid": "Hybrid", "Hybrid": "Hybrid",
        "hybrid-v2-raw": "Hybrid-V2-Raw", "Hybrid-V2-Raw": "Hybrid-V2-Raw",
        "hybrid-v2-calibrated": "Hybrid-V2-Calibrated",
        "Hybrid-V2-Calibrated": "Hybrid-V2-Calibrated",
        "gru": "GRU", "GRU": "GRU",
        "rf": "Random Forest", "RF": "Random Forest", "Random Forest": "Random Forest",
        "lightgbm": "LightGBM", "LightGBM": "LightGBM",
        "xgboost": "XGBoost", "XGBoost": "XGBoost",
    }

    def _load_return_map(path) -> dict[str, float]:
        out: dict[str, float] = {}
        if not path.exists():
            return out
        try:
            bt = json.loads(path.read_text(encoding="utf-8"))
            fee = bt.get("fee_0.0000") if isinstance(bt, dict) else None
            if not isinstance(fee, dict):
                return out
            for k, blk in fee.items():
                if not isinstance(blk, dict) or blk.get("returnPct") is None:
                    continue
                rp = float(blk["returnPct"])
                display = key_alias.get(k, k)
                out[k] = rp
                out[display] = rp
        except Exception:
            pass
        return out

    ret_map = _load_return_map(bt_path)

    meta_by_name = {
        r.get("name"): r for r in (mc.get("regression") or []) if isinstance(r, dict)
    }

    regression: list[dict[str, Any]] = []
    horizon_steps = None
    # 覆盖件数：优先用 compare_results_* 里模型自带的 items（volume-free 后为 155），
    # 避免被陈旧 model_comparison.json 的 113 盖住。
    n_items = None
    # 1) fair-test compare_results_* 优先
    if cmp_path.exists():
        try:
            cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
            horizon_steps = cmp.get("horizon_steps") if isinstance(cmp, dict) else None
            if isinstance(cmp, dict):
                n_items = cmp.get("n_items") or cmp.get("nItems")
            models_blk = cmp.get("models") if isinstance(cmp, dict) else None
            if n_items is None and isinstance(models_blk, dict):
                for name in ("LSTM-C", "Hybrid", "XGBoost", "LightGBM", "RF", "Random Forest"):
                    blk = models_blk.get(name)
                    if isinstance(blk, dict) and blk.get("items"):
                        n_items = blk.get("items")
                        break
                if n_items is None:
                    for blk in models_blk.values():
                        if isinstance(blk, dict) and blk.get("items"):
                            n_items = blk.get("items")
                            break
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

    if n_items is None:
        n_items = mc.get("nItems") if isinstance(mc, dict) else None

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
    historical = {
        "track": "historical",
        "regression": regression,
        "classification": classification,
        "buyAndHold": buy_hold,
        "horizonSteps": horizon_steps or mc.get("horizonSteps") or 7,
        "metadata": {
            "label": "2019-2023 canonical fair test",
            "dataSource": "steam-history-canonical-test",
            "items": n_items or mc.get("nItems") or 155,
        },
    }
    online_path = OUTPUT_DIR / "online_model_comparison.json"
    online_bt_path = OUTPUT_DIR / "backtest_online" / "backtest_results.json"
    online_ret_map = _load_return_map(online_bt_path)
    online: dict[str, Any] = {
        "track": "online", "regression": [], "classification": [],
        "horizonSteps": 7, "metadata": {}, "trend30": None,
    }
    if online_path.exists():
        try:
            payload = json.loads(online_path.read_text(encoding="utf-8"))
            online["regression"] = [
                {
                    "name": name,
                    "type": "DL" if name in ("LSTM-C", "LSTM-D") else "Fusion",
                    "course": "recent 180d online holdout",
                    "rmse": metrics.get("rmse"),
                    "mae": metrics.get("mae"),
                    "mape": metrics.get("mapePct"),
                    "r2": metrics.get("r2"),
                    "returnPct": (
                        online_ret_map.get(name)
                        or online_ret_map.get(key_alias.get(name, name))
                        or metrics.get("returnPct")
                    ),
                    "directionAccuracy": metrics.get("directionAccuracy"),
                    "over30Rate": metrics.get("over30Rate"),
                }
                for name, metrics in (payload.get("models") or {}).items()
                if isinstance(metrics, dict)
            ]
            online["metadata"] = {
                key: payload.get(key) for key in (
                    "dataSource", "split", "dateRange", "items", "decisions", "modelVersion"
                )
            }
            if online_ret_map and "backtestSource" not in online["metadata"]:
                online["metadata"]["backtestSource"] = "backtest_online/backtest_results.json"
        except Exception:
            pass
    trend_path = OUTPUT_DIR / "trend_30d_results_test.json"
    if trend_path.exists():
        try:
            trend = json.loads(trend_path.read_text(encoding="utf-8"))
            overall = trend.get("overall") or {}
            online["trend30"] = {
                "name": "Keras-Seq2Seq-30D", "horizonSteps": 30,
                "split": trend.get("split"), "items": trend.get("items"),
                "rows": trend.get("rows"), "rmse": overall.get("rmse"),
                "mae": overall.get("mae"), "mape": overall.get("mape_pct"),
                "r2": overall.get("r2"), "coverage": overall.get("coverage"),
            }
        except Exception:
            pass
    return {
        **historical,
        "tracks": {"historical": historical, "online": online},
        "defaultTrack": "historical",
        "nItems": n_items or mc.get("nItems") or 155,
    }


@app.get("/api/models/backtest")
def models_backtest(
    days: int = 60,
    skinId: str | None = None,
    track: Literal["historical", "online"] = "historical",
):
    p = OUTPUT_DIR / ("backtest_online" if track == "online" else "backtest") / "backtest_curves.json"
    if track == "historical" and not p.exists():
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
                    "Hybrid-V2-Raw": ("hybrid_v2_raw", "Hybrid-V2-Raw"),
                    "Hybrid-V2-Calibrated": ("hybrid_v2_calibrated", "Hybrid-V2-Calibrated"),
                    "GRU": ("gru", "GRU"),
                    "Random Forest": ("rf", "RF", "Random Forest"),
                    "LightGBM": ("lightgbm", "LightGBM"),
                    "XGBoost": ("xgboost", "XGBoost"),
                }
                # 图表主系列：策略模型 + Buy&Hold（避免一次塞太多线）
                prefer_labels = (
                    ("LSTM-C", "LSTM-D", "Hybrid-V2-Raw", "Hybrid-V2-Calibrated")
                    if track == "online"
                    else ("LSTM-C", "LSTM-D", "Hybrid", "Random Forest", "XGBoost")
                )

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
                    """全程起点=100 的净值指数（先归一再截窗口，避免后期平台期被压成直线）。"""
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

                # 先按全样本起点归一，再截最近 N 天（否则窗口内各自 /首点 会把已上涨的策略压成近乎水平线）
                series = {k: _reindex(v) for k, v in series_raw.items()}
                if days and days > 0 and len(dates) > days:
                    dates = dates[-days:]
                    series = {k: v[-days:] for k, v in series.items()}

                return {
                    "track": track,
                    "dates": dates,
                    "series": series,
                    "indexed": True,
                    "indexBase": "full_start",
                    "note": (
                        "净值以整段回测起点=100；策略含现金/固定仓位，波动通常小于满仓 Buy&Hold。"
                    ),
                }
            return raw
        except Exception:
            pass
    return {"track": track, "dates": [], "series": {}}


@app.get("/api/models/shap")
def models_shap(model: str = "xgboost"):
    """返回模型特征重要性（优先真实 mean |SHAP| 产物）。"""
    return _load_shap_artifact(model)



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
