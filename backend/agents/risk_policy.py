"""Transparent policy layer that converts market evidence into user actions.

Bull and Bear estimate evidence strength.  This module is deliberately
deterministic: the user's risk preference changes action thresholds and
position sizing, never the underlying market-evidence score.
"""

from __future__ import annotations

from dataclasses import dataclass

from .localization import is_english, risk_label
from .schemas import MarketSnapshot, UserProfile


StrategyAction = str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _linear(value: float | None, low: float, high: float, weight: float) -> float:
    if value is None or high <= low:
        return weight / 2
    return _clamp((value - low) / (high - low), 0.0, 1.0) * weight


@dataclass(frozen=True)
class RiskPolicyResult:
    opportunity_score: float
    risk_score: float
    decision_score: float
    action: StrategyAction
    position_size_pct: float
    stop_loss: float
    recheck_after_days: int
    threshold_summary: str
    explanation: tuple[str, ...]
    entry_strategy: tuple[str, ...]
    buy_triggers: tuple[str, ...]
    exit_triggers: tuple[str, ...]


POLICY = {
    "low": {
        "buy": 78.0, "scale": 62.0, "wait": 38.0,
        "max_risk_buy": 35.0, "max_risk_scale": 55.0,
        "buy_position": 15.0, "scale_position": 7.0, "default_stop": 4.0,
    },
    "medium": {
        "buy": 72.0, "scale": 52.0, "wait": 30.0,
        "max_risk_buy": 45.0, "max_risk_scale": 70.0,
        "buy_position": 25.0, "scale_position": 15.0, "default_stop": 7.0,
    },
    "high": {
        "buy": 65.0, "scale": 38.0, "wait": 20.0,
        "max_risk_buy": 60.0, "max_risk_scale": 85.0,
        "buy_position": 35.0, "scale_position": 22.0, "default_stop": 10.0,
    },
}

def _round_price(value: float, current_price: float) -> float:
    """Keep penny-skin stop prices meaningful instead of rounding to no change."""

    digits = 4 if current_price < 1 else 3 if current_price < 10 else 2
    return round(max(0, value), digits)


def score_market(
    snapshot: MarketSnapshot, locale: str = "zh-CN"
) -> tuple[float, float, float, tuple[str, ...]]:
    """Return objective opportunity/risk/decision scores for one snapshot."""

    prediction = snapshot.hybrid_prediction
    positive = sum(item.direction == "positive" for item in snapshot.evidence)
    negative = sum(item.direction == "negative" for item in snapshot.evidence)
    directional = max(1, positive + negative)

    momentum_7 = _linear(snapshot.change_7d, -15, 15, 25)
    momentum_30 = _linear(snapshot.change_30d, -30, 30, 15)
    hybrid_direction = _linear(prediction.change_pct, -15, 15, 20)
    reliability = 0.40 + 0.60 * prediction.confidence / 100
    if prediction.degraded:
        reliability = min(reliability, 0.55)
    hybrid_score = hybrid_direction * reliability
    liquidity = (
        _clamp(snapshot.liquidity_score or 0) / 100 * 15
        if snapshot.liquidity_score is not None else 7.5
    )
    evidence_balance = positive / directional * 15
    trend_alignment = 5.0 if (
        snapshot.change_7d is not None
        and snapshot.change_30d is not None
        and snapshot.change_7d > 0
        and snapshot.change_30d > 0
    ) else 0.0
    opportunity = _clamp(
        momentum_7 + momentum_30 + hybrid_score
        + liquidity + evidence_balance + trend_alignment
    )

    drawdown = min((snapshot.max_drawdown_30d or 0) / 20, 1) * 30
    volatility = min((snapshot.volatility_30d or 0) / 10, 1) * 15
    negative_30 = min(max(-(snapshot.change_30d or 0), 0) / 30, 1) * 20
    negative_7 = min(max(-(snapshot.change_7d or 0), 0) / 15, 1) * 10
    low_liquidity = (
        (100 - _clamp(snapshot.liquidity_score or 0)) / 100 * 15
        if snapshot.liquidity_score is not None else 7.5
    )
    uncertainty = min((100 - prediction.confidence) / 100 * 10 + (4 if prediction.degraded else 0), 10)
    risk = _clamp(
        drawdown + volatility + negative_30 + negative_7
        + low_liquidity + uncertainty
    )
    decision = _clamp(opportunity - 0.45 * risk + 20)
    components = (
        (
            f"Opportunity {opportunity:.0f}/100: 7/30-day momentum, Hybrid direction, "
            "liquidity and positive evidence."
        ),
        (
            f"Risk {risk:.0f}/100: drawdown, volatility, negative momentum, "
            "low liquidity and model uncertainty."
        ),
        f"Decision {decision:.0f}/100 = opportunity - 0.45 × risk + 20.",
    ) if is_english(locale) else (
        f"机会分 {opportunity:.0f}/100：由 7 日/30 日动量、Hybrid 方向、流动性和正面证据合成。",
        f"风险分 {risk:.0f}/100：由回撤、波动、负动量、流动性不足和模型不确定性合成。",
        f"决策分 {decision:.0f}/100 = 机会分 - 0.45 × 风险分 + 20。",
    )
    return round(opportunity, 1), round(risk, 1), round(decision, 1), components


def select_action(
    decision_score: float,
    risk_score: float,
    snapshot: MarketSnapshot,
    profile: UserProfile,
) -> tuple[StrategyAction, str]:
    """Apply risk-specific thresholds and hard user constraints."""

    rule = POLICY[profile.risk_level]
    label = risk_label(profile.risk_level, profile.locale)
    english = is_english(profile.locale)
    if profile.budget is not None and snapshot.current_price > profile.budget:
        return "avoid", (
            f"Current price exceeds the {profile.budget:g} budget; budget is a hard constraint."
            if english else f"当前价格超过预算 {profile.budget:g}，预算是硬约束。"
        )
    if (
        profile.liquidity_priority == "high"
        and (snapshot.liquidity_score is None or snapshot.liquidity_score < 60)
    ):
        return "avoid", (
            "You require high liquidity, but the current liquidity score is below 60."
            if english else "你要求高流动性，但当前流动性评分不足 60。"
        )

    buy_ready = (
        decision_score >= rule["buy"]
        and risk_score <= rule["max_risk_buy"]
        and snapshot.hybrid_prediction.confidence >= 50
        and not snapshot.hybrid_prediction.degraded
    )
    if buy_ready:
        return "buy_now", (
            f"The decision score meets the {label} buy-now threshold of {rule['buy']:.0f}, "
            f"and risk is no higher than {rule['max_risk_buy']:.0f}."
            if english else
            f"决策分达到{label}档立即买入门槛 {rule['buy']:.0f}，且风险分不高于 {rule['max_risk_buy']:.0f}。"
        )
    if decision_score >= rule["scale"] and risk_score <= rule["max_risk_scale"]:
        return "scale_in", (
            f"The decision score meets the {label} scale-in threshold of {rule['scale']:.0f}, "
            "but not every buy-now condition is satisfied."
            if english else
            f"决策分达到{label}档试仓门槛 {rule['scale']:.0f}，但尚未同时满足立即买入条件。"
        )
    if decision_score >= rule["wait"]:
        return "wait_for_trigger", (
            f"The decision score is below the {label} scale-in threshold of {rule['scale']:.0f}; "
            f"wait for now. The watch threshold is {rule['wait']:.0f}."
            if english else
            f"决策分未达到{label}档试仓门槛 {rule['scale']:.0f}，当前先等待；最低观察门槛为 {rule['wait']:.0f}。"
        )
    return "avoid", (
        f"The decision score is below the {label} minimum watch threshold of {rule['wait']:.0f}."
        if english else f"决策分低于{label}档最低观察门槛 {rule['wait']:.0f}。"
    )


def apply_risk_policy(snapshot: MarketSnapshot, profile: UserProfile) -> RiskPolicyResult:
    opportunity, risk, decision, components = score_market(snapshot, profile.locale)
    action, threshold = select_action(decision, risk, snapshot, profile)
    rule = POLICY[profile.risk_level]
    position = (
        rule["buy_position"] if action == "buy_now"
        else rule["scale_position"] if action == "scale_in"
        else 0.0
    )
    stop_pct = profile.loss_tolerance_pct or rule["default_stop"]
    stop_pct = max(2.0, min(12.0, stop_pct))
    stop_loss = _round_price(
        snapshot.current_price * (1 - stop_pct / 100), snapshot.current_price
    )
    recheck = max(3, min(14, profile.horizon_days))

    english = is_english(profile.locale)
    if action == "buy_now" and english:
        entry = (
            "Deploy 50% of the planned position first to avoid chasing.",
            "Add the remaining 50% only while trend and liquidity conditions remain valid.",
        )
    elif action == "scale_in" and english:
        entry = (
            "Deploy only one third of the planned position initially.",
            "Add the second and third tranches after price and volume confirmation.",
            "Stop adding when any exit condition is triggered.",
        )
    elif action == "wait_for_trigger" and english:
        entry = ("Do not place an order yet; keep it on the watchlist and review the triggers.",)
    elif english:
        entry = ("Do not open a position; compare alternatives that better meet the hard constraints.",)
    elif action == "buy_now":
        entry = (
            "先投入计划仓位的 50%，避免一次性追价。",
            "剩余 50% 仅在趋势和流动性条件继续成立时补入。",
        )
    elif action == "scale_in":
        entry = (
            "首次只投入计划仓位的三分之一。",
            "趋势和成交信号确认后再投入第二、第三批。",
            "任一退出条件触发时停止补仓。",
        )
    elif action == "wait_for_trigger":
        entry = ("当前不下单，保留在观察清单中并按触发条件复查。",)
    else:
        entry = ("当前不建立仓位，优先比较更符合硬约束的候选。",)

    buy_triggers = (
        f"Decision score reaches this risk profile's scale-in threshold of {rule['scale']:.0f}.",
        "Hybrid remains positive and its reliability does not deteriorate.",
        "Short-term momentum improves while liquidity remains sufficient for an exit.",
    ) if english else (
        f"决策分达到当前风险档的试仓门槛 {rule['scale']:.0f}。",
        "Hybrid 保持正向，且可靠度没有继续下降。",
        "短期动量改善，同时流动性满足退出需要。",
    )
    exit_triggers = (
        f"Price falls to ${stop_loss:.4f}, triggering the roughly {stop_pct:g}% risk limit.",
        "Hybrid turns negative while short-term momentum also weakens.",
        "Liquidity deteriorates and expected exit cost exceeds the acceptable range.",
    ) if english else (
        f"价格跌至 ${stop_loss:.4f}，触发约 {stop_pct:g}% 的风险退出线。",
        "Hybrid 转负且短期价格动量同步转弱。",
        "流动性恶化，预期退出成本超过可接受范围。",
    )
    return RiskPolicyResult(
        opportunity_score=opportunity,
        risk_score=risk,
        decision_score=decision,
        action=action,
        position_size_pct=position,
        stop_loss=stop_loss,
        recheck_after_days=recheck,
        threshold_summary=threshold,
        explanation=(*components, threshold),
        entry_strategy=entry,
        buy_triggers=buy_triggers,
        exit_triggers=exit_triggers,
    )
