"""
SkinVision AI — BUFF 实时价格爬虫(组员 3)
==========================================
从 BUFF 采集 800 件目标饰品的 180 天价格历史,写入 price_history 表。
滚动窗口:每次采集把超过 BUFF_HISTORY_DAYS 的旧数据删掉,保留最近窗口,作为预测/训练数据源。

设计:
  - 分批(BUFF_BATCH_SIZE,默认 50)+ 断点续传(跳过近 BUFF_REFRESH_HOURS 内已采集的)
  - 礼貌限速(BUFF_REQUEST_DELAY,默认 1.5s)+ 3 次重试 + 指数退避
  - 搜索 goods_id → 拉 price_history(days=180)→ 按 天 聚合(每天取最后一条)→ upsert
  - daily_volume 用搜索结果里的 sell_num(挂单数)作流动性代理
  - Cookie 从 .env 读(BUFF_COOKIE),不进仓库

运行:
  python scraper_buff.py                  # 增量(跳过近 6h 内已采集)
  python scraper_buff.py --force          # 强制全量重采
  python scraper_buff.py --limit 20       # 只跑前 20 件(测试用)
  python scraper_buff.py --batch 0 50     # 跑第 0~50 件(分批)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

# Windows 控制台默认 GBK,打印 StatTrak™ 的 ™ 等字符会 UnicodeEncodeError 崩溃。
# 强制 stdout/stderr 走 UTF-8(失败用 ? 替代,绝不因编码崩)。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx
import pandas as pd

from config import (
    BUFF_BASE_URL, BUFF_COOKIE, BUFF_HISTORY_DAYS, BUFF_REFRESH_HOURS,
    BUFF_REQUEST_DELAY, BUFF_BATCH_SIZE,
)
from database import get_connection, _utcnow

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://buff.163.com/market/csgo",
}

# 限流/鉴权失败时抛出,主流程捕获后立即整批中止(避免继续猛打加重封禁)
class RateLimited(Exception):
    """BUFF 返回 Action Forbidden / 限频,session 被风控。"""

class AuthFailed(Exception):
    """BUFF 返回 Login Required,cookie 失效。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _classify_buff_code(data: dict):
    """把 BUFF 返回的 code 分类:OK / 限流 / 鉴权失败 / 可重试。
    返回 ('ok'|'rate_limited'|'auth_failed'|'retry', code_str)"""
    code = data.get("code")
    if code == "OK":
        return "ok", code
    if code in ("Action Forbidden", "Too Many Requests", "Frequent Request"):
        return "rate_limited", code
    if code in ("Login Required", "Please Login", "Not Login"):
        return "auth_failed", code
    return "retry", code  # System Error 等其他非 OK,可重试


# ============================================================
# BUFF 单步:搜索 goods_id
# ============================================================
def search_goods_id(client: httpx.Client, name: str) -> tuple[int | None, dict | None]:
    """返回 (goods_id, item_info)。只认 market_hash_name 精确匹配(避免抓到 StatTrak/错磨损变体)。
    BUFF 限频时返回非 OK code,按可重试处理。"""
    query = name[:60]  # BUFF 搜索串过长不认
    for attempt in range(4):
        try:
            r = client.get(
                f"{BUFF_BASE_URL}/api/market/goods",
                params={"game": "csgo", "page_num": 1, "page_size": 20, "search": query},
                headers=HEADERS,
            )
            data = r.json()
            kind, code = _classify_buff_code(data)
            if kind == "ok":
                items = data["data"].get("items", []) or []
                for it in items:
                    if it.get("market_hash_name") == name:
                        return it["id"], it
                return None, None   # 精确匹配不存在,不取近似(避免 StatTrak 串价)
            if kind == "rate_limited":
                raise RateLimited(f"搜索限流 code={code}(item={name[:40]})")
            if kind == "auth_failed":
                raise AuthFailed(f"cookie 失效 code={code}")
            # retry(System Error 等)→ 退避重试
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
            else:
                return None, None
        except (RateLimited, AuthFailed):
            raise   # 直接上抛,主流程整批中止
        except Exception as e:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"    SEARCH ERROR: {e}")
                return None, None
    return None, None


# ============================================================
# BUFF 单步:拉 180 天价格历史
# ============================================================
def fetch_price_history(client: httpx.Client, goods_id: int, days: int) -> list[tuple[str, float]]:
    """返回 [(date_str, price), ...]。按天聚合(每天取最后一条)。"""
    for attempt in range(3):
        try:
            r = client.get(
                f"{BUFF_BASE_URL}/api/market/goods/price_history",
                params={"game": "csgo", "goods_id": goods_id, "days": days},
                headers=HEADERS,
            )
            data = r.json()
            kind, code = _classify_buff_code(data)
            if kind == "ok":
                history = data["data"]["price_history"]
                if not history:
                    return []
                # history = [[ts_ms, price], ...];按天聚合
                day_map: dict[str, float] = {}
                for ts, price in history:
                    d = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                    day_map[d] = float(price)
                return sorted(day_map.items())
            if kind == "rate_limited":
                raise RateLimited(f"历史接口限流 code={code}(goods_id={goods_id})")
            if kind == "auth_failed":
                raise AuthFailed(f"cookie 失效 code={code}")
            return []
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                print(f"    HISTORY ERROR: {e}")
                return []
    return []


# ============================================================
# 写库 + 滚动清理
# ============================================================
def upsert_price_history(skin_id: int, rows: list[tuple[str, float]],
                         sell_num: int, window_days: int) -> int:
    """upsert 价格;删超过 window_days 的旧数据。返回写入条数。"""
    if not rows:
        return 0
    with get_connection() as conn:
        for date_str, price in rows:
            conn.execute(
                """INSERT INTO price_history(skin_id, date, price, daily_volume)
                   VALUES (?,?,?,?)
                   ON CONFLICT(skin_id, date) DO UPDATE SET price=excluded.price, daily_volume=excluded.daily_volume""",
                (skin_id, date_str, round(price, 4), sell_num),
            )
        # 滚动:删超过 window_days 的旧数据
        conn.execute(
            "DELETE FROM price_history WHERE skin_id=? AND date < date('now', ?)",
            (skin_id, f"-{window_days} days"),
        )
        conn.commit()
    return len(rows)


# ============================================================
# 待采物品列表(断点续传)
# ============================================================
def get_pending_items(force: bool, limit: int | None) -> list[dict]:
    """返回 [{id, market_hash_name}, ...]。
    force=False:跳过已有价格数据的(断点续传);force=True:全量重采。"""
    with get_connection() as conn:
        if force:
            rows = conn.execute(
                "SELECT id, market_hash_name FROM skins WHERE source='buff' ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.id, s.market_hash_name FROM skins s
                   WHERE s.source='buff'
                     AND NOT EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id)
                   ORDER BY s.id"""
            ).fetchall()
        items = [{"id": r["id"], "name": r["market_hash_name"]} for r in rows]
    if limit:
        items = items[:limit]
    return items


# ============================================================
# 主流程
# ============================================================
def scrape_buff(force: bool = False, limit: int | None = None,
                start: int = 0, end: int | None = None) -> dict:
    if not BUFF_COOKIE:
        print("[scraper] ⚠ 未配置 BUFF_COOKIE,无法爬取(见 .env)")
        return {"error": "no cookie"}

    items = get_pending_items(force=force, limit=None)
    if end is not None:
        items = items[start:end]
    elif start:
        items = items[start:]
    if limit:
        items = items[:limit]

    print(f"[scraper] 待采: {len(items)} 件 | cookie: {BUFF_COOKIE[:20]}... | 窗口: {BUFF_HISTORY_DAYS}天")
    if not items:
        print("[scraper] 无待采物品(全部近期已采)。--force 可强制重采。")
        return {"scraped": 0, "skipped": 0, "failed": 0}

    client = httpx.Client(timeout=25, follow_redirects=True)
    client.cookies.set("session", BUFF_COOKIE, domain="buff.163.com")

    scraped = skipped = failed = 0
    stopped_reason = None
    t0 = time.time()
    for i, it in enumerate(items, 1):
        name = it["name"]
        try:
            gid, info = search_goods_id(client, name)
            if not gid:
                print(f"[{i}/{len(items)}] NOT FOUND: {name[:45]}")
                failed += 1
                time.sleep(BUFF_REQUEST_DELAY)
                continue
            sell_num = int(info.get("sell_num") or 0) if info else 0
            time.sleep(BUFF_REQUEST_DELAY)
            rows = fetch_price_history(client, gid, BUFF_HISTORY_DAYS)
            if not rows:
                print(f"[{i}/{len(items)}] EMPTY HISTORY: {name[:45]}")
                failed += 1
                time.sleep(BUFF_REQUEST_DELAY)
                continue
            n = upsert_price_history(it["id"], rows, sell_num, BUFF_HISTORY_DAYS)
            scraped += 1
            if i % 10 == 0 or i == len(items):
                print(f"[{i}/{len(items)}] ✓ {name[:40]} → {n} 天 | 累计采 {scraped} 跳 {skipped} 败 {failed} | {time.time()-t0:.0f}s")
        except RateLimited as e:
            # ⛔ 限流:立即整批中止,不再继续(继续只会加重封禁)。已采数据已落库。
            print(f"\n[scraper] ⛔ 限流,停止采集: {e}")
            print(f"[scraper] 进度 {i}/{len(items)} | 已采 {scraped} / 败 {failed} / 剩 {len(items)-i} | 耗时 {time.time()-t0:.0f}s")
            stopped_reason = "rate_limited"
            break
        except AuthFailed as e:
            print(f"\n[scraper] 🔑 cookie 失效,停止采集: {e}")
            print(f"[scraper] 进度 {i}/{len(items)} | 已采 {scraped} / 败 {failed} / 剩 {len(items)-i} | 耗时 {time.time()-t0:.0f}s")
            stopped_reason = "auth_failed"
            break
        except Exception as e:
            print(f"[{i}/{len(items)}] ERROR {name[:40]}: {e}")
            failed += 1
        time.sleep(BUFF_REQUEST_DELAY)

    if stopped_reason:
        print(f"\n[scraper] ⛔ 提前停止({stopped_reason}): 采 {scraped} / 跳 {skipped} / 败 {failed} | 耗时 {time.time()-t0:.0f}s")
    else:
        print(f"\n[scraper] 完成: 采 {scraped} / 跳 {skipped} / 败 {failed} | 耗时 {time.time()-t0:.0f}s")
    return {"scraped": scraped, "skipped": skipped, "failed": failed,
            "total": len(items), "stopped": stopped_reason}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="强制全量重采(跳过断点续传)")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 件")
    p.add_argument("--start", type=int, default=0, help="起始偏移(分批)")
    p.add_argument("--end", type=int, default=None, help="结束偏移(分批)")
    args = p.parse_args()
    result = scrape_buff(force=args.force, limit=args.limit, start=args.start, end=args.end)
    print(result)


if __name__ == "__main__":
    main()
