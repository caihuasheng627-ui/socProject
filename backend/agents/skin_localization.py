"""Resolve Chinese skin names in chat messages to market_hash_name."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

WEAR_ZH_TO_EN = {
    "崭新出厂": "Factory New",
    "略有磨损": "Minimal Wear",
    "久经沙场": "Field-Tested",
    "破损不堪": "Well-Worn",
    "战痕累累": "Battle-Scarred",
}

WEAR_PRIORITY = (
    "Field-Tested",
    "Minimal Wear",
    "Factory New",
    "Well-Worn",
    "Battle-Scarred",
)

NICK_HINTS: tuple[tuple[str, str], ...] = (
    ("火蛇", "Fire Serpent"),
    ("龙狙", "Dragon Lore"),
    ("巨龙传说", "Dragon Lore"),
    ("二西莫夫", "Asiimov"),
    ("红线", "Redline"),
    ("表面淬火", "Case Hardened"),
    ("多普勒", "Doppler"),
    ("渐变之色", "Fade"),
    ("印花集", "Printstream"),
    ("血腥运动", "Bloodsport"),
    ("金蛇缠绕", "Golden Coil"),
    ("黑色魅影", "Neo-Noir"),
)

WEAPON_ZH_HINTS: tuple[tuple[str, str], ...] = (
    ("m4a1消音版", "M4A1-S"),
    ("m4a1-s", "M4A1-S"),
    ("爪子刀", "Karambit"),
    ("蝴蝶刀", "Butterfly"),
)


def _normalize_zh_text(text: str) -> str:
    cleaned = re.sub(r"[★™（）()]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _english_weapon(en_name: str) -> str:
    weapon = en_name.split("|", 1)[0].strip()
    return re.sub(r"^(StatTrak™|Souvenir|★)\s*", "", weapon).strip()


def _wear_from_en(en_name: str) -> str | None:
    match = re.search(r"\(([^)]+)\)\s*$", en_name)
    return match.group(1) if match else None


def _wear_rank(en_name: str) -> int:
    wear = _wear_from_en(en_name)
    if wear is None:
        return len(WEAR_PRIORITY)
    try:
        return WEAR_PRIORITY.index(wear)
    except ValueError:
        return len(WEAR_PRIORITY)


def _parse_zh_display(zh_name: str) -> tuple[str, str, str | None]:
    text = _normalize_zh_text(zh_name)
    wear = None
    match = re.search(r"\(([^)]+)\)\s*$", text)
    if match:
        wear = match.group(1).strip()
        text = text[: match.start()].strip()
    if "|" in text:
        weapon, skin = text.split("|", 1)
        return weapon.strip(), skin.strip(), wear
    return "", text.strip(), wear


def _weapon_matches(message: str, en_name: str, weapon_zh: str) -> bool:
    msg = message.lower()
    norm = _normalize_zh_text(message).lower()
    weapon_en = _english_weapon(en_name).lower()

    if weapon_zh and weapon_zh.lower() in norm:
        return True
    if weapon_en and weapon_en.lower() in msg:
        return True
    for hint, prefix in WEAPON_ZH_HINTS:
        if hint in norm and prefix.lower() in weapon_en:
            return True
    return False


def _filter_by_wear_in_message(message: str, candidates: list[str]) -> list[str]:
    for wear_zh, wear_en in WEAR_ZH_TO_EN.items():
        if wear_zh in message:
            filtered = [name for name in candidates if f"({wear_en})" in name]
            if filtered:
                return filtered
            break
    return candidates


def _pick_best_wear(candidates: list[str]) -> str:
    return sorted(candidates, key=_wear_rank)[0]


@lru_cache(maxsize=1)
def get_en_to_zh_map() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    json_path = root / "backend" / "data" / "skin_names_zh.json"
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    js_path = root / "js" / "skin-names-zh.js"
    if js_path.exists():
        raw = js_path.read_text(encoding="utf-8")
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
    return {}


def match_market_hash_names(message: str, available: frozenset[str]) -> list[str]:
    """Return market_hash_name candidates ordered by match quality."""
    if not message or not available:
        return []

    mapping = {en: zh for en, zh in get_en_to_zh_map().items() if en in available}
    if not mapping:
        return []

    norm_msg = _normalize_zh_text(message)
    hits: list[tuple[str, int, int]] = []

    for en_name, zh_name in mapping.items():
        zh_norm = _normalize_zh_text(zh_name)
        if zh_name in message or (zh_norm and zh_norm in norm_msg):
            hits.append((en_name, 3, len(zh_norm)))
            continue

        weapon_zh, skin_zh, wear_zh = _parse_zh_display(zh_name)
        if not skin_zh or skin_zh not in message:
            continue
        if not _weapon_matches(message, en_name, weapon_zh):
            continue
        score = 2 if wear_zh and wear_zh in message else 1
        hits.append((en_name, score, len(skin_zh)))

    if not hits:
        for nick, hint in NICK_HINTS:
            if nick not in message:
                continue
            family = [en for en in mapping if hint.lower() in en.lower()]
            if not family:
                continue
            filtered = [
                en for en in family if _weapon_matches(message, en, "")
                or not any(_english_weapon(other).lower() != _english_weapon(en).lower()
                           for other in family)
            ]
            if not filtered:
                filtered = family
            weapon_families = {_english_weapon(name) for name in filtered}
            if len(weapon_families) > 1:
                for en_name in sorted(filtered, key=_wear_rank):
                    hits.append((en_name, 1, len(nick)))
                continue
            chosen = _pick_best_wear(_filter_by_wear_in_message(message, filtered))
            hits.append((chosen, 2, len(nick)))

    if not hits:
        return []

    hits.sort(key=lambda item: (-item[1], -item[2], _wear_rank(item[0])))
    seen: set[str] = set()
    ordered: list[str] = []
    for en_name, _, _ in hits:
        if en_name in seen:
            continue
        seen.add(en_name)
        ordered.append(en_name)
    return ordered


def resolve_chinese_skin(
    message: str,
    rows: list[Any],
    *,
    build_candidate: Any,
) -> dict[str, Any] | None:
    """``build_candidate(row, *, ambiguous=False)`` returns a skin dict."""
    """Resolve a Chinese skin reference against DB rows.

    ``build_candidate`` is ``(row, price) -> candidate dict`` supplied by the
    caller so this module stays DB-shape agnostic in tests.
    """
    available = frozenset(str(row["market_hash_name"]) for row in rows)
    matches = match_market_hash_names(message, available)
    if not matches:
        return None

    if len(matches) == 1:
        row = next(item for item in rows if item["market_hash_name"] == matches[0])
        return build_candidate(row, ambiguous=False)

    weapons = {_english_weapon(name) for name in matches}
    if len(weapons) == 1:
        best = _pick_best_wear(_filter_by_wear_in_message(message, matches))
        row = next(item for item in rows if item["market_hash_name"] == best)
        return build_candidate(row, ambiguous=False)

    candidates = []
    seen_ids: set[str] = set()
    for en_name in matches[:8]:
        row = next((item for item in rows if item["market_hash_name"] == en_name), None)
        if row is None:
            continue
        candidate = build_candidate(row, ambiguous=True)
        skin_id = candidate.get("skinId")
        if skin_id in seen_ids:
            continue
        seen_ids.add(skin_id)
        candidates.append(candidate)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    query = _parse_zh_display(get_en_to_zh_map().get(matches[0], matches[0]))[1] or matches[0]
    return {"ambiguous": True, "query": query, "candidates": candidates}
