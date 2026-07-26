"""Evidence-based skin recommendation before a specific-skin debate."""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable


CandidateLoader = Callable[[], Iterable[dict[str, Any]]]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _default_candidate_loader() -> list[dict[str, Any]]:
    from database import change_pct, get_connection, latest_price

    candidates: list[dict[str, Any]] = []
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM skins
               WHERE EXISTS (
                   SELECT 1 FROM price_history p WHERE p.skin_id=skins.id
               )"""
        ).fetchall()
        for row in rows:
            price, _ = latest_price(connection, row["id"])
            stats = connection.execute(
                """SELECT AVG(daily_volume) AS avg_volume,
                          AVG(price * price) - AVG(price) * AVG(price) AS variance
                   FROM (
                       SELECT price, daily_volume FROM price_history
                       WHERE skin_id=? ORDER BY date DESC LIMIT 30
                   )""",
                (row["id"],),
            ).fetchone()
            variance = max(0.0, float(stats["variance"] or 0.0))
            volatility = math.sqrt(variance) / price * 100 if price else 100.0
            candidates.append(
                {
                    "skinId": row["slug"],
                    "name": row["market_hash_name"],
                    "category": row["category"],
                    "price": float(price or 0.0),
                    "change7d": float(change_pct(connection, row["id"], 7) or 0.0),
                    "change30d": float(change_pct(connection, row["id"], 30) or 0.0),
                    "volume": float(stats["avg_volume"] or 0.0),
                    "volatility": volatility,
                    "rarity": int(row["rarity_rank"] or 0),
                }
            )
    return candidates


class RecommendationAgent:
    """Rank candidates with an explicit, deterministic evidence policy."""

    def __init__(self, candidate_loader: CandidateLoader | None = None) -> None:
        self._candidate_loader = candidate_loader or _default_candidate_loader

    @staticmethod
    def _query_tokens(query: str) -> tuple[str, ...]:
        lowered = query.lower()
        tokens = re.findall(r"[a-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", lowered)
        ignored = {
            "推荐", "皮肤", "饰品", "一个", "几个", "适合", "预算", "以内",
            "recommend", "skin", "skins", "please", "with", "under", "an", "a",
            "the", "for", "me", "of",
        }
        return tuple(token for token in tokens if token not in ignored)

    def recommend(
        self,
        query: str,
        *,
        budget: float | None = None,
        risk_level: str = "medium",
        limit: int = 5,
        locale: str = "zh-CN",
    ) -> list[dict[str, Any]]:
        if risk_level not in {"low", "medium", "high"}:
            raise ValueError("risk_level must be low, medium, or high")
        tokens = self._query_tokens(query)
        market_tokens = (
            "ak", "ak-47", "awp", "m4", "m4a1", "m4a4", "knife", "glove",
            "\u5200", "\u624b\u5957", "\u6b65\u67aa", "\u72d9\u51fb",
        )
        required_tokens = tuple(token for token in tokens if token in market_tokens)
        ranked: list[dict[str, Any]] = []

        for raw in self._candidate_loader():
            item = dict(raw)
            price = float(item.get("price") or 0.0)
            if price <= 0 or (budget is not None and price > budget):
                continue

            haystack = f"{item.get('name', '')} {item.get('category', '')}".lower()
            if required_tokens and not any(token in haystack for token in required_tokens):
                continue
            matches = sum(1 for token in tokens if token in haystack)
            if tokens and matches == 0:
                # A recommendation request often contains generic prose. Only
                # enforce filtering for recognizable market/category tokens.
                if any(token in query.lower() for token in market_tokens):
                    continue

            momentum = float(item.get("change7d") or 0.0)
            long_momentum = float(item.get("change30d") or 0.0)
            volume = float(item.get("volume") or 0.0)
            volatility = float(item.get("volatility") or 0.0)
            liquidity_score = _clamp(math.log10(max(1.0, volume)) * 20.0)
            momentum_score = _clamp(50.0 + momentum * 5.0 + long_momentum * 1.5)
            stability_score = _clamp(100.0 - volatility * 12.0)
            affordability = 70.0 if budget is None else _clamp((1.0 - price / budget) * 65.0 + 35.0)

            weights = {
                "low": (0.18, 0.37, 0.30, 0.15),
                "medium": (0.30, 0.30, 0.22, 0.18),
                "high": (0.44, 0.24, 0.12, 0.20),
            }[risk_level]
            score = (
                momentum_score * weights[0]
                + liquidity_score * weights[1]
                + stability_score * weights[2]
                + affordability * weights[3]
                + min(matches, 2) * 8.0
            )
            english = locale == "en-US"
            reasons = [
                f"7-day change {momentum:+.2f}%" if english else f"7日变化 {momentum:+.2f}%",
                f"Average volume {volume:.0f}" if english else f"平均成交量 {volume:.0f}",
                f"30-day volatility {volatility:.2f}%" if english else f"30日波动率 {volatility:.2f}%",
            ]
            if budget is not None:
                reasons.append(
                    f"Price uses {price / budget * 100:.1f}% of budget"
                    if english else f"价格占预算 {price / budget * 100:.1f}%"
                )
            ranked.append(
                {
                    "skinId": item.get("skinId"),
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "price": round(price, 2),
                    "change7d": round(momentum, 2),
                    "liquidity": round(liquidity_score, 1),
                    "risk": "low" if volatility < 2 else "medium" if volatility < 5 else "high",
                    "score": round(score, 1),
                    "reasons": reasons,
                }
            )

        ranked.sort(key=lambda item: (-item["score"], item["price"]))
        return ranked[: max(1, min(limit, 10))]
