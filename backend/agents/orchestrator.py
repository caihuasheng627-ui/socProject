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
    "\u63a8\u8350", "\u9009\u4e00\u4e2a", "\u6709\u54ea\u4e9b",
    "recommend", "recommendation", "suggest", "candidates",
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


def is_model_performance_query(message: str) -> bool:
    """True when the user asks about model metrics, not a skin price forecast."""
    lowered = message.lower()
    return any(word.lower() in lowered for word in MODEL_PERF_WORDS)


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


def _market_brief(limit: int = 5) -> str:
    """Real 7-day top movers from the local DB so plain chat never has to
    invent numbers. Returns '' when the DB is unavailable (tests, cold start)."""
    try:
        from database import get_connection

        query = """
            WITH latest AS (
                SELECT skin_id, MAX(date) AS d FROM price_history GROUP BY skin_id
            ),
            cur AS (
                SELECT ph.skin_id, ph.price FROM price_history ph
                JOIN latest l ON ph.skin_id = l.skin_id AND ph.date = l.d
            ),
            past AS (
                SELECT ph.skin_id, ph.price FROM price_history ph
                JOIN latest l ON ph.skin_id = l.skin_id AND ph.date = DATE(l.d, '-7 day')
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
        with get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
            gainers = conn.execute(query.format(direction="DESC"), (limit,)).fetchall()
            losers = conn.execute(query.format(direction="ASC"), (limit,)).fetchall()
        if not gainers and not losers:
            return ""
        fmt = lambda row: f"{row['name']}: ${row['price']:.2f} ({row['chg']:+.2f}%/7d)"
        lines = [f"Skins tracked in database: {total}"]
        if gainers:
            lines.append("Top 7-day gainers: " + "; ".join(fmt(r) for r in gainers))
        if losers:
            lines.append("Top 7-day losers: " + "; ".join(fmt(r) for r in losers))
        return "\n".join(lines)
    except Exception:
        return ""


def grounded_chat_system_prompt(locale: str = "zh-CN", *, user_message: str | None = None) -> str:
    """Shared grounded prompt for plain chat — used by both the orchestrator
    and the streaming /api/chat endpoint so answers never invent numbers."""
    language = "English" if is_english(locale) else "Simplified Chinese"
    prompt = (
        f"Always answer in {language}. You are CSVest Main AI, a CS2 skin market assistant. "
        "STRICT GROUNDING RULES: use ONLY the market snapshot below (if present) for any "
        "concrete numbers. NEVER invent prices, percentage changes, model names, model outputs, "
        "or confidence values that are not in the snapshot. Never invent a multi-model "
        "consensus table (ARIMA / XGBoost / LightGBM / LSTM / GRU, etc.) for a single skin — "
        "live forecasts are Hybrid-V2 only. If the user asks for data you do not "
        "have, say plainly that the data is unavailable here and point them to the Market Center "
        "dashboard or the prediction page. Qualitative market reasoning is fine; fabricated "
        "statistics are not. Keep answers concise — normally under 200 words unless the user "
        "explicitly asks for detail."
    )
    if user_message and is_model_performance_query(user_message):
        model_brief = _model_comparison_brief(locale)
        if model_brief:
            prompt += f"\n\nModel lab metrics (real data):\n{model_brief}"
            return prompt
    brief = _market_brief()
    if brief:
        prompt += f"\n\nMarket snapshot from the local database (real data):\n{brief}"
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
) -> Intent:
    if session_id and target_agent in {"bull", "bear", "judge"}:
        return "agent_followup"
    # "预测模型表现如何" contains 预测 but must NOT become a single-skin forecast.
    if is_model_performance_query(message) and action in {"auto", "qa", "chat"}:
        return "chat"
    lowered = message.lower()
    wants_recommend = any(word in lowered for word in RECOMMEND_WORDS)
    if action == "qa":
        # Strict normal Q&A: never route into the debate pipeline, even when
        # the wording sounds like a buy/sell decision. Prediction and
        # recommendation cards are still allowed.
        if wants_recommend:
            return "recommendation"
        if has_skin and any(word in lowered for word in PREDICT_WORDS):
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
            return "prediction"
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
    if has_skin and any(word in lowered for word in PREDICT_WORDS):
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
            lambda messages: llm.chat_sync(messages, max_tokens=700)
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
    ) -> dict[str, Any]:
        clean_message = message.strip()
        english = is_english(locale)
        if not clean_message:
            raise ValueError("message must not be empty")
        if horizon_days not in {7, 30}:
            raise ValueError("horizonDays must be 7 or 30")
        skin = self.skin_resolver(clean_message, skin_id)
        if skin and skin.get("ambiguous"):
            wants_predict = (
                action == "predict"
                or any(word in clean_message.lower() for word in PREDICT_WORDS)
            )
            if action in {"qa", "chat"} and not wants_predict:
                # Strict Q&A must not push the user into a debate picker;
                # answer the question as plain grounded chat instead.
                skin = None
            else:
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
                "content": grounded_chat_system_prompt(locale, user_message=clean_message),
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
