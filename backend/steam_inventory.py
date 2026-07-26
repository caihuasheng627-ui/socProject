"""
Steam CS2 库存拉取(组员 3)
==========================
从 Steam 拉取某个公开(或带 cookie 的私有)账号的 CS2 库存,聚合为
[{market_hash_name, quantity}, ...],供 /api/inventory/steam/import 映射入库。

设计(对标 scraper_buff.py):
  - 只认 /profiles/<steamid64> 链接(不做 vanity 解析,免 Steam Web API key)
  - 分页拉 inventory 端点(count=2000, start_assetid 翻页)
  - 完整浏览器 UA;提供 steamLoginSecure cookie 则带上(私有库存必填,公开建议填以抗限流)
  - 429 指数退避重试;私有/404/持续限流抛清晰异常,端点转 HTTP 错误
  - 按 (classid, instanceid) 把 asset 关联到 description,聚合 market_hash_name 数量
"""
from __future__ import annotations

import re
import sys
import time
from collections import defaultdict

import httpx

# Windows GBK 控制台打印含 StatTrak™ 等字符会崩,强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

STEAM_INV_URL = "https://steamcommunity.com/inventory/{steamid}/730/2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://steamcommunity.com/",
}


# ============================================================
# 异常:端点捕获后转成对应 HTTP 错误 + 中文提示
# ============================================================
class SteamError(Exception):
    """Steam 拉取失败基类。"""


class SteamBadUrl(SteamError):
    """链接格式不对,提取不到 steamid64。"""


class SteamPrivate(SteamError):
    """库存私有,需提供 steamLoginSecure cookie。"""


class SteamNotFound(SteamError):
    """账号不存在或无 CS2 库存。"""


class SteamRateLimited(SteamError):
    """被 Steam 限流(429),重试后仍失败。"""


# ============================================================
# 解析链接
# ============================================================
def parse_steamid(url: str) -> str | None:
    """从 Steam 库存链接提取 steamid64(17 位数字)。
    只认 /profiles/<steamid64> 形式;不解析 /id/<vanity>。"""
    if not url:
        return None
    m = re.search(r"/profiles/(\d{17,20})", url)
    return m.group(1) if m else None


# ============================================================
# 拉取 CS2 库存(分页)
# ============================================================
def fetch_cs2_inventory(steamid: str, cookie: str | None = None) -> list[dict]:
    """返回 [{market_hash_name, quantity}, ...]。
    cookie: steamLoginSecure 值(私有库存必填)。"""
    client = httpx.Client(timeout=15, follow_redirects=True)
    if cookie:
        client.cookies.set("steamLoginSecure", cookie, domain="steamcommunity.com")

    aggregated: dict[str, int] = defaultdict(int)
    start_assetid: str | None = None
    total_seen = 0
    total_count = None

    try:
        for _page in range(50):  # 上限 50 页(50*2000=10w 件),足够兜底
            params = {"l": "english", "count": 2000}
            if start_assetid:
                params["start_assetid"] = start_assetid

            data = _get_with_retry(client, STEAM_INV_URL.format(steamid=steamid), params)

            if not data.get("success"):
                # Steam 返回 success=false 通常是私有 / 被限流
                err = (data.get("error") or "").lower()
                if "private" in err or "profile" in err:
                    raise SteamPrivate("库存私有或不可访问,请提供 steamLoginSecure cookie")
                if data.get("last_assetid") is None and total_seen == 0:
                    raise SteamPrivate("Steam 拒绝访问(success=false),多为私有库存,请提供 cookie")
                break

            assets = data.get("assets") or []
            descriptions = data.get("descriptions") or []
            if not assets:
                break

            # 按 (classid, instanceid) 建 description 索引
            desc_map: dict[tuple[str, str], dict] = {}
            for d in descriptions:
                desc_map[(str(d.get("classid")), str(d.get("instanceid")))] = d

            for a in assets:
                key = (str(a.get("classid")), str(a.get("instanceid")))
                d = desc_map.get(key)
                if not d:
                    continue
                name = d.get("market_hash_name")
                if not name:
                    continue
                # 跳过不可交易/箱子等无 market_hash_name 的; amount 通常是 "1"
                try:
                    qty = int(a.get("amount") or 1)
                except (TypeError, ValueError):
                    qty = 1
                aggregated[name] += qty

            total_seen += len(assets)
            if total_count is None:
                total_count = data.get("total_inventory_count")
            start_assetid = data.get("last_assetid")
            if not start_assetid or (total_count and total_seen >= total_count):
                break
            time.sleep(0.5)  # 礼貌限速,降低 429 概率
    finally:
        client.close()

    # 空库存(账号确实没有 CS2 物品)不是错误,返回空列表让端点报 imported=0
    return [{"market_hash_name": n, "quantity": q} for n, q in aggregated.items()]


def _get_with_retry(client: httpx.Client, url: str, params: dict) -> dict:
    """带 429 退避重试的 GET。非 429 的失败按状态码分类抛异常。"""
    last_exc: SteamError | None = None
    for attempt in range(4):
        try:
            r = client.get(url, params=params, headers=HEADERS)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                last_exc = SteamRateLimited("Steam 限流(429),请稍后再试或提供 cookie")
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise last_exc
            if r.status_code in (401, 403):
                raise SteamPrivate("库存私有或被拒(403/401),请提供 steamLoginSecure cookie")
            if r.status_code == 404:
                raise SteamNotFound("账号不存在或无该库存(404)")
            # 其他:尝试解析 JSON 错误,否则按限流重试一次
            try:
                data = r.json()
                if data.get("success") is False:
                    return data  # 让上层按 success=false 处理(私有等)
                return data
            except Exception:
                last_exc = SteamError(f"Steam 返回 {r.status_code}")
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise last_exc
        except httpx.TransportError as e:
            # Steam 对匿名访问会断连(10054)/SSL 握手超时/读超时——都是 2026 年 Steam
            # 限制匿名库存访问的表现。重试无意义 → 立即提示需 steamLoginSecure cookie。
            raise SteamPrivate(
                f"Steam 拒绝访问或超时(匿名受限),请填写 steamLoginSecure cookie 后重试 ({e.__class__.__name__})"
            )
        except httpx.HTTPError as e:
            last_exc = SteamError(f"网络错误: {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise last_exc
    raise last_exc or SteamError("未知错误")
