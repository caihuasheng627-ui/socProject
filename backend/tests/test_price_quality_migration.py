import math
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.modules.pop("config", None)

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


def test_repair_endpoint_price_outliers_fixes_latest_spike_and_clears_predictions():
    from database import repair_endpoint_price_outliers

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
            raw_price REAL,
            is_outlier INTEGER DEFAULT 0,
            outlier_reason TEXT,
            UNIQUE (skin_id, date)
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY,
            skin_id INTEGER NOT NULL,
            horizon INTEGER NOT NULL
        );
        INSERT INTO skins(id, market_hash_name)
        VALUES (1, 'Desert Eagle | Heat Treated (Field-Tested)');
        INSERT INTO predictions(skin_id, horizon) VALUES (1, 7);
        """
    )
    for day in range(1, 15):
        conn.execute(
            """INSERT INTO price_history(
                   skin_id, date, price, daily_volume, raw_price, is_outlier
               ) VALUES (1, ?, 2.2, 10, 2.2, 0)""",
            (f"2026-07-{day:02d}",),
        )
    conn.execute(
        """INSERT INTO price_history(
               skin_id, date, price, daily_volume, raw_price, is_outlier
           ) VALUES (1, '2026-07-15', 5.8, 10, 5.8, 0)"""
    )
    conn.commit()

    result = repair_endpoint_price_outliers(conn)
    latest = conn.execute(
        """SELECT price, raw_price, is_outlier, outlier_reason
           FROM price_history WHERE date='2026-07-15'"""
    ).fetchone()
    pred_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]

    assert result["items"] == 1
    assert result["rows"] >= 1
    assert latest["raw_price"] == 5.8
    assert latest["is_outlier"] == 1
    assert latest["outlier_reason"] == "endpoint_price_spike"
    assert abs(latest["price"] - 2.2) < 1e-9
    assert pred_count == 0
    # 幂等
    assert repair_endpoint_price_outliers(conn) == {
        "items": 0, "rows": 0, "outliers": 0
    }


def test_prediction_cache_migration_adds_identity_and_drops_legacy_rows():
    from database import migrate_prediction_cache_contract

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY,
            skin_id INTEGER,
            horizon INTEGER,
            model TEXT,
            current_price REAL,
            generated_at TEXT,
            expires_at TEXT
        );
        INSERT INTO predictions VALUES (
            1, 1, 7, 'LSTM', 100.0,
            '2026-07-24T00:00:00+00:00',
            '2026-07-24T06:00:00+00:00'
        );
        """
    )

    migrate_prediction_cache_contract(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
    assert {"decision_date", "model_version", "data_through"} <= columns
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


def test_prediction_cache_migration_is_idempotent():
    from database import migrate_prediction_cache_contract

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE predictions (
               id INTEGER PRIMARY KEY,
               decision_date TEXT,
               model_version TEXT,
               data_through TEXT
           )"""
    )

    migrate_prediction_cache_contract(conn)
    migrate_prediction_cache_contract(conn)

    columns = [row[1] for row in conn.execute("PRAGMA table_info(predictions)")]
    assert columns.count("decision_date") == 1
    assert columns.count("model_version") == 1
    assert columns.count("data_through") == 1
