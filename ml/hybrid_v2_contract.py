"""Validation for the Hybrid V2 adapter shared by training and inference."""
from __future__ import annotations

import math
from typing import Any


CONTRACT_VERSION = "hybrid-v2-volume-free-v1"
HORIZONS = {str(day) for day in range(1, 8)}
COMPONENTS = ("c", "d", "recent")


def validate_hybrid_v2_adapter(
    adapter: Any, *, require_accepted: bool = True
) -> dict[str, Any]:
    if not isinstance(adapter, dict):
        raise ValueError("Hybrid V2 adapter must be an object")
    if adapter.get("contractVersion") != CONTRACT_VERSION:
        raise ValueError("invalid Hybrid V2 adapter contract")
    if adapter.get("selectionSplit") != "train":
        raise ValueError("Hybrid V2 weights must be selected on train only")
    if adapter.get("horizonSteps") != 7:
        raise ValueError("Hybrid V2 adapter must contain seven horizons")
    if require_accepted and adapter.get("accepted") is not True:
        raise ValueError("Hybrid V2 adapter must be accepted before deployment")
    if not isinstance(adapter.get("accepted"), bool):
        raise ValueError("Hybrid V2 adapter requires an acceptance decision")

    weights = adapter.get("weights")
    if not isinstance(weights, dict) or "global" not in weights:
        raise ValueError("Hybrid V2 adapter requires global weights")
    for tier, horizons in weights.items():
        if not isinstance(tier, str) or not isinstance(horizons, dict):
            raise ValueError("Hybrid V2 tier weights must be objects")
        if set(horizons) != HORIZONS:
            raise ValueError(f"Hybrid V2 tier {tier!r} requires every horizon 1-7")
        for day, raw in horizons.items():
            if not isinstance(raw, dict):
                raise ValueError(f"Hybrid V2 horizon {day} weights must be an object")
            try:
                components = [float(raw[name]) for name in COMPONENTS]
                bias = float(raw.get("bias", 0.0))
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Hybrid V2 horizon {day} weights are incomplete") from error
            if not all(math.isfinite(value) for value in [*components, bias]):
                raise ValueError(f"Hybrid V2 horizon {day} weights must be finite")
            if any(value < 0.0 for value in components):
                raise ValueError(f"Hybrid V2 horizon {day} weights must be non-negative")
            if not math.isclose(sum(components), 1.0, abs_tol=1e-6):
                raise ValueError(f"Hybrid V2 horizon {day} weights must sum to one")
    return adapter
