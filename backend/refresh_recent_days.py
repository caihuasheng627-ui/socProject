"""
刷新 seed DB 的最近几天(组员3, 2026-07-27)
==========================================
为 Expo(7/28) 把 price_history 窗口向前滚 5 天:
  - 用皮肤名走 BUFF 搜索 goods_id → 拉 price_history → 取 7/23–7/27 共 5 天真实价 upsert
  - 对每件**成功补到新数据**的皮肤, 删除它最早的等量天数 → 该皮肤行数不变
  - 失败的皮肤(NOT FOUND / EMPTY) 不动, 最后汇总报告
  - 只处理已有足够历史(默认 ≥61 天)的皮肤,绝不给空/短序列目录件新建行情
  - 断点续传: 目标日期全部齐了才跳过;缺 7/26–7/27 仍会补
  - 限流 / cookie 失效立即整批停止(已采数据已按皮肤落库, 总量仍守恒)

直接操作 backend/seed/skinvision.db(git 跟踪的种子库, Docker 首启灌 volume 的源头),
不碰 runtime 库(backend/data/, 本地不存在)。复用 scraper_buff 的 BUFF 请求逻辑。

运行:
  py refresh_recent_days.py              # 全量
  py refresh_recent_days.py --limit 20   # 只跑前 20 件(测试)
  py refresh_recent_days.py --min-days 61
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
# 与 prune_short_history 对齐: 不足该天数的件不参与刷新,避免空目录被补成 1–4 天假可用
DEFAULT_MIN_HISTORY_DAYS = 61


def _existing_dates(conn: sqlite3.Connection, skin_id: int) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT date FROM price_history WHERE skin_id=?", (skin_id,)
        )
    }


def _is_complete(existing: set[str]) -> bool:
    return TARGET_SET.issubset(existing)


def run(
    limit: int | None = None,
    start: int = 0,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
) -> dict:
    if not BUFF_COOKIE:
        print("[refresh] ⚠ 未配置 BUFF_COOKIE(见 backend/.env), 退出")
        return {"error": "no_cookie"}

    conn = sqlite3.connect(str(SEED_DB), timeout=30)
    conn.row_factory = sqlite3.Row

    # 只处理「已有足够历史」且「目标窗未齐」的件:
    #   - 跳过空目录 / 短序列(曾被 prune,只留 skin 行)
    #   - 跳过目标 5 天已齐全的件
    skins = conn.execute(
        """SELECT s.id, s.market_hash_name, s.source,
                  COUNT(DISTINCT p.date) AS days
           FROM skins s
           JOIN price_history p ON p.skin_id = s.id
           GROUP BY s.id
           HAVING days >= ?
           ORDER BY s.id""",
        (min_history_days,),
    ).fetchall()
    # 再滤掉目标日期已齐全的
    pending = []
    for sk in skins:
        existing = _existing_dates(conn, sk["id"])
        if _is_complete(existing):
            continue
        pending.append(sk)
    skins = pending
    if start:
        skins = skins[start:]
    if limit:
        skins = skins[:limit]

    print(
        f"[refresh] 待处理 {len(skins)} 件 | 目标 {TARGET_DATES[0]}~{TARGET_DATES[-1]} "
        f"| min_history_days={min_history_days} | 延时 {BUFF_REQUEST_DELAY}s"
    )

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

            existing = _existing_dates(conn, sid)
            if len(existing) < min_history_days:
                skipped += 1
                continue
            if _is_complete(existing):
                skipped += 1
                continue

            try:
                gid, _ = search_goods_id(client, name)
                time.sleep(BUFF_REQUEST_DELAY)
                if not gid:
                    notfound += 1
                    failed.append((sid, name, "NOT_FOUND"))
                    if i % 50 == 0 or i == len(skins):
                        print(
                            f"[{i}/{len(skins)}] NOT FOUND: {name[:42]} | "
                            f"ok={ok} 败={notfound+empty+err} | {time.time()-t0:.0f}s"
                        )
                    continue
                rows = fetch_price_history(client, gid, DAYS_FETCH)
                # 只补缺的目标日;已有的不覆盖删除逻辑的守恒前提
                missing = TARGET_SET - existing
                new_rows = [(d, p) for d, p in rows if d in missing]
                if not new_rows:
                    empty += 1
                    failed.append((sid, name, "EMPTY"))
                    continue
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
                # 防御: 不删目标日期本身
                earliest = [d for d in earliest if d not in TARGET_SET]
                for d in earliest:
                    conn.execute(
                        "DELETE FROM price_history WHERE skin_id=? AND date=?",
                        (sid, d),
                    )
                conn.commit()
                added_total += added
                deleted_total += len(earliest)
                ok += 1
                if i % 25 == 0 or i == len(skins):
                    print(
                        f"[{i}/{len(skins)}] ✓ {name[:38]} +{added} -{len(earliest)} | "
                        f"ok={ok} 跳={skipped} 败={notfound+empty+err} | "
                        f"+{added_total}/-{deleted_total} | {time.time()-t0:.0f}s"
                    )
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
    print(
        f"\n[refresh] {'⛔提前停止(' + stopped + ')' if stopped else '完成'}: "
        f"ok={ok} 跳过={skipped} | NOT_FOUND={notfound} EMPTY={empty} ERR={err} | "
        f"+{added_total}行 -{deleted_total}行 | 耗时 {time.time()-t0:.0f}s"
    )
    if failed:
        out = Path(__file__).resolve().parent / "refresh_failed.log"
        with out.open("w", encoding="utf-8") as f:
            f.write(f"# refresh_recent_days 失败清单 ({len(failed)} 件)\n")
            for sid, name, reason in failed:
                f.write(f"{sid}\t{reason}\t{name}\n")
        print(f"[refresh] 失败清单 → {out.name}")

    conn2 = sqlite3.connect(str(SEED_DB))
    total_now = conn2.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    mn, mx = conn2.execute("SELECT MIN(date), MAX(date) FROM price_history").fetchone()
    with_data = conn2.execute(
        "SELECT COUNT(DISTINCT skin_id) FROM price_history"
    ).fetchone()[0]
    conn2.close()
    print(f"[refresh] 当前 price_history: {total_now} 行 | 日期 {mn} ~ {mx} | 有行情 {with_data} 件")
    return {
        "ok": ok,
        "skipped": skipped,
        "not_found": notfound,
        "empty": empty,
        "err": err,
        "added": added_total,
        "deleted": deleted_total,
        "total_rows": total_now,
        "with_data": with_data,
        "stopped": stopped,
        "failed_count": total_fail,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 件(测试)")
    p.add_argument("--start", type=int, default=0, help="起始偏移(分批)")
    p.add_argument(
        "--min-days",
        type=int,
        default=DEFAULT_MIN_HISTORY_DAYS,
        help="至少已有多少天历史才允许刷新(默认 61,防空目录回潮)",
    )
    args = p.parse_args()
    run(limit=args.limit, start=args.start, min_history_days=args.min_days)


if __name__ == "__main__":
    main()
