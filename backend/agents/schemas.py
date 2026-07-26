"""Structured contracts shared by the debate agents.

Agents exchange these validated objects instead of loosely formatted prose.
This makes evidence traceable and lets the Judge reject unsupported claims.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    """Base for source facts that agents may read but must not mutate."""

    model_config = ConfigDict(frozen=True)


class UserProfile(BaseModel):
    """User constraints that affect a recommendation or final decision."""

    budget: float | None = Field(default=None, gt=0)
    horizon_days: int = Field(default=7, ge=1, le=365)
    risk_level: Literal["low", "medium", "high"] = "medium"
    preferred_categories: list[str] = Field(default_factory=list)
    purpose: Literal["unspecified", "use", "investment", "collection", "mixed"] = "unspecified"
    liquidity_priority: Literal["low", "medium", "high"] = "medium"
    loss_tolerance_pct: float | None = Field(default=None, ge=0, le=100)
    purchase_timing: Literal["flexible", "now", "wait"] = "flexible"
    decision_priorities: list[str] = Field(default_factory=list)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


class HybridPrediction(FrozenModel):
    """Normalized output from the existing Hybrid prediction service."""

    model: str
    predicted_price: float = Field(ge=0)
    change_pct: float
    confidence: float = Field(ge=0, le=100)
    horizon_days: int = Field(default=7, ge=1, le=365)
    decision_date: str | None = None
    degraded: bool = False


class Evidence(FrozenModel):
    """A stable, citable fact available to all debate participants."""

    evidence_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    direction: Literal["positive", "negative", "neutral"] = "neutral"
    timestamp: str | None = None


class MarketSnapshot(FrozenModel):
    """Immutable market state used for one debate run."""

    skin_id: str = Field(min_length=1)
    skin_name: str = Field(min_length=1)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    current_price: float = Field(ge=0)
    change_7d: float | None = None
    change_30d: float | None = None
    volatility_30d: float | None = Field(default=None, ge=0)
    max_drawdown_30d: float | None = Field(default=None, ge=0)
    liquidity_score: float | None = Field(default=None, ge=0, le=100)
    hybrid_prediction: HybridPrediction
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)

    def evidence_ids(self) -> set[str]:
        return {item.evidence_id for item in self.evidence}


class AgentArgument(FrozenModel):
    """A public argument whose supporting evidence can be verified."""

    claim: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    importance: float = Field(default=0.5, ge=0, le=1)
    explanation: str = ""
    decision_impact: str = ""


class BullOpinion(FrozenModel):
    position: Literal["buy", "watch"]
    arguments: tuple[AgentArgument, ...] = Field(default_factory=tuple)
    target_price: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=100)
    assumptions: tuple[str, ...] = Field(default_factory=tuple)


class BearOpinion(FrozenModel):
    position: Literal["avoid", "watch"]
    arguments: tuple[AgentArgument, ...] = Field(default_factory=tuple)
    stop_loss: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=100)
    risks: tuple[str, ...] = Field(default_factory=tuple)


class JudgeDecision(FrozenModel):
    decision: Literal["buy", "watch", "avoid", "insufficient_evidence"]
    winner: Literal["bull", "bear", "draw"]
    reasoning: tuple[str, ...] = Field(default_factory=tuple)
    entry_low: float | None = Field(default=None, ge=0)
    entry_high: float | None = Field(default=None, ge=0)
    target_price: float | None = Field(default=None, ge=0)
    stop_loss: float | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=100)
    evidence_used: tuple[str, ...] = Field(default_factory=tuple)
    changed_from_previous: bool = False
    change_summary: str = ""
    user_view_considered: str = ""
    summary: str = ""
    recommendation: str = ""
    key_conflict: str = ""
    confidence_basis: tuple[str, ...] = Field(default_factory=tuple)
    confidence_components: dict[str, float] = Field(default_factory=dict)
    profile_fit: str = ""
    conditions_to_buy: tuple[str, ...] = Field(default_factory=tuple)
    conditions_to_wait: tuple[str, ...] = Field(default_factory=tuple)
    risk_warning: str = ""
    strategy_action: Literal[
        "buy_now", "scale_in", "wait_for_trigger", "avoid", "insufficient"
    ] = "insufficient"
    agreed_facts: tuple[str, ...] = Field(default_factory=tuple)
    complementary_views: tuple[str, ...] = Field(default_factory=tuple)
    true_conflicts: tuple[str, ...] = Field(default_factory=tuple)
    evidence_verdicts: tuple[str, ...] = Field(default_factory=tuple)
    position_size_pct: float | None = Field(default=None, ge=0, le=100)
    entry_strategy: tuple[str, ...] = Field(default_factory=tuple)
    buy_triggers: tuple[str, ...] = Field(default_factory=tuple)
    exit_triggers: tuple[str, ...] = Field(default_factory=tuple)
    recheck_after_days: int | None = Field(default=None, ge=1, le=90)
    alternative_action: str = ""
    opportunity_score: float = Field(default=0, ge=0, le=100)
    risk_score: float = Field(default=0, ge=0, le=100)
    decision_score: float = Field(default=0, ge=0, le=100)
    policy_risk_level: Literal["low", "medium", "high"] = "medium"
    policy_threshold: str = ""
    policy_explanation: tuple[str, ...] = Field(default_factory=tuple)

    def unsupported_evidence(self, snapshot: MarketSnapshot) -> set[str]:
        """Return evidence references absent from the source snapshot."""

        return set(self.evidence_used) - snapshot.evidence_ids()


class BullInput(BaseModel):
    snapshot: MarketSnapshot
    user_profile: UserProfile = Field(default_factory=UserProfile)
    round_no: int = Field(default=1, ge=1, le=10)
    bear_opinion: BearOpinion | None = None
    bull_history: tuple[BullOpinion, ...] = Field(default_factory=tuple)
    user_message: str | None = None


class BearInput(BaseModel):
    snapshot: MarketSnapshot
    user_profile: UserProfile = Field(default_factory=UserProfile)
    round_no: int = Field(default=1, ge=1, le=10)
    bull_opinion: BullOpinion | None = None
    bear_history: tuple[BearOpinion, ...] = Field(default_factory=tuple)
    user_message: str | None = None


class JudgeInput(BaseModel):
    snapshot: MarketSnapshot
    user_profile: UserProfile = Field(default_factory=UserProfile)
    bull_history: list[BullOpinion] = Field(default_factory=list)
    bear_history: list[BearOpinion] = Field(default_factory=list)
    judge_history: list[JudgeDecision] = Field(default_factory=list)
    user_message: str | None = None


class DebateRoundOutcome(FrozenModel):
    round_no: int = Field(ge=1)
    bull: BullOpinion
    bear: BearOpinion


class DebateOutcome(FrozenModel):
    snapshot: MarketSnapshot
    rounds: tuple[DebateRoundOutcome, ...]
    judge: JudgeDecision
