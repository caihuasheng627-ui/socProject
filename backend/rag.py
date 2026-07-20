"""
SkinVision AI — RAG 检索 + 解释(组员 3 第 5 步)
===============================================
轻量 RAG:无外部向量库依赖(Qdrant/pgvector 课程环境难装),用 SQLite news 表 +
内置知识库做关键词检索,再由 DeepSeek 生成可读解释。

检索源:
  1. news 表(Valve/HLTV/Reddit/internal,RSS 增量补充)
  2. 内置 CS2 饰品市场知识片段(Major 赛程、磨损、StatTrak、流动性等)
  3. 该饰品近 30 日价量行为(从 price_history 聚合)

降级:LLM 不可用时返回规则拼接的解释。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import llm
from database import get_connection, resolve_skin, latest_price, change_pct

# 内置知识库(可被关键词命中)
KB: list[dict[str, str]] = [
    {"k": "major 赛事 sticker 涨价 流动性", "v": "Major 赛事前后 7-14 天,相关贴纸与饰品成交量通常上升 15-30%,但赛事结束后有回调压力。"},
    {"k": "磨损 wear factory new fn", "v": "同款饰品中 Factory New(FN)磨损最稀有,价格显著高于 Field-Tested(FT);磨损越低流动性往往越弱。"},
    {"k": "stattrak 计数", "v": "StatTrak™ 版本因计数器稀有度,价格通常为普通版 1.5-3 倍,但流通量更小。"},
    {"k": "地板价 floor price 箱子 case", "v": "部分低价饰品与箱子存在'地板价',跌破后跌不动,适合低风险短线;但上涨空间亦有限。"},
    {"k": "流动性 成交量 volume 稀有", "v": "高价值低流动性饰品(刀/手套)日内波动大,买卖价差宽,不适合大额短线。"},
    {"k": "valve 更新 沉浸 贴图 重做", "v": "Valve 武器贴图/磨损重做会改变供给预期,相关饰品短期价格波动加大。"},
]


def _kb_retrieve(query: str, top_k: int = 3) -> list[str]:
    q = (query or "").lower()
    scored = []
    for item in KB:
        score = sum(1 for kw in item["k"].split() if kw in q)
        if score > 0:
            scored.append((score, item["v"]))
    scored.sort(key=lambda x: -x[0])
    return [v for _, v in scored[:top_k]]


def _news_retrieve(conn: sqlite3.Connection, query: str, top_k: int = 3) -> list[sqlite3.Row]:
    q = (query or "").lower()
    rows = conn.execute(
        "SELECT * FROM news ORDER BY published_at DESC LIMIT 50"
    ).fetchall()
    scored = []
    for r in rows:
        text = (r["title"] + " " + r["summary"]).lower()
        score = sum(1 for tok in q.split() if len(tok) > 1 and tok in text)
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    # 取相关度>0 的,不足则取最新 top_k
    hit = [r for s, r in scored if s > 0]
    if len(hit) < top_k:
        hit = [r for _, r in scored[:top_k]]
    return hit[:top_k]


def retrieve_context(skin_row: sqlite3.Row, query: str | None = None) -> dict[str, Any]:
    """汇总检索上下文(供 explain / debate / diagnose 复用)。"""
    name = skin_row["market_hash_name"]
    q = query or name
    with get_connection() as conn:
        news = _news_retrieve(conn, q)
        cur, cur_date = latest_price(conn, skin_row["id"])
        ch7 = change_pct(conn, skin_row["id"], 7)
        ch30 = change_pct(conn, skin_row["id"], 30)
    kb_hits = _kb_retrieve(q)
    return {
        "name": name,
        "current_price": cur,
        "current_date": cur_date,
        "change7d": ch7,
        "change30d": ch30,
        "kb": kb_hits,
        "news": [{"title": n["title"], "summary": n["summary"],
                  "source": n["source"], "published_at": n["published_at"],
                  "sentiment": n["sentiment"], "impact": n["impact"]} for n in news],
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
    }
