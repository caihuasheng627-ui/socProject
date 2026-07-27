"""
刷新 seed DB 的最近几天(组员3, 2026-07-27)
==========================================
为 Expo(7/28) 把 price_history 窗口向前滚 5 天:
  - 用皮肤名走 BUFF 搜索 goods_id → 拉 price_history → 取 7/23–7/27 共 5 天真实价 upsert
  - 对每件**成功补到新数据**的皮肤, 删除它最早的等量天数 → 该皮肤行数不变
  - 失败的皮肤(NOT FOUND / EMPTY) 不动, 保持原 1/25–7/22, 最后汇总报告
  - 断点续传: 已有 7/23+ 数据的皮肤跳过(避免重跑重复删最早几天)
  - 限流 / cookie 失效立即整批停止(已采数据已按皮肤落库, 总量仍守恒)

直接操作 backend/seed/skinvision.db(git 跟踪的种子库, Docker 首启灌 volume 的源头),
不碰 runtime 库(backend/data/, 本地不存在)。复用 scraper_buff 的 BUFF 请求逻辑。

运行:
  py refresh_recent_days.py              # 全量
  py refresh_recent_days.py --limit 20   # 只跑前 20 件(测试)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

# Windows 控制台 GBK, 打印 StatTrak™ 的 ™ 会崩, 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx

from config import BUFF_COOKIE, BUFF_REQUEST_DELAY
from scraper_buff import search_goods_id, fetch_price_history, RateLimited, AuthFailed

# 直接写 seed 库(runtime 库 backend/data/ 本地不存在, 且 seed 才是 Docker 灌 volume 的源头)
SEED_DB = Path(__file__).resolve().parent / "seed" / "skinvision.db"

# 要补的目标 5 天(今天 7/27, 数据停在 7/22, 缺口 7/23–7/27)
TARGET_DATES = ["2026-07-23", "2026-07-24", "2026-07-25", "2026-07-26", "2026-07-27"]
TARGET_SET = set(TARGET_DATES)
# 拉 180 天历史(BUFF 可能返回更早的, 无所谓, 只取目标 5 天); 与原 scraper 一致
DAYS_FETCH = 180
# 已有该日期视作已刷新, 跳过(断点续传阈值)
RESUME_THRESHOLD = "2026-07-23"


def run(limit: int | None = None, start: int = 0) -> dict:
    if not BUFF_COOKIE:
        print("[refresh] ⚠ 未配置 BUFF_COOKIE(见 backend/.env), 退出")
        return {"error": "no_cookie"}

    conn = sqlite3.connect(str(SEED_DB), timeout=30)
    conn.row_factory = sqlite3.Row

    # 只处理"有历史数据但还没刷新到 7/23+"的件:
    #   跳过 304 已完成(max>=7/23)、跳过 196 无历史件(贵刀/手套,BUFF 本就无数据,试也白试)
    skins = conn.execute(
        """SELECT s.id, s.market_hash_name, s.source FROM skins s
           WHERE EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id)
             AND NOT EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id AND p.date>='2026-07-23')
           ORDER BY s.id"""
    ).fetchall()
    if start:
        skins = skins[start:]
    if limit:
        skins = skins[:limit]

    print(f"[refresh] 待处理 {len(skins)} 件 | 目标日期 {TARGET_DATES[0]}~{TARGET_DATES[-1]} | 延时 {BUFF_REQUEST_DELAY}s")

    client = httpx.Client(timeout=25, follow_redirects=True)
    client.cookies.set("session", BUFF_COOKIE, domain="buff.163.com")

    ok = skipped = notfound = empty = err = 0
    added_total = deleted_total = 0
    failed: list[tuple] = []
    t0 = time.time()
    stopped = None

    try:
        for i, sk in enumerate(skins, 1):
            sid = sk["id"]
            name = sk["market_hash_name"]

            # 断点续传: 已有 7/23+ 数据 → 跳过(避免重跑重复删最早几天)
            maxdate = conn.execute(
                "SELECT MAX(date) FROM price_history WHERE skin_id=?", (sid,)
            ).fetchone()[0]
            if maxdate and maxdate >= RESUME_THRESHOLD:
                skipped += 1
                continue

            try:
                gid, _ = search_goods_id(client, name)
                time.sleep(BUFF_REQUEST_DELAY)
                if not gid:
                    notfound += 1
                    failed.append((sid, name, "NOT_FOUND"))
                    if i % 50 == 0 or i == len(skins):
                        print(f"[{i}/{len(skins)}] NOT FOUND: {name[:42]} | ok={ok} 败={notfound+empty+err} | {time.time()-t0:.0f}s")
                    continue
                rows = fetch_price_history(client, gid, DAYS_FETCH)
                new_rows = [(d, p) for d, p in rows if d in TARGET_SET]
                if not new_rows:
                    empty += 1
                    failed.append((sid, name, "EMPTY"))
                    continue
                # upsert 目标 5 天(最新真实价, raw, 不标 outlier; init 时 endpoint 修复兜底)
                for d, p in new_rows:
                    price = round(float(p), 4)
                    conn.execute(
                        """INSERT INTO price_history(
                               skin_id, date, price, daily_volume, raw_price, is_outlier, outlier_reason
                           ) VALUES(?,?,?,?,?,?,NULL)
                           ON CONFLICT(skin_id, date) DO UPDATE SET
                               price=excluded.price,
                               daily_volume=0,
                               raw_price=excluded.raw_price,
                               is_outlier=0,
                               outlier_reason=NULL""",
                        (sid, d, price, 0, price, 0),
                    )
                # 删除该皮肤最早的 added 天 → 该皮肤行数守恒
                added = len(new_rows)
                earliest = [
                    r["date"]
                    for r in conn.execute(
                        "SELECT date FROM price_history WHERE skin_id=? ORDER BY date ASC LIMIT ?",
                        (sid, added),
                    )
                ]
                # 防御: 不删目标日期本身(理论上 earliest 是 1 月, 不会撞 7 月)
                earliest = [d for d in earliest if d not in TARGET_SET]
                for d in earliest:
                    conn.execute(
                        "DELETE FROM price_history WHERE skin_id=? AND date=?", (sid, d)
                    )
                conn.commit()
                added_total += added
                deleted_total += len(earliest)
                ok += 1
                if i % 25 == 0 or i == len(skins):
                    print(f"[{i}/{len(skins)}] ✓ {name[:38]} +{added} -{len(earliest)} | ok={ok} 跳={skipped} 败={notfound+empty+err} | +{added_total}/-{deleted_total} | {time.time()-t0:.0f}s")
            except RateLimited as e:
                print(f"\n[refresh] ⛔ 限流, 整批停止: {e}")
                stopped = "rate_limited"
                break
            except AuthFailed as e:
                print(f"\n[refresh] 🔑 cookie 失效, 整批停止: {e}")
                stopped = "auth_failed"
                break
            except Exception as e:
                err += 1
                failed.append((sid, name, f"ERR:{e}"))
            finally:
                time.sleep(BUFF_REQUEST_DELAY)
    finally:
        conn.close()
        client.close()

    total_fail = notfound + empty + err
    print(f"\n[refresh] {'⛔提前停止(' + stopped + ')' if stopped else '完成'}: "
          f"ok={ok} 跳过={skipped} | NOT_FOUND={notfound} EMPTY={empty} ERR={err} | "
          f"+{added_total}行 -{deleted_total}行 | 耗时 {time.time()-t0:.0f}s")
    if failed:
        # 把失败清单落盘, 方便回顾
        out = Path(__file__).resolve().parent / "refresh_failed.log"
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"# refresh_recent_days 失败清单 ({len(failed)} 件)\n")
            for sid, name, reason in failed:
                f.write(f"{sid}\t{reason}\t{name}\n")
        print(f"[refresh] 失败清单 → {out.name}")

    # 末尾再核对一次总量
    conn2 = sqlite3.connect(str(SEED_DB))
    total_now = conn2.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    mn, mx = conn2.execute("SELECT MIN(date), MAX(date) FROM price_history").fetchone()
    conn2.close()
    print(f"[refresh] 当前 price_history: {total_now} 行 | 日期 {mn} ~ {mx}")
    return {
        "ok": ok, "skipped": skipped, "not_found": notfound, "empty": empty,
        "err": err, "added": added_total, "deleted": deleted_total,
        "total_rows": total_now, "stopped": stopped, "failed_count": total_fail,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 件(测试)")
    p.add_argument("--start", type=int, default=0, help="起始偏移(分批)")
    args = p.parse_args()
    run(limit=args.limit, start=args.start)


if __name__ == "__main__":
    main()
