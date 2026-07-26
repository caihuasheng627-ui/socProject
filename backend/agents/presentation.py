"""User-facing explanations for market evidence and debate changes."""

from __future__ import annotations

from typing import Literal

import re

from .base import model_dump
from .localization import is_english
from .schemas import AgentArgument, Evidence, MarketSnapshot, UserProfile


def infer_risk_level(message: str, current: str = "medium") -> str:
    text = message.lower()
    high_words = ("激进", "高风险", "能承受", "愿意冒险", "aggressive", "higher risk")
    low_words = ("保守", "稳妥", "低风险", "不能亏", "conservative", "safer")
    if any(word in text for word in high_words):
        return "high"
    if any(word in text for word in low_words):
        return "low"
    return current


def infer_user_profile(message: str, current: UserProfile) -> tuple[UserProfile, list[str]]:
    """Extract explicit user constraints without treating every message as a preference."""

    text = message.strip()
    lowered = text.lower()
    data = model_dump(current)
    changes: list[str] = []

    risk = infer_risk_level(text, current.risk_level)
    if risk != current.risk_level:
        data["risk_level"] = risk
        risk_labels = (
            {"low": "Conservative", "medium": "Balanced", "high": "Aggressive"}
            if is_english(current.locale) else
            {"low": "低", "medium": "中等", "high": "高"}
        )
        changes.append(
            f"Risk preference: {risk_labels[current.risk_level]} → {risk_labels[risk]}"
            if is_english(current.locale) else
            f"风险偏好：{risk_labels[current.risk_level]} → {risk_labels[risk]}"
        )

    budget_match = re.search(
        r"(?:预算|最多|不超过|控制在|budget(?: is)?|under|up to)\s*(?:约|大概|about)?\s*[￥¥$]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if budget_match:
        budget = float(budget_match.group(1))
        if budget > 0 and budget != current.budget:
            data["budget"] = budget
            changes.append(
                f"Budget: {budget:g}" if is_english(current.locale) else f"预算：{budget:g}"
            )

    horizon_match = re.search(r"(\d+)\s*(天|周|个月|月|年)", text)
    if horizon_match and any(word in text for word in ("持有", "周期", "长期", "短期", "准备拿", "放")):
        value = int(horizon_match.group(1))
        unit = horizon_match.group(2)
        days = value * ({"天": 1, "周": 7, "个月": 30, "月": 30, "年": 365}[unit])
        days = max(1, min(365, days))
        if days != current.horizon_days:
            data["horizon_days"] = days
            changes.append(f"计划持有：约 {days} 天")
    elif is_english(current.locale):
        english_horizon = re.search(
            r"(\d+)\s*(days?|weeks?|months?|years?)", lowered
        )
        if english_horizon and any(
            word in lowered for word in ("hold", "horizon", "long term", "short term")
        ):
            value = int(english_horizon.group(1))
            unit = english_horizon.group(2)
            multiplier = (
                1 if unit.startswith("day") else
                7 if unit.startswith("week") else
                30 if unit.startswith("month") else 365
            )
            days = max(1, min(365, value * multiplier))
            if days != current.horizon_days:
                data["horizon_days"] = days
                changes.append(f"Holding horizon: about {days} days")

    purpose = current.purpose
    if any(word in lowered for word in (
        "自用", "自己用", "游戏里用", "play skin", "personal use", "use in game",
    )):
        purpose = "use"
    elif any(word in lowered for word in ("收藏", "collect")):
        purpose = "collection"
    elif any(word in lowered for word in ("投资", "升值", "赚钱", "回报", "investment")):
        purpose = "investment"
    if purpose != current.purpose:
        data["purpose"] = purpose
        purpose_labels = {"use": "自用", "investment": "投资", "collection": "收藏", "mixed": "自用兼投资", "unspecified": "未说明"}
        changes.append(
            f"Purchase purpose: {purpose}"
            if is_english(current.locale) else
            f"购买用途：{purpose_labels[purpose]}"
        )

    liquidity = current.liquidity_priority
    if any(word in lowered for word in (
        "容易卖", "好出手", "随时卖", "流动性重要", "变现",
        "easy to sell", "high liquidity", "liquidity matters",
    )):
        liquidity = "high"
    elif any(word in lowered for word in (
        "不急着卖", "流动性不重要", "难卖也可以",
        "liquidity is not important", "do not need to sell quickly",
    )):
        liquidity = "low"
    if liquidity != current.liquidity_priority:
        data["liquidity_priority"] = liquidity
        liquidity_labels = {"low": "低", "medium": "中等", "high": "高"}
        changes.append(
            f"Liquidity priority: {liquidity}"
            if is_english(current.locale) else
            f"流动性要求：{liquidity_labels[liquidity]}"
        )

    loss_match = re.search(
        r"(?:(?:最多|可以|能|接受|承受).*?(?:亏损?|回撤|止损)|"
        r"(?:accept|tolerate|risk)(?: a)?(?: maximum)?(?: loss of)?)\s*(\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )
    if loss_match:
        tolerance = float(loss_match.group(1))
        if tolerance != current.loss_tolerance_pct:
            data["loss_tolerance_pct"] = tolerance
            changes.append(
                f"Maximum acceptable loss: {tolerance:g}%"
                if is_english(current.locale) else
                f"可接受亏损：{tolerance:g}%"
            )

    timing = current.purchase_timing
    if any(word in lowered for word in (
        "现在就买", "马上买", "今天买", "急着买",
        "buy now", "buy today", "need it now",
    )):
        timing = "now"
    elif any(word in lowered for word in (
        "可以等", "不着急", "等等看", "等回调",
        "can wait", "not in a hurry", "wait for a pullback",
    )):
        timing = "wait"
    if timing != current.purchase_timing:
        data["purchase_timing"] = timing
        timing_labels = {"flexible": "灵活", "now": "现在", "wait": "愿意等待"}
        changes.append(
            f"Purchase timing: {timing}"
            if is_english(current.locale) else
            f"购买时机：{timing_labels[timing]}"
        )

    priorities = list(current.decision_priorities)
    priority_map = ({
        "Capital preservation": ("capital safety", "preserve value"),
        "Return": ("return", "profit", "appreciation"),
        "Liquidity": ("liquidity", "easy to sell", "cash out"),
        "Appearance": ("appearance", "looks", "personal use"),
        "Short-term entry": ("entry point", "buy the dip", "short term"),
    } if is_english(current.locale) else {
        "保值": ("保值", "本金安全"),
        "收益": ("收益", "升值", "回报"),
        "流动性": ("流动性", "好出手", "变现"),
        "外观喜好": ("喜欢外观", "颜值", "自用"),
        "短期入场": ("入场点", "抄底", "短期"),
    })
    for label, words in priority_map.items():
        if any(word in text for word in words) and label not in priorities:
            priorities.append(label)
            changes.append(
                f"Priority: {label}"
                if is_english(current.locale) else f"关注重点：{label}"
            )
    data["decision_priorities"] = priorities[-8:]
    return UserProfile(**data), changes


def evidence_explanation(
    evidence: Evidence, role: Literal["bull", "bear"], locale: str = "zh-CN"
) -> tuple[str, str]:
    evidence_id = evidence.evidence_id
    if is_english(locale):
        explanations = {
            "model:hybrid_7d": (
                "Hybrid combines multiple price models to estimate the next 7-day direction; low confidence should not be used alone as a buying signal.",
                "A positive forecast indicates upside potential, but volume, volatility and drawdown must confirm it.",
            ),
            "market:change_7d": (
                "The 7-day change measures short-term momentum; positive means recent buying pressure, while negative means near-term weakness.",
                "Short-term momentum helps time an entry but does not prove the trend will continue.",
            ),
            "market:change_30d": (
                "The 30-day change provides a medium-term view and helps distinguish a sustained move from a brief rebound.",
                "Opposite 7-day and 30-day directions reduce confidence and argue against chasing.",
            ),
            "risk:volatility_30d": (
                "30-day volatility measures day-to-day price swings; higher values imply larger profit-and-loss variation after entry.",
                "High volatility calls for a smaller position, a clear stop and more observation time.",
            ),
            "risk:max_drawdown_30d": (
                "Maximum drawdown is the worst peak-to-trough loss over the last 30 days.",
                "It estimates the floating loss a holder may face and directly informs position size and stops.",
            ),
            "market:liquidity": (
                "Liquidity indicates how easily the skin can trade; low liquidity may require a discount or a longer wait to sell.",
                "Low liquidity raises exit cost, while high liquidity makes stops and profit-taking more executable.",
            ),
            "market:volume_change_7d": (
                "Volume change measures participation; price and volume moving together is usually more reliable than a low-volume rise.",
                "Falling volume weakens an upside case, while rising volume strengthens trend confirmation.",
            ),
        }
        meaning, impact = explanations.get(evidence_id, (
            "This is public evidence from the market snapshot or knowledge base and must be read with its source and timestamp.",
            "It changes the strength of an argument but cannot override budget, risk preference or opposing evidence.",
        ))
        if role == "bear" and evidence.direction == "negative":
            impact = "Bear treats this as a reason to delay, reduce position size or set a stop. " + impact
        return meaning, impact
    if evidence_id == "model:hybrid_7d":
        meaning = "Hybrid 会综合多个价格模型估计未来 7 天方向；置信度越低，越不适合单独作为买入依据。"
        impact = "上涨预测代表潜在收益空间，但仍需由成交量、波动和回撤交叉验证。"
    elif evidence_id == "market:change_7d":
        meaning = "7 日涨跌反映最近一周的短期动量，正值代表近期买盘占优，负值代表短期承压。"
        impact = "短期动量可帮助判断入场节奏，但不能证明趋势会持续。"
    elif evidence_id == "market:change_30d":
        meaning = "30 日涨跌用于观察中期方向，可识别最近一周的变化是否只是短暂反弹。"
        impact = "若 7 日与 30 日方向相反，应降低结论置信度并避免追涨杀跌。"
    elif evidence_id == "risk:volatility_30d":
        meaning = "30 日波动率衡量价格每天上下摆动的幅度；数值越高，买入后的盈亏变化越剧烈。"
        impact = "高波动意味着需要更小仓位、明确止损，并预留更长观察时间。"
    elif evidence_id == "risk:max_drawdown_30d":
        meaning = "最大回撤表示过去 30 天从阶段高点买入后，最差经历的峰值到谷底跌幅。"
        impact = "它反映持有中可能承受的浮亏，是判断仓位和止损的重要依据。"
    elif evidence_id == "market:liquidity":
        meaning = "流动性评分反映是否容易成交；评分低时，卖出可能需要降价等待买家。"
        impact = "低流动性会放大退出成本，高流动性则提高止盈止损的可执行性。"
    elif evidence_id == "market:volume_change_7d":
        meaning = "成交量变化反映市场参与度；量价同向通常更可靠，上涨但缩量可能缺乏持续买盘。"
        impact = "成交量下降会削弱上涨论据，成交量上升则增强趋势可信度。"
    else:
        meaning = "这是来自市场快照或资讯库的公开证据，需要结合来源和时间理解。"
        impact = "该证据只影响观点强弱，不能绕过预算、风险偏好和相反证据。"
    if role == "bear" and evidence.direction == "negative":
        impact = "Bear 将其视为推迟买入、降低仓位或设置止损的理由。" + impact
    return meaning, impact


def _localized_claim(evidence: Evidence, locale: str) -> str:
    if not is_english(locale):
        return evidence.content
    replacements = {
        "当前价格": "Current price",
        "预测价格": "Predicted price",
        "变化": "change",
        "置信度": "confidence",
        "近7日涨跌": "7-day change",
        "近30日涨跌": "30-day change",
        "30日波动率": "30-day volatility",
        "30日最大回撤": "30-day maximum drawdown",
        "流动性评分": "Liquidity score",
        "近7日成交量变化": "7-day volume change",
    }
    result = evidence.content
    for source, target in replacements.items():
        result = result.replace(source, target)
    if re.search(r"[\u3400-\u9fff]", result):
        timestamp = evidence.timestamp or "time unavailable"
        return (
            f"Public {evidence.direction} signal from {evidence.source} "
            f"({timestamp}; evidence {evidence.evidence_id})"
        )
    return result


def argument_from_evidence(
    evidence: Evidence, role: Literal["bull", "bear"], locale: str = "zh-CN"
) -> AgentArgument:
    meaning, impact = evidence_explanation(evidence, role, locale)
    return AgentArgument(
        claim=_localized_claim(evidence, locale),
        evidence_ids=(evidence.evidence_id,),
        importance=0.72,
        explanation=meaning,
        decision_impact=impact,
    )


def contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value or ""))


def normalize_argument_locale(
    argument: AgentArgument,
    evidence_by_id: dict[str, Evidence],
    role: Literal["bull", "bear"],
    locale: str,
) -> AgentArgument:
    """Keep valid LLM prose, replacing only fields that violate the UI locale."""

    if not is_english(locale):
        return argument
    evidence = next(
        (
            evidence_by_id[evidence_id]
            for evidence_id in argument.evidence_ids
            if evidence_id in evidence_by_id
        ),
        None,
    )
    canonical = (
        argument_from_evidence(evidence, role, locale)
        if evidence is not None else
        AgentArgument(
            claim="User constraint or debate context",
            evidence_ids=argument.evidence_ids,
            importance=argument.importance,
            explanation=(
                "This is debate context or a user preference, not independent market evidence."
            ),
            decision_impact=(
                "Judge may use it to adjust strategy thresholds, position size, and exits, "
                "but it does not change market-evidence confidence."
            ),
        )
    )
    return AgentArgument(
        claim=canonical.claim if contains_cjk(argument.claim) else argument.claim,
        evidence_ids=argument.evidence_ids,
        importance=argument.importance,
        explanation=(
            canonical.explanation
            if contains_cjk(argument.explanation) else argument.explanation
        ),
        decision_impact=(
            canonical.decision_impact
            if contains_cjk(argument.decision_impact) else argument.decision_impact
        ),
    )


def metric_explanations(snapshot: MarketSnapshot, locale: str = "zh-CN") -> list[dict]:
    evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
    rows = []
    for evidence_id in (
        "model:hybrid_7d", "market:change_7d", "market:change_30d",
        "risk:volatility_30d", "risk:max_drawdown_30d",
        "market:liquidity", "market:volume_change_7d",
    ):
        evidence = evidence_by_id.get(evidence_id)
        if not evidence:
            continue
        meaning, impact = evidence_explanation(evidence, "bull", locale)
        rows.append({
            "evidenceId": evidence.evidence_id,
            "label": _localized_claim(
                Evidence(
                    evidence_id=evidence.evidence_id,
                    source=evidence.source,
                    title=evidence.title,
                    content=evidence.title,
                    direction=evidence.direction,
                    timestamp=evidence.timestamp,
                ),
                locale,
            ),
            "value": _localized_claim(evidence, locale),
            "meaning": meaning,
            "decisionImpact": impact,
            "direction": evidence.direction,
            "source": evidence.source,
        })
    return rows
