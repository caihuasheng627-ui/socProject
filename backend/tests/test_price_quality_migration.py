import math
import sqlite3

from database import migrate_price_history_quality


def make_legacy_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE skins (
            id INTEGER PRIMARY KEY,
            market_hash_name TEXT NOT NULL
        );
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY,
            skin_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            price REAL NOT NULL,
            daily_volume INTEGER DEFAULT 0,
            UNIQUE (skin_id, date)
        );
        INSERT INTO skins(id, market_hash_name)
        VALUES (1, 'M4A1-S | Printstream (Factory New)');
        INSERT INTO price_history(id, skin_id, date, price, daily_volume) VALUES
            (1, 1, '2026-05-26', 626.20, 540),
            (2, 1, '2026-05-27', 0.64, 540),
            (3, 1, '2026-05-28', 677.71, 540);
        """
    )
    return conn


def test_migration_preserves_raw_price_and_replaces_effective_price():
    conn = make_legacy_database()

    result = migrate_price_history_quality(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(price_history)")}
    assert {"raw_price", "is_outlier", "outlier_reason"} <= columns
    middle = conn.execute(
        "SELECT price, raw_price, is_outlier, outlier_reason FROM price_history WHERE id=2"
    ).fetchone()
    assert middle["raw_price"] == 0.64
    assert math.isclose(middle["price"], math.sqrt(626.2 * 677.71), rel_tol=1e-9)
    assert middle["is_outlier"] == 1
    assert middle["outlier_reason"] == "isolated_price_spike"
    assert result == {"items": 1, "rows": 3, "outliers": 1}


def test_migration_is_idempotent():
    conn = make_legacy_database()
    migrate_price_history_quality(conn)
    first = [tuple(row) for row in conn.execute("SELECT * FROM price_history ORDER BY id")]

    result = migrate_price_history_quality(conn)
    second = [tuple(row) for row in conn.execute("SELECT * FROM price_history ORDER BY id")]

    assert result == {"items": 0, "rows": 0, "outliers": 0}
    assert second == first


def test_migration_can_reclean_from_preserved_raw_prices():
    conn = make_legacy_database()
    migrate_price_history_quality(conn)

    result = migrate_price_history_quality(conn, force=True)

    middle = conn.execute(
        "SELECT raw_price, is_outlier FROM price_history WHERE id=2"
    ).fetchone()
    assert result == {"items": 1, "rows": 3, "outliers": 1}
    assert middle["raw_price"] == 0.64
    assert middle["is_outlier"] == 1
