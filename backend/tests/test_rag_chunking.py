"""RAG 分块 / 扩充知识库 / 关键词不零分灌水。"""
from __future__ import annotations

import rag


def test_kb_expanded_beyond_legacy_six():
    assert len(rag.KB) >= 20
    keys = " ".join(item["k"] for item in rag.KB).lower()
    for needle in ("fade", "doppler", "trade up", "buff", "case", "float"):
        assert needle in keys


def test_chunk_text_respects_size_and_overlap():
    body = "这是一句测试。" * 80  # ~560+ chars
    chunks = rag.chunk_text(body, chunk_size=200, overlap=40)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 + 40 for c in chunks)
    # 相邻块应有重叠痕迹
    assert chunks[0][-20:] in chunks[1] or any(
        chunks[0][-10:] in chunks[1] for _ in [0]
    )


def test_chunk_text_short_stays_single():
    assert rag.chunk_text("短文本无需切分", chunk_size=700, overlap=120) == ["短文本无需切分"]


def test_collect_docs_chunks_long_news(monkeypatch):
    long_summary = ("Valve 更新影响市场流动性。" * 40)

    class FakeRow(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

        def keys(self):
            return dict.keys(self)

    row = FakeRow({
        "id": 99,
        "title": "CS2 更新与饰品供给",
        "summary": long_summary,
        "source": "valve",
        "published_at": "2026-07-01T00:00:00+00:00",
        "sentiment": "neutral",
        "url": "https://example.com/n",
    })

    class FakeConn:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return [row]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rag, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(rag, "RAG_CHUNK_SIZE", 180)
    monkeypatch.setattr(rag, "RAG_CHUNK_OVERLAP", 40)
    monkeypatch.setattr(rag, "RAG_NEWS_LIMIT", 50)

    docs = rag._collect_docs()
    news_docs = [d for d in docs if d["type"] == "news"]
    assert len(news_docs) >= 2
    assert all(d["parent_uid"] == "news:99" for d in news_docs)
    assert len(rag.KB) <= len([d for d in docs if d["type"] == "kb"]) + 5


def test_keyword_retrieve_does_not_pad_zero_score(monkeypatch):
    class FakeRow(dict):
        def keys(self):
            return dict.keys(self)

    unrelated = FakeRow({
        "id": 1,
        "title": "Unrelated sports trivia",
        "summary": "football basketball scoreboard",
        "source": "reddit",
        "published_at": "2026-07-01T00:00:00+00:00",
        "sentiment": "neutral",
        "url": "",
    })

    class FakeConn:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return [unrelated]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rag, "get_connection", lambda: FakeConn())
    sources = rag._retrieve_keyword("完全无关的宇宙射线问题 xyzzy", kb_k=3, news_k=5)
    assert sources == [] or all(float(s["score"]) > 0 for s in sources)
    assert not any(s.get("type") == "news" and float(s["score"]) <= 0 for s in sources)


def test_keyword_retrieve_hits_expanded_kb(monkeypatch):
    class FakeConn:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rag, "get_connection", lambda: FakeConn())
    sources = rag._retrieve_keyword("Doppler 蓝宝石 Phase 怎么定价", kb_k=3, news_k=2)
    assert sources
    assert any(s["type"] == "kb" for s in sources)
    assert all(float(s["score"]) >= 1 for s in sources)
