"""Small locale helpers shared by deterministic Agent output."""

from __future__ import annotations

from typing import Literal


Locale = Literal["zh-CN", "en-US"]


def normalize_locale(locale: str | None) -> Locale:
    return "en-US" if str(locale or "").lower().startswith("en") else "zh-CN"


def is_english(locale: str | None) -> bool:
    return normalize_locale(locale) == "en-US"


def choose(locale: str | None, zh: str, en: str) -> str:
    return en if is_english(locale) else zh


def risk_label(level: str, locale: str | None) -> str:
    labels = {
        "zh-CN": {"low": "保守", "medium": "均衡", "high": "激进"},
        "en-US": {"low": "Conservative", "medium": "Balanced", "high": "Aggressive"},
    }
    return labels[normalize_locale(locale)].get(level, level)
