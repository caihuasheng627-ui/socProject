import math
import sqlite3
from datetime import datetime

import scraper_buff
from price_cleaning import aggregate_daily_prices, clean_price_points
from scraper_buff import fetch_price_history, upsert_price_history


def test_aggregate_daily_prices_uses_median_instead_of_last_value():
    rows = [
        ("2026-01-01", 100.0),
        ("2026-01-01", 900.0),
        ("2026-01-01", 110.0),
        ("2026-01-02", 120.0),
    ]

    assert aggregate_daily_prices(rows) == [
        ("2026-01-01", 110.0),
        ("2026-01-02", 120.0),
    ]


def test_clean_price_points_replaces_clear_isolated_low_spike():
    cleaned = clean_price_points(
        "M4A1-S | Printstream (Factory New)",
        [("2026-05-26", 626.2), ("2026-05-27", 0.64), ("2026-05-28", 677.71)],
    )

    middle = cleaned[1]
    assert middle.raw_price == 0.64
    assert middle.is_outlier is True
    assert middle.outlier_reason == "isolated_price_spike"
    assert math.isclose(middle.price, math.sqrt(626.2 * 677.71), rel_tol=1e-9)


def test_clean_price_points_keeps_move_when_neighbours_are_not_stable():
    cleaned = clean_price_points(
        "AK-47 | Redline (Field-Tested)",
        [("2026-01-01", 100.0), ("2026-01-02", 400.0), ("2026-01-03", 500.0)],
    )

    assert cleaned[1].price == 400.0
    assert cleaned[1].is_outlier is False


def test_pattern_finish_uses_stricter_threshold():
    cleaned = clean_price_points(
        "AK-47 | Case Hardened (Field-Tested)",
        [("2026-01-01", 100.0), ("2026-01-02", 700.0), ("2026-01-03", 105.0)],
    )

    assert cleaned[1].price == 700.0
    assert cleaned[1].is_outlier is False


def test_cheap_item_uses_stricter_threshold_but_still_removes_extreme_error():
    protected = clean_price_points(
        "Dual Berettas | Colony (Field-Tested)",
        [("2026-01-01", 0.1), ("2026-01-02", 0.8), ("2026-01-03", 0.1)],
    )
    extreme = clean_price_points(
        "Dual Berettas | Colony (Field-Tested)",
        [("2026-01-01", 0.03), ("2026-01-02", 1.7), ("2026-01-03", 0.03)],
    )

    assert protected[1].is_outlier is False
    assert extreme[1].is_outlier is True
    assert extreme[1].price == 0.03


def test_first_and_last_points_are_never_replaced_automatically():
    cleaned = clean_price_points(
        "USP-S | The Traitor (Factory New)",
        [("2026-01-01", 1.0), ("2026-01-02", 100.0)],
    )

    assert [point.price for point in cleaned] == [1.0, 100.0]
    assert not any(point.is_outlier for point in cleaned)


def test_adjacent_candidates_are_left_for_manual_review():
    cleaned = clean_price_points(
        "Dual Berettas | Contractor (Field-Tested)",
        [
            ("2026-03-15", 0.03),
            ("2026-03-16", 0.45),
            ("2026-03-17", 0.03),
            ("2026-03-18", 0.49),
        ],
    )

    assert [point.price for point in cleaned] == [0.03, 0.45, 0.03, 0.49]
    assert not any(point.is_outlier for point in cleaned)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *_args, **_kwargs):
        return FakeResponse(self.payload)


def test_fetch_price_history_reduces_intraday_samples_with_median():
    def ts(hour):
        return int(datetime(2026, 7, 20, hour).timestamp() * 1000)

    client = FakeClient({
        "code": "OK",
        "data": {"price_history": [[ts(8), 100.0], [ts(12), 110.0], [ts(18), 900.0]]},
    })

    assert fetch_price_history(client, goods_id=123, days=180) == [
        ("2026-07-20", 110.0)
    ]


def test_scraper_log_does_not_expose_cookie(monkeypatch, capsys):
    cookie = "secret-session-cookie-value-that-must-stay-private"
    monkeypatch.setattr(scraper_buff, "BUFF_COOKIE", cookie)
    monkeypatch.setattr(
        scraper_buff, "get_pending_items", lambda force, limit: []
    )

    result = scraper_buff.scrape_buff()
    output = capsys.readouterr().out

    assert result == {"scraped": 0, "skipped": 0, "failed": 0}
    assert cookie not in output
    assert cookie[:20] not in output


def test_upsert_preserves_raw_price_and_writes_cleaned_effective_price(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE skins (id INTEGER PRIMARY KEY, market_hash_name TEXT NOT NULL);
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
        INSERT INTO skins VALUES (1, 'M4A1-S | Printstream (Factory New)');
        """
    )
    monkeypatch.setattr("scraper_buff.get_connection", lambda: conn)

    upsert_price_history(
        1,
        [("2026-07-20", 100.0), ("2026-07-21", 1000.0), ("2026-07-22", 100.0)],
        sell_num=50,
        window_days=180,
    )

    middle = conn.execute(
        """SELECT price, raw_price, is_outlier, outlier_reason
           FROM price_history WHERE date='2026-07-21'"""
    ).fetchone()
    assert middle["price"] == 100.0
    assert middle["raw_price"] == 1000.0
    assert middle["is_outlier"] == 1
    assert middle["outlier_reason"] == "isolated_price_spike"
