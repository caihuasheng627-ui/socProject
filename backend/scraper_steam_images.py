"""
CSVest — Steam 饰品主图爬虫(组员 3)
====================================
为 skins 表里有价格历史的可见饰品(约 681 件)从 Steam Community Market 抓取
饰品主图,把 Steam CDN 的 economy/image base URL 写入 skins.image_url。

设计要点(已实测验证):
  - Steam listings 页 <head> 第 588 字节处有 <link rel="preload" as="image">
    指向饰品主图,只需流式读前 ~4KB,不必下整页 4.9MB。
  - market_hash_name 已带 Steam 正确前缀(StatTrak™ / ★ ),urllib.parse.quote
    后直接请求即可,无需前缀修补。
  - 存 base URL(不带尺寸后缀),前端拼 /360fx360f 显示。
  - 礼貌限速(--delay,默认 3s)+ 429/5xx 指数退避(最多 4 次)。
  - 断点续传:只抓 image_url 为空的件,每件抓到立即 UPDATE 提交。
  - 404 / 无匹配 → 记 miss 并继续(image_url 留空,前端回落 emoji)。
  - Cookie 可选:读 .env 的 STEAM_COOKIE(steamLoginSecure),有则 429 显著减少。

运行:
  python scraper_steam_images.py --limit 20    # 先跑 20 件验证 429/miss 比例
  python scraper_steam_images.py               # 全量(可中断重跑,自动续传)
  python scraper_steam_images.py --delay 5     # 加大限速
  python scraper_steam_images.py --retry 6     # 加大重试次数
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from urllib.parse import quote

# Windows 控制台默认 GBK,打印 StatTrak™ / ★ 等字符会 UnicodeEncodeError 崩溃。
# 强制 stdout/stderr 走 UTF-8(失败用 ? 替代,绝不因编码崩)。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

from config import STEAM_COOKIE
from database import get_connection, migrate_add_user_columns

# listings 页 <head> 里的主图 preload:
#   <link rel="preload" as="image" href="https://community.steamstatic.com/economy/image/<hash>..."/>
# 只取 base URL(到 hash 为止,不含 query/后缀),前端再拼 /360fx360f。
IMG_RE = re.compile(
    r"https://community\.steamstatic\.com/economy/image/[-A-Za-z0-9_]+"
)

LISTINGS_URL = "https://steamcommunity.com/market/listings/730/{name}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 读前 8KB 足矣(主图在第 588 字节);留余量应对 Steam 偶发改版。
READ_BYTES = 8192


def _cookie_header() -> str | None:
    """把 .env 的 STEAM_COOKIE(steamLoginSecure=...)拼成 Cookie 头。"""
    c = STEAM_COOKIE.strip()
    if not c:
        return None
    # 用户可能填了整段 cookie 或只填 steamLoginSecure 的值
    if c.startswith("steamLoginSecure=") or "=" in c:
        return c
    return f"steamLoginSecure={c}"


def _request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    retries: int,
) -> httpx.Response | None:
    """非流式 GET,429/5xx 指数退避重试。"""
    headers = dict(HEADERS)
    ck = _cookie_header()
    if ck:
        headers["Cookie"] = ck

    for attempt in range(retries + 1):
        try:
            r = client.get(url, headers=headers, timeout=30.0)
            if r.status_code in (429,) or r.status_code >= 500:
                if attempt < retries:
                    time.sleep(min(2 ** attempt * 3, 30))
                    continue
            return r
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt < retries:
                time.sleep(min(2 ** attempt * 3, 30))
                continue
            print(f"    [net-err] {type(e).__name__}: {e}")
            return None
    return None


def _from_listings(client: httpx.Client, market_hash_name: str, *, retries: int) -> str | None:
    """从 listings 页 <head> 的 preload 抽主图。无活跃挂单的件此页不内嵌图 → 返回 None。"""
    url = LISTINGS_URL.format(name=quote(market_hash_name, safe=""))
    try:
        with client.stream("GET", url, headers=dict(HEADERS), timeout=30.0) as r:
            if r.status_code != 200 or r.status_code == 404:
                return None
            buf = b""
            for chunk in r.iter_bytes(chunk_size=2048):
                buf += chunk
                if len(buf) >= READ_BYTES:
                    break
            m = IMG_RE.search(buf.decode("utf-8", errors="replace"))
            return m.group(0) if m else None
    except (httpx.TransportError, httpx.TimeoutException):
        return None


SEARCH_URL = "https://steamcommunity.com/market/search/render?norender=1&count=10&appid=730&query={name}"


def _from_search(client: httpx.Client, market_hash_name: str, *, retries: int) -> str | None:
    """fallback:search/render 返回 asset_description.icon_url,按 name 精确匹配。

    listings 页无内嵌图(无活跃挂单)的件用此法;JSON 小(~2KB)且直接给 icon hash。
    """
    url = SEARCH_URL.format(name=quote(market_hash_name, safe=""))
    r = _request_with_retry(client, url, retries=retries)
    if r is None or r.status_code != 200:
        return None
    try:
        j = r.json()
    except Exception:
        return None
    for res in j.get("results", []) or []:
        ad = res.get("asset_description") or {}
        if ad.get("name") == market_hash_name and ad.get("icon_url"):
            return f"https://community.steamstatic.com/economy/image/{ad['icon_url']}"
    return None


def fetch_image_url(
    client: httpx.Client,
    market_hash_name: str,
    *,
    retries: int,
) -> str | None:
    """取饰品主图 base URL。先试 listings 页 <head> preload,无图再 fallback 到 search/render。"""
    url = _from_listings(client, market_hash_name, retries=retries)
    if url:
        return url
    return _from_search(client, market_hash_name, retries=retries)


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 Steam 饰品主图写入 skins.image_url")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 件(0=全量)")
    ap.add_argument("--delay", type=float, default=3.0, help="每件间隔秒数(默认 3)")
    ap.add_argument("--retries", type=int, default=4, help="429/5xx 重试次数(默认 4)")
    ap.add_argument("--log-every", type=int, default=10, help="每 N 件打印一次进度")
    args = ap.parse_args()

    # 确保 image_url 列存在(幂等)
    migrate_add_user_columns()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, market_hash_name FROM skins s
            WHERE EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id = s.id)
              AND (s.image_url IS NULL OR s.image_url = '')
            ORDER BY s.id
            """
        ).fetchall()

    total = len(rows)
    if args.limit > 0:
        rows = rows[: args.limit]
    n = len(rows)
    print(f"待抓: {n} 件(可见且未抓图;DB 中共 {total} 件待补)")
    print(f"限速: {args.delay}s/件 | 重试: {args.retries} | Cookie: {'有' if _cookie_header() else '无'}")
    if n == 0:
        print("无待抓件,退出。")
        return 0

    ok = miss = 0
    misses: list[str] = []
    t0 = time.time()
    # 连接复用 + HTTP/2 关(Steam 用 1.1 即可,避免 h2 握手开销)
    with httpx.Client(http2=False, follow_redirects=True) as client:
        for i, row in enumerate(rows, 1):
            skin_id, name = row["id"], row["market_hash_name"]
            url = fetch_image_url(client, name, retries=args.retries)
            if url:
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE skins SET image_url = ? WHERE id = ?", (url, skin_id)
                    )
                    conn.commit()
                ok += 1
            else:
                miss += 1
                misses.append(name)

            if i % args.log_every == 0 or i == n:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (n - i) / rate if rate > 0 else 0
                print(
                    f"  [{i}/{n}] ok={ok} miss={miss} "
                    f"({rate:.2f}件/s, ETA {eta/60:.1f}min) "
                    f"last={'OK ' + name[:30] if url else 'MISS ' + name[:30]}"
                )

            if i < n:
                time.sleep(args.delay)

    print(f"\n完成: 成功 {ok} / 失败 {miss} / 共 {n} 件,用时 {(time.time()-t0)/60:.1f}min")
    if misses:
        print(f"失败件({len(misses)}):")
        for m in misses[:50]:
            print(f"  - {m}")
        if len(misses) > 50:
            print(f"  ... 及另外 {len(misses)-50} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
