"""
临时脚本：爬取 train/val/test 那 154 件饰品的 BUFF 近 180 天价格。
运行后 price_history 表会同时有 CSV 旧数据(2019-2023)和 BUFF 新数据(2026)。
"""
import sys, time
sys.path.insert(0, ".")

from scraper_buff import (
    search_goods_id, fetch_price_history, upsert_price_history,
    RateLimited, AuthFailed, HEADERS,
)
from config import BUFF_COOKIE, BUFF_BASE_URL, BUFF_HISTORY_DAYS, BUFF_REQUEST_DELAY
from database import get_connection
import httpx

client = httpx.Client(timeout=25, follow_redirects=True)
client.cookies.set("session", BUFF_COOKIE, domain="buff.163.com")

# 取 source='csv' 的所有饰品
with get_connection() as conn:
    rows = conn.execute(
        "SELECT id, market_hash_name FROM skins WHERE source='csv' ORDER BY id"
    ).fetchall()
    items = [{"id": r["id"], "name": r["market_hash_name"]} for r in rows]

print(f"共 {len(items)} 件待爬 | cookie: {BUFF_COOKIE[:20]}... | 窗口: {BUFF_HISTORY_DAYS}天")

scraped = failed = 0
t0 = time.time()

for i, it in enumerate(items, 1):
    name = it["name"]
    try:
        gid, info = search_goods_id(client, name)
        if not gid:
            print(f"[{i}/{len(items)}] NOT FOUND: {name[:50]}")
            failed += 1
            time.sleep(BUFF_REQUEST_DELAY)
            continue
        sell_num = int(info.get("sell_num") or 0) if info else 0
        time.sleep(BUFF_REQUEST_DELAY)
        rows_data = fetch_price_history(client, gid, BUFF_HISTORY_DAYS)
        if not rows_data:
            print(f"[{i}/{len(items)}] EMPTY: {name[:50]}")
            failed += 1
            time.sleep(BUFF_REQUEST_DELAY)
            continue
        n = upsert_price_history(it["id"], rows_data, sell_num, BUFF_HISTORY_DAYS)
        scraped += 1
        if i % 10 == 0 or i == len(items):
            print(f"[{i}/{len(items)}] OK {name[:40]} → {n}天 | 已采{scraped} 败{failed} | {time.time()-t0:.0f}s")
    except RateLimited as e:
        print(f"\n⛔ 限流 @ {i}/{len(items)}: {e}")
        break
    except AuthFailed as e:
        print(f"\n🔑 Cookie失效 @ {i}/{len(items)}: {e}")
        break
    except Exception as e:
        print(f"[{i}/{len(items)}] ERR {name[:40]}: {e}")
        failed += 1
    time.sleep(BUFF_REQUEST_DELAY)

print(f"\n完成: 采{scraped} 败{failed} | 耗时 {time.time()-t0:.0f}s")
