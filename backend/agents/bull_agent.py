"""Bull Agent: evidence-backed upside analysis with isolated state."""

from __future__ import annotations

from typing import Any

from config import BULL_MODEL, BULL_TEMPERATURE

from .base import BaseAgent, model_dump
from .localization import is_english
from .prompts import BULL_SYSTEM_PROMPT, BULL_SYSTEM_PROMPT_EN
from .presentation import (
    argument_from_evidence, contains_cjk, normalize_argument_locale,
)
from .schemas import AgentArgument, BullInput, BullOpinion, Evidence
from .tools import AgentToolbox, BULL_FOCUS_TOOL


class BullAgent(BaseAgent[BullOpinion]):
    def __init__(self, *, llm_callable=None, toolbox: AgentToolbox | None = None) -> None:
        super().__init__(
            name="bull",
            system_prompt=BULL_SYSTEM_PROMPT,
            system_prompt_en=BULL_SYSTEM_PROMPT_EN,
            output_schema=BullOpinion,
            model=BULL_MODEL,
            temperature=BULL_TEMPERATURE,
            allowed_tools={BULL_FOCUS_TOOL},
            llm_callable=llm_callable,
        )
        self.toolbox = toolbox or AgentToolbox()

    def build_user_payload(self, input_data: Any) -> dict[str, Any]:
        if not isinstance(input_data, BullInput):
            raise TypeError("BullAgent expects BullInput")
        focused = self.toolbox.call(self, BULL_FOCUS_TOOL, input_data.snapshot)
        return {
            "task": "analyze_upside" if input_data.round_no == 1 else "rebut_bear",
            "round_no": input_data.round_no,
            "market_snapshot": model_dump(input_data.snapshot),
            "focus_evidence": [model_dump(item) for item in focused],
            "user_profile": model_dump(input_data.user_profile),
            "own_public_history": [model_dump(item) for item in input_data.bull_history],
            "user_message": input_data.user_message,
            "bear_public_opinion": (
                model_dump(input_data.bear_opinion) if input_data.bear_opinion else None
            ),
        }

    def analyze(self, input_data: BullInput) -> BullOpinion:
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
                    argument, evidence_by_id, "bull", input_data.user_profile.locale
                ))
                for argument in arguments
            ]
            data["assumptions"] = [
                value for value in result.assumptions if not contains_cjk(value)
            ] or list(fallback.assumptions)
        return BullOpinion(**data)

    @staticmethod
    def _evidence_confidence(input_data: BullInput) -> float:
        """Keep evidence confidence independent from user risk preferences."""

        has_positive = any(
            item.direction == "positive" for item in input_data.snapshot.evidence
        )
        confidence = (
            input_data.snapshot.hybrid_prediction.confidence if has_positive else 35.0
        )
        return max(20.0, min(90.0, confidence))

    @staticmethod
    def _mock_opinion(input_data: BullInput) -> BullOpinion:
        prediction = input_data.snapshot.hybrid_prediction
        positive = [
            item for item in input_data.snapshot.evidence if item.direction == "positive"
        ]
        locale = input_data.user_profile.locale
        english = is_english(locale)
        arguments = [
            argument_from_evidence(item, "bull", locale) for item in positive[:3]
        ]
        if input_data.bear_opinion and arguments:
            first = arguments[0]
            arguments[0] = AgentArgument(
                claim=(
                    f"Response to Bear's risk case: {first.claim}"
                    if english else f"回应 Bear 的风险观点：{first.claim}"
                ),
                evidence_ids=first.evidence_ids,
                importance=0.82,
                explanation=first.explanation,
                decision_impact=(
                    "This does not remove Bear's risk, but it shows an upside condition "
                    "that may fit within a controlled risk budget."
                    if english else
                    "该证据没有消除 Bear 指出的风险，但说明仍存在可被风险预算接受的上行条件。"
                ),
            )
        if input_data.user_message:
            arguments.append(AgentArgument(
                claim=(
                    f"User input this round: {input_data.user_message.strip()}"
                    if english else f"用户本轮意见：{input_data.user_message.strip()}"
                ),
                evidence_ids=(),
                importance=0.55,
                explanation=(
                    "This is a user constraint or preference, not market data, so it is not treated as price evidence."
                    if english else
                    "这是用户的风险承受能力和决策偏好，不是市场数据，因此不会被当作价格证据。"
                ),
                decision_impact=(
                    "This preference does not increase evidence confidence; Judge uses it only "
                    "to adjust action thresholds, position size and stops."
                    if english else
                    "该偏好不会提高市场证据置信度；它只由 Judge 的风险策略层用于调整行动门槛、仓位和止损。"
                ),
            ))
        can_buy = (
            prediction.change_pct > 0
            and prediction.confidence >= 55
            and bool(arguments)
        )
        return BullOpinion(
            position="buy" if can_buy else "watch",
            arguments=arguments,
            target_price=prediction.predicted_price,
            confidence=BullAgent._evidence_confidence(input_data),
            assumptions=[
                (
                    "Bull confidence measures positive market evidence only; it is independent of user risk preference."
                    if english else
                    "Bull 置信度只表示正面市场证据的支持强度，不受用户风险偏好影响"
                ),
                (
                    "Budget, purpose, horizon and risk preference are handled by Judge's policy layer."
                    if english else
                    "预算、用途、持有期与风险偏好由 Judge 的策略层处理"
                ),
            ],
        )
