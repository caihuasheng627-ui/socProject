"""Multi-round Bull/Bear orchestration followed by an independent Judge."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

from .base import model_dump
from .bear_agent import BearAgent
from .bull_agent import BullAgent
from .evidence import EvidenceBuilder
from .judge_agent import JudgeAgent
from .schemas import (
    BearInput,
    BearOpinion,
    BullInput,
    BullOpinion,
    DebateOutcome,
    DebateRoundOutcome,
    JudgeInput,
    UserProfile,
)


class DebateService:
    """Run isolated agents over one shared snapshot.

    Bull and Bear execute concurrently within each round. Only their public,
    structured opinions cross the role boundary between rounds.
    """

    def __init__(
        self,
        *,
        evidence_builder: EvidenceBuilder | None = None,
        bull_agent: BullAgent | None = None,
        bear_agent: BearAgent | None = None,
        judge_agent: JudgeAgent | None = None,
        parallel: bool = True,
    ) -> None:
        self.evidence_builder = evidence_builder or EvidenceBuilder()
        self.bull_agent = bull_agent or BullAgent()
        self.bear_agent = bear_agent or BearAgent()
        self.judge_agent = judge_agent or JudgeAgent()
        self.parallel = parallel

    def run(
        self,
        skin_id: str,
        *,
        user_profile: UserProfile | None = None,
        rounds: int = 3,
    ) -> DebateOutcome:
        if not 1 <= rounds <= 5:
            raise ValueError("rounds must be between 1 and 5")
        profile = user_profile or UserProfile()
        snapshot = self.evidence_builder.build(
            skin_id, horizon_days=profile.horizon_days
        )

        bull_history: list[BullOpinion] = []
        bear_history: list[BearOpinion] = []
        outcomes: list[DebateRoundOutcome] = []

        for round_no in range(1, rounds + 1):
            bull_input = BullInput(
                snapshot=snapshot,
                user_profile=profile,
                round_no=round_no,
                bear_opinion=bear_history[-1] if bear_history else None,
                bull_history=tuple(bull_history),
            )
            bear_input = BearInput(
                snapshot=snapshot,
                user_profile=profile,
                round_no=round_no,
                bull_opinion=bull_history[-1] if bull_history else None,
                bear_history=tuple(bear_history),
            )
            bull, bear = self._run_round(bull_input, bear_input)
            bull_history.append(bull)
            bear_history.append(bear)
            outcomes.append(
                DebateRoundOutcome(round_no=round_no, bull=bull, bear=bear)
            )

        judge = self.judge_agent.decide(
            JudgeInput(
                snapshot=snapshot,
                user_profile=profile,
                bull_history=bull_history,
                bear_history=bear_history,
            )
        )
        return DebateOutcome(snapshot=snapshot, rounds=tuple(outcomes), judge=judge)

    def _run_round(
        self, bull_input: BullInput, bear_input: BearInput
    ) -> tuple[BullOpinion, BearOpinion]:
        if not self.parallel:
            return (
                self.bull_agent.analyze(bull_input),
                self.bear_agent.analyze(bear_input),
            )
        bull_context = copy_context()
        bear_context = copy_context()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="debate") as executor:
            bull_future = executor.submit(
                bull_context.run, self.bull_agent.analyze, bull_input
            )
            bear_future = executor.submit(
                bear_context.run, self.bear_agent.analyze, bear_input
            )
            return bull_future.result(), bear_future.result()


def _argument_text(opinion: BullOpinion | BearOpinion, locale: str) -> str:
    claims = [argument.claim for argument in opinion.arguments]
    return " ".join(claims) if claims else (
        "Current evidence is insufficient for a strong view."
        if locale == "en-US" else "当前证据不足，暂不形成强观点。"
    )


def _bull_text(opinion: BullOpinion, locale: str) -> str:
    if locale == "en-US":
        target = f"Target ${opinion.target_price:.2f}. " if opinion.target_price is not None else ""
        return f"{_argument_text(opinion, locale)} {target}Confidence {opinion.confidence:.1f}%.".strip()
    target = f"目标价 ${opinion.target_price:.2f}。" if opinion.target_price is not None else ""
    return f"{_argument_text(opinion, locale)} {target}置信度 {opinion.confidence:.1f}%。".strip()


def _bear_text(opinion: BearOpinion, locale: str) -> str:
    if locale == "en-US":
        stop = f"Reference stop ${opinion.stop_loss:.2f}. " if opinion.stop_loss is not None else ""
        return f"{_argument_text(opinion, locale)} {stop}Confidence {opinion.confidence:.1f}%.".strip()
    stop = f"参考止损 ${opinion.stop_loss:.2f}。" if opinion.stop_loss is not None else ""
    return f"{_argument_text(opinion, locale)} {stop}置信度 {opinion.confidence:.1f}%。".strip()


def outcome_to_api(
    outcome: DebateOutcome,
    *,
    mode: str,
    requested_mode: str = "bull_bear",
    locale: str = "zh-CN",
) -> dict[str, Any]:
    """Expose schema v2 while preserving fields consumed by the old page."""

    judge = outcome.judge
    snapshot = outcome.snapshot
    decision_labels = ({
        "buy": "Buy",
        "watch": "Watch",
        "avoid": "Avoid",
        "insufficient_evidence": "Insufficient evidence",
    } if locale == "en-US" else {
        "buy": "买入",
        "watch": "观望",
        "avoid": "不建议选择",
        "insufficient_evidence": "证据不足",
    })
    confidence_label = (
        "high" if judge.confidence >= 75 else "medium" if judge.confidence >= 50 else "low"
    )
    entry_range = (
        f"${judge.entry_low:.2f} ~ ${judge.entry_high:.2f}"
        if judge.entry_low is not None and judge.entry_high is not None
        else "—"
    )
    final_bear = outcome.rounds[-1].bear
    rounds = [
        {
            "round": item.round_no,
            "bull": _bull_text(item.bull, locale),
            "bear": _bear_text(item.bear, locale),
            "bullOpinion": model_dump(item.bull),
            "bearOpinion": model_dump(item.bear),
        }
        for item in outcome.rounds
    ]
    prediction = model_dump(snapshot.hybrid_prediction)
    prediction["current_price"] = snapshot.current_price

    return {
        "schemaVersion": 2,
        "skinId": snapshot.skin_id,
        "mode": mode,
        "requestedMode": requested_mode,
        "rounds": rounds,
        "judge": model_dump(judge),
        "consensus": {
            "recommendation": decision_labels[judge.decision],
            "entryRange": entry_range,
            "stopLoss": f"${judge.stop_loss:.2f}" if judge.stop_loss is not None else "—",
            "targetPrice": f"${judge.target_price:.2f}" if judge.target_price is not None else "—",
            "consensusScore": judge.confidence,
            "confidence": confidence_label,
            "risks": list(final_bear.risks),
        },
        "prediction": prediction,
        "marketSnapshot": model_dump(snapshot),
        "agentMeta": {
            "isolated": True,
            "rounds": len(outcome.rounds),
            "bullHistoryMessages": len(outcome.rounds) * 2,
            "bearHistoryMessages": len(outcome.rounds) * 2,
            "judgeHistoryMessages": 2,
        },
    }
