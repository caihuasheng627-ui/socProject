"""Route one AI Chat request to recommendation, prediction, debate, or chat."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, Callable, Literal

import llm

from .recommendation_agent import RecommendationAgent
from .conversation import classify_session_input
from .localization import is_english
from .schemas import UserProfile
from .session_service import AgentSessionService


Intent = Literal[
    "recommendation", "prediction", "debate", "debate_round", "debate_answer", "profile_update",
    "agent_followup", "chat"
]
Action = Literal["auto", "recommend", "predict", "debate", "chat", "qa"]
SkinResolver = Callable[[str, str | None], dict[str, Any] | None]
PredictionLoader = Callable[[str, int], dict[str, Any]]
ChatLoader = Callable[[list[dict[str, str]]], str]


def normalize_capabilities(capabilities: dict[str, Any] | None = None) -> dict[str, bool]:
    """Q&A capability toggles — default all on so existing callers stay unchanged."""
    caps = capabilities if isinstance(capabilities, dict) else {}
    return {
        "analyze": bool(caps.get("analyze", True)),
        "predict": bool(caps.get("predict", True)),
        "recommend": bool(caps.get("recommend", True)),
    }


def _chat_prediction_contract(raw: dict[str, Any], horizon_days: int) -> dict[str, Any]:
    """Adapt the canonical /api/predict response for the chat card.

    The chat must never invent a second forecast or read fields that do not
    exist on the canonical prediction response.  An unavailable model is an
    explicit error, not a zero-price forecast.
    """
    # Small injected loaders in tests/integrations may already return the
    # chat-card contract. Keep that contract intact; production main.py
    # always supplies the canonical /api/predict shape below.
    if "status" not in raw and "targetPrice" in raw:
        return raw
    if raw.get("status") != "available":
        raise RuntimeError(f"Hybrid prediction unavailable: {raw.get('reason') or 'UNKNOWN'}")
    predictions = raw.get("predictions") or []
    if not predictions:
        raise RuntimeError("Hybrid prediction unavailable: EMPTY_PREDICTIONS")
    item = predictions[0]
    current_price = raw.get("forecastAnchorPrice", raw.get("currentPrice"))
    target_price = raw.get("targetPrice", item.get("price"))
    confidence = item.get("confidence")
    if current_price is None or target_price is None or confidence is None:
        raise RuntimeError("Hybrid prediction unavailable: INVALID_CONTRACT")
    entry_range = raw.get("entryRange") or {}
    return {
        "skinId": raw.get("skinId"),
        "horizon": horizon_days,
        "price": float(current_price),
        "targetPrice": float(target_price),
        "change7d": float(item.get("change") or 0.0),
        "confidence": float(confidence),
        "entryLow": entry_range.get("low"),
        "entryHigh": entry_range.get("high"),
        "model": item.get("model"),
        "routeModel": item.get("routeModel"),
        "decisionDate": item.get("decisionDate"),
    }


RECOMMEND_WORDS = (
    "\u63a8\u8350", "\u9009\u4e00\u4e2a", "\u6709\u54ea\u4e9b", "\u4e70\u4ec0\u4e48", "\u63a8\u8350\u4ec0\u4e48",
    "\u5e2e\u6211\u9009",
    "recommend", "recommendation", "suggest", "candidates",
    "what should i buy", "what to buy", "pick for me",
)
PREDICT_WORDS = (
    "\u9884\u6d4b", "\u4ef7\u683c", "\u8d70\u52bf", "\u6da8\u8dcc", "\u76ee\u6807\u4ef7",
    "forecast", "price", "trend",
)
ACTIVE_SESSION_PREDICT_WORDS = (
    "\u9884\u6d4b", "\u672a\u6765\u4ef7\u683c", "\u672a\u6765\u8d70\u52bf", "\u76ee\u6807\u4ef7",
    "predict", "prediction", "forecast", "future price", "price forecast",
)
DEBATE_WORDS = (
    "\u662f\u5426", "\u8be5\u4e0d\u8be5", "\u503c\u4e0d\u503c\u5f97", "\u503c\u5f97\u4e70", "\u80fd\u4e70\u5417",
    "\u8981\u4e0d\u8981", "\u5165\u624b", "\u9009\u62e9", "should i", "worth", "buy",
)
MODEL_PERF_WORDS = (
    "模型表现", "模型对比", "预测模型", "各个模型", "各模型", "模型实验室",
    "model comparison", "model performance", "models lab", "how do the model",
    "model-comparison", "compare models", "comparison results",
)
HOW_TO_WORDS = (
    "怎么用", "如何使用", "会什么", "能做什么", "功能", "帮助", "使用说明",
    "how to use", "what can you", "capabilities", "help me with", "what do you do",
)
MARKET_OVERVIEW_WORDS = (
    "今天", "上涨", "下跌", "涨幅", "跌幅", "热门", "成交", "流动性", "市场",
    "rising", "falling", "gainers", "losers", "hot", "volume", "market", "today",
)


def is_model_performance_query(message: str) -> bool:
    """True when the user asks about model metrics, not a skin price forecast."""
    lowered = message.lower()
    return any(word.lower() in lowered for word in MODEL_PERF_WORDS)


def is_howto_query(message: str) -> bool:
    lowered = message.lower()
    return any(word.lower() in lowered for word in HOW_TO_WORDS)


def is_market_overview_query(message: str) -> bool:
    lowered = message.lower()
    return any(word.lower() in lowered for word in MARKET_OVERVIEW_WORDS)


def _model_comparison_brief(locale: str = "zh-CN") -> str:
    """Inject fair-test regression metrics so chat can answer model-lab questions."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    output_dir = root / "ml" / "outputs"
    cmp_path = output_dir / "compare_results_test.json"
    if not cmp_path.exists():
        cmp_path = output_dir / "compare_results.json"
    if not cmp_path.exists():
        return ""
    try:
        cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    models_blk = cmp.get("models") if isinstance(cmp, dict) else None
    if not isinstance(models_blk, dict) or not models_blk:
        return ""
    english = is_english(locale)
    n_items = cmp.get("n_items") or cmp.get("nItems")
    horizon = cmp.get("horizon_steps") or cmp.get("horizonSteps") or 7
    lines = [
        (
            f"Fair-test model comparison (horizon={horizon}d"
            + (f", items={n_items}" if n_items else "")
            + "):"
        )
        if english else
        (
            f"公平测试模型对比（horizon={horizon}天"
            + (f"，覆盖 {n_items} 件" if n_items else "")
            + "）："
        )
    ]
    preferred = ("LSTM-C", "LSTM-D", "Hybrid", "Random Forest", "RF", "LightGBM", "XGBoost", "GRU")
    names = [n for n in preferred if n in models_blk] + [
        n for n in models_blk if n not in preferred
    ]
    for name in names[:8]:
        blk = models_blk.get(name)
        if not isinstance(blk, dict):
            continue
        display = "Random Forest" if name == "RF" else name
        rmse = blk.get("rmse")
        mae = blk.get("mae")
        mape = blk.get("mape")
        r2 = blk.get("r2")
        parts = [display]
        if rmse is not None:
            parts.append(f"RMSE={float(rmse):.2f}")
        if mae is not None:
            parts.append(f"MAE={float(mae):.2f}")
        if mape is not None:
            parts.append(f"MAPE={float(mape):.2f}%")
        if r2 is not None:
            parts.append(f"R²={float(r2):.4f}")
        if len(parts) > 1:
            lines.append("- " + ", ".join(parts))
    if len(lines) <= 1:
        return ""
    lines.append(
        "Answer ONLY from this comparison table. Do not invent metrics or run a single-skin Hybrid forecast."
        if english else
        "只能依据上表回答；不要编造指标，也不要对某件饰品跑 Hybrid 预测。"
    )
    return "\n".join(lines)


def _change_movers(conn, days: int, direction: str, limit: int):
    query = f"""
        WITH latest AS (
            SELECT skin_id, MAX(date) AS d FROM price_history GROUP BY skin_id
        ),
        cur AS (
            SELECT ph.skin_id, ph.price FROM price_history ph
            JOIN latest l ON ph.skin_id = l.skin_id AND ph.date = l.d
        ),
        past AS (
            SELECT ph.skin_id, ph.price FROM price_history ph
            JOIN latest l ON ph.skin_id = l.skin_id AND ph.date = DATE(l.d, '-{int(days)} day')
        )
        SELECT s.market_hash_name AS name, cur.price AS price,
               ROUND((cur.price - past.price) / past.price * 100, 2) AS chg
        FROM cur
        JOIN past ON cur.skin_id = past.skin_id
        JOIN skins s ON s.id = cur.skin_id
        WHERE past.price > 0 AND cur.price > 0
        ORDER BY chg {direction}
        LIMIT ?
    """
    return conn.execute(query, (limit,)).fetchall()


def _hot_volume_rows(conn, limit: int = 5):
    return conn.execute(
        """
        WITH latest AS (
            SELECT skin_id, MAX(date) AS d FROM price_history GROUP BY skin_id
        )
        SELECT s.market_hash_name AS name, ph.price AS price,
               IFNULL(ph.daily_volume, 0) AS volume
        FROM price_history ph
        JOIN latest l ON ph.skin_id = l.skin_id AND ph.date = l.d
        JOIN skins s ON s.id = ph.skin_id
        WHERE IFNULL(ph.daily_volume, 0) > 0 AND ph.price > 0
        ORDER BY ph.daily_volume DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def _market_brief(limit: int = 5) -> str:
    """Real movers + volume from the local DB so plain chat never invents numbers."""
    try:
        from database import get_connection

        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
            gainers7 = _change_movers(conn, 7, "DESC", limit)
            losers7 = _change_movers(conn, 7, "ASC", limit)
            gainers30 = _change_movers(conn, 30, "DESC", limit)
            hot = _hot_volume_rows(conn, limit)
        if not gainers7 and not losers7 and not hot:
            return ""

        def fmt_chg(row) -> str:
            return f"{row['name']}: ${row['price']:.2f} ({row['chg']:+.2f}%)"

        def fmt_vol(row) -> str:
            return f"{row['name']}: ${row['price']:.2f} (vol {int(row['volume'])})"

        lines = [f"Skins tracked in database: {total}"]
        if gainers7:
            lines.append("Top 7-day gainers: " + "; ".join(fmt_chg(r) for r in gainers7))
        if losers7:
            lines.append("Top 7-day losers: " + "; ".join(fmt_chg(r) for r in losers7))
        if gainers30:
            lines.append("Top 30-day gainers: " + "; ".join(fmt_chg(r) for r in gainers30))
        if hot:
            lines.append("Highest recent daily volume: " + "; ".join(fmt_vol(r) for r in hot))
        return "\n".join(lines)
    except Exception:
        return ""


def _news_brief(limit: int = 5) -> str:
    """Recent news titles from SQLite — titles only, no invented commentary."""
    try:
        from database import get_connection

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT title, source, substr(IFNULL(published_at,''), 1, 10) AS day
                FROM news
                WHERE IFNULL(title, '') != ''
                ORDER BY
                  CASE WHEN IFNULL(url, '') != '' THEN 0 ELSE 1 END,
                  substr(IFNULL(published_at,''), 1, 10) DESC,
                  id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        if not rows:
            return ""
        lines = ["Recent market news (titles only):"]
        for row in rows:
            day = row["day"] or "n/a"
            source = row["source"] or "news"
            lines.append(f"- [{day} · {source}] {row['title']}")
        return "\n".join(lines)
    except Exception:
        return ""


def _retrieval_brief(query: str, limit: int = 4) -> str:
    """Lightweight RAG snippets for the user's question when available."""
    if not query or not query.strip():
        return ""
    try:
        import rag

        sources = rag._retrieve_sources(query.strip(), kb_k=2, news_k=max(2, limit - 2))
    except Exception:
        return ""
    if not sources:
        return ""
    lines = ["Retrieved context for this question:"]
    for item in sources[:limit]:
        kind = item.get("type") or "source"
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        text = snippet or title
        if not text:
            continue
        lines.append(f"- ({kind}) {text[:220]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _capabilities_brief(
    locale: str = "zh-CN",
    *,
    capabilities: dict[str, bool] | None = None,
) -> str:
    caps = normalize_capabilities(capabilities)
    if is_english(locale):
        lines = ["What CSVest Main AI can do in Q&A (only enabled tools):"]
        if caps["predict"]:
            lines.append("- Name an exact skin → Hybrid-V2 7-day forecast card")
        if caps["recommend"]:
            lines.append("- Say a budget / recommend → ranked candidate cards")
        if caps["analyze"]:
            lines.append("- Ask rising/falling / volume / market → answer from the live market snapshot")
        lines.append("- Ask model comparison → fair-test lab metrics")
        lines.append("- Switch to Debate mode for Bull / Bear / Judge on one skin")
        lines.append("- Price alerts: open Alerts from the app (chat cannot create them here)")
        disabled = [name for name, on in caps.items() if not on]
        if disabled:
            lines.append(
                "Disabled for this turn (do not offer to run them): "
                + ", ".join(disabled)
                + "."
            )
        lines.append("If data is missing, say so — do not invent prices or fake model tables.")
        return "\n".join(lines)
    lines = ["CSVest 普通问答能做的事（仅列出当前已启用能力）："]
    if caps["predict"]:
        lines.append("- 说出具体饰品名 → 直接给出 Hybrid-V2 7 日预测卡片")
    if caps["recommend"]:
        lines.append("- 说出预算/推荐 → 返回候选推荐卡片")
    if caps["analyze"]:
        lines.append("- 问上涨/下跌/成交量/市场概况 → 依据下方实时市场快照回答")
    lines.append("- 问模型对比/模型表现 → 使用公平测试指标回答")
    lines.append("- 需要 Bull/Bear/Judge 请切换到 Debate 模式")
    lines.append("- 价格预警请到应用内「预警」页设置（问答里不能直接创建）")
    disabled = [name for name, on in caps.items() if not on]
    if disabled:
        lines.append("本轮已关闭（不要主动去跑）：" + "、".join(disabled) + "。")
    lines.append("缺数据就直说，不要编造价格或多模型共识表。")
    return "\n".join(lines)


def grounded_chat_system_prompt(
    locale: str = "zh-CN",
    *,
    user_message: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> str:
    """Shared grounded prompt for plain chat — used by both the orchestrator
    and the streaming /api/chat endpoint so answers never invent numbers."""
    caps = normalize_capabilities(capabilities)
    language = "English" if is_english(locale) else "Simplified Chinese"
    prompt = (
        f"Always answer in {language}. You are CSVest Main AI, a CS2 skin market assistant. "
        "STRICT GROUNDING RULES: use ONLY the market snapshot / retrieved context below "
        "(if present) for any concrete numbers. NEVER invent prices, percentage changes, "
        "model names, model outputs, or confidence values that are not supplied. Never invent "
        "a multi-model consensus table (ARIMA / XGBoost / LightGBM / LSTM / GRU, etc.) for a "
        "single skin — live forecasts are Hybrid-V2 only. Prefer actionable next steps that "
        "match the currently enabled capabilities. "
        "If the user asks for data you do not have, say plainly that it is unavailable and "
        "point them to Market Center, Prediction, or Alerts. Keep answers concise — normally "
        "under 220 words unless the user asks for detail."
    )
    if user_message and is_model_performance_query(user_message):
        model_brief = _model_comparison_brief(locale)
        if model_brief:
            prompt += f"\n\nModel lab metrics (real data):\n{model_brief}"
            return prompt

    sections: list[str] = []
    if user_message and is_howto_query(user_message):
        sections.append(_capabilities_brief(locale, capabilities=caps))
    elif not user_message:
        sections.append(_capabilities_brief(locale, capabilities=caps))

    if caps["analyze"]:
        brief = _market_brief()
        if brief:
            sections.append(f"Market snapshot from the local database (real data):\n{brief}")

        if user_message and (is_market_overview_query(user_message) or is_howto_query(user_message)):
            news = _news_brief()
            if news:
                sections.append(news)

        if user_message and not is_model_performance_query(user_message):
            retrieved = _retrieval_brief(user_message)
            if retrieved:
                sections.append(retrieved)

    # Always give a short capability footer so generic questions are not dead ends.
    if user_message and not is_howto_query(user_message):
        sections.append(_capabilities_brief(locale, capabilities=caps))

    if sections:
        prompt += "\n\n" + "\n\n".join(sections)
    return prompt

def _latest_debate_round(session: dict[str, Any]) -> dict[str, Any]:
    rounds = session.get("debateRounds") or []
    if rounds:
        return rounds[-1]
    bulls = session.get("bullHistory") or []
    bears = session.get("bearHistory") or []
    judges = session.get("judgeHistory") or []
    return {
        "roundNo": max(len(bulls), len(bears), len(judges), 1),
        "userMessage": None,
        "bull": bulls[-1] if bulls else None,
        "bear": bears[-1] if bears else None,
        "judge": judges[-1] if judges else None,
    }


def _normalize_skin_text(text: Any) -> str:
    """Compare skin names ignoring ★/™ decorations and extra whitespace."""
    return " ".join(str(text or "").replace("★", " ").replace("™", " ").lower().split())


def _default_skin_resolver(message: str, explicit_skin_id: str | None) -> dict[str, Any] | None:
    from database import get_connection, latest_price, resolve_skin, weapon_to_category

    with get_connection() as connection:
        if explicit_skin_id:
            row = resolve_skin(connection, explicit_skin_id)
            if row:
                price, _ = latest_price(connection, row["id"])
                return {"skinId": row["slug"], "name": row["market_hash_name"], "price": price}

        lowered = _normalize_skin_text(message)
        rows = connection.execute("SELECT * FROM skins ORDER BY LENGTH(market_hash_name) DESC").fetchall()
        matches = [
            row for row in rows
            if (_normalize_skin_text(row["market_hash_name"])
                and _normalize_skin_text(row["market_hash_name"]) in lowered)
            or str(row["slug"] or "").lower() in lowered
        ]
        if not matches:
            from .skin_localization import resolve_chinese_skin

            def build_candidate(row, *, ambiguous: bool = False) -> dict[str, Any]:
                price, _ = latest_price(connection, row["id"])
                candidate = {
                    "skinId": row["slug"],
                    "name": row["market_hash_name"],
                    "price": round(price, 2) if price is not None else None,
                }
                if ambiguous:
                    candidate["category"] = weapon_to_category(
                        row["weapon_type"] or row["market_hash_name"] or ""
                    )
                return candidate

            zh_result = resolve_chinese_skin(message, rows, build_candidate=build_candidate)
            if zh_result:
                return zh_result
            weapon_aliases = (
                "ak-47", "m4a1-s", "m4a4", "awp", "glock-18", "usp-s",
                "desert eagle", "p250", "mp9", "mac-10", "galil ar", "famas",
                "aug", "sg 553", "ssg 08", "five-seven", "tec-9",
            )
            weapon = next((alias for alias in weapon_aliases if alias in lowered), None)
            if not weapon:
                return None
            candidate_rows = connection.execute(
                """SELECT * FROM skins
                   WHERE LOWER(weapon_type)=? OR LOWER(market_hash_name) LIKE ?
                   ORDER BY rarity_rank DESC, market_hash_name ASC
                   LIMIT 8""",
                (weapon, f"{weapon}%"),
            ).fetchall()
            candidates = []
            for candidate in candidate_rows:
                price, _ = latest_price(connection, candidate["id"])
                candidates.append({
                    "skinId": candidate["slug"],
                    "name": candidate["market_hash_name"],
                    "price": round(price, 2) if price is not None else None,
                    "category": weapon_to_category(
                        candidate["weapon_type"] or candidate["market_hash_name"] or ""
                    ),
                })
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                return {
                    "ambiguous": True,
                    "query": weapon.upper(),
                    "candidates": candidates,
                }
            return None
        row = matches[0]
        price, _ = latest_price(connection, row["id"])
        return {"skinId": row["slug"], "name": row["market_hash_name"], "price": price}


def detect_intent(
    message: str,
    *,
    action: Action = "auto",
    has_skin: bool = False,
    session_id: str | None = None,
    target_agent: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> Intent:
    caps = normalize_capabilities(capabilities)
    if session_id and target_agent in {"bull", "bear", "judge"}:
        return "agent_followup"
    # "预测模型表现如何" contains 预测 but must NOT become a single-skin forecast.
    if is_model_performance_query(message) and action in {"auto", "qa", "chat"}:
        return "chat"
    lowered = message.lower()
    wants_recommend = any(word in lowered for word in RECOMMEND_WORDS) and (
        caps["recommend"] or action == "recommend"
    )
    if action == "qa":
        # Normal Q&A still blocks Debate, but must run Hybrid when a skin is
        # named — otherwise the mode can only answer from the tiny market brief.
        # Both recommendation and prediction honor the user's capability toggles.
        if wants_recommend and caps["recommend"]:
            return "recommendation"
        if has_skin and caps["predict"]:
            return "prediction"
        return "chat"
    explicit = {
        "recommend": "recommendation",
        "predict": "prediction",
        "debate": "debate",
        "chat": "chat",
    }
    if session_id:
        if action in explicit:
            # Debate-mode UI forces action=debate; still honor recommend wording.
            if action != "recommend" and wants_recommend:
                return "recommendation"
            return explicit[action]  # type: ignore[return-value]
        if wants_recommend:
            return "recommendation"
        if any(word in lowered for word in ACTIVE_SESSION_PREDICT_WORDS):
            if is_model_performance_query(message):
                return "debate_answer"
            if caps["predict"] or action == "predict":
                return "prediction"
            return "debate_answer"
        session_kind = classify_session_input(message)
        if session_kind == "question":
            return "debate_answer"
        if session_kind == "preference":
            return "debate_round"
        return "debate_round"
    if action in explicit:
        # Without a skin, "you can recommend" must not dead-end on debate's
        # "specify one exact skin" clarification.
        if action in {"debate", "predict", "chat"} and wants_recommend:
            return "recommendation"
        return explicit[action]  # type: ignore[return-value]
    if wants_recommend:
        return "recommendation"
    if has_skin and any(word in lowered for word in DEBATE_WORDS):
        return "debate"
    if has_skin and any(word in lowered for word in PREDICT_WORDS) and caps["predict"]:
        return "prediction"
    return "chat"


class AIOrchestrator:
    def __init__(
        self,
        *,
        recommender: RecommendationAgent | None = None,
        session_service: AgentSessionService | None = None,
        skin_resolver: SkinResolver | None = None,
        prediction_loader: PredictionLoader | None = None,
        chat_loader: ChatLoader | None = None,
    ) -> None:
        self.recommender = recommender or RecommendationAgent()
        self.session_service = session_service or AgentSessionService()
        self.skin_resolver = skin_resolver or _default_skin_resolver
        self.prediction_loader = prediction_loader
        # Cap answer length: uncapped DeepSeek replies routinely exceeded the
        # HTTP timeout, which surfaced as timeout-fallback "ghost replies".
        self.chat_loader = chat_loader or (
            lambda messages: llm.chat_sync(messages, max_tokens=900)
        )

    def handle(
        self,
        message: str,
        *,
        action: Action = "auto",
        skin_id: str | None = None,
        session_id: str | None = None,
        target_agent: str | None = None,
        budget: float | None = None,
        horizon_days: int = 7,
        risk_level: str = "medium",
        history: list[dict[str, str]] | None = None,
        locale: str = "zh-CN",
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_message = message.strip()
        english = is_english(locale)
        caps = normalize_capabilities(capabilities)
        if not clean_message:
            raise ValueError("message must not be empty")
        if horizon_days not in {7, 30}:
            raise ValueError("horizonDays must be 7 or 30")
        skin = self.skin_resolver(clean_message, skin_id)
        if skin and skin.get("ambiguous"):
            wants_predict = (
                (
                    action in {"predict", "qa"}
                    or any(word in clean_message.lower() for word in PREDICT_WORDS)
                )
                and (caps["predict"] or action == "predict")
            )
            if action == "chat" and not wants_predict:
                # Plain chat may keep talking without forcing a skin picker.
                skin = None
            elif action == "qa" and not wants_predict:
                # Prediction capability off — keep talking without a Hybrid picker.
                skin = None
            else:
                # Q&A / explicit predict → Hybrid picker; otherwise Debate picker.
                requested_action = "predict" if wants_predict else "debate"
                return {
                    "type": "clarification",
                    "message": (
                        (
                            f"{skin.get('query', 'This weapon')} is a weapon category, not one unique skin. "
                            "Choose a specific candidate below; I will then start "
                            + ("the Hybrid forecast." if requested_action == "predict" else "the Bull / Bear / Judge debate.")
                        ) if english else (
                            f"{skin.get('query', '该武器')} 是一个武器类别，不是唯一皮肤。"
                            "我不会替你猜具体款式；请选择下面一款，随后会立即启动 "
                            + ("Hybrid 预测。" if requested_action == "predict" else "Bull / Bear / Judge Debate。")
                        )
                    ),
                    "skinCandidates": skin.get("candidates", []),
                    "requestedAction": requested_action,
                }
        intent = detect_intent(
            clean_message,
            action=action,
            has_skin=skin is not None,
            session_id=session_id,
            target_agent=target_agent,
            capabilities=caps,
        )
        if intent in {"prediction", "debate"} and skin is None and session_id:
            active_session = self.session_service.get(session_id)
            snapshot = active_session.get("marketSnapshot") or {}
            skin = {
                "skinId": active_session.get("skinId") or snapshot.get("skin_id"),
                "name": snapshot.get("skin_name") or active_session.get("skinId"),
                "price": snapshot.get("current_price"),
            }

        if intent == "agent_followup":
            session = self.session_service.send_message(
                session_id or "",
                message=clean_message,
                target_agent=target_agent,  # type: ignore[arg-type]
                locale=locale,
            )
            return {
                "type": intent,
                "message": (
                    f"{target_agent.title()} responded; its public argument and evidence were added to this session."
                    if english else
                    f"{target_agent.title()} 已回应，公开观点和证据已写入当前会话。"
                ),
                "agentSession": session,
            }

        if intent == "debate_round":
            session = self.session_service.run_round(
                session_id or "", message=clean_message, locale=locale
            )
            latest_round = _latest_debate_round(session)
            judge = latest_round.get("judge") or {}
            return {
                "type": intent,
                "message": (
                    (
                        "Your input is now public context for this round: Bull responds to the prior risks, "
                        "Bear rebuts Bull, and Judge issues a new ruling. "
                        f"Current decision: {judge.get('decision', 'pending')}."
                    ) if english else (
                        "我已把你的意见作为本轮公开上下文：Bull 先回应上一轮风险，"
                        "Bear 再反驳 Bull，最后 Judge 重新裁决。"
                        f" 当前结论为 {judge.get('decision', '待观察')}。"
                    )
                ),
                "agentSession": session,
                "debateRound": latest_round,
            }

        if intent == "debate_answer":
            session = self.session_service.answer_question(
                session_id or "", message=clean_message, locale=locale
            )
            # Return the grounded template answer as-is. Re-phrasing through the
            # chat LLM previously invented fake ARIMA/XGBoost "Model Consensus"
            # tables even when Hybrid-V2 was the only live forecast in the facts.
            grounded_facts = session.pop("answer")
            return {
                "type": intent,
                "message": grounded_facts,
                "agentSession": session,
                "profileChanges": session.get("profileChanges", []),
                "answerMode": "grounded_direct",
            }

        if intent == "profile_update":
            session = self.session_service.update_profile(
                session_id or "", message=clean_message, locale=locale
            )
            changes = session.get("profileChanges", [])
            change_text = (
                "; ".join(changes) if changes else
                ("No new structured condition was detected" if english else "没有识别到新的结构化条件")
            )
            return {
                "type": intent,
                "message": (
                    (
                        f"Recorded: {change_text}. Judge has not been recalculated yet; "
                        'say “rerun the debate with these conditions” to recalculate all agents.'
                    ) if english else (
                        f"已记录：{change_text}。当前 Judge 结论尚未重新计算；"
                        "如果希望 Bull、Bear 和 Judge 按这些条件重算，请说“按这些条件再辩一轮”。"
                    )
                ),
                "agentSession": session,
                "profileChanges": changes,
            }

        if intent == "recommendation":
            items = self.recommender.recommend(
                clean_message, budget=budget, risk_level=risk_level, locale=locale
            )
            if not items:
                message_text = (
                    "No suitable candidate matches the current constraints. Raise the budget or relax the category filter."
                    if english else "当前条件下没有找到合适候选，请提高预算或放宽品类限制。"
                )
            else:
                message_text = (
                    "I ranked candidates by budget, risk, momentum, liquidity and volatility. Select one to run a Hybrid forecast or multi-Agent debate."
                    if english else
                    "我先按预算、风险、动量、流动性和波动率筛出候选。选中一款后可继续做 Hybrid 预测或启动多 Agent 辩论。"
                )
            return {"type": intent, "message": message_text, "recommendations": items}

        if intent in {"prediction", "debate"} and skin is None:
            return {
                "type": "clarification",
                "message": (
                    "Specify one exact skin first, or ask me to recommend candidates."
                    if english else "请先指定一款具体皮肤，或先让我推荐候选。"
                ),
            }

        if intent == "prediction":
            if self.prediction_loader is None:
                raise RuntimeError("prediction loader is not configured")
            prediction = _chat_prediction_contract(
                self.prediction_loader(skin["skinId"], horizon_days), horizon_days
            )
            return {
                "type": intent,
                "message": (
                    f"Hybrid completed a {horizon_days}-day price forecast for {skin['name']}."
                    if english else
                    f"已调用 Hybrid 模型完成 {skin['name']} 的 {horizon_days} 天价格预测。"
                ),
                "skin": skin,
                "prediction": prediction,
            }

        if intent == "debate":
            profile = UserProfile(
                budget=budget, horizon_days=horizon_days, risk_level=risk_level, locale=locale
            )
            session = self.session_service.create(
                skin["skinId"], user_profile=profile, rounds=1
            )
            return {
                "type": intent,
                "message": (
                    (
                        f"The first independent analysis for {skin['name']} is complete. "
                        "Tell me your view or concern and Main AI will moderate another evidence-based round."
                    ) if english else (
                        f"已针对 {skin['name']} 完成第一轮独立分析。"
                        "接下来直接告诉我你的判断或担忧，Main AI 会主持双方基于它再辩一轮。"
                    )
                ),
                "skin": skin,
                "agentSession": session,
                "debateRound": _latest_debate_round(session),
            }

        safe_history = [
            {"role": item.get("role", "user"), "content": item.get("content", "")[:2000]}
            for item in (history or [])[-8:]
            if item.get("role") in {"user", "assistant"}
        ]
        reply = self.chat_loader([
            {
                "role": "system",
                "content": grounded_chat_system_prompt(
                    locale,
                    user_message=clean_message,
                    capabilities=caps,
                ),
            },
            *safe_history,
            {"role": "user", "content": clean_message},
        ])
        return {"type": "chat", "message": reply}

    def handle_debate_stream(
        self,
        message: str,
        *,
        action: Action = "debate",
        skin_id: str | None = None,
        session_id: str | None = None,
        budget: float | None = None,
        horizon_days: int = 7,
        risk_level: str = "medium",
        history: list[dict[str, str]] | None = None,
        locale: str = "zh-CN",
    ) -> Generator[dict[str, Any], None, None]:
        """Stream debate progress agent-by-agent.

        Stageable intents (new debate / user-participating round) yield
        'stage' and 'agent' events as each agent finishes, then a final
        'done' event whose payload matches handle()'s return shape.
        Every other intent falls back to a single 'done' event.
        """
        clean_message = message.strip()
        english = is_english(locale)
        if not clean_message:
            raise ValueError("message must not be empty")
        skin = self.skin_resolver(clean_message, skin_id)
        ambiguous = bool(skin and skin.get("ambiguous"))
        intent = None if ambiguous else detect_intent(
            clean_message,
            action=action,
            has_skin=skin is not None,
            session_id=session_id,
            target_agent=None,
        )

        if not ambiguous and intent == "debate" and skin is not None:
            profile = UserProfile(
                budget=budget, horizon_days=horizon_days,
                risk_level=risk_level, locale=locale,
            )
            session: dict[str, Any] | None = None
            for event in self.session_service.create_stream(
                skin["skinId"], user_profile=profile
            ):
                if event.get("event") == "session":
                    session = event["session"]
                else:
                    yield event
            assert session is not None
            yield {"event": "done", "payload": {
                "type": "debate",
                "message": (
                    (
                        f"The first independent analysis for {skin['name']} is complete. "
                        "Tell me your view or concern and Main AI will moderate another evidence-based round."
                    ) if english else (
                        f"已针对 {skin['name']} 完成第一轮独立分析。"
                        "接下来直接告诉我你的判断或担忧，Main AI 会主持双方基于它再辩一轮。"
                    )
                ),
                "skin": skin,
                "agentSession": session,
                "debateRound": _latest_debate_round(session),
            }}
            return

        if not ambiguous and intent == "debate_round" and session_id:
            session = None
            for event in self.session_service.run_round_stream(
                session_id, message=clean_message, locale=locale
            ):
                if event.get("event") == "session":
                    session = event["session"]
                else:
                    yield event
            assert session is not None
            latest_round = _latest_debate_round(session)
            judge = latest_round.get("judge") or {}
            yield {"event": "done", "payload": {
                "type": "debate_round",
                "message": (
                    (
                        "Your input is now public context for this round: Bull responds to the prior risks, "
                        "Bear rebuts Bull, and Judge issues a new ruling. "
                        f"Current decision: {judge.get('decision', 'pending')}."
                    ) if english else (
                        "我已把你的意见作为本轮公开上下文：Bull 先回应上一轮风险，"
                        "Bear 再反驳 Bull，最后 Judge 重新裁决。"
                        f" 当前结论为 {judge.get('decision', '待观察')}。"
                    )
                ),
                "agentSession": session,
                "debateRound": latest_round,
            }}
            return

        # Non-stageable intents (clarification, follow-up, Q&A about the
        # debate, profile updates, ...) run through the normal handler.
        result = self.handle(
            message,
            action=action,
            skin_id=skin_id,
            session_id=session_id,
            target_agent=None,
            budget=budget,
            horizon_days=horizon_days,
            risk_level=risk_level,
            history=history,
            locale=locale,
        )
        yield {"event": "done", "payload": result}
