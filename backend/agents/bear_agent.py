"""Bear Agent: evidence-backed downside analysis with isolated state."""

from __future__ import annotations

from typing import Any

from config import BEAR_MODEL, BEAR_TEMPERATURE

from .base import BaseAgent, model_dump
from .localization import is_english
from .prompts import BEAR_SYSTEM_PROMPT, BEAR_SYSTEM_PROMPT_EN
from .presentation import (
    argument_from_evidence, contains_cjk, normalize_argument_locale,
)
from .schemas import AgentArgument, BearInput, BearOpinion
from .tools import AgentToolbox, BEAR_FOCUS_TOOL


class BearAgent(BaseAgent[BearOpinion]):
    def __init__(self, *, llm_callable=None, toolbox: AgentToolbox | None = None) -> None:
        super().__init__(
            name="bear",
            system_prompt=BEAR_SYSTEM_PROMPT,
            system_prompt_en=BEAR_SYSTEM_PROMPT_EN,
            output_schema=BearOpinion,
            model=BEAR_MODEL,
            temperature=BEAR_TEMPERATURE,
            allowed_tools={BEAR_FOCUS_TOOL},
            llm_callable=llm_callable,
        )
        self.toolbox = toolbox or AgentToolbox()

    def build_user_payload(self, input_data: Any) -> dict[str, Any]:
        if not isinstance(input_data, BearInput):
            raise TypeError("BearAgent expects BearInput")
        focused = self.toolbox.call(self, BEAR_FOCUS_TOOL, input_data.snapshot)
        return {
            "task": "analyze_downside" if input_data.round_no == 1 else "rebut_bull",
            "round_no": input_data.round_no,
            "market_snapshot": model_dump(input_data.snapshot),
            "focus_evidence": [model_dump(item) for item in focused],
            "user_profile": model_dump(input_data.user_profile),
            "own_public_history": [model_dump(item) for item in input_data.bear_history],
            "user_message": input_data.user_message,
            "bull_public_opinion": (
                model_dump(input_data.bull_opinion) if input_data.bull_opinion else None
            ),
        }

    def analyze(self, input_data: BearInput) -> BearOpinion:
        result = self.run(input_data, mock_data=lambda: self._mock_opinion(input_data))
        data = model_dump(result)
        data["confidence"] = self._evidence_confidence(input_data)
        fallback = self._mock_opinion(input_data)
        if not result.arguments:
            data["arguments"] = [model_dump(item) for item in fallback.arguments]
        if is_english(input_data.user_profile.locale):
            evidence_by_id = {
                item.evidence_id: item for item in input_data.snapshot.evidence
            }
            arguments = result.arguments or fallback.arguments
            data["arguments"] = [
                model_dump(normalize_argument_locale(
                    argument, evidence_by_id, "bear", input_data.user_profile.locale
                ))
                for argument in arguments
            ]
            data["risks"] = [
                value for value in result.risks if not contains_cjk(value)
            ] or list(fallback.risks)
        return BearOpinion(**data)

    @staticmethod
    def _evidence_confidence(input_data: BearInput) -> float:
        """Keep objective risk confidence independent from user preferences."""

        snapshot = input_data.snapshot
        has_negative = any(item.direction == "negative" for item in snapshot.evidence)
        material_risk = (
            snapshot.hybrid_prediction.change_pct <= 0
            or (snapshot.volatility_30d or 0) >= 5
            or (snapshot.max_drawdown_30d or 0) >= 10
            or (snapshot.liquidity_score is not None and snapshot.liquidity_score < 30)
        )
        if not has_negative:
            return 30.0
        return 65.0 if material_risk else 45.0

    @staticmethod
    def _mock_opinion(input_data: BearInput) -> BearOpinion:
        snapshot = input_data.snapshot
        negative = [item for item in snapshot.evidence if item.direction == "negative"]
        locale = input_data.user_profile.locale
        english = is_english(locale)
        arguments = [
            argument_from_evidence(item, "bear", locale) for item in negative[:3]
        ]
        if input_data.bull_opinion and arguments:
            first = arguments[0]
            arguments[0] = AgentArgument(
                claim=(
                    f"Rebuttal to Bull's upside case: {first.claim}"
                    if english else f"反驳 Bull 的本轮上行观点：{first.claim}"
                ),
                evidence_ids=first.evidence_ids,
                importance=0.85,
                explanation=first.explanation,
                decision_impact=(
                    "Even if Bull's upside condition holds, this risk may keep actual returns below the model target."
                    if english else
                    "即使 Bull 的上涨条件成立，这项风险仍可能使实际回报低于模型目标。"
                ),
            )
        if input_data.user_message:
            arguments.append(AgentArgument(
                claim=(
                    f"User input this round: {input_data.user_message.strip()}"
                    if english else f"用户本轮意见：{input_data.user_message.strip()}"
                ),
                evidence_ids=(),
                importance=0.5,
                explanation=(
                    "The user may accept more risk, but preference does not reduce objective volatility, drawdown or liquidity risk."
                    if english else
                    "用户可以选择承担更多风险，但主观偏好不会降低客观波动、回撤或流动性风险。"
                ),
                decision_impact=(
                    "A higher risk tolerance can permit a small trial position, but never removes exit-cost and stop-loss constraints."
                    if english else
                    "风险偏好较高时可讨论小仓位试仓，但不能因此忽略退出成本和止损。"
                ),
            ))
        material_risk = (
            snapshot.hybrid_prediction.change_pct <= 0
            or (snapshot.volatility_30d or 0) >= 5
            or (snapshot.max_drawdown_30d or 0) >= 10
            or (snapshot.liquidity_score is not None and snapshot.liquidity_score < 30)
        )
        return BearOpinion(
            position="avoid" if material_risk else "watch",
            arguments=arguments,
            stop_loss=round(snapshot.current_price * 0.93, 2),
            confidence=BearAgent._evidence_confidence(input_data),
            risks=[
                *[argument.claim for argument in arguments[:len(negative[:3])]],
                (
                    "Bear confidence measures objective risk evidence only; it is independent of user risk preference."
                    if english else
                    "Bear 置信度只表示客观风险证据强度，不受用户风险偏好影响"
                ),
            ],
        )
