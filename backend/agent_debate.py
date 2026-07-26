"""CSVest multi-agent debate entry point.

Modes:
  - default: return an exact-match pre-recorded Expo seed when available;
  - live=1: run isolated Bull and Bear agents for multiple rounds, then Judge;
  - no LLM key/no seed: run the same structured pipeline with explicit mocks.

The response keeps legacy ``rounds[].bull/bear`` and ``consensus`` fields so
the current prediction page remains compatible while schema v2 consumers can
use the structured opinions, market snapshot, and Judge decision.
"""

from __future__ import annotations

import json
from typing import Any

from agents import DebateService, UserProfile, outcome_to_api
from config import DEBATE_ROUNDS, LLM_ENABLED, SEED_DIR
from database import get_connection, resolve_skin


def _load_seed_debate(slug: str) -> dict[str, Any] | None:
    """Load only a seed that exactly matches the resolved skin slug."""

    if not SEED_DIR.exists():
        return None
    for path in SEED_DIR.glob("seed_debate_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("skinId") == slug or data.get("slug") == slug:
                return data
        except (OSError, ValueError, TypeError):
            continue
    return None


def _resolve_slug(skin_id: str) -> str | None:
    with get_connection() as connection:
        skin = resolve_skin(connection, skin_id)
        return str(skin["slug"] or skin_id) if skin else None


def debate(
    skin_id: str,
    live: bool = False,
    mode: str = "bull_bear",
    *,
    budget: float | None = None,
    risk_level: str = "medium",
    horizon_days: int = 7,
    rounds: int | None = None,
    locale: str = "zh-CN",
    service: DebateService | None = None,
) -> dict[str, Any]:
    """Run or replay a debate for ``/api/debate/{skinId}``."""

    slug = _resolve_slug(skin_id) if service is None else skin_id
    if slug is None:
        return {"error": "skin not found", "skinId": skin_id}

    if not live:
        seed = _load_seed_debate(slug)
        if seed:
            # Copy before adding transport metadata; never mutate cached seed data.
            result = dict(seed)
            result["mode"] = "pre_recorded"
            return result

    try:
        profile = UserProfile(
            budget=budget,
            horizon_days=horizon_days,
            risk_level=risk_level,
            locale=locale,
        )
        runner = service or DebateService()
        outcome = runner.run(
            slug,
            user_profile=profile,
            rounds=rounds if rounds is not None else DEBATE_ROUNDS,
        )
    except LookupError:
        return {"error": "skin not found", "skinId": skin_id}
    except ValueError as exc:
        return {"error": str(exc), "skinId": skin_id}

    execution_mode = "live" if live and LLM_ENABLED else "structured_mock"
    return outcome_to_api(
        outcome, mode=execution_mode, requested_mode=mode, locale=locale
    )
