"""Tests for prune_short_history helpers."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from prune_short_history import prune


def _make_db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE skins (
            id INTEGER PRIMARY KEY,
            market_hash_name TEXT NOT NULL,
            source TEXT
        );
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY,
            skin_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            price REAL NOT NULL,
            UNIQUE(skin_id, date)
        );
        INSERT INTO skins VALUES (1, 'Long Skin', 'buff');
        INSERT INTO skins VALUES (2, 'Short Buff', 'buff');
        INSERT INTO skins VALUES (3, 'Short CSV', 'csv');
        """
    )
    for i in range(61):
        day = i + 1
        month = 1 if day <= 31 else 2
        dom = day if day <= 31 else day - 31
        conn.execute(
            "INSERT INTO price_history(skin_id, date, price) VALUES (1, ?, 1.0)",
            (f"2026-{month:02d}-{dom:02d}",),
        )
    conn.execute(
        "INSERT INTO price_history(skin_id, date, price) VALUES (2, '2026-07-25', 2.0)"
    )
    conn.execute(
        "INSERT INTO price_history(skin_id, date, price) VALUES (3, '2026-07-25', 3.0)"
    )
    conn.commit()
    conn.close()
    return path


def test_prune_removes_short_history_for_all_sources(tmp_path):
    db = _make_db(tmp_path)
    result = prune(min_days=61, source="all", db_path=db, dry_run=False)
    assert result["pruned"] == 2
    assert result["with_data"] == 1

    conn = sqlite3.connect(db)
    names = {
        r[0]
        for r in conn.execute(
            """SELECT s.market_hash_name FROM skins s
               WHERE EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id)"""
        )
    }
    assert names == {"Long Skin"}
    # skin rows retained
    assert conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0] == 3
    conn.close()


def test_prune_source_filter_keeps_csv_short(tmp_path):
    db = _make_db(tmp_path)
    result = prune(min_days=61, source="buff", db_path=db, dry_run=False)
    assert result["pruned"] == 1
    conn = sqlite3.connect(db)
    names = {
        r[0]
        for r in conn.execute(
            """SELECT s.market_hash_name FROM skins s
               WHERE EXISTS (SELECT 1 FROM price_history p WHERE p.skin_id=s.id)"""
        )
    }
    assert names == {"Long Skin", "Short CSV"}
    conn.close()
