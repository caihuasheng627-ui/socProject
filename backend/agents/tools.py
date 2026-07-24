"""Controlled, deterministic tools available to debate agents."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable

from .schemas import Evidence, MarketSnapshot


BULL_FOCUS_TOOL = "focus_positive_evidence"
BEAR_FOCUS_TOOL = "focus_risk_evidence"
JUDGE_EVIDENCE_TOOL = "all_evidence"


def focus_positive_evidence(snapshot: MarketSnapshot) -> tuple[Evidence, ...]:
    """Rank positive evidence first without hiding the shared raw snapshot."""

    return tuple(
        sorted(
            snapshot.evidence,
            key=lambda item: (
                0 if item.direction == "positive" else 1 if item.direction == "neutral" else 2,
                item.evidence_id,
            ),
        )
    )


def focus_risk_evidence(snapshot: MarketSnapshot) -> tuple[Evidence, ...]:
    """Rank negative/risk evidence first without changing source facts."""

    return tuple(
        sorted(
            snapshot.evidence,
            key=lambda item: (
                0 if item.direction == "negative" else 1 if item.direction == "neutral" else 2,
                item.evidence_id,
            ),
        )
    )


def all_evidence(snapshot: MarketSnapshot) -> tuple[Evidence, ...]:
    return snapshot.evidence


DEFAULT_TOOLS = MappingProxyType(
    {
        BULL_FOCUS_TOOL: focus_positive_evidence,
        BEAR_FOCUS_TOOL: focus_risk_evidence,
        JUDGE_EVIDENCE_TOOL: all_evidence,
    }
)


class AgentToolbox:
    """Execute only tools explicitly allowed by an Agent instance."""

    def __init__(self, tools: dict[str, Callable[..., Any]] | None = None) -> None:
        self._tools = MappingProxyType(dict(DEFAULT_TOOLS if tools is None else tools))

    def call(self, agent: Any, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        agent.require_tool(tool_name)
        try:
            tool = self._tools[tool_name]
        except KeyError as exc:
            raise LookupError(f"unknown agent tool: {tool_name}") from exc
        return tool(*args, **kwargs)
