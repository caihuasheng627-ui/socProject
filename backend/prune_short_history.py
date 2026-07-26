"""
prune_short_history.py — 清理 price_history 中数据不足的饰品(组员 3)
=========================================================================
删除 source='buff' 且 distinct 日期数 < MIN_DAYS(默认 61) 的饰品的 price_history。

为什么只删 price_history、保留 skin 行:
  import_catalog_800() 启动时按 market_hash_name 用 INSERT OR IGNORE 重新插入 skin 行,
  所以删 skin 行会被重新导回;真正让一件饰品"不可用"的是删掉它的 price_history——
  /api/skins 用 EXISTS(price_history) 过滤,删后该件不可见、不参与预测,等同于移出数据集。
  skin 行作为目录条目保留,后续若有更好数据源可重新填充。

用法:
  py prune_short_history.py                # 默认 MIN_DAYS=61, source=buff
  py prune_short_history.py --min 61       # 指定阈值
  py prune_short_history.py --dry-run      # 只统计不删除
"""
from __future__ import annotations

import argparse
import sys
import io

# Windows GBK 控制台打印含 StatTrak™ 等字符会崩,强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from database import get_connection


def main():
    p = argparse.ArgumentParser(description="清理 price_history 中数据不足的饰品")
    p.add_argument("--min", type=int, default=61, dest="min_days",
                   help="天数阈值,distinct 日期数 < 该值的件被清理(默认 61)")
    p.add_argument("--source", default="buff", help="只清理该 source 的饰品(默认 buff)")
    p.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = p.parse_args()

    with get_connection() as conn:
        rows = conn.execute(
            """SELECT s.id, s.market_hash_name, COUNT(DISTINCT p.date) days
               FROM skins s JOIN price_history p ON p.skin_id = s.id
               WHERE s.source = ?
               GROUP BY s.id
               HAVING days < ?""",
            (args.source, args.min_days),
        ).fetchall()

        print(f"[prune] source={args.source} | 天数 < {args.min_days} 的件数: {len(rows)}")
        if not rows:
            print("[prune] 无需清理。")
            return

        # 天数分布
        buckets = {"<30": 0, "30-60": 0}
        for r in rows:
            buckets["<30" if r["days"] < 30 else "30-60"] += 1
        print(f"[prune]   其中 <30天 {buckets['<30']} 件, 30-60天 {buckets['30-60']} 件")
        print("[prune] 最短 10 件:")
        for r in sorted(rows, key=lambda x: x["days"])[:10]:
            print(f"    {r['days']:3}天  {r['market_hash_name']}")

        if args.dry_run:
            print("[prune] --dry-run, 未删除。")
            return

        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))

        before = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        conn.execute(
            f"DELETE FROM price_history WHERE skin_id IN ({placeholders})", ids
        )
        # 同步清掉这些件的预测缓存(若有该表)
        try:
            conn.execute(
                f"DELETE FROM predictions WHERE skin_id IN ({placeholders})", ids
            )
        except Exception:
            pass
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        with_data = conn.execute(
            "SELECT COUNT(DISTINCT skin_id) FROM price_history"
        ).fetchone()[0]
        print(f"\n[prune] 已删除 {len(ids)} 件的 price_history")
        print(f"[prune] price_history 行数: {before} -> {after} (删 {before - after})")
        print(f"[prune] 剩余有数据的饰品: {with_data}")


if __name__ == "__main__":
    main()
