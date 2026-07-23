"""
SkinVision AI — RAG 向量检索 + 解释
==================================
检索: 阿里云百炼 DashScope Embedding API → 余弦相似度 Top-K
生成: DeepSeek 根据检索片段生成带 [编号] 引用的答案

检索源:
  1. news 表(Valve/HLTV/Reddit/internal, RSS 增量补充)
  2. 内置 CS2 饰品市场知识片段
  3. (explain) 该饰品近 30 日价量行为

降级:
  - 无 DASHSCOPE_API_KEY / API 调用失败 → 关键词检索
  - LLM 不可用 → Mock 回答
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from typing import Any

import httpx
import numpy as np

import llm
from config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    RAG_EMBED_DIM,
    RAG_EMBED_ENABLED,
    RAG_EMBED_MODEL,
    RAG_INDEX_PATH,
    RAG_USE_VECTOR,
)
from database import get_connection, resolve_skin, latest_price, change_pct

# 内置知识库
KB: list[dict[str, str]] = [
    {"k": "major 赛事 sticker 涨价 流动性", "v": "Major 赛事前后 7-14 天,相关贴纸与饰品成交量通常上升 15-30%,但赛事结束后有回调压力。"},
    {"k": "磨损 wear factory new fn", "v": "同款饰品中 Factory New(FN)磨损最稀有,价格显著高于 Field-Tested(FT);磨损越低流动性往往越弱。"},
    {"k": "stattrak 计数", "v": "StatTrak™ 版本因计数器稀有度,价格通常为普通版 1.5-3 倍,但流通量更小。"},
    {"k": "地板价 floor price 箱子 case", "v": "部分低价饰品与箱子存在'地板价',跌破后跌不动,适合低风险短线;但上涨空间亦有限。"},
    {"k": "流动性 成交量 volume 稀有", "v": "高价值低流动性饰品(刀/手套)日内波动大,买卖价差宽,不适合大额短线。"},
    {"k": "valve 更新 沉浸 贴图 重做", "v": "Valve 武器贴图/磨损重做会改变供给预期,相关饰品短期价格波动加大。"},
]

# ============================================================
# 关键词降级(向量模型不可用时)
# ============================================================
_SPLIT_RE = re.compile(r"[\s,，。;；:：!！?？、()（）\[\]【】\"'`/\\|-]+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokens(text: str) -> set[str]:
    t = (text or "").lower()
    toks = {w for w in _SPLIT_RE.split(t) if len(w) > 1}
    han = _HAN_RE.findall(t)
    for i in range(len(han) - 1):
        toks.add(han[i] + han[i + 1])
    return toks


def _kw_score(query_tokens: set[str], doc_text: str) -> int:
    if not query_tokens:
        return 0
    return len(query_tokens & _tokens(doc_text))


# ============================================================
# 阿里云 DashScope Embedding(OpenAI 兼容接口)
# ============================================================
_BATCH_SIZE = 10  # text-embedding-v3/v4 单批上限
_index_lock = threading.Lock()
_index_cache: dict[str, Any] | None = None
_index_fp: str | None = None
_embed_ok_logged = False


def vector_status() -> dict[str, Any]:
    """供 /api/health 或调试: 当前检索后端状态。"""
    ok = bool(RAG_EMBED_ENABLED)
    return {
        "mode": "vector" if ok else "keyword",
        "provider": "dashscope" if ok else None,
        "model": RAG_EMBED_MODEL if ok else None,
        "dim": RAG_EMBED_DIM if ok else None,
        "enabled": RAG_USE_VECTOR,
        "hasKey": bool(DASHSCOPE_API_KEY),
    }


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (vecs / norms).astype(np.float32)


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """调用 DashScope OpenAI 兼容 /embeddings 接口(单批 ≤10)。"""
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY missing")
    payload: dict[str, Any] = {
        "model": RAG_EMBED_MODEL,
        "input": texts,
        "encoding_format": "float",
    }
    # v3/v4 支持 dimensions
    if RAG_EMBED_MODEL.startswith("text-embedding-v"):
        payload["dimensions"] = RAG_EMBED_DIM

    url = f"{DASHSCOPE_BASE_URL}/embeddings"
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    items = data.get("data") or []
    # OpenAI 兼容: data[].embedding, 按 index 排序
    items = sorted(items, key=lambda x: int(x.get("index", 0)))
    if len(items) != len(texts):
        raise RuntimeError(f"embedding count mismatch: got {len(items)} want {len(texts)}")
    return [it["embedding"] for it in items]


def _embed_texts(texts: list[str]) -> np.ndarray:
    """批量向量化 + L2 归一化。无 Key 时抛错由上层降级。"""
    global _embed_ok_logged
    if not RAG_EMBED_ENABLED:
        raise RuntimeError("dashscope embedding disabled")
    if not texts:
        return np.zeros((0, RAG_EMBED_DIM), dtype=np.float32)

    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = [t if (t and t.strip()) else " " for t in texts[i:i + _BATCH_SIZE]]
        all_vecs.extend(_embed_batch(batch))

    if not _embed_ok_logged:
        print(f"[rag] DashScope embedding OK · model={RAG_EMBED_MODEL} dim={RAG_EMBED_DIM}")
        _embed_ok_logged = True

    return _l2_normalize(np.asarray(all_vecs, dtype=np.float32))


def _cosine_top(query_vec: np.ndarray, doc_vecs: np.ndarray, top_k: int) -> list[tuple[int, float]]:
    """已 L2 归一化向量 → 点积即余弦相似度。"""
    if doc_vecs.size == 0:
        return []
    sims = doc_vecs @ query_vec.reshape(-1)
    k = min(top_k, len(sims))
    idx = np.argpartition(-sims, k - 1)[:k]
    idx = idx[np.argsort(-sims[idx])]
    return [(int(i), float(sims[i])) for i in idx]


# ============================================================
# 文档语料 + 向量索引
# ============================================================
def _collect_docs() -> list[dict[str, Any]]:
    """统一语料: 知识库 + news 表。"""
    docs: list[dict[str, Any]] = []
    for i, item in enumerate(KB):
        text = f"{item['k']} {item['v']}"
        docs.append({
            "uid": f"kb:{i}",
            "type": "kb",
            "title": "CS2 市场知识库",
            "snippet": item["v"],
            "source": "内置知识库",
            "date": None,
            "sentiment": None,
            "text": text,
            "kb_item": item,
        })
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM news ORDER BY published_at DESC LIMIT 80"
        ).fetchall()
    for r in rows:
        text = f"{r['title']} {r['summary'] or ''}"
        docs.append({
            "uid": f"news:{r['id']}",
            "type": "news",
            "title": r["title"],
            "snippet": r["summary"],
            "source": r["source"],
            "date": r["published_at"],
            "sentiment": r["sentiment"],
            "text": text,
            "news_row": r,
        })
    return docs


def _fingerprint(docs: list[dict[str, Any]]) -> str:
    h = hashlib.sha1()
    h.update(f"{RAG_EMBED_MODEL}:{RAG_EMBED_DIM}".encode("utf-8"))
    for d in docs:
        h.update(d["uid"].encode("utf-8"))
        h.update((d.get("text") or "").encode("utf-8"))
    return h.hexdigest()[:16]


def _build_or_load_index() -> tuple[list[dict[str, Any]], np.ndarray]:
    """构建/加载向量索引(进程内缓存 + 磁盘 npz)。"""
    global _index_cache, _index_fp
    docs = _collect_docs()
    fp = _fingerprint(docs)

    with _index_lock:
        if _index_cache is not None and _index_fp == fp:
            return _index_cache["docs"], _index_cache["vectors"]

        # 尝试磁盘缓存
        if RAG_INDEX_PATH.exists():
            try:
                data = np.load(RAG_INDEX_PATH, allow_pickle=True)
                if str(data["fp"]) == fp:
                    vectors = np.asarray(data["vectors"], dtype=np.float32)
                    _index_cache = {"docs": docs, "vectors": vectors}
                    _index_fp = fp
                    print(f"[rag] 向量索引命中磁盘缓存 ({len(docs)} docs)")
                    return docs, vectors
            except Exception as e:
                print(f"[rag] 读向量缓存失败: {e}")

        # 重新编码
        texts = [d["text"] for d in docs]
        vectors = _embed_texts(texts)
        try:
            RAG_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                RAG_INDEX_PATH,
                fp=np.asarray(fp),
                vectors=vectors,
                uids=np.asarray([d["uid"] for d in docs], dtype=object),
            )
        except Exception as e:
            print(f"[rag] 写向量缓存失败: {e}")

        _index_cache = {"docs": docs, "vectors": vectors}
        _index_fp = fp
        print(f"[rag] 向量索引已重建 ({len(docs)} docs, dim={vectors.shape[1]})")
        return docs, vectors


def invalidate_index() -> None:
    """RSS 增量后可调用,强制下次重建。"""
    global _index_cache, _index_fp
    with _index_lock:
        _index_cache = None
        _index_fp = None
        try:
            if RAG_INDEX_PATH.exists():
                RAG_INDEX_PATH.unlink()
        except Exception:
            pass


# ============================================================
# 检索
# ============================================================
def _retrieve_vector(query: str, kb_k: int, news_k: int) -> list[dict[str, Any]]:
    docs, vectors = _build_or_load_index()
    qv = _embed_texts([query])[0]
    # 多取一些再按类型切分
    ranked = _cosine_top(qv, vectors, top_k=max(kb_k + news_k, 12))

    kb_hits: list[tuple[float, dict]] = []
    news_hits: list[tuple[float, dict]] = []
    for i, sim in ranked:
        d = docs[i]
        if d["type"] == "kb" and len(kb_hits) < kb_k:
            kb_hits.append((sim, d))
        elif d["type"] == "news" and len(news_hits) < news_k:
            news_hits.append((sim, d))
        if len(kb_hits) >= kb_k and len(news_hits) >= news_k:
            break

    # 资讯不足时按相似度补齐(含低分)
    if len(news_hits) < news_k:
        seen = {d["uid"] for _, d in news_hits}
        for i, sim in ranked:
            if len(news_hits) >= news_k:
                break
            d = docs[i]
            if d["type"] == "news" and d["uid"] not in seen:
                news_hits.append((sim, d))

    sources: list[dict[str, Any]] = []
    for sim, d in kb_hits + news_hits:
        sources.append({
            "type": d["type"],
            "title": d["title"],
            "snippet": d["snippet"],
            "source": d["source"],
            "date": d["date"],
            "sentiment": d.get("sentiment"),
            "score": round(float(sim), 4),
            "method": "vector",
        })

    # 余弦相似度通常在 [-1,1], 映射到 0-1 展示
    for i, x in enumerate(sources):
        x["id"] = i + 1
        x["relevance"] = round(max(0.0, min(1.0, (x["score"] + 1) / 2)), 2)
    # 再按原始相似度排序编号
    sources.sort(key=lambda s: -s["score"])
    for i, x in enumerate(sources):
        x["id"] = i + 1
    return sources


def _retrieve_keyword(query: str, kb_k: int, news_k: int) -> list[dict[str, Any]]:
    qt = _tokens(query)
    kb_scored: list[tuple[int, dict[str, str]]] = []
    for item in KB:
        s = _kw_score(qt, item["k"] + " " + item["v"])
        if s > 0:
            kb_scored.append((s, item))
    kb_scored.sort(key=lambda x: -x[0])

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM news ORDER BY published_at DESC LIMIT 60"
        ).fetchall()
    news_scored = [(_kw_score(qt, r["title"] + " " + (r["summary"] or "")), r) for r in rows]
    news_scored.sort(key=lambda x: -x[0])
    hit = [(s, r) for s, r in news_scored if s > 0]
    if len(hit) < news_k:
        seen = {r["id"] for _, r in hit}
        for s, r in news_scored:
            if len(hit) >= news_k:
                break
            if r["id"] not in seen:
                hit.append((0, r))
    news_top = hit[:news_k]

    sources: list[dict[str, Any]] = []
    for s, item in kb_scored[:kb_k]:
        sources.append({
            "type": "kb", "title": "CS2 市场知识库", "snippet": item["v"],
            "source": "内置知识库", "date": None, "score": float(s),
            "method": "keyword",
        })
    for s, r in news_top:
        sources.append({
            "type": "news", "title": r["title"], "snippet": r["summary"],
            "source": r["source"], "date": r["published_at"],
            "sentiment": r["sentiment"], "score": float(s),
            "method": "keyword",
        })

    max_s = max((x["score"] for x in sources), default=0) or 1
    for i, x in enumerate(sources):
        x["id"] = i + 1
        x["relevance"] = round(min(1.0, x["score"] / max_s), 2)
    return sources


def _retrieve_sources(query: str, kb_k: int = 3, news_k: int = 5) -> list[dict[str, Any]]:
    """优先 DashScope 向量检索,失败自动降级关键词。"""
    if RAG_EMBED_ENABLED:
        try:
            return _retrieve_vector(query, kb_k=kb_k, news_k=news_k)
        except Exception as e:
            print(f"[rag] 向量检索失败,降级关键词: {type(e).__name__}: {e}")
    return _retrieve_keyword(query, kb_k=kb_k, news_k=news_k)


# ============================================================
# 旧接口兼容(explain / debate / diagnose)
# ============================================================
def _kb_retrieve(query: str, top_k: int = 3) -> list[str]:
    src = _retrieve_sources(query, kb_k=top_k, news_k=0)
    return [s["snippet"] for s in src if s["type"] == "kb"][:top_k]


def _news_retrieve(conn: sqlite3.Connection, query: str, top_k: int = 3) -> list[Any]:
    # 保留签名供旧调用; 实际走统一检索
    src = _retrieve_sources(query, kb_k=0, news_k=top_k)
    out = []
    for s in src:
        if s["type"] != "news":
            continue
        # 从 DB 再取完整 row(若索引里带着)
        out.append(type("N", (), {
            "title": s["title"], "summary": s["snippet"], "source": s["source"],
            "published_at": s["date"], "sentiment": s.get("sentiment"),
            "impact": None, "id": s["id"],
        })())
    return out[:top_k]


def retrieve_context(skin_row: sqlite3.Row, query: str | None = None) -> dict[str, Any]:
    """汇总检索上下文(供 explain / debate / diagnose 复用)。"""
    name = skin_row["market_hash_name"]
    q = query or name
    with get_connection() as conn:
        cur, cur_date = latest_price(conn, skin_row["id"])
        ch7 = change_pct(conn, skin_row["id"], 7)
        ch30 = change_pct(conn, skin_row["id"], 30)
    sources = _retrieve_sources(q, kb_k=3, news_k=3)
    kb_hits = [s["snippet"] for s in sources if s["type"] == "kb"]
    news = [{
        "title": s["title"], "summary": s["snippet"], "source": s["source"],
        "published_at": s["date"], "sentiment": s.get("sentiment"),
        "impact": None,
    } for s in sources if s["type"] == "news"]
    return {
        "name": name,
        "current_price": cur,
        "current_date": cur_date,
        "change7d": ch7,
        "change30d": ch30,
        "kb": kb_hits,
        "news": news,
        "retrieval": vector_status(),
    }


def explain(skin_id: str, days: int = 7) -> dict[str, Any]:
    """/api/explain/{skinId} 主入口。"""
    with get_connection() as conn:
        skin = resolve_skin(conn, skin_id)
        if not skin:
            return {"error": "skin not found", "skinId": skin_id}
        ctx = retrieve_context(skin, skin["market_hash_name"])

    context_text = (
        f"饰品:{ctx['name']}\n当前价:${ctx['current_price']} ({ctx['current_date']})\n"
        f"7日涨跌:{ctx['change7d']}% | 30日涨跌:{ctx['change30d']}%\n"
        f"知识库:{'; '.join(ctx['kb']) or '无'}\n"
        f"相关资讯:{'; '.join(n['title'] for n in ctx['news']) or '无'}"
    )
    prompt = (
        f"基于以下检索到的市场上下文,用 3-5 句中文解释该饰品近期价格变动的主要原因,"
        f"并给出一句操作提示(含风险)。\n\n{context_text}"
    )
    summary = llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.5)

    return {
        "skinId": skin["slug"],
        "summary": summary,
        "relatedNews": ctx["news"],
        "sources": ["知识库"] + [n["source"] for n in ctx["news"]],
        "context": ctx,
        "retrieval": ctx.get("retrieval"),
    }


def retrieve_daily_sources(query: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """日报用的市场级检索来源。"""
    q = query or "CS2 饰品 市场 行情 Major 赛事 Valve 更新 流动性 磨损 StatTrak"
    return _retrieve_sources(q, kb_k=3, news_k=limit)


def ask(query: str, top_k: int = 5) -> dict[str, Any]:
    """RAG 问答: 向量检索 → LLM 生成带 [编号] 引用的答案。"""
    q = (query or "").strip()
    if not q:
        return {"query": "", "answer": "请输入你的问题。", "sources": [], "retrieval": vector_status()}

    sources = _retrieve_sources(q, kb_k=3, news_k=top_k)
    context_text = "\n".join(
        f"[{s['id']}] ({s['source']}) {s['snippet']}" for s in sources
    ) or "(无检索结果)"
    status = vector_status()

    prompt = (
        "你是 CS2 饰品市场 RAG 助手。请【仅依据】下面向量检索到的资料回答用户问题,"
        "在相关句子末尾用 [编号] 标注引用的资料(可多个);不要编造资料之外的事实,"
        "若资料不足请直接说明。用中文,3-6 句,并在结尾附一句风险提示。\n\n"
        f"检索方式:{status['mode']}\n"
        f"检索资料:\n{context_text}\n\n用户问题:{q}"
    )
    answer = llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.4)
    return {
        "query": q,
        "answer": answer,
        "sources": sources,
        "retrieval": status,
    }
