"""Grounded conversation helpers for an active debate session."""

from __future__ import annotations

from typing import Literal

from .localization import is_english, risk_label
from .schemas import BearOpinion, BullOpinion, JudgeDecision, MarketSnapshot, UserProfile


SessionInputKind = Literal["question", "preference", "rerun"]

RERUN_WORDS = (
    "再辩", "重新辩", "再讨论", "重新分析", "让双方", "让bull", "让 bear",
    "重新裁决", "再来一轮", "rerun", "debate again",
)
PREFERENCE_WORDS = (
    "我愿意", "我更", "我主要", "我的预算", "我预算", "我准备持有", "我打算持有",
    "我最多", "我可以接受", "我不能接受", "我不着急", "我急着", "我想自用",
    "我想投资", "我想收藏", "我希望", "希望更", "想更激进", "想更保守",
    "对我来说", "优先考虑",
    "i prefer", "i want", "my budget", "my priority", "more aggressive",
    "more conservative", "i can accept", "i can tolerate",
)
OPINION_WORDS = (
    "我觉得", "我认为", "我倾向", "我的判断", "我担心", "我不同意", "我赞同",
    "看多", "看空", "应该买", "不该买",
    "i think", "i believe", "i am bullish", "i am bearish",
)
QUESTION_WORDS = (
    "什么", "为何", "为什么", "怎么", "如何", "依据", "怎么算", "是否", "建议",
    "买入还是", "观望", "能不能", "可以吗", "吗", "呢", "解释", "区别", "多少",
    "what", "why", "how", "should", "explain", "mean",
)


def classify_session_input(message: str) -> SessionInputKind:
    """Separate questions from profile updates and explicit requests to rerun."""

    text = message.strip().lower()
    if any(word in text for word in RERUN_WORDS):
        return "rerun"
    if any(word in text for word in OPINION_WORDS):
        return "rerun"
    if any(word in text for word in PREFERENCE_WORDS):
        return "preference"
    if "?" in text or "？" in text or any(word in text for word in QUESTION_WORDS):
        return "question"
    return "question"


def profile_summary(profile: UserProfile) -> str:
    if is_english(profile.locale):
        parts = [
            f"{risk_label(profile.risk_level, profile.locale)} risk",
            f"about {profile.horizon_days} days",
            f"purpose: {profile.purpose}",
            f"liquidity priority: {profile.liquidity_priority}",
        ]
        if profile.budget is not None:
            parts.append(f"budget: {profile.budget:g}")
        if profile.loss_tolerance_pct is not None:
            parts.append(f"maximum acceptable loss: {profile.loss_tolerance_pct:g}%")
        if profile.purchase_timing != "flexible":
            parts.append(f"purchase timing: {profile.purchase_timing}")
        if profile.decision_priorities:
            parts.append("priorities: " + ", ".join(profile.decision_priorities))
        return "; ".join(parts)
    risk_labels = {"low": "低", "medium": "中等", "high": "高"}
    purpose_labels = {
        "unspecified": "未说明", "use": "自用", "investment": "投资",
        "collection": "收藏", "mixed": "自用兼投资",
    }
    liquidity_labels = {"low": "低", "medium": "中等", "high": "高"}
    timing_labels = {"now": "现在", "wait": "等待", "flexible": "灵活"}
    parts = [
        f"风险偏好 {risk_labels[profile.risk_level]}",
        f"持有期约 {profile.horizon_days} 天",
        f"用途 {purpose_labels[profile.purpose]}",
        f"流动性要求 {liquidity_labels[profile.liquidity_priority]}",
    ]
    if profile.budget is not None:
        parts.append(f"预算 {profile.budget:g}")
    if profile.loss_tolerance_pct is not None:
        parts.append(f"可接受亏损 {profile.loss_tolerance_pct:g}%")
    if profile.purchase_timing != "flexible":
        parts.append(f"购买时机 {timing_labels[profile.purchase_timing]}")
    if profile.decision_priorities:
        parts.append("关注 " + "、".join(profile.decision_priorities))
    return "；".join(parts)


def answer_debate_question(
    message: str,
    *,
    judge: JudgeDecision,
    snapshot: MarketSnapshot,
    profile: UserProfile,
    bull: BullOpinion | None = None,
    bear: BearOpinion | None = None,
    profile_changes: list[str] | None = None,
) -> str:
    """Answer common follow-ups from public debate outputs without inventing facts."""

    text = message.lower()
    if is_english(profile.locale):
        parts: list[str] = []
        if profile_changes:
            parts.append("I recorded these explicit conditions: " + "; ".join(profile_changes) + ".")
        if any(word in text for word in ("confidence", "basis", "why", "evidence")):
            parts.append(judge.summary or f"The current Judge decision is {judge.decision}.")
            role_values = [
                f"Hybrid {snapshot.hybrid_prediction.confidence:.0f}%: reliability of the price model",
                *([f"Bull {bull.confidence:.0f}%: support strength of upside evidence"] if bull else []),
                *([f"Bear {bear.confidence:.0f}%: support strength of risk evidence"] if bear else []),
                f"Judge {judge.confidence:.0f}%: evidence support for the final ruling",
            ]
            parts.append(
                "These confidence values measure different things and none is a direct probability of a price rise:\n- "
                + "\n- ".join(role_values)
            )
            if judge.confidence_basis:
                parts.append("Judge confidence basis:\n- " + "\n- ".join(judge.confidence_basis))
            parts.append("Therefore, " + (judge.recommendation or "follow the conditional entry rules."))
        elif any(word in text for word in ("buy", "wait", "watch", "recommend", "should")):
            parts.append(judge.summary or f"The current Judge decision is {judge.decision}.")
            parts.append(judge.recommendation or "Follow the current conditional strategy.")
            if judge.conditions_to_buy:
                parts.append("Conditions required to buy:\n- " + "\n- ".join(judge.conditions_to_buy))
            if judge.conditions_to_wait:
                parts.append("Conditions to keep waiting or exit:\n- " + "\n- ".join(judge.conditions_to_wait))
        elif any(word in text for word in ("bull", "bear", "conflict", "disagree")):
            parts.append(judge.key_conflict or "The roles evaluate upside and downside from different evidence policies.")
            parts.append(judge.recommendation or "Judge combines entry triggers with risk limits.")
        elif any(word in text for word in ("liquidity", "volume")):
            value = snapshot.liquidity_score if snapshot.liquidity_score is not None else "unavailable"
            parts.append(
                f"Current liquidity score is {value}. Liquidity estimates how easily the skin can trade near market price; "
                "low liquidity increases waiting time and discount risk."
            )
            parts.append(
                f"Your liquidity priority is {profile.liquidity_priority}. "
                + (judge.recommendation or "")
            )
        else:
            parts.append(judge.summary or f"The current Judge decision is {judge.decision}.")
            parts.append(judge.key_conflict or "Judge combined the public evidence from both agents.")
            parts.append(judge.recommendation or "Add a budget, horizon or purpose for a more personal explanation.")
        parts.append("Current user conditions: " + profile_summary(profile) + ".")
        return "\n\n".join(part for part in parts if part)

    parts: list[str] = []
    if profile_changes:
        parts.append("我已记录你这次明确给出的条件：" + "；".join(profile_changes) + "。")

    if any(word in text for word in ("置信度", "可信", "依据", "怎么算", "为什么")):
        parts.append(judge.summary or f"当前 Judge 结论为 {judge.decision}。")
        role_values = [
            f"Hybrid {snapshot.hybrid_prediction.confidence:.0f}%：价格模型本身的可靠度",
            *([f"Bull {bull.confidence:.0f}%：上行证据对 Bull 观点的支持强度"] if bull else []),
            *([f"Bear {bear.confidence:.0f}%：风险证据对 Bear 观点的支持强度"] if bear else []),
            f"Judge {judge.confidence:.0f}%：综合证据对最终裁决的支持强度",
        ]
        parts.append(
            "页面上的置信度不是同一个概念，也都不等于“上涨概率”：\n- "
            + "\n- ".join(role_values)
        )
        if judge.confidence_basis:
            parts.append("Judge 本轮合成依据：\n- " + "\n- ".join(judge.confidence_basis))
        parts.append("因此，" + (judge.recommendation or "应结合入场条件继续观察。"))
    elif any(word in text for word in ("买入", "观望", "建议", "能不能买", "可以买吗")):
        parts.append(judge.summary or f"当前 Judge 结论为 {judge.decision}。")
        parts.append(judge.recommendation or "请按当前条件式结论执行。")
        if judge.conditions_to_buy:
            parts.append("转为买入需要看到：\n- " + "\n- ".join(judge.conditions_to_buy))
        if judge.conditions_to_wait:
            parts.append("继续观望的情况：\n- " + "\n- ".join(judge.conditions_to_wait))
    elif "bull" in text or "bear" in text or "分歧" in text or "矛盾" in text:
        parts.append(judge.key_conflict or "双方主要围绕上涨空间与下行风险发生分歧。")
        parts.append(judge.recommendation or "Judge 会根据证据强度和你的约束作条件式裁决。")
    elif "流动性" in text or "成交" in text:
        parts.append(
            f"当前流动性评分为 {snapshot.liquidity_score if snapshot.liquidity_score is not None else '暂无'}。"
            "流动性反映是否容易按接近市场价成交；低流动性会增加等待时间和降价退出成本。"
        )
        parts.append("你的当前流动性要求是 " + profile.liquidity_priority + "。" + (judge.recommendation or ""))
    else:
        parts.append(judge.summary or f"当前 Judge 结论为 {judge.decision}。")
        parts.append(judge.key_conflict or "Judge 已综合双方公开证据。")
        parts.append(judge.recommendation or "如果你补充预算、持有期或用途，我可以进一步解释结论是否适合你。")

    parts.append("当前采用的用户条件：" + profile_summary(profile) + "。")
    return "\n\n".join(part for part in parts if part)
