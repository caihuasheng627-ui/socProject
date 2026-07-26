"""Judge Agent: neutral, evidence-validated, user-facing final decision."""

from __future__ import annotations

from typing import Any

from config import JUDGE_MODEL, JUDGE_TEMPERATURE

from .base import BaseAgent, model_dump
from .localization import is_english, risk_label
from .presentation import contains_cjk
from .prompts import JUDGE_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT_EN
from .risk_policy import apply_risk_policy
from .schemas import JudgeDecision, JudgeInput
from .tools import AgentToolbox, JUDGE_EVIDENCE_TOOL


class EvidenceValidationError(ValueError):
    pass


class JudgeAgent(BaseAgent[JudgeDecision]):
    def __init__(self, *, llm_callable=None, toolbox: AgentToolbox | None = None) -> None:
        super().__init__(
            name="judge",
            system_prompt=JUDGE_SYSTEM_PROMPT,
            system_prompt_en=JUDGE_SYSTEM_PROMPT_EN,
            output_schema=JudgeDecision,
            model=JUDGE_MODEL,
            temperature=JUDGE_TEMPERATURE,
            allowed_tools={JUDGE_EVIDENCE_TOOL},
            llm_callable=llm_callable,
        )
        self.toolbox = toolbox or AgentToolbox()

    def build_user_payload(self, input_data: Any) -> dict[str, Any]:
        if not isinstance(input_data, JudgeInput):
            raise TypeError("JudgeAgent expects JudgeInput")
        self._validate_opinion_evidence(input_data)
        evidence = self.toolbox.call(self, JUDGE_EVIDENCE_TOOL, input_data.snapshot)
        return {
            "task": "issue_neutral_decision_with_plain_language_report",
            "market_snapshot": model_dump(input_data.snapshot),
            "all_evidence": [model_dump(item) for item in evidence],
            "user_profile": model_dump(input_data.user_profile),
            "user_message": input_data.user_message,
            "bull_history": [model_dump(item) for item in input_data.bull_history],
            "bear_history": [model_dump(item) for item in input_data.bear_history],
            "judge_history": [model_dump(item) for item in input_data.judge_history],
        }

    def decide(self, input_data: JudgeInput) -> JudgeDecision:
        result = self.run(input_data, mock_data=lambda: self._mock_decision(input_data))
        return self._complete_strategy(result, input_data)

    @staticmethod
    def _complete_strategy(result: JudgeDecision, input_data: JudgeInput) -> JudgeDecision:
        """Turn a verdict into an executable policy, including for legacy LLM output."""

        snapshot = input_data.snapshot
        profile = input_data.user_profile
        bull = input_data.bull_history[-1] if input_data.bull_history else None
        bear = input_data.bear_history[-1] if input_data.bear_history else None
        data = model_dump(result)
        english = is_english(profile.locale)
        policy = apply_risk_policy(snapshot, profile)
        action = policy.action

        decision_map = {
            "buy_now": "buy", "scale_in": "buy", "wait_for_trigger": "watch",
            "avoid": "avoid", "insufficient": "insufficient_evidence",
        }
        data["strategy_action"] = action
        data["decision"] = decision_map[action]
        data["opportunity_score"] = policy.opportunity_score
        data["risk_score"] = policy.risk_score
        data["decision_score"] = policy.decision_score
        data["policy_risk_level"] = profile.risk_level
        data["policy_threshold"] = policy.threshold_summary
        data["policy_explanation"] = policy.explanation
        data["confidence"] = max(25.0, min(85.0, policy.decision_score))

        bull_ids = {
            evidence_id for opinion in input_data.bull_history
            for argument in opinion.arguments for evidence_id in argument.evidence_ids
        }
        bear_ids = {
            evidence_id for opinion in input_data.bear_history
            for argument in opinion.arguments for evidence_id in argument.evidence_ids
        }
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        if english or not result.agreed_facts:
            shared = [evidence_by_id[item] for item in bull_ids & bear_ids if item in evidence_by_id]
            agreed = [] if english else [f"双方共同采用：{item.content}" for item in shared[:4]]
            if english:
                agreed = [
                    f"Current price: ${snapshot.current_price:.4f}.",
                    f"Hybrid {snapshot.hybrid_prediction.horizon_days}-day forecast: "
                    f"{snapshot.hybrid_prediction.change_pct:+.2f}% at "
                    f"{snapshot.hybrid_prediction.confidence:.0f}% model confidence.",
                    f"7-day change: {snapshot.change_7d:+.2f}%."
                    if snapshot.change_7d is not None else "7-day change is unavailable.",
                    f"30-day change: {snapshot.change_30d:+.2f}%."
                    if snapshot.change_30d is not None else "30-day change is unavailable.",
                ]
            elif not agreed:
                agreed = [
                    f"当前价格为 ${snapshot.current_price:.2f}。",
                    f"Hybrid 对未来 {snapshot.hybrid_prediction.horizon_days} 天的预测为 "
                    f"{snapshot.hybrid_prediction.change_pct:+.2f}%。",
                ]
            data["agreed_facts"] = agreed

        if (english or not result.complementary_views) and bull and bear:
            bull_claim = bull.arguments[0].claim if bull.arguments else (
                "upside condition" if english else "上行条件"
            )
            bear_claim = bear.arguments[0].claim if bear.arguments else (
                "downside risk" if english else "下行风险"
            )
            data["complementary_views"] = [
                (
                    f'Bull’s “{bull_claim}” defines when action may be justified; '
                    f'Bear’s “{bear_claim}” limits position size and exit risk. Both can be true.'
                    if english else
                    f"Bull 的“{bull_claim}”用于定义何时值得行动；"
                    f"Bear 的“{bear_claim}”用于限制仓位和退出风险，两者可以同时成立。"
                )
            ]

        # Role recommendations are expected to differ. Only incompatible factual
        # claims (value, scope, time window, or source) belong in true_conflicts.
        factual_conflict_markers = (
            "数值不一致", "数据不一致", "口径不同", "时间窗口不同",
            "来源冲突", "同一事实", "互不相容的事实",
            "inconsistent value", "inconsistent data", "different scope",
            "different time window", "source conflict", "same fact",
            "mutually incompatible fact",
        )
        data["true_conflicts"] = [
            item for item in result.true_conflicts
            if any(marker in item for marker in factual_conflict_markers)
            and (not english or not contains_cjk(item))
        ]

        if english:
            data["evidence_verdicts"] = [
                (
                    f"Hybrid confidence is {snapshot.hybrid_prediction.confidence:.0f}%; "
                    + (
                        "the prediction is degraded and can only be a supporting signal."
                        if snapshot.hybrid_prediction.degraded else
                        "it is directional evidence, but cannot decide a purchase by itself."
                    )
                ),
                "The 7-day and 30-day changes come from the same price series, so they are combined without double-counting.",
                (
                    f"Current liquidity score is {snapshot.liquidity_score:.1f}."
                    if snapshot.liquidity_score is not None else
                    "A current liquidity score is unavailable."
                ),
            ]
        elif not result.evidence_verdicts:
            data["evidence_verdicts"] = [
                f"Hybrid 置信度为 {snapshot.hybrid_prediction.confidence:.0f}%，"
                f"{'属于降级预测，只能作为辅助信号。' if snapshot.hybrid_prediction.degraded else '可作为方向证据，但不能单独决定买入。'}",
                "7 日和 30 日涨跌都来自价格序列，属于相关证据，合并判断而不重复加权。",
                f"当前流动性评分为 {snapshot.liquidity_score:.1f}。"
                if snapshot.liquidity_score is not None else "当前缺少流动性评分。",
            ]

        data["stop_loss"] = policy.stop_loss
        data["position_size_pct"] = policy.position_size_pct
        data["entry_strategy"] = policy.entry_strategy
        data["buy_triggers"] = policy.buy_triggers
        data["exit_triggers"] = policy.exit_triggers
        data["conditions_to_buy"] = policy.buy_triggers
        data["conditions_to_wait"] = policy.exit_triggers
        data["recheck_after_days"] = policy.recheck_after_days

        action_labels = ({
            "buy_now": "buy now in tranches",
            "scale_in": "open a small trial position",
            "wait_for_trigger": "wait for a trigger",
            "avoid": "avoid for now",
            "insufficient": "collect more data",
        } if english else {
            "buy_now": "立即分批买入",
            "scale_in": "小仓位分批试仓",
            "wait_for_trigger": "等待触发条件",
            "avoid": "暂时排除",
            "insufficient": "补充数据后再判断",
        })
        data["summary"] = (
            (
                f"Integrated strategy: {action_labels[action]}. Opportunity "
                f"{policy.opportunity_score:.0f}, risk {policy.risk_score:.0f}, "
                f"decision {policy.decision_score:.0f}. {policy.threshold_summary}"
            ) if english else (
                f"综合策略：{action_labels[action]}。机会分 {policy.opportunity_score:.0f}，"
                f"风险分 {policy.risk_score:.0f}，决策分 {policy.decision_score:.0f}；"
                f"{policy.threshold_summary}"
            )
        )
        recommendation = ({
            "buy_now": f"Start buying in tranches, cap the planned position at {data['position_size_pct']:.0f}%, and enforce the exit rules.",
            "scale_in": f"Do not commit a full position. Test with at most {data['position_size_pct']:.0f}% of the planned allocation, then add only after confirmation.",
            "wait_for_trigger": f"Keep a zero position for now; recheck within {data['recheck_after_days']} days and enter only after a buy trigger.",
            "avoid": "Do not open a position; a risk constraint or hard user requirement is not met. Compare more liquid alternatives.",
            "insufficient": "Do not act yet; first obtain enough price, volume and liquidity data.",
        } if english else {
            "buy_now": f"可以开始分批买入，计划仓位上限为 {data['position_size_pct']:.0f}%，并严格执行退出条件。",
            "scale_in": f"不做满仓判断，先用最多 {data['position_size_pct']:.0f}% 的计划仓位试仓，再由触发条件决定是否加仓。",
            "wait_for_trigger": f"当前保持零仓位；最多等待 {data['recheck_after_days']} 天，满足买入触发条件后再进入。",
            "avoid": "当前不建仓；风险约束或用户硬条件不满足，优先比较同类中流动性更好的候选。",
            "insufficient": "暂不行动，先补齐价格、成交量或流动性数据。",
        })[action]
        if action == "avoid" and profile.budget is not None and snapshot.current_price > profile.budget:
            recommendation = (
                f"Current price ${snapshot.current_price:.2f} exceeds your {profile.budget:g} budget. "
                "Do not stretch the budget; compare similar candidates within it."
                if english else
                f"当前价格 ${snapshot.current_price:.2f} 超过你的预算 {profile.budget:g}，"
                "不建议扩大预算；优先比较预算内的同类候选。"
            )
        data["recommendation"] = recommendation
        previous = input_data.judge_history[-1] if input_data.judge_history else None
        if previous:
            previous_action = previous.strategy_action
            previous_position = previous.position_size_pct or 0
            changed = (
                previous_action != action
                or abs(previous_position - policy.position_size_pct) >= 0.1
                or previous.policy_risk_level != profile.risk_level
            )
            data["changed_from_previous"] = changed
            if previous_action != action or abs(previous_position - policy.position_size_pct) >= 0.1:
                data["change_summary"] = (
                    (
                        "Market facts did not change with the preference. The risk policy moved from "
                        f"{previous.policy_risk_level}/{previous_action}/{previous_position:.0f}% to "
                        f"{profile.risk_level}/{action}/{policy.position_size_pct:.0f}%. "
                        f"{policy.threshold_summary}"
                    ) if english else (
                        "市场事实没有因偏好变化；风险策略从 "
                        f"{previous.policy_risk_level}/{previous_action}/{previous_position:.0f}% "
                        f"调整为 {profile.risk_level}/{action}/{policy.position_size_pct:.0f}%。"
                        f"{policy.threshold_summary}"
                    )
                )
            else:
                data["change_summary"] = (
                    f"Risk profile is now {risk_label(profile.risk_level, profile.locale)}, but the action remains {action}: {policy.threshold_summary}"
                    if english else
                    f"风险档已更新为 {profile.risk_level}，但动作仍为 {action}：{policy.threshold_summary}"
                )
        else:
            data["changed_from_previous"] = False
            data["change_summary"] = (
                f"This is the initial strategy under the {risk_label(profile.risk_level, profile.locale)} profile. {policy.threshold_summary}"
                if english else
                f"这是初始策略，按 {profile.risk_level} 风险档计算。{policy.threshold_summary}"
            )
        if english:
            liquidity = (
                f"{snapshot.liquidity_score:.1f}" if snapshot.liquidity_score is not None
                else "unavailable"
            )
            data["key_conflict"] = (
                f"Hybrid forecasts {snapshot.hybrid_prediction.change_pct:+.2f}% over "
                f"{snapshot.hybrid_prediction.horizon_days} days. Bull defines upside triggers; "
                f"Bear defines drawdown and liquidity limits. Liquidity is {liquidity}."
            )
            data["profile_fit"] = (
                f"Policy uses a {risk_label(profile.risk_level, profile.locale)} risk profile, "
                f"a {profile.horizon_days}-day horizon, purpose “{profile.purpose}”, and "
                f"{profile.liquidity_priority} liquidity priority."
            )
            data["confidence_basis"] = (
                f"Hybrid model reliability: {snapshot.hybrid_prediction.confidence:.0f}/100.",
                f"Bull evidence confidence: {bull.confidence:.0f}/100." if bull else "Bull evidence is unavailable.",
                f"Bear evidence confidence: {bear.confidence:.0f}/100." if bear else "Bear evidence is unavailable.",
                "Judge confidence is evidence support for the ruling, not a probability of a price rise.",
            )
            data["reasoning"] = (
                data["key_conflict"],
                data["profile_fit"],
                policy.threshold_summary,
            )
            data["risk_warning"] = (
                "Skin-price forecasts contain model error and event risk. This is a conditional decision, not a return guarantee."
            )
            data["user_view_considered"] = (
                f"User input included this round: {input_data.user_message.strip()}"
                if input_data.user_message else
                "No new user constraint was added this round; the current profile was applied."
            )
            data["alternative_action"] = (
                "If this risk boundary is unacceptable, compare skins in the same weapon category with better liquidity and steadier trends."
            )
        elif not result.alternative_action:
            data["alternative_action"] = (
                "若不接受当前风险边界，可比较同武器类别中流动性更高、趋势更稳定的皮肤。"
            )
        if not english and not result.confidence_basis:
            data["confidence_basis"] = (
                f"Hybrid 模型可靠度：{snapshot.hybrid_prediction.confidence:.0f}/100。",
                *(("Bull 证据置信度：{:.0f}/100。".format(bull.confidence),) if bull else ()),
                *(("Bear 证据置信度：{:.0f}/100。".format(bear.confidence),) if bear else ()),
                "Judge 置信度是裁决证据支持强度，不是价格上涨概率。",
            )
        return JudgeDecision(**data)

    def validate_result(self, result: JudgeDecision, input_data: Any) -> None:
        if not isinstance(input_data, JudgeInput):
            raise TypeError("JudgeAgent expects JudgeInput")
        unsupported = result.unsupported_evidence(input_data.snapshot)
        if unsupported:
            raise EvidenceValidationError(
                "Judge referenced unsupported evidence: " + ", ".join(sorted(unsupported))
            )

    @staticmethod
    def _validate_opinion_evidence(input_data: JudgeInput) -> None:
        valid = input_data.snapshot.evidence_ids()
        referenced = {
            evidence_id
            for opinion in [*input_data.bull_history, *input_data.bear_history]
            for argument in opinion.arguments
            for evidence_id in argument.evidence_ids
        }
        unsupported = referenced - valid
        if unsupported:
            raise EvidenceValidationError(
                "Agent opinion referenced unsupported evidence: "
                + ", ".join(sorted(unsupported))
            )

    @staticmethod
    def _mock_decision(input_data: JudgeInput) -> JudgeDecision:
        snapshot = input_data.snapshot
        if not input_data.bull_history or not input_data.bear_history:
            return JudgeDecision(
                decision="insufficient_evidence",
                winner="draw",
                reasoning=("Bull 或 Bear 观点缺失，无法完成独立裁决。",),
                confidence=20,
                evidence_used=(),
                summary="当前证据链不完整，Judge 暂不提供买入方向。",
                recommendation="先补齐正反双方分析，再决定是否买入。",
                confidence_basis=("双方观点不完整，因此置信度仅为 20%。",),
                risk_warning="信息不足本身就是风险。",
            )

        bull = input_data.bull_history[-1]
        bear = input_data.bear_history[-1]
        profile = input_data.user_profile
        difference = bull.confidence - bear.confidence
        cautious_profile = (
            profile.risk_level == "low"
            or (profile.loss_tolerance_pct is not None and profile.loss_tolerance_pct < 7)
            or profile.liquidity_priority == "high"
            or "保值" in profile.decision_priorities
        )
        buy_threshold = 12 if profile.risk_level == "high" else (24 if cautious_profile else 18)
        avoid_threshold = -25 if profile.risk_level == "high" else (-10 if cautious_profile else -15)
        if difference >= buy_threshold and snapshot.hybrid_prediction.change_pct > 0:
            decision, winner = "buy", "bull"
        elif difference <= avoid_threshold:
            decision, winner = "avoid", "bear"
        else:
            decision, winner = "watch", "draw"

        affordability_risk = profile.budget is not None and snapshot.current_price > profile.budget
        liquidity_mismatch = (
            profile.liquidity_priority == "high"
            and (snapshot.liquidity_score or 0) < 60
        )
        if affordability_risk:
            decision, winner = "avoid", "bear"
        elif liquidity_mismatch and decision == "buy":
            decision, winner = "watch", "draw"
        elif profile.purchase_timing == "wait" and decision == "buy":
            decision, winner = "watch", "draw"

        used: list[str] = []
        for opinion in (bull, bear):
            for argument in opinion.arguments:
                used.extend(argument.evidence_ids)
        evidence_used = tuple(dict.fromkeys(used))

        model_reliability = float(snapshot.hybrid_prediction.confidence)
        evidence_coverage = min(
            100.0,
            len(evidence_used) / max(1, len(snapshot.evidence_ids())) * 100,
        )
        agent_separation = min(100.0, abs(difference) * 3)
        prediction_supports_decision = (
            (snapshot.hybrid_prediction.change_pct > 0 and decision == "buy")
            or (snapshot.hybrid_prediction.change_pct < 0 and decision == "avoid")
            or decision == "watch"
        )
        direction_consistency = 75.0 if prediction_supports_decision else 35.0
        profile_compatibility = 100.0
        if affordability_risk:
            profile_compatibility -= 60
        if liquidity_mismatch:
            profile_compatibility -= 30
        if profile.loss_tolerance_pct is not None and profile.loss_tolerance_pct < 7:
            profile_compatibility -= 25
        if profile.horizon_days > snapshot.hybrid_prediction.horizon_days * 3:
            profile_compatibility -= 20
        profile_compatibility = max(0.0, profile_compatibility)
        confidence = round(min(85.0, max(
            25.0,
            model_reliability * 0.35
            + evidence_coverage * 0.20
            + agent_separation * 0.15
            + direction_consistency * 0.15
            + profile_compatibility * 0.15,
        )))

        previous = input_data.judge_history[-1] if input_data.judge_history else None
        changed = bool(
            previous
            and (previous.decision != decision or abs(previous.confidence - confidence) >= 1)
        )
        change_summary = (
            f"上一轮为 {previous.decision}（{previous.confidence:.0f}%），"
            f"本轮为 {decision}（{confidence:.0f}%）。"
            if previous
            else "这是初始裁决，后续将根据新证据、用户约束和双方反驳显示变化。"
        )

        decision_cn = {
            "buy": "可以考虑买入",
            "watch": "建议继续观望",
            "avoid": "当前不建议买入",
        }[decision]
        winner_cn = {
            "bull": "Bull 的上行证据更占优",
            "bear": "Bear 的风险证据更占优",
            "draw": "双方证据暂未拉开差距",
        }[winner]
        key_conflict = (
            f"关键权衡是：Hybrid 预测未来 {snapshot.hybrid_prediction.horizon_days} 天变化 "
            f"{snapshot.hybrid_prediction.change_pct:+.2f}%；Bull 提供上行触发条件，Bear 提供风险边界。"
            f"当前两侧证据强度相差 {abs(difference):.0f} 个百分点，{winner_cn}，"
            "但这不妨碍把正面信号用于入场、把风险信号用于仓位和退出。"
        )
        risk_cn = {"low": "低", "medium": "中等", "high": "高"}[profile.risk_level]
        purpose_cn = {
            "unspecified": "未说明", "use": "自用", "investment": "投资",
            "collection": "收藏", "mixed": "自用兼投资",
        }[profile.purpose]
        liquidity_cn = {"low": "低", "medium": "中等", "high": "高"}[profile.liquidity_priority]
        profile_fit = (
            f"当前按{risk_cn}风险、约 {profile.horizon_days} 天持有期、"
            f"用途为{purpose_cn}、流动性要求{liquidity_cn}进行判断。"
        )
        if profile.loss_tolerance_pct is not None:
            profile_fit += f"用户最多接受约 {profile.loss_tolerance_pct:g}% 的亏损。"
        if profile.budget is not None:
            profile_fit += f"预算为 {profile.budget:g}，当前价格{'在预算内' if not affordability_risk else '超过预算'}。"
        if profile.purchase_timing == "wait":
            profile_fit += "用户不急于立即购买。"
        if profile.decision_priorities:
            profile_fit += "重点关注" + "、".join(profile.decision_priorities) + "。"

        summary = f"Judge 综合结论：{decision_cn}。{key_conflict}"
        if affordability_risk:
            recommendation = "当前价格超过你的预算，不建议为了买入该饰品扩大预算；先换候选或等待价格进入预算。"
        elif liquidity_mismatch:
            recommendation = "当前流动性不符合你的退出要求，建议等待成交条件改善或选择更容易变现的饰品。"
        elif decision == "buy":
            recommendation = "不要追高，优先在建议区间分批进入，并让单次亏损不超过你的容忍范围。"
        elif decision == "avoid":
            recommendation = "当前风险收益比不合适；等待趋势、成交量或模型可靠性改善后再评估。"
        else:
            recommendation = "这不是否定该饰品，而是现有证据不足以支持立即买入；先等待更明确的价格与成交信号。"

        confidence_basis = (
            f"Hybrid 模型可靠度：{model_reliability:.0f}/100（占 35%）。",
            f"有效证据覆盖度：{evidence_coverage:.0f}/100（占 20%）。",
            f"Bull/Bear 分歧清晰度：{agent_separation:.0f}/100（占 15%）。",
            f"预测方向与裁决一致性：{direction_consistency:.0f}/100（占 15%）。",
            f"与你的预算、周期、用途、流动性和亏损容忍的适配度：{profile_compatibility:.0f}/100（占 15%）。",
            "该置信度表示公开证据对本裁决的支持强度，不等于价格上涨概率。",
        )
        return JudgeDecision(
            decision=decision,
            winner=winner,
            reasoning=(
                key_conflict,
                f"Bull 置信度 {bull.confidence:.0f}%，Bear 置信度 {bear.confidence:.0f}%。",
                profile_fit,
            ),
            entry_low=round(snapshot.current_price * 0.97, 2) if decision == "buy" else None,
            entry_high=round(snapshot.current_price * 0.99, 2) if decision == "buy" else None,
            target_price=snapshot.hybrid_prediction.predicted_price,
            stop_loss=round(snapshot.current_price * 0.93, 2),
            confidence=confidence,
            evidence_used=evidence_used,
            changed_from_previous=changed,
            change_summary=change_summary,
            user_view_considered=(
                f"已纳入用户意见与本轮补充：{input_data.user_message.strip()}"
                if input_data.user_message else "本轮没有新增用户约束，按当前用户画像裁决。"
            ),
            summary=summary,
            recommendation=recommendation,
            key_conflict=key_conflict,
            confidence_basis=confidence_basis,
            confidence_components={
                "modelReliability": round(model_reliability, 1),
                "evidenceCoverage": round(evidence_coverage, 1),
                "agentSeparation": round(agent_separation, 1),
                "directionConsistency": round(direction_consistency, 1),
                "profileCompatibility": round(profile_compatibility, 1),
            },
            profile_fit=profile_fit,
            conditions_to_buy=(
                "价格进入 Judge 给出的入场区间，避免追高。",
                "Hybrid 方向保持为正，且置信度或成交量较当前改善。",
                "Bear 指出的回撤与流动性风险在你的承受范围内。",
            ),
            conditions_to_wait=(
                "短期与中期趋势继续冲突，或模型置信度仍偏低。",
                "成交量下降、流动性恶化，导致退出成本上升。",
                "预期亏损超过你的止损或亏损容忍范围。",
            ),
            risk_warning="饰品价格预测存在模型误差和市场事件风险；Judge 的结论是条件式决策，不是收益保证。",
        )
