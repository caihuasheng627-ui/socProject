"""
补齐每件饰品日期范围内的内部缺口：
- 缺口 ≤2 天：线性插值
- 缺口 >2 天：前向填充（保留最近有效价格，模拟非交易日持价）
- 首日之前不补（不延伸范围外）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from database import get_connection


INTERP_MAX_GAP = 2  # ≤2 天用线性插值，超过用前向填充


def fill_skin_gaps(skin_id: int) -> int:
    """补齐单件饰品内部日期缺口，返回插入行数。"""
    with get_connection() as conn:
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
        date_is_outlier = {}
        date_reason = {}
        for date_str, price, raw_price, is_outlier, reason in rows:
            date_price[date_str] = price
            date_raw[date_str] = raw_price if raw_price else price
            date_is_outlier[date_str] = is_outlier
            date_reason[date_str] = reason

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
                elif prev_ds:
                    # 前向填充
                    fill_price = date_price[prev_ds]
                    fill_raw = date_raw.get(prev_ds, fill_price)
                else:
                    # 后向填充（理论上不会出现，首日之前不补）
                    fill_price = date_price[next_ds]
                    fill_raw = date_raw.get(next_ds, fill_price)

                inserts.append((skin_id, ds, fill_price, 0, fill_raw, 0, "interpolated"))

            cursor += timedelta(days=1)

        if inserts:
            conn.executemany(
                """INSERT OR IGNORE INTO price_history
                   (skin_id, date, price, daily_volume, raw_price, is_outlier, outlier_reason)
                   VALUES (?,?,?,?,?,?,?)""",
                inserts,
            )
            conn.commit()
        return len(inserts)


def main():
    with get_connection() as conn:
        skin_ids = [
            r[0] for r in conn.execute(
                """SELECT skin_id FROM price_history
                   GROUP BY skin_id HAVING COUNT(DISTINCT date) >= 61"""
            ).fetchall()
        ]

    print(f"[补缺] {len(skin_ids)} 件饰品, 阈值 ≤{INTERP_MAX_GAP}天插值, >{INTERP_MAX_GAP}天前向填充")

    total_inserted = 0
    for i, sid in enumerate(skin_ids, 1):
        n = fill_skin_gaps(sid)
        total_inserted += n
        if i % 50 == 0 or i == len(skin_ids):
            print(f"[{i}/{len(skin_ids)}] 已处理, 累计补入 {total_inserted} 行")

    print(f"\n[补缺] 完成: {len(skin_ids)} 件 → 补入 {total_inserted} 行")


if __name__ == "__main__":
    main()
