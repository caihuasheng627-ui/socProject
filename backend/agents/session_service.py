"""Create and continue user-participating multi-agent sessions."""

from __future__ import annotations

from typing import Any, Literal

from .base import model_dump
from .bear_agent import BearAgent
from .bull_agent import BullAgent
from .debate_service import DebateService
from .judge_agent import JudgeAgent
from .conversation import answer_debate_question
from .presentation import infer_user_profile, metric_explanations
from .schemas import BearInput, BullInput, JudgeInput, UserProfile
from .session_store import SessionRecord, SessionStore


AgentTarget = Literal["bull", "bear", "judge"]


def _opinion_summary(result: Any) -> str:
    arguments = getattr(result, "arguments", ())
    claims = [argument.claim for argument in arguments]
    if claims:
        return " ".join(claims)
    reasoning = getattr(result, "reasoning", ())
    if reasoning:
        return " ".join(reasoning)
    return "已生成结构化结果。"


def session_to_api(record: SessionRecord) -> dict[str, Any]:
    grouped: dict[int, dict[str, Any]] = {}
    for message in record.messages:
        round_no = message.get("round")
        if round_no is None:
            continue
        item = grouped.setdefault(
            int(round_no),
            {"roundNo": int(round_no), "userMessage": None, "bull": None, "bear": None, "judge": None},
        )
        if message.get("role") == "user":
            item["userMessage"] = message.get("content")
        elif message.get("agentName") in {"bull", "bear", "judge"}:
            item[message["agentName"]] = message.get("structured")
    return {
        "sessionId": record.session_id,
        "skinId": record.skin_id,
        "status": record.status,
        "userProfile": model_dump(record.user_profile),
        "marketSnapshot": model_dump(record.snapshot),
        "bullHistory": [model_dump(item) for item in record.bull_history],
        "bearHistory": [model_dump(item) for item in record.bear_history],
        "judgeHistory": [model_dump(item) for item in record.judge_history],
        "messages": list(record.messages),
        "debateRounds": [grouped[key] for key in sorted(grouped)],
        "evidenceGuide": metric_explanations(record.snapshot, record.user_profile.locale),
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
    }


class AgentSessionService:
    def __init__(
        self,
        *,
        store: SessionStore | None = None,
        debate_service: DebateService | None = None,
    ) -> None:
        self.store = store or SessionStore()
        self.debate_service = debate_service or DebateService()

    def create(
        self,
        skin_id: str,
        *,
        user_profile: UserProfile | None = None,
        rounds: int = 1,
    ) -> dict[str, Any]:
        profile = user_profile or UserProfile()
        outcome = self.debate_service.run(
            skin_id, user_profile=profile, rounds=rounds
        )
        session_id = self.store.create(
            skin_id=outcome.snapshot.skin_id,
            user_profile=profile,
            snapshot=outcome.snapshot,
        )
        for item in outcome.rounds:
            self.store.append_agent_result(
                session_id,
                agent_name="bull",
                content=_opinion_summary(item.bull),
                result=item.bull,
                round_no=item.round_no,
                model=self.debate_service.bull_agent.model,
            )
            self.store.append_agent_result(
                session_id,
                agent_name="bear",
                content=_opinion_summary(item.bear),
                result=item.bear,
                round_no=item.round_no,
                model=self.debate_service.bear_agent.model,
            )
        self.store.append_agent_result(
            session_id,
            agent_name="judge",
            content=_opinion_summary(outcome.judge),
            result=outcome.judge,
            round_no=len(outcome.rounds),
            model=self.debate_service.judge_agent.model,
        )
        return session_to_api(self.store.get(session_id))

    def get(self, session_id: str) -> dict[str, Any]:
        return session_to_api(self.store.get(session_id))

    def send_message(
        self,
        session_id: str,
        *,
        message: str,
        target_agent: AgentTarget,
        locale: str | None = None,
    ) -> dict[str, Any]:
        if target_agent not in {"bull", "bear", "judge"}:
            raise ValueError("targetAgent must be bull, bear, or judge")
        if not message.strip():
            raise ValueError("message must not be empty")

        state = self.store.get(session_id)
        if locale and locale != state.user_profile.locale:
            profile_data = model_dump(state.user_profile)
            profile_data["locale"] = locale
            self.store.update_user_profile(session_id, UserProfile(**profile_data))
            state = self.store.get(session_id)
        self.store.append_user_message(session_id, message.strip(), target_agent)

        if target_agent == "bull":
            agent = BullAgent()
            round_no = len(state.bull_history) + 1
            result = agent.analyze(
                BullInput(
                    snapshot=state.snapshot,
                    user_profile=state.user_profile,
                    round_no=round_no,
                    bear_opinion=state.bear_history[-1] if state.bear_history else None,
                    bull_history=state.bull_history,
                    user_message=message.strip(),
                )
            )
        elif target_agent == "bear":
            agent = BearAgent()
            round_no = len(state.bear_history) + 1
            result = agent.analyze(
                BearInput(
                    snapshot=state.snapshot,
                    user_profile=state.user_profile,
                    round_no=round_no,
                    bull_opinion=state.bull_history[-1] if state.bull_history else None,
                    bear_history=state.bear_history,
                    user_message=message.strip(),
                )
            )
        else:
            if not state.bull_history or not state.bear_history:
                raise ValueError("Judge requires at least one Bull and Bear opinion")
            agent = JudgeAgent()
            round_no = None
            result = agent.decide(
                JudgeInput(
                    snapshot=state.snapshot,
                    user_profile=state.user_profile,
                    bull_history=list(state.bull_history),
                    bear_history=list(state.bear_history),
                    judge_history=list(state.judge_history),
                    user_message=message.strip(),
                )
            )

        self.store.append_agent_result(
            session_id,
            agent_name=target_agent,
            content=_opinion_summary(result),
            result=result,
            round_no=round_no,
            model=agent.model,
        )
        return session_to_api(self.store.get(session_id))

    def run_round(
        self, session_id: str, *, message: str, locale: str | None = None
    ) -> dict[str, Any]:
        """Run one user-participating, sequential Bull -> Bear -> Judge round."""

        clean_message = message.strip()
        if not clean_message:
            raise ValueError("message must not be empty")
        state = self.store.get(session_id)
        if locale and locale != state.user_profile.locale:
            profile_data = model_dump(state.user_profile)
            profile_data["locale"] = locale
            self.store.update_user_profile(session_id, UserProfile(**profile_data))
            state = self.store.get(session_id)
        round_no = len(state.judge_history) + 1

        profile, profile_changes = infer_user_profile(clean_message, state.user_profile)
        if profile != state.user_profile:
            self.store.update_user_profile(session_id, profile)

        self.store.append_user_message(
            session_id, clean_message, "orchestrator", round_no=round_no
        )

        bull_agent = BullAgent()
        bull = bull_agent.analyze(BullInput(
            snapshot=state.snapshot,
            user_profile=profile,
            round_no=round_no,
            bear_opinion=state.bear_history[-1] if state.bear_history else None,
            bull_history=state.bull_history,
            user_message=clean_message,
        ))
        self.store.append_agent_result(
            session_id,
            agent_name="bull",
            content=_opinion_summary(bull),
            result=bull,
            round_no=round_no,
            model=bull_agent.model,
        )

        bear_agent = BearAgent()
        bear = bear_agent.analyze(BearInput(
            snapshot=state.snapshot,
            user_profile=profile,
            round_no=round_no,
            bull_opinion=bull,
            bear_history=state.bear_history,
            user_message=clean_message,
        ))
        self.store.append_agent_result(
            session_id,
            agent_name="bear",
            content=_opinion_summary(bear),
            result=bear,
            round_no=round_no,
            model=bear_agent.model,
        )

        judge_agent = JudgeAgent()
        judge = judge_agent.decide(JudgeInput(
            snapshot=state.snapshot,
            user_profile=profile,
            bull_history=[*state.bull_history, bull],
            bear_history=[*state.bear_history, bear],
            judge_history=list(state.judge_history),
            user_message=clean_message,
        ))
        self.store.append_agent_result(
            session_id,
            agent_name="judge",
            content=_opinion_summary(judge),
            result=judge,
            round_no=round_no,
            model=judge_agent.model,
        )
        response = session_to_api(self.store.get(session_id))
        response["profileChanges"] = profile_changes
        return response

    def answer_question(
        self, session_id: str, *, message: str, locale: str | None = None
    ) -> dict[str, Any]:
        """Answer against the latest public debate without silently starting a new round."""

        clean_message = message.strip()
        if not clean_message:
            raise ValueError("message must not be empty")
        state = self.store.get(session_id)
        if locale and locale != state.user_profile.locale:
            profile_data = model_dump(state.user_profile)
            profile_data["locale"] = locale
            self.store.update_user_profile(session_id, UserProfile(**profile_data))
            state = self.store.get(session_id)
        if not state.judge_history:
            raise ValueError("session has no Judge decision to explain")

        profile, profile_changes = infer_user_profile(clean_message, state.user_profile)
        if profile != state.user_profile:
            self.store.update_user_profile(session_id, profile)
        self.store.append_user_message(session_id, clean_message, "main_ai")

        answer = answer_debate_question(
            clean_message,
            judge=state.judge_history[-1],
            snapshot=state.snapshot,
            profile=profile,
            bull=state.bull_history[-1] if state.bull_history else None,
            bear=state.bear_history[-1] if state.bear_history else None,
            profile_changes=profile_changes,
        )
        response = session_to_api(self.store.get(session_id))
        response["profileChanges"] = profile_changes
        response["answer"] = answer
        return response

    def update_profile(
        self, session_id: str, *, message: str, locale: str | None = None
    ) -> dict[str, Any]:
        """Persist explicit constraints without silently rerunning all agents."""

        clean_message = message.strip()
        if not clean_message:
            raise ValueError("message must not be empty")
        state = self.store.get(session_id)
        if locale and locale != state.user_profile.locale:
            profile_data = model_dump(state.user_profile)
            profile_data["locale"] = locale
            self.store.update_user_profile(session_id, UserProfile(**profile_data))
            state = self.store.get(session_id)
        profile, profile_changes = infer_user_profile(clean_message, state.user_profile)
        if profile != state.user_profile:
            self.store.update_user_profile(session_id, profile)
        self.store.append_user_message(session_id, clean_message, "main_ai")
        response = session_to_api(self.store.get(session_id))
        response["profileChanges"] = profile_changes
        return response
