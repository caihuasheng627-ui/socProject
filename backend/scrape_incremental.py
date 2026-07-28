"""
增量补缺：只拉每件饰品缺失的最近几天（到今天 2026-07-28），已有数据不重拉。
"""
from __future__ import annotations

import sys
import time
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

from config import BUFF_BASE_URL, BUFF_COOKIE, BUFF_HISTORY_DAYS, BUFF_REQUEST_DELAY
from database import get_connection
from scraper_buff import (
    search_goods_id, fetch_price_history, upsert_price_history,
    RateLimited, AuthFailed, HEADERS,
)

TARGET_DATE = "2026-07-28"


def get_skins_with_gap():
    """返回 [(skin_id, market_hash_name, gap_days), ...]，只取 ≥61 天且有缺口的饰品。"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.id, s.market_hash_name,
                   CAST(julianday(?) - julianday(MAX(ph.date)) AS INTEGER) AS gap_days
            FROM price_history ph
            JOIN skins s ON s.id = ph.skin_id
            WHERE ph.skin_id IN (
                SELECT skin_id FROM price_history
                GROUP BY skin_id HAVING COUNT(DISTINCT date) >= 61
            )
            GROUP BY ph.skin_id
            HAVING gap_days > 0
            ORDER BY gap_days
        """, (TARGET_DATE,)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def main():
    # 强制关闭输出缓冲，后台运行也能实时看到进度
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

    if not BUFF_COOKIE:
        print("[增量] ⚠ 未配置 BUFF_COOKIE")
        return

    items = get_skins_with_gap()
    print(f"[增量] 待补缺: {len(items)} 件 | 目标日期: {TARGET_DATE}")
    if not items:
        print("[增量] 全部已是最新，无需采集。")
        return

    client = httpx.Client(timeout=25, follow_redirects=True)
    client.cookies.set("session", BUFF_COOKIE, domain="buff.163.com")

    scraped = failed = skipped = 0
    t0 = time.time()

    for i, (skin_id, name, gap_days) in enumerate(items, 1):
        try:
            # 礼貌限速
            time.sleep(BUFF_REQUEST_DELAY)

            gid, _info = search_goods_id(client, name)
            if not gid:
                print(f"[{i}/{len(items)}] NOT FOUND: {name[:50]}")
                failed += 1
                continue

            time.sleep(BUFF_REQUEST_DELAY)
            rows = fetch_price_history(client, gid, days=gap_days)
            if not rows:
                skipped += 1
                if i % 20 == 0:
                    print(f"[{i}/{len(items)}] … 采 {scraped} 跳 {skipped} 败 {failed} | {time.time()-t0:.0f}s")
                continue

            upsert_price_history(skin_id, rows, BUFF_HISTORY_DAYS)
            scraped += 1

            if i % 20 == 0 or i == len(items):
                elapsed = time.time() - t0
                eta = (elapsed / i) * (len(items) - i) if scraped else 0
                print(f"[{i}/{len(items)}] ✓ {name[:35]} +{gap_days}天 → {len(rows)}条 | "
                      f"采{scraped} 跳{skipped} 败{failed} | {elapsed:.0f}s ETA{eta:.0f}s")

        except RateLimited as e:
            print(f"\n[增量] ⛔ 限流，停止: {e}")
            print(f"[增量] 进度 {i}/{len(items)} | 采{scraped} 败{failed} | {time.time()-t0:.0f}s")
            break
        except AuthFailed as e:
            print(f"\n[增量] 🔑 cookie 失效: {e}")
            break
        except Exception as e:
            print(f"[{i}/{len(items)}] ERROR {name[:40]}: {e}")
            failed += 1

    print(f"\n[增量] 完成: 采 {scraped} / 跳 {skipped} / 败 {failed} | 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
