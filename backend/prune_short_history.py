"""
prune_short_history.py — 清理 price_history 中数据不足的饰品(组员 3)
=========================================================================
删除 distinct 日期数 < MIN_DAYS(默认 61) 的饰品的 price_history。

为什么只删 price_history、保留 skin 行:
  import_catalog_800() 启动时按 market_hash_name 用 INSERT OR IGNORE 重新插入 skin 行,
  所以删 skin 行会被重新导回;真正让一件饰品"不可用"的是删掉它的 price_history——
  /api/skins 用 EXISTS(price_history) 过滤,删后该件不可见、不参与预测,等同于移出数据集。
  skin 行作为目录条目保留,后续若有更好数据源可重新填充。

用法:
  py prune_short_history.py                       # 默认 MIN_DAYS=61, 全部 source
  py prune_short_history.py --source buff         # 只清 buff
  py prune_short_history.py --db seed/skinvision.db
  py prune_short_history.py --dry-run
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

# Windows GBK 控制台打印含 StatTrak™ 等字符会崩,强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _connect(db_path: Path | None) -> sqlite3.Connection:
    if db_path is not None:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    from database import get_connection
    return get_connection()


def prune(
    *,
    min_days: int = 61,
    source: str | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    owns = db_path is not None
    conn = _connect(db_path)
    try:
        params: list = []
        source_clause = ""
        if source and source.lower() not in {"", "all", "*"}:
            source_clause = "WHERE s.source = ?"
            params.append(source)
        params.append(min_days)

        rows = conn.execute(
            f"""SELECT s.id, s.market_hash_name, s.source,
                       COUNT(DISTINCT p.date) AS days
                FROM skins s
                JOIN price_history p ON p.skin_id = s.id
                {source_clause}
                GROUP BY s.id
                HAVING days < ?
                ORDER BY days ASC, s.market_hash_name""",
            params,
        ).fetchall()

        label = source or "all"
        print(f"[prune] db={db_path or 'runtime'} source={label} | 天数 < {min_days}: {len(rows)}")
        if not rows:
            print("[prune] 无需清理。")
            return {"pruned": 0, "rows_deleted": 0, "with_data": _with_data(conn)}

        buckets = {"<30": 0, "30-60": 0}
        by_source: dict[str, int] = {}
        for r in rows:
            buckets["<30" if r["days"] < 30 else "30-60"] += 1
            by_source[r["source"] or "?"] = by_source.get(r["source"] or "?", 0) + 1
        print(f"[prune]   其中 <30天 {buckets['<30']} 件, 30-60天 {buckets['30-60']} 件")
        print(f"[prune]   按 source: {by_source}")
        print("[prune] 最短 10 件:")
        for r in rows[:10]:
            print(f"    {r['days']:3}天  [{r['source']}]  {r['market_hash_name']}")

        if dry_run:
            print("[prune] --dry-run, 未删除。")
            return {
                "pruned": 0,
                "rows_deleted": 0,
                "candidates": len(rows),
                "with_data": _with_data(conn),
            }

        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        before = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        conn.execute(
            f"DELETE FROM price_history WHERE skin_id IN ({placeholders})", ids
        )
        try:
            conn.execute(
                f"DELETE FROM predictions WHERE skin_id IN ({placeholders})", ids
            )
        except Exception:
            pass
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        with_data = _with_data(conn)
        print(f"\n[prune] 已删除 {len(ids)} 件的 price_history")
        print(f"[prune] price_history 行数: {before} -> {after} (删 {before - after})")
        print(f"[prune] 剩余有数据的饰品: {with_data}")
        return {
            "pruned": len(ids),
            "rows_deleted": before - after,
            "with_data": with_data,
        }
    finally:
        if owns:
            conn.close()


def _with_data(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT skin_id) FROM price_history"
    ).fetchone()[0]


def main():
    p = argparse.ArgumentParser(description="清理 price_history 中数据不足的饰品")
    p.add_argument("--min", type=int, default=61, dest="min_days",
                   help="天数阈值,distinct 日期数 < 该值的件被清理(默认 61)")
    p.add_argument(
        "--source",
        default="all",
        help="只清理该 source;默认 all(含 buff/csv)。刷新误补短序列时需清全部。",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="指定 sqlite 路径(如 seed/skinvision.db);默认 runtime DB",
    )
    p.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = p.parse_args()
    prune(
        min_days=args.min_days,
        source=args.source,
        db_path=args.db,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
