"""Shared foundations for isolated CSVest agents."""

from .base import BaseAgent
from .bear_agent import BearAgent
from .bull_agent import BullAgent
from .debate_service import DebateService, outcome_to_api
from .evidence import EvidenceBuilder, build_market_snapshot
from .judge_agent import EvidenceValidationError, JudgeAgent
from .orchestrator import AIOrchestrator, detect_intent
from .recommendation_agent import RecommendationAgent
from .session_service import AgentSessionService, session_to_api
from .session_store import SessionNotFoundError, SessionStore
from .schemas import (
    AgentArgument,
    BearInput,
    BearOpinion,
    BullInput,
    BullOpinion,
    DebateOutcome,
    DebateRoundOutcome,
    Evidence,
    HybridPrediction,
    JudgeDecision,
    JudgeInput,
    MarketSnapshot,
    UserProfile,
)

__all__ = [
    "AgentArgument",
    "AIOrchestrator",
    "AgentSessionService",
    "BaseAgent",
    "BearAgent",
    "BearInput",
    "BearOpinion",
    "BullInput",
    "BullAgent",
    "BullOpinion",
    "DebateOutcome",
    "DebateRoundOutcome",
    "DebateService",
    "Evidence",
    "EvidenceBuilder",
    "EvidenceValidationError",
    "HybridPrediction",
    "JudgeDecision",
    "JudgeInput",
    "JudgeAgent",
    "MarketSnapshot",
    "RecommendationAgent",
    "SessionNotFoundError",
    "SessionStore",
    "UserProfile",
    "build_market_snapshot",
    "detect_intent",
    "outcome_to_api",
    "session_to_api",
]
