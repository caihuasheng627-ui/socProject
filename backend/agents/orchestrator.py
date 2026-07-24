"""Route one AI Chat request to recommendation, prediction, debate, or chat."""

from __future__ import annotations

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
Action = Literal["auto", "recommend", "predict", "debate", "chat"]
SkinResolver = Callable[[str, str | None], dict[str, Any] | None]
PredictionLoader = Callable[[str, int], dict[str, Any]]
ChatLoader = Callable[[list[dict[str, str]]], str]


RECOMMEND_WORDS = (
    "\u63a8\u8350", "\u9009\u4e00\u4e2a", "\u6709\u54ea\u4e9b", "recommend", "suggest"
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


def _default_skin_resolver(message: str, explicit_skin_id: str | None) -> dict[str, Any] | None:
    from database import get_connection, latest_price, resolve_skin, weapon_to_category

    with get_connection() as connection:
        if explicit_skin_id:
            row = resolve_skin(connection, explicit_skin_id)
            if row:
                price, _ = latest_price(connection, row["id"])
                return {"skinId": row["slug"], "name": row["market_hash_name"], "price": price}

        lowered = message.lower()
        rows = connection.execute("SELECT * FROM skins ORDER BY LENGTH(market_hash_name) DESC").fetchall()
        matches = [
            row for row in rows
            if str(row["market_hash_name"] or "").lower() in lowered
            or str(row["slug"] or "").lower() in lowered
        ]
        if not matches:
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
    explicit = {
        "recommend": "recommendation",
        "predict": "prediction",
        "debate": "debate",
        "chat": "chat",
    }
    if session_id:
        if action in explicit:
            return explicit[action]  # type: ignore[return-value]
        lowered = message.lower()
        if any(word in lowered for word in RECOMMEND_WORDS):
            return "recommendation"
        if any(word in lowered for word in ACTIVE_SESSION_PREDICT_WORDS):
            return "prediction"
        session_kind = classify_session_input(message)
        if session_kind == "question":
            return "debate_answer"
        if session_kind == "preference":
            return "debate_round"
        return "debate_round"
    if action in explicit:
        return explicit[action]  # type: ignore[return-value]
    lowered = message.lower()
    if any(word in lowered for word in RECOMMEND_WORDS):
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
        self.chat_loader = chat_loader or (lambda messages: llm.chat_sync(messages))

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
            requested_action = (
                "predict" if action == "predict" or any(word in clean_message.lower() for word in PREDICT_WORDS)
                else "debate"
            )
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
            grounded_facts = session.pop("answer")
            language = "English" if english else "Simplified Chinese"
            reply = self.chat_loader([
                {
                    "role": "system",
                    "content": (
                        f"Always answer in {language}. You are CSVest Main AI. "
                        "Answer the user's exact question directly and naturally using only "
                        "the supplied public debate facts. Do not repeat the full debate report "
                        "unless the user asks for it. Do not invent prices, evidence, news, or "
                        "hidden reasoning. Explain uncertainty plainly."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Public debate facts:\n{grounded_facts}\n\n"
                        f"User question:\n{clean_message}"
                    ),
                },
            ])
            return {
                "type": intent,
                "message": reply,
                "agentSession": session,
                "profileChanges": session.get("profileChanges", []),
                "answerMode": "llm_grounded",
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
            prediction = self.prediction_loader(skin["skinId"], horizon_days)
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
                budget=budget, horizon_days=7, risk_level=risk_level, locale=locale
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
        language = "English" if locale == "en-US" else "Simplified Chinese"
        reply = self.chat_loader([
            {"role": "system", "content": f"Always answer in {language}."},
            *safe_history,
            {"role": "user", "content": clean_message},
        ])
        return {"type": "chat", "message": reply}
