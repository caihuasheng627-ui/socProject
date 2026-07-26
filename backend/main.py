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
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import (
    PRED_CACHE_TTL_HOURS, OUTPUT_DIR, LLM_ENABLED, DEEPSEEK_MODEL,
    BULL_MODEL, BEAR_MODEL, JUDGE_MODEL, ensure_dirs,
)
from database import (
    get_connection, resolve_skin, latest_price, change_pct, run_init, _utcnow,
    weapon_to_category,
)
from model_loader import get_loader
import rag
import agent_debate
import portfolio_diagnose
import llm
from agents import AIOrchestrator, AgentSessionService, SessionNotFoundError, UserProfile

# ---------- 启动初始化 ----------
ensure_dirs()
run_init()
_loader = get_loader()


def _ai_runtime_metadata(
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose configuration separately from the outcome of the current call."""

    execution = execution or llm.get_execution_status()
    llm_mode = execution["mode"]
    hybrid_mode = "live" if _loader.tf_available else "mock"
    return {
        "llm": {
            "mode": llm_mode,
            "enabled": LLM_ENABLED,
            "configured": LLM_ENABLED,
            "provider": "DeepSeek" if LLM_ENABLED else "Local",
            "model": DEEPSEEK_MODEL if LLM_ENABLED else "template-fallback",
            "calls": execution.get("calls", 0),
            "liveCalls": execution.get("liveCalls", 0),
            "fallbackCalls": execution.get("fallbackCalls", 0),
            "lastError": execution.get("lastError"),
        },
        "agents": {
            "mode": llm_mode,
            "bullModel": BULL_MODEL if LLM_ENABLED else "bull-rule-fallback",
            "bearModel": BEAR_MODEL if LLM_ENABLED else "bear-rule-fallback",
            "judgeModel": JUDGE_MODEL if LLM_ENABLED else "judge-rule-fallback",
        },
        "hybrid": {
            "mode": hybrid_mode,
            "model": "trained-hybrid" if _loader.tf_available else "trend-fallback",
        },
    }

app = FastAPI(title="SkinVision AI API", version="1.1.0",
              description="CS2 饰品 AI 智能分析平台后端(组员 3)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 课程演示:前端 CDN 直连
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
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


class AIOrchestratorReq(BaseModel):
    message: str
    action: Literal["auto", "recommend", "predict", "debate", "chat"] = "auto"
    skinId: str | None = None
    sessionId: str | None = None
    targetAgent: Literal["bull", "bear", "judge"] | None = None
    budget: float | None = None
    horizonDays: Literal[7, 30] = 7
    riskLevel: Literal["low", "medium", "high"] = "medium"
    history: list[dict[str, str]] | None = None
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


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
    # 流动性:由近 7 日均成交量映射 0-100
    vol_row = conn.execute(
        "SELECT AVG(daily_volume) v FROM price_history WHERE skin_id=? "
        "AND date >= (SELECT MAX(date) FROM price_history WHERE skin_id=?)",
        (row["id"], row["id"]),
    ).fetchone()
    vol24 = int(vol_row["v"] or 0) if vol_row else 0
    liquidity = min(100, int(vol24 / 50))
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
        "source": "BUFF",
        "weaponType": row["weapon_type"],
    }


# ============================================================
# P0:健康检查
# ============================================================
@app.get("/api/health")
def health():
    with get_connection() as conn:
        n_skins = conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
        n_price = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        n_portfolio = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        n_news = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    models_status = {
        "lstm_hybrid": "ok" if _loader.tf_available else "mock",
        "gru": "ok" if _loader.tf_available else "mock",
        "trees": "ok",
        "deepseek": "ok" if LLM_ENABLED else "mock",
    }
    status = "ok" if (_loader.tf_available and n_price > 0) else "degraded"
    return {
        "status": status,
        "dataSources": {"skins": n_skins, "price_history": n_price,
                        "portfolio": n_portfolio, "news": n_news,
                        "buff_live": False},
        "models": models_status,
        "aiRuntime": _ai_runtime_metadata(),
        "timestamp": _utcnow().isoformat(),
    }


# ============================================================
# P0:行情
# ============================================================
@app.get("/api/skins")
def list_skins(category: str | None = None, sort: str = "volume_desc",
               limit: int = Query(200, le=500)):
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
        decision_cur = live_cur
        decision_date = None
        if cached:
            preds = [{"model": c["model"], "type": c["type"],
                      "price": c["predicted_price"],
                      "priceUsd": c["predicted_price"],
                      "change": c["change_pct"], "confidence": c["confidence"]} for c in cached]
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
                preds.append({
                    "model": r["model"], "type": r.get("type", "ML"),
                    "price": r["predicted_price"],
                    "priceUsd": r["predicted_price"],
                    "change": r["change_pct"],
                    "confidence": r["confidence"],
                    "decisionDate": r.get("date"),
                })
                conn.execute(
                    """INSERT INTO predictions(skin_id, horizon, model, type, predicted_price,
                       current_price, change_pct, confidence, generated_at, expires_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (skin_id, req.horizon, r["model"], r.get("type", "ML"),
                     r["predicted_price"], r["current_price"], r["change_pct"],
                     r["confidence"], now_iso, exp_iso),
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
        language = "English" if req.locale == "en-US" else "Simplified Chinese"
        messages = [
            {"role": "system", "content": f"Always answer in {language}."},
            {"role": "user", "content": req.message},
        ]
        for ch in llm.chat_stream(messages):
            yield f"data: {json.dumps({'chunk': ch}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': 'deepseek-chat' if LLM_ENABLED else 'mock'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/ai/orchestrate")
def orchestrate_ai(req: AIOrchestratorReq):
    """Single AI Chat entry point for recommendation, prediction and debate."""

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
        )
        result["runtime"] = _ai_runtime_metadata(llm.get_execution_status())
        return result
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ============================================================
# 🆕 P0:Portfolio CRUD
# ============================================================
@app.get("/api/portfolio")
def get_portfolio():
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.*, s.market_hash_name, s.slug, s.category
               FROM portfolio p JOIN skins s ON s.id=p.skin_id ORDER BY p.id"""
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
def add_portfolio(req: PortfolioReq):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        cur = _utcnow().isoformat()
        c = conn.execute(
            """INSERT INTO portfolio(skin_id, holding_type, buy_price, buy_date, quantity, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (skin["id"], req.holdingType, req.buyPrice, req.buyDate,
             req.quantity, None, cur),
        )
        conn.commit()
        pid = c.lastrowid
    return {"id": pid, "skinId": skin["slug"], "holdingType": req.holdingType,
            "buyPrice": req.buyPrice, "quantity": req.quantity, "buyDate": req.buyDate}


@app.delete("/api/portfolio/{item_id}")
def delete_portfolio(item_id: int):
    with get_connection() as conn:
        r = conn.execute("DELETE FROM portfolio WHERE id=?", (item_id,))
        conn.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "not found")
    return {"success": True}


# ============================================================
# 🆕 P1:Portfolio value_history(SQL 聚合)
# ============================================================
@app.get("/api/portfolio/value_history")
def portfolio_value_history(days: int = 90):
    """portfolio JOIN price_history GROUP BY date → 总市值曲线。"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.date AS date, SUM(p.price * po.quantity) AS value
               FROM price_history p
               JOIN portfolio po ON po.skin_id = p.skin_id
               WHERE p.date >= date((SELECT MAX(date) FROM price_history), ?)
               GROUP BY p.date ORDER BY p.date""",
            (f"-{days} days",),
        ).fetchall()
    if not rows:
        # 兜底:用各持仓最新价 × 数量 单点
        return {"dates": [], "values": [], "total": 0}
    dates = [r["date"] for r in rows]
    values = [round(r["value"], 2) for r in rows]
    return {"dates": dates, "values": values, "total": values[-1] if values else 0}


# ============================================================
# 🆕 P1:组合诊断
# ============================================================
@app.post("/api/portfolio/diagnose")
def diagnose_portfolio():
    result = portfolio_diagnose.diagnose()
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
def get_news(limit: int = 20, sentiment: str | None = None, source: str | None = None):
    with get_connection() as conn:
        q = "SELECT * FROM news WHERE 1=1"
        params: list[Any] = []
        if sentiment:
            q += " AND sentiment=?"; params.append(sentiment)
        if source:
            q += " AND source=?"; params.append(source)
        q += " ORDER BY published_at DESC LIMIT ?"; params.append(limit)
        rows = conn.execute(q, params).fetchall()
    return [{"id": r["id"], "title": r["title"], "summary": r["summary"],
             "source": r["source"], "time": r["published_at"],
             "sentiment": r["sentiment"], "impact": r["impact"],
             "relatedSkins": r["related_skins"].split(",") if r["related_skins"] else []}
            for r in rows]


@app.get("/api/daily-report")
def daily_report(date: str | None = None):
    # 优先读 Expo 预生成日报(兜底),否则现场生成
    from config import SEED_DIR
    seed = SEED_DIR / "seed_daily_report.json"
    if seed.exists():
        try:
            return json.loads(seed.read_text(encoding="utf-8"))
        except Exception:
            pass
    import scheduler
    return scheduler.generate_daily_report()


# ============================================================
# P2:Bull / Bear / Judge 多 Agent 辩论(双模式)
# ============================================================
@app.post("/api/debate/{skin_id}")
def debate(
    skin_id: str,
    mode: str = "bull_bear",
    live: bool = False,
    budget: float | None = Query(default=None, gt=0),
    riskLevel: str = Query(default="medium", pattern="^(low|medium|high)$"),
    horizon: int = Query(default=7, ge=7, le=7),
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
    )


# ============================================================
# 用户参与式 Agent 会话
# ============================================================
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
# 预警
# ============================================================
@app.get("/api/alerts")
def get_alerts(active: bool | None = None):
    with get_connection() as conn:
        q = """SELECT a.*, s.market_hash_name, s.slug FROM alerts a
               JOIN skins s ON s.id=a.skin_id WHERE 1=1"""
        params: list[Any] = []
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
def create_alert(req: AlertReq):
    with get_connection() as conn:
        skin = resolve_skin(conn, req.skinId)
        if not skin:
            raise HTTPException(404, "skin not found")
        c = conn.execute(
            "INSERT INTO alerts(skin_id, type, target_price, note, active, created_at) VALUES (?,?,?,?,1,?)",
            (skin["id"], req.type, req.targetPrice, req.note, _utcnow().isoformat()),
        )
        conn.commit()
        return {"id": c.lastrowid, "skinId": skin["slug"], "type": req.type,
                "targetPrice": req.targetPrice, "active": True, "triggered": False}


@app.delete("/api/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: int):
    with get_connection() as conn:
        r = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        conn.commit()
        if r.rowcount == 0:
            raise HTTPException(404, "not found")


# ============================================================
# 模型对比 / 回测 / SHAP
# ============================================================
@app.get("/api/models/comparison")
def models_comparison():
    mc_path = OUTPUT_DIR / "model_comparison.json"
    cmp_path = OUTPUT_DIR / "compare_results_test.json"
    if not cmp_path.exists():
        cmp_path = OUTPUT_DIR / "compare_results.json"
    regression, classification = [], []
    if mc_path.exists():
        mc = json.loads(mc_path.read_text(encoding="utf-8"))
        regression = mc.get("regression", [])
    # 无独立 model_comparison.json 时,从 compare_results_* 组装回归表
    if not regression and cmp_path.exists():
        cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
        models_blk = cmp.get("models") if isinstance(cmp, dict) else None
        if isinstance(models_blk, dict):
            for name, blk in models_blk.items():
                if not isinstance(blk, dict):
                    continue
                regression.append({
                    "name": name,
                    "type": ("DL" if any(x in name.upper() for x in ("LSTM", "GRU"))
                             else "Route" if "Hybrid" in name else "ML"),
                    "rmse": blk.get("rmse"), "mae": blk.get("mae"),
                    "mape": blk.get("mape"), "r2": blk.get("r2"),
                    "returnPct": None, "speed": "—", "course": "fair test",
                })
    if cmp_path.exists():
        cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
        models_blk = cmp.get("models") if isinstance(cmp, dict) else cmp
        if isinstance(models_blk, dict):
            for name, blk in models_blk.items():
                if not isinstance(blk, dict):
                    continue
                d = blk.get("direction") or {}
                if d:
                    classification.append({
                        "name": name,
                        "type": "DL" if any(x in name.upper() for x in ("LSTM", "GRU")) else "ML",
                        "accuracy": d.get("accuracy"), "auc": d.get("auc"),
                        "precision": d.get("precision"), "recall": d.get("recall"),
                        "f1": d.get("f1"),
                    })
    buy_hold = {"name": "Buy & Hold", "type": "基准",
                "rmse": 0, "mae": 0, "mape": 0, "r2": 0,
                "returnPct": 0, "speed": "—", "course": "基准策略"}
    return {"regression": regression, "classification": classification, "buyAndHold": buy_hold}


@app.get("/api/models/backtest")
def models_backtest(days: int = 60, skinId: str | None = None):
    p = OUTPUT_DIR / "backtest" / "backtest_curves.json"
    if not p.exists():
        p = OUTPUT_DIR / "backtest_curves.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
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
