"""Unit tests for multi-platform live price scraper (mocked HTTP)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from platforms import (  # noqa: E402
    BuffClient,
    Quote,
    SkinportClient,
    SteamClient,
    parse_money,
)
from fetch_live_prices import (  # noqa: E402
    quotes_to_rows,
    resolve_names,
    write_csv,
)


def test_parse_money():
    assert parse_money("$1,234.56") == 1234.56
    assert parse_money("¥89.1") == 89.1
    assert parse_money(12) == 12.0
    assert parse_money(None) is None
    assert parse_money("") is None


def test_skinport_fetch_quotes():
    payload = [
        {
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "currency": "USD",
            "min_price": 28.42,
            "median_price": 35.91,
            "mean_price": 57.86,
            "suggested_price": 41.42,
            "max_price": 100.0,
            "quantity": 84,
            "item_page": "https://skinport.com/item/x",
        },
        {
            "market_hash_name": "Other Item",
            "currency": "USD",
            "min_price": 1.0,
            "quantity": 1,
        },
    ]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = httpx.Client(transport=transport)
    sp = SkinportClient(client=client)
    quotes = sp.fetch_quotes(["AK-47 | Redline (Field-Tested)", "Missing Item"])
    assert quotes[0].ok and quotes[0].price == 28.42
    assert quotes[0].volume == 84
    assert quotes[0].platform == "skinport"
    assert not quotes[1].ok and quotes[1].error == "NOT_FOUND"
    sp.close()


def test_buff_requires_session():
    buff = BuffClient(session="")
    q = buff.fetch_one("AK-47 | Redline (Field-Tested)")
    assert not q.ok
    assert "BUFF_SESSION" in (q.error or "")
    buff.close()


def test_buff_fetch_one_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if "goods/sell_order" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": "OK",
                    "data": {
                        "total_count": 12,
                        "goods": {
                            "market_hash_name": "AK-47 | Redline (Field-Tested)",
                            "sell_min_price": "210.5",
                            "buy_max_price": "200.0",
                            "sell_num": 12,
                        },
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": "OK",
                "data": {
                    "items": [
                        {
                            "id": 12345,
                            "market_hash_name": "AK-47 | Redline (Field-Tested)",
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    buff = BuffClient(
        client=client, session="fake-session", usd_cny_rate=7.0, request_interval=0
    )
    q = buff.fetch_one("AK-47 | Redline (Field-Tested)")
    assert q.ok
    assert q.price_native == 210.5
    assert abs(q.price - round(210.5 / 7.0, 4)) < 1e-9
    assert q.volume == 12
    buff.close()


def test_steam_fetch_one_ok():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "lowest_price": "$29.41",
                "volume": "1,234",
                "median_price": "$30.12",
            },
        )
    )
    client = httpx.Client(transport=transport)
    steam = SteamClient(client=client, request_interval=0, max_retries=1)
    q = steam.fetch_one("AK-47 | Redline (Field-Tested)")
    assert q.ok and q.price == 29.41 and q.volume == 1234
    steam.close()


def test_write_csv_and_resolve_names(tmp_path: Path):
    args = argparse.Namespace(items=["A", "B", "C"], from_csv=None, limit=2)
    assert resolve_names(args) == ["A", "B"]

    rows = quotes_to_rows(
        [
            Quote(
                market_hash_name="A",
                platform="skinport",
                currency="USD",
                price=1.5,
                volume=3,
            )
        ]
    )
    out = tmp_path / "q.csv"
    write_csv(out, rows, append=False)
    write_csv(out, rows, append=True)
    text = out.read_text(encoding="utf-8")
    assert text.count("skinport") == 2
    assert "market_hash_name" in text.splitlines()[0]
