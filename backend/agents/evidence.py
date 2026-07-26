"""Build one immutable market snapshot shared by all debate agents."""

from __future__ import annotations

import hashlib
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Callable

from .schemas import Evidence, HybridPrediction, MarketSnapshot


ContextLoader = Callable[[str], dict[str, Any] | None]
PredictionLoader = Callable[[str], dict[str, Any] | None]


def _default_context_loader(skin_id: str) -> dict[str, Any] | None:
    # Lazy imports keep the Agent package testable without loading pandas/TF.
    from database import change_pct, get_connection, latest_price, resolve_skin
    import rag

    with get_connection() as conn:
        skin = resolve_skin(conn, skin_id)
        if not skin:
            return None
        current_price, current_date = latest_price(conn, skin["id"])
        rows = conn.execute(
            "SELECT date, price, daily_volume FROM price_history "
            "WHERE skin_id=? ORDER BY date DESC LIMIT 31",
            (skin["id"],),
        ).fetchall()
        rows = list(reversed(rows))
        base = {
            "slug": skin["slug"] or skin_id,
            "name": skin["market_hash_name"],
            "current_price": current_price,
            "current_date": current_date,
            "change_7d": change_pct(conn, skin["id"], 7),
            "change_30d": change_pct(conn, skin["id"], 30),
            "prices": [float(row["price"]) for row in rows],
            "volumes": [int(row["daily_volume"] or 0) for row in rows],
        }

    rag_context = rag.retrieve_context(skin, skin["market_hash_name"])
    base["news"] = rag_context.get("news", [])
    base["kb"] = rag_context.get("kb", [])
    return base


def _default_prediction_loader(market_hash_name: str) -> dict[str, Any] | None:
    from model_loader import get_loader

    return get_loader().predict_hybrid(market_hash_name)


def _daily_volatility(prices: list[float]) -> float | None:
    returns = [
        prices[index] / prices[index - 1] - 1
        for index in range(1, len(prices))
        if prices[index - 1] > 0
    ]
    if len(returns) < 2:
        return None
    return round(statistics.pstdev(returns) * 100, 2)


def _max_drawdown(prices: list[float]) -> float | None:
    if not prices:
        return None
    peak = prices[0]
    maximum = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak > 0:
            maximum = max(maximum, (peak - price) / peak * 100)
    return round(maximum, 2)


def _liquidity_score(volumes: list[int]) -> float | None:
    recent = volumes[-7:]
    if not recent:
        return None
    average = sum(recent) / len(recent)
    # Keep the same simple volume-to-score convention used by the current API.
    return round(min(100.0, max(0.0, average / 50.0)), 1)


def _volume_change(volumes: list[int]) -> float | None:
    if len(volumes) < 14:
        return None
    recent = sum(volumes[-7:]) / 7
    previous = sum(volumes[-14:-7]) / 7
    if previous <= 0:
        return None
    return round((recent - previous) / previous * 100, 2)


def _news_id(item: dict[str, Any]) -> str:
    raw = "|".join(
        str(item.get(key) or "") for key in ("source", "title", "published_at")
    )
    return "news:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _direction_from_change(value: float | None) -> str:
    if value is None or math.isclose(value, 0.0, abs_tol=0.01):
        return "neutral"
    return "positive" if value > 0 else "negative"


class EvidenceBuilder:
    """Create a single source-of-truth snapshot for one skin and one run."""

    def __init__(
        self,
        *,
        context_loader: ContextLoader | None = None,
        prediction_loader: PredictionLoader | None = None,
    ) -> None:
        self._context_loader = context_loader or _default_context_loader
        self._prediction_loader = prediction_loader or _default_prediction_loader

    def build(self, skin_id: str, horizon_days: int = 7) -> MarketSnapshot:
        if horizon_days != 7:
            raise ValueError("current Hybrid contract supports a 7-day horizon only")

        context = self._context_loader(skin_id)
        if not context:
            raise LookupError(f"skin not found: {skin_id}")
        name = str(context["name"])
        current_price = float(context.get("current_price") or 0)
        prediction_raw = self._prediction_loader(name)
        prediction_raw = prediction_raw or {
            "model": "Unavailable",
            "predicted_price": current_price,
            "change_pct": 0.0,
            "confidence": 0.0,
            "date": context.get("current_date"),
        }
        model_name = str(prediction_raw.get("model") or "Unavailable")
        prediction = HybridPrediction(
            model=model_name,
            predicted_price=float(prediction_raw.get("predicted_price") or current_price),
            change_pct=float(prediction_raw.get("change_pct") or 0),
            confidence=float(prediction_raw.get("confidence") or 0),
            horizon_days=horizon_days,
            decision_date=prediction_raw.get("date"),
            degraded=("mock" in model_name.lower() or model_name == "Unavailable"),
        )

        prices = [float(value) for value in context.get("prices", [])]
        volumes = [int(value or 0) for value in context.get("volumes", [])]
        volatility = _daily_volatility(prices)
        drawdown = _max_drawdown(prices)
        liquidity = _liquidity_score(volumes)
        volume_change = _volume_change(volumes)
        change_7d = context.get("change_7d")
        change_30d = context.get("change_30d")
        timestamp = context.get("current_date")

        evidence: list[Evidence] = [
            Evidence(
                evidence_id="market:current_price",
                source="price_history",
                title="当前价格",
                content=f"当前价格 ${current_price:.2f}",
                direction="neutral",
                timestamp=timestamp,
            ),
            Evidence(
                evidence_id="model:hybrid_7d",
                source=model_name,
                title="Hybrid 7日预测",
                content=(
                    f"预测价格 ${prediction.predicted_price:.2f}，"
                    f"变化 {prediction.change_pct:.2f}%，置信度 {prediction.confidence:.1f}%"
                ),
                direction=_direction_from_change(prediction.change_pct),
                timestamp=prediction.decision_date,
            ),
        ]

        metric_rows = (
            ("market:change_7d", "近7日涨跌", change_7d, "price_history", _direction_from_change(change_7d)),
            ("market:change_30d", "近30日涨跌", change_30d, "price_history", _direction_from_change(change_30d)),
            ("risk:volatility_30d", "30日波动率", volatility, "price_history", "negative"),
            ("risk:max_drawdown_30d", "30日最大回撤", drawdown, "price_history", "negative"),
            ("market:liquidity", "流动性评分", liquidity, "daily_volume", "positive" if (liquidity or 0) >= 60 else "neutral"),
            ("market:volume_change_7d", "近7日成交量变化", volume_change, "daily_volume", _direction_from_change(volume_change)),
        )
        for evidence_id, title, value, source, direction in metric_rows:
            if value is not None:
                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source=source,
                        title=title,
                        content=f"{title}: {float(value):.2f}%",
                        direction=direction,
                        timestamp=timestamp,
                    )
                )

        for index, text in enumerate(context.get("kb", []), start=1):
            evidence.append(
                Evidence(
                    evidence_id=f"kb:{index}",
                    source="internal_kb",
                    title="市场知识",
                    content=str(text),
                    direction="neutral",
                )
            )
        for item in context.get("news", []):
            sentiment = str(item.get("sentiment") or "neutral").lower()
            if sentiment not in {"positive", "negative", "neutral"}:
                sentiment = "neutral"
            evidence.append(
                Evidence(
                    evidence_id=_news_id(item),
                    source=str(item.get("source") or "news"),
                    title=str(item.get("title") or "市场资讯"),
                    content=str(item.get("summary") or item.get("title") or "无摘要"),
                    direction=sentiment,
                    timestamp=item.get("published_at"),
                )
            )

        return MarketSnapshot(
            skin_id=str(context.get("slug") or skin_id),
            skin_name=name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            current_price=current_price,
            change_7d=change_7d,
            change_30d=change_30d,
            volatility_30d=volatility,
            max_drawdown_30d=drawdown,
            liquidity_score=liquidity,
            hybrid_prediction=prediction,
            evidence=tuple(evidence),
        )


def build_market_snapshot(skin_id: str, horizon_days: int = 7) -> MarketSnapshot:
    return EvidenceBuilder().build(skin_id, horizon_days)
