"""
多平台实时报价服务
==================
包装 ml/data/scraper/platforms.py,供 FastAPI /api/skins/{id}/quotes 使用。

策略:
  1. USE_BUFF_LIVE=1 时尝试真实拉取(批量平台有进程内缓存)
  2. 失败 / 默认关闭时,按库内最新价生成演示用跨平台价差(课程降级)
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from config import REPO_ROOT, USE_BUFF_LIVE, USD_CNY_RATE

_SCRAPER = REPO_ROOT / "ml" / "data" / "scraper"
if str(_SCRAPER) not in sys.path:
    sys.path.insert(0, str(_SCRAPER))

# 默认给前端展示的平台(免 Cookie、可批量缓存; Steam/CSFloat/BUFF 限流或需登录,按需)
DEFAULT_PLATFORMS = [
    "skinport",
    "waxpeer",
    "marketcsgo",
    "lootfarm",
    "csgotrader",
    "steam",
    "buff",
    "csfloat",
]

# 演示降级:相对库内基准价的价差系数(贴近各平台常见溢价)
_MOCK_FACTORS = {
    "buff": 0.93,
    "skinport": 0.97,
    "csfloat": 0.96,
    "waxpeer": 0.99,
    "marketcsgo": 0.98,
    "steam": 1.06,
    "csgotrader": 1.02,
    "lootfarm": 1.18,
}

_PLATFORM_LABELS = {
    "buff": "BUFF",
    "skinport": "Skinport",
    "steam": "Steam",
    "waxpeer": "Waxpeer",
    "marketcsgo": "Market.CSGO",
    "lootfarm": "Loot.farm",
    "csgotrader": "CSGOTrader",
    "csfloat": "CSFloat",
}

# 进程内报价缓存: key=(name, platforms_tuple, live) → (expires_at, payload)
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL_LIVE = 90.0
_CACHE_TTL_MOCK = 30.0

# 仅对这些平台做真拉取(批量价表); 其余走 mock / 需要 Cookie
_LIVE_SAFE = {"skinport", "waxpeer", "marketcsgo", "lootfarm", "csgotrader"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def platform_label(key: str) -> str:
    return _PLATFORM_LABELS.get(key, key)


def _mock_quotes(
    market_hash_name: str,
    base_price: float | None,
    platforms: list[str],
) -> list[dict[str, Any]]:
    base = float(base_price or 0.0)
    out: list[dict[str, Any]] = []
    for p in platforms:
        factor = _MOCK_FACTORS.get(p, 1.0)
        price = round(base * factor, 2) if base > 0 else None
        out.append(
            {
                "platform": p,
                "label": platform_label(p),
                "currency": "USD",
                "price": price,
                "priceNative": price,
                "buyPrice": round(price * 0.97, 2) if price else None,
                "sellPrice": price,
                "volume": None,
                "ok": price is not None,
                "error": None if price is not None else "NO_BASE_PRICE",
                "live": False,
            }
        )
    return out


def _live_quotes(
    market_hash_name: str,
    platforms: list[str],
    base_price: float | None,
) -> list[dict[str, Any]]:
    from platforms import build_clients  # type: ignore

    live_platforms = [p for p in platforms if p in _LIVE_SAFE]
    deferred = [p for p in platforms if p not in _LIVE_SAFE]

    results: dict[str, dict[str, Any]] = {}
    if live_platforms:
        clients = build_clients(
            live_platforms,
            usd_cny_rate=USD_CNY_RATE,
            buff_interval=0,
            steam_interval=0,
        )
        try:
            for client in clients:
                try:
                    qs = client.fetch_quotes([market_hash_name])
                    q = qs[0] if qs else None
                    if q is None:
                        raise RuntimeError("empty")
                    results[client.name] = {
                        "platform": client.name,
                        "label": platform_label(client.name),
                        "currency": q.currency,
                        "price": round(q.price, 4) if q.price is not None else None,
                        "priceNative": q.price_native,
                        "buyPrice": q.buy_price,
                        "sellPrice": q.sell_price,
                        "volume": q.volume,
                        "ok": bool(q.ok and q.price is not None),
                        "error": q.error,
                        "live": True,
                    }
                except Exception as e:
                    results[client.name] = {
                        "platform": client.name,
                        "label": platform_label(client.name),
                        "currency": "USD",
                        "price": None,
                        "priceNative": None,
                        "buyPrice": None,
                        "sellPrice": None,
                        "volume": None,
                        "ok": False,
                        "error": str(e),
                        "live": True,
                    }
        finally:
            for c in clients:
                try:
                    c.close()
                except Exception:
                    pass

    # 限流/需登录平台:用基准价合成演示价,避免表格大片空白
    for mq in _mock_quotes(market_hash_name, base_price, deferred):
        mq["error"] = "SYNTHETIC_DEMO"
        results[mq["platform"]] = mq

    return [results[p] for p in platforms if p in results]


def _spread(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    ok = [q for q in quotes if q.get("ok") and q.get("price") is not None]
    if len(ok) < 2:
        return None
    prices = [float(q["price"]) for q in ok]
    lo, hi = min(prices), max(prices)
    lo_p = next(q["platform"] for q in ok if float(q["price"]) == lo)
    hi_p = next(q["platform"] for q in ok if float(q["price"]) == hi)
    return {
        "min": lo,
        "max": hi,
        "minPlatform": lo_p,
        "maxPlatform": hi_p,
        "spreadPct": round((hi - lo) / lo * 100, 2) if lo > 0 else 0.0,
    }


def get_skin_quotes(
    market_hash_name: str,
    base_price: float | None,
    platforms: list[str] | None = None,
    live: bool | None = None,
) -> dict[str, Any]:
    """返回统一报价 payload。"""
    plats = [p.strip().lower() for p in (platforms or DEFAULT_PLATFORMS) if p.strip()]
    # 去重保序
    deduped: list[str] = []
    seen: set[str] = set()
    for p in plats:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    plats = deduped

    want_live = USE_BUFF_LIVE if live is None else bool(live)
    cache_key = (market_hash_name, tuple(plats), want_live)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]

    mode = "live"
    quotes: list[dict[str, Any]]
    if want_live:
        try:
            quotes = _live_quotes(market_hash_name, plats)
            # 若全部失败则降级 mock
            if not any(q.get("ok") for q in quotes):
                quotes = _mock_quotes(market_hash_name, base_price, plats)
                mode = "mock_fallback"
        except Exception:
            quotes = _mock_quotes(market_hash_name, base_price, plats)
            mode = "mock_fallback"
    else:
        quotes = _mock_quotes(market_hash_name, base_price, plats)
        mode = "mock"

    # mock 模式下把 live 标记统一为 False
    if mode.startswith("mock"):
        for q in quotes:
            q["live"] = False

    payload = {
        "skinId": None,  # 由路由填充
        "marketHashName": market_hash_name,
        "basePrice": round(base_price, 2) if base_price is not None else None,
        "mode": mode,
        "fetchedAt": _utcnow_iso(),
        "quotes": quotes,
        "spread": _spread(quotes),
    }
    ttl = _CACHE_TTL_LIVE if mode == "live" else _CACHE_TTL_MOCK
    with _cache_lock:
        _cache[cache_key] = (now + ttl, payload)
    return payload
