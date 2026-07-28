"""
补齐每件饰品日期范围内的内部缺口，并可选把尾部对齐到统一截止日期：
- 缺口 ≤2 天：线性插值
- 缺口 >2 天：前向填充（保留最近有效价格，模拟非交易日持价）
- 首日之前不补（不延伸范围外）
- --extend-to：把每件最新日向前填到目标日（增量采集未跑完时对齐监控池）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


INTERP_MAX_GAP = 2  # ≤2 天用线性插值，超过用前向填充


def _connect(db_path: Path | None) -> sqlite3.Connection:
    if db_path is not None:
        conn = sqlite3.connect(str(db_path), timeout=30)
        return conn
    from database import get_connection
    return get_connection()


def fill_skin_gaps(conn: sqlite3.Connection, skin_id: int) -> int:
    """补齐单件饰品内部日期缺口，返回插入行数。"""
    rows = conn.execute(
        """SELECT date, price, raw_price, is_outlier, outlier_reason
           FROM price_history WHERE skin_id=? ORDER BY date""",
        (skin_id,),
    ).fetchall()

    if len(rows) < 2:
        return 0

    # 用 price（已清洗价）作为主价；raw_price 同步填充
    date_price = {}
    date_raw = {}
    for date_str, price, raw_price, _is_outlier, _reason in rows:
        date_price[date_str] = price
        date_raw[date_str] = raw_price if raw_price else price

    all_dates = sorted(date_price.keys())
    min_date = all_dates[0]
    max_date = all_dates[-1]

    inserts = []
    cursor = datetime.strptime(min_date, "%Y-%m-%d")
    end = datetime.strptime(max_date, "%Y-%m-%d")

    while cursor <= end:
        ds = cursor.strftime("%Y-%m-%d")
        if ds not in date_price:
            # 找前后最近的有效日期
            prev_ds = None
            next_ds = None
            for d in all_dates:
                if d < ds:
                    prev_ds = d
                if d > ds and next_ds is None:
                    next_ds = d

            gap = 999
            if prev_ds and next_ds:
                gap = (datetime.strptime(next_ds, "%Y-%m-%d")
                       - datetime.strptime(prev_ds, "%Y-%m-%d")).days

            if prev_ds and next_ds and gap <= INTERP_MAX_GAP + 1:
                # 线性插值
                prev_dt = datetime.strptime(prev_ds, "%Y-%m-%d")
                next_dt = datetime.strptime(next_ds, "%Y-%m-%d")
                cur_dt = datetime.strptime(ds, "%Y-%m-%d")
                total_days = (next_dt - prev_dt).days
                cur_days = (cur_dt - prev_dt).days
                w_next = cur_days / total_days
                w_prev = 1 - w_next

                fill_price = round(date_price[prev_ds] * w_prev + date_price[next_ds] * w_next, 4)
                fill_raw = round((date_raw.get(prev_ds, date_price[prev_ds]) * w_prev
                                  + date_raw.get(next_ds, date_price[next_ds]) * w_next), 4)
                reason = "interpolated"
            elif prev_ds:
                # 前向填充
                fill_price = date_price[prev_ds]
                fill_raw = date_raw.get(prev_ds, fill_price)
                reason = "forward_fill"
            else:
                # 后向填充（理论上不会出现，首日之前不补）
                fill_price = date_price[next_ds]
                fill_raw = date_raw.get(next_ds, fill_price)
                reason = "back_fill"

            inserts.append((skin_id, ds, fill_price, 0, fill_raw, 0, reason))

        cursor += timedelta(days=1)

    if inserts:
        conn.executemany(
            """INSERT OR IGNORE INTO price_history
               (skin_id, date, price, daily_volume, raw_price, is_outlier, outlier_reason)
               VALUES (?,?,?,?,?,?,?)""",
            inserts,
        )
    return len(inserts)


def extend_skin_to_date(conn: sqlite3.Connection, skin_id: int, target_date: str) -> int:
    """把单件最新日向前填到 target_date（含），用最近收盘价持平。"""
    row = conn.execute(
        """SELECT date, price, raw_price FROM price_history
           WHERE skin_id=? ORDER BY date DESC LIMIT 1""",
        (skin_id,),
    ).fetchone()
    if not row:
        return 0

    last_date, last_price, last_raw = row
    if last_date >= target_date:
        return 0

    last_raw = last_raw if last_raw is not None else last_price
    inserts = []
    cursor = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
    end = datetime.strptime(target_date, "%Y-%m-%d")
    while cursor <= end:
        ds = cursor.strftime("%Y-%m-%d")
        inserts.append((skin_id, ds, last_price, 0, last_raw, 0, "trailing_fill"))
        cursor += timedelta(days=1)

    if inserts:
        conn.executemany(
            """INSERT OR IGNORE INTO price_history
               (skin_id, date, price, daily_volume, raw_price, is_outlier, outlier_reason)
               VALUES (?,?,?,?,?,?,?)""",
            inserts,
        )
    return len(inserts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill internal gaps and optional trailing dates")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="sqlite 路径(如 seed/skinvision.db);默认 runtime DB",
    )
    parser.add_argument(
        "--extend-to",
        default=None,
        help="把每件最新日向前填到该日期(YYYY-MM-DD)，对齐增量未跑完的尾部",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=61,
        help="只处理至少有这么多交易日的饰品(默认 61)",
    )
    args = parser.parse_args()

    owns = args.db is not None
    conn = _connect(args.db)
    try:
        skin_ids = [
            r[0]
            for r in conn.execute(
                """SELECT skin_id FROM price_history
                   GROUP BY skin_id HAVING COUNT(DISTINCT date) >= ?""",
                (args.min_days,),
            ).fetchall()
        ]

        print(
            f"[补缺] db={args.db or 'runtime'} | {len(skin_ids)} 件 | "
            f"内部缺口 ≤{INTERP_MAX_GAP}天插值 / 更大前向填充"
            + (f" | 尾部对齐到 {args.extend_to}" if args.extend_to else "")
        )

        total_internal = 0
        total_trailing = 0
        for i, sid in enumerate(skin_ids, 1):
            n = fill_skin_gaps(conn, sid)
            total_internal += n
            if args.extend_to:
                total_trailing += extend_skin_to_date(conn, sid, args.extend_to)
            if i % 50 == 0 or i == len(skin_ids):
                print(
                    f"[{i}/{len(skin_ids)}] 内部+{total_internal} 尾部+{total_trailing}"
                )

        conn.commit()

        # 汇总最新日分布
        dist = conn.execute(
            """SELECT MAX(date) AS latest, COUNT(*) AS n
               FROM price_history GROUP BY skin_id"""
        ).fetchall()
        from collections import Counter
        c = Counter(r[0] for r in dist)
        print(f"\n[补缺] 完成: 内部 {total_internal} 行, 尾部 {total_trailing} 行")
        print("[补缺] 最新日分布:")
        for d, n in sorted(c.items()):
            print(f"  {d}: {n}")
    finally:
        if owns:
            conn.close()


if __name__ == "__main__":
    main()
