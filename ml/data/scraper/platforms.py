"""
多平台 CS2 饰品实时报价适配器
================================
统一输出字段见 Quote.to_dict()。

平台:
  - Skinport — 公开 API(需 Brotli Accept-Encoding)
  - BUFF     — 需登录 Cookie(环境变量 BUFF_SESSION)
  - Steam    — Community Market priceoverview(限流严格)

注意: 仅用于课程演示 / 个人研究; 请遵守各平台 ToS, 控制请求频率。
"""
from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_money(text: Any) -> float | None:
    """解析 '$1,234.56' / '¥12.3' / 12.3 等价格文本。"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s:
        return None
    s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


@dataclass
class Quote:
    market_hash_name: str
    platform: str
    currency: str
    price: float | None               # 主价(卖一 / min), 尽量换算为 USD
    price_native: float | None = None
    buy_price: float | None = None    # 买一 / bid
    sell_price: float | None = None   # 卖一 / ask
    volume: int | None = None         # 在售量或成交量
    fetched_at: str = field(default_factory=utc_now_iso)
    ok: bool = True
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # extra 扁平化时写入 JSON 字符串太重; CLI 侧会处理
        return d


class PlatformClient(ABC):
    name: str

    def __init__(self, client: httpx.Client | None = None, timeout: float = 30.0):
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self.last_error: str | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @abstractmethod
    def fetch_quotes(self, names: Iterable[str]) -> list[Quote]:
        ...


class SkinportClient(PlatformClient):
    """Skinport 公开批量接口 — 一次拉全量再本地过滤。"""

    name = "skinport"
    URL = "https://api.skinport.com/v1/items"

    def __init__(self, client: httpx.Client | None = None, currency: str = "USD"):
        super().__init__(client)
        self.currency = currency
        self._cache: dict[str, dict] | None = None
        self._cache_at: float = 0.0
        self.cache_ttl = 60.0  # 秒; Skinport 建议勿频繁刷全量

    def _load_catalog(self, force: bool = False) -> dict[str, dict]:
        now = time.time()
        if (
            not force
            and self._cache is not None
            and (now - self._cache_at) < self.cache_ttl
        ):
            return self._cache
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json",
            # Skinport 强制要求 Brotli
            "Accept-Encoding": "br",
        }
        r = self.client.get(
            self.URL,
            params={"app_id": 730, "currency": self.currency, "tradable": 0},
            headers=headers,
        )
        r.raise_for_status()
        items = r.json()
        catalog: dict[str, dict] = {}
        for it in items:
            name = it.get("market_hash_name")
            if not name:
                continue
            # Doppler 等相位可能同名多条: 优先保留有 min_price 的
            prev = catalog.get(name)
            if prev is None:
                catalog[name] = it
            elif prev.get("min_price") is None and it.get("min_price") is not None:
                catalog[name] = it
            elif (
                prev.get("min_price") is not None
                and it.get("min_price") is not None
                and float(it["min_price"]) < float(prev["min_price"])
            ):
                catalog[name] = it
        self._cache = catalog
        self._cache_at = now
        return self._cache

    def fetch_quotes(self, names: Iterable[str]) -> list[Quote]:
        catalog = self._load_catalog()
        out: list[Quote] = []
        for name in names:
            it = catalog.get(name)
            if not it:
                out.append(
                    Quote(
                        market_hash_name=name,
                        platform=self.name,
                        currency=self.currency,
                        price=None,
                        ok=False,
                        error="NOT_FOUND",
                    )
                )
                continue
            min_p = parse_money(it.get("min_price"))
            med_p = parse_money(it.get("median_price"))
            mean_p = parse_money(it.get("mean_price"))
            qty = it.get("quantity")
            out.append(
                Quote(
                    market_hash_name=name,
                    platform=self.name,
                    currency=it.get("currency") or self.currency,
                    price=min_p if min_p is not None else med_p,
                    price_native=min_p if min_p is not None else med_p,
                    sell_price=min_p,
                    buy_price=None,
                    volume=int(qty) if qty is not None else None,
                    extra={
                        "median_price": med_p,
                        "mean_price": mean_p,
                        "suggested_price": parse_money(it.get("suggested_price")),
                        "max_price": parse_money(it.get("max_price")),
                        "item_page": it.get("item_page"),
                    },
                )
            )
        return out


class BuffClient(PlatformClient):
    """BUFF 实时挂单价 — 需 session Cookie。"""

    name = "buff"
    SEARCH_URL = "https://buff.163.com/api/market/goods"
    DETAIL_URL = "https://buff.163.com/api/market/goods/sell_order"

    def __init__(
        self,
        client: httpx.Client | None = None,
        session: str | None = None,
        usd_cny_rate: float | None = None,
        request_interval: float = 1.2,
    ):
        super().__init__(client)
        self.session = (session or os.getenv("BUFF_SESSION", "")).strip()
        # 显式传参优先; 否则读环境变量; 再回退 7.2
        if usd_cny_rate is None:
            usd_cny_rate = float(os.getenv("USD_CNY_RATE", "7.2"))
        self.usd_cny_rate = float(usd_cny_rate)
        self.request_interval = request_interval
        self._id_cache: dict[str, int] = {}
        if self.session:
            self.client.cookies.set("session", self.session, domain="buff.163.com")

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json",
            "Referer": "https://buff.163.com/market/csgo",
        }

    def _cny_to_usd(self, cny: float | None) -> float | None:
        if cny is None:
            return None
        if self.usd_cny_rate <= 0:
            return cny
        return round(cny / self.usd_cny_rate, 4)

    def resolve_goods_id(self, name: str) -> int | None:
        if name in self._id_cache:
            return self._id_cache[name]
        if not self.session:
            self.last_error = "BUFF_SESSION missing"
            return None
        r = self.client.get(
            self.SEARCH_URL,
            params={
                "game": "csgo",
                "page_num": 1,
                "page_size": 5,
                "search": name[:60],
            },
            headers=self._headers(),
        )
        data = r.json()
        if data.get("code") != "OK":
            self.last_error = str(data.get("error") or data.get("code"))
            return None
        items = (data.get("data") or {}).get("items") or []
        goods_id = None
        for it in items:
            if it.get("market_hash_name") == name:
                goods_id = it["id"]
                break
        if goods_id is None and items:
            goods_id = items[0]["id"]
        if goods_id is not None:
            self._id_cache[name] = int(goods_id)
        return goods_id

    def fetch_one(self, name: str) -> Quote:
        if not self.session:
            return Quote(
                market_hash_name=name,
                platform=self.name,
                currency="CNY",
                price=None,
                ok=False,
                error="BUFF_SESSION missing — set env BUFF_SESSION",
            )
        try:
            goods_id = self.resolve_goods_id(name)
            if not goods_id:
                return Quote(
                    market_hash_name=name,
                    platform=self.name,
                    currency="CNY",
                    price=None,
                    ok=False,
                    error=self.last_error or "NOT_FOUND",
                )
            # sell_order 带 goods 摘要(含 sell_min_price / buy_max_price)
            r = self.client.get(
                self.DETAIL_URL,
                params={"game": "csgo", "goods_id": goods_id, "page_num": 1, "page_size": 1},
                headers=self._headers(),
            )
            data = r.json()
            if data.get("code") != "OK":
                return Quote(
                    market_hash_name=name,
                    platform=self.name,
                    currency="CNY",
                    price=None,
                    ok=False,
                    error=str(data.get("error") or data.get("code")),
                    extra={"goods_id": goods_id},
                )
            payload = data.get("data") or {}
            goods = payload.get("goods") or {}
            sell_min = parse_money(goods.get("sell_min_price") or goods.get("price"))
            buy_max = parse_money(goods.get("buy_max_price"))
            sell_num = goods.get("sell_num") or payload.get("total_count")
            matched = goods.get("market_hash_name") or name
            return Quote(
                market_hash_name=matched,
                platform=self.name,
                currency="USD",
                price=self._cny_to_usd(sell_min),
                price_native=sell_min,
                sell_price=self._cny_to_usd(sell_min),
                buy_price=self._cny_to_usd(buy_max),
                volume=int(sell_num) if sell_num is not None else None,
                extra={
                    "goods_id": goods_id,
                    "currency_native": "CNY",
                    "usd_cny_rate": self.usd_cny_rate,
                    "sell_min_price_cny": sell_min,
                    "buy_max_price_cny": buy_max,
                },
            )
        except Exception as e:
            return Quote(
                market_hash_name=name,
                platform=self.name,
                currency="CNY",
                price=None,
                ok=False,
                error=str(e),
            )

    def fetch_quotes(self, names: Iterable[str]) -> list[Quote]:
        out: list[Quote] = []
        for i, name in enumerate(names):
            if i > 0 and self.request_interval > 0:
                time.sleep(self.request_interval)
            out.append(self.fetch_one(name))
        return out


class SteamClient(PlatformClient):
    """Steam Community Market 实时价(priceoverview)。限流严格,默认慢速。"""

    name = "steam"
    URL = "https://steamcommunity.com/market/priceoverview/"

    def __init__(
        self,
        client: httpx.Client | None = None,
        currency: int = 1,  # 1=USD
        request_interval: float = 3.0,
        max_retries: int = 3,
    ):
        super().__init__(client)
        self.currency = currency
        self.request_interval = request_interval
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json",
            "Referer": "https://steamcommunity.com/market/",
        }

    def fetch_one(self, name: str) -> Quote:
        last_err = "UNKNOWN"
        for attempt in range(self.max_retries):
            try:
                r = self.client.get(
                    self.URL,
                    params={
                        "appid": 730,
                        "currency": self.currency,
                        "market_hash_name": name,
                    },
                    headers=self._headers(),
                )
                if r.status_code == 429:
                    last_err = "RATE_LIMITED"
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    last_err = f"HTTP_{r.status_code}"
                    time.sleep(2)
                    continue
                data = r.json()
                if not data.get("success"):
                    return Quote(
                        market_hash_name=name,
                        platform=self.name,
                        currency="USD",
                        price=None,
                        ok=False,
                        error="NOT_FOUND_OR_UNAVAILABLE",
                    )
                lowest = parse_money(data.get("lowest_price"))
                median = parse_money(data.get("median_price"))
                volume = parse_money(data.get("volume"))
                return Quote(
                    market_hash_name=name,
                    platform=self.name,
                    currency="USD",
                    price=lowest if lowest is not None else median,
                    price_native=lowest if lowest is not None else median,
                    sell_price=lowest,
                    volume=int(volume) if volume is not None else None,
                    extra={"median_price": median, "volume_raw": data.get("volume")},
                )
            except Exception as e:
                last_err = str(e)
                time.sleep(2 * (attempt + 1))
        return Quote(
            market_hash_name=name,
            platform=self.name,
            currency="USD",
            price=None,
            ok=False,
            error=last_err,
        )

    def fetch_quotes(self, names: Iterable[str]) -> list[Quote]:
        out: list[Quote] = []
        for i, name in enumerate(names):
            if i > 0 and self.request_interval > 0:
                time.sleep(self.request_interval)
            out.append(self.fetch_one(name))
        return out


PLATFORM_REGISTRY: dict[str, type[PlatformClient]] = {
    "skinport": SkinportClient,
    "buff": BuffClient,
    "steam": SteamClient,
}


def build_clients(
    platforms: Iterable[str],
    **kwargs: Any,
) -> list[PlatformClient]:
    clients: list[PlatformClient] = []
    for p in platforms:
        key = p.strip().lower()
        cls = PLATFORM_REGISTRY.get(key)
        if not cls:
            raise ValueError(f"未知平台: {p} (可选: {', '.join(PLATFORM_REGISTRY)})")
        if key == "buff":
            rate = kwargs.get("usd_cny_rate")
            clients.append(
                BuffClient(
                    session=kwargs.get("buff_session"),
                    usd_cny_rate=None if rate is None else float(rate),
                    request_interval=float(kwargs.get("buff_interval", 1.2)),
                )
            )
        elif key == "steam":
            clients.append(
                SteamClient(request_interval=float(kwargs.get("steam_interval", 3.0)))
            )
        else:
            clients.append(SkinportClient(currency=kwargs.get("currency", "USD")))
    return clients


# 默认演示 watchlist(与前端 data.js 对齐; 磨损取 Skinport 常见在售档)
DEFAULT_WATCHLIST = [
    "AK-47 | Redline (Field-Tested)",
    "AK-47 | Fire Serpent (Field-Tested)",
    "AWP | Dragon Lore (Factory New)",
    "AWP | Asiimov (Field-Tested)",
    "M4A1-S | Printstream (Field-Tested)",
    "M4A4 | Howl (Field-Tested)",
    "Desert Eagle | Printstream (Field-Tested)",
    "★ Butterfly Knife | Doppler (Factory New)",
    "★ Karambit | Doppler (Factory New)",
    "★ Sport Gloves | Pandora's Box (Field-Tested)",
    "Dreams & Nightmares Case",
    "Danger Zone Case",
    "USP-S | Kill Confirmed (Field-Tested)",
    "Glock-18 | Fade (Factory New)",
    "AWP | Hyper Beast (Field-Tested)",
]


def load_names_from_csv(path: str, column: str = "market_hash_name") -> list[str]:
    import csv
    from pathlib import Path

    names: list[str] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"CSV 缺少列 {column}: {path}")
        for row in reader:
            n = (row.get(column) or "").strip()
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names
