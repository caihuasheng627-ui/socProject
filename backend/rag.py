"""
SkinVision AI — RAG 向量检索 + 解释
==================================
检索: 阿里云百炼 DashScope Embedding + 关键词 RRF 混合 → Top-K
生成: DeepSeek 根据检索片段生成带 [编号] 引用的答案

检索源:
  1. news 表(Valve/HLTV/Reddit/internal, RSS 增量补充) — 长文按块切分
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
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_EMBED_DIM,
    RAG_EMBED_ENABLED,
    RAG_EMBED_MODEL,
    RAG_HYBRID_RRF_K,
    RAG_INDEX_PATH,
    RAG_MIN_KEYWORD_SCORE,
    RAG_MIN_VECTOR_SCORE,
    RAG_NEWS_LIMIT,
    RAG_USE_VECTOR,
)
from database import get_connection, resolve_skin, latest_price, change_pct

# 内置知识库(手工短文案,每条作为独立检索单元)
KB: list[dict[str, str]] = [
    # —— 赛事 / 供给冲击 ——
    {
        "k": "major 赛事 sticker 涨价 流动性 capsule",
        "v": "Major 赛事前后 7-14 天,相关战队贴纸与胶囊成交量通常上升 15-30%,但赛事结束后常有回调。"
             "决赛圈战队贴纸溢价更高;赛后新开胶囊会稀释旧贴纸稀缺预期。",
    },
    {
        "k": "valve 更新 沉浸 贴图 重做 armory",
        "v": "Valve 武器贴图/磨损重做、Armory 通行证奖励或掉落池调整会改变供给预期,"
             "相关饰品短期波动加大;官方博客与更新日志是领先信号。",
    },
    {
        "k": "operation 大行动 通行证 pass 掉落",
        "v": "大行动/通行证期间常伴随新收藏品、任务奖励与限时掉落。"
             "活动期内相关皮肤供给上升压制价格;活动结束后停产收藏品可能转为长期通缩逻辑。",
    },
    {
        "k": "case 停产 discontinued 箱子 供给",
        "v": "箱子从活跃掉落池移除后,新开箱供给下降,箱内皮肤长期或呈通缩;"
             "但短线仍受开箱潮、新箱子分流与宏观风险偏好影响,停产≠立刻暴涨。",
    },
    # —— 磨损 / 图案 ——
    {
        "k": "磨损 wear factory new fn mw ft ww bs float",
        "v": "磨损档:Factory New(FN)→Minimal Wear(MW)→Field-Tested(FT)→Well-Worn(WW)→Battle-Scarred(BS)。"
             "同款中 FN 最稀有、溢价最高;低磨损流动性往往弱于主流 FT。"
             "关键 float 区间(如接近 0.00/0.07/0.15 边界)可产生额外溢价。",
    },
    {
        "k": "stattrak 计数 普通版 溢价",
        "v": "StatTrak™ 因击杀计数器稀有,价格通常为普通版约 1.5-3 倍,流通量更小、买卖价差更宽。"
             "高价刀/手套上 StatTrak 溢价不稳定,需结合近期成交而非静态倍数。",
    },
    {
        "k": "souvenir 纪念品 大赛 地图",
        "v": "Souvenir 纪念品皮肤来自 Major 等赛事掉落,供给有限且带战队/选手签名贴纸。"
             "热门地图/冠军战队组合溢价显著;冷门组合流动性极差,报价参考意义有限。",
    },
    {
        "k": "doppler 多普勒 phase 蓝宝石 红宝石 黑珍珠",
        "v": "Doppler/Gamma Doppler 按 Phase 与宝石类区分定价:蓝宝石、红宝石、黑珍珠显著稀缺。"
             "同 Phase 内也有图案差异;成交稀疏时勿被个别挂单价误导。",
    },
    {
        "k": "fade 渐变 percentage 90 99 100",
        "v": "Fade 系列常按渐变百分比定价(如 80%/90%/99%/100%)。"
             "高百分比溢价陡峭但成交更少;报价需对照近期同百分比成交而非仅看最高求购。",
    },
    {
        "k": "case hardened 蓝宝石 blue gem 图案 pattern seed",
        "v": "Case Hardened 等看图案(pattern seed):大蓝/蓝宝石类稀缺图案溢价极高。"
             "无公开成交支撑时挂价可能长期无人接;投资逻辑更接近收藏品而非短线波动率交易。",
    },
    # —— 品类 / 流动性 ——
    {
        "k": "地板价 floor price 箱子 case 低价饰品",
        "v": "部分低价饰品与箱子存在'地板价',跌破后买盘支撑强、适合低风险短线,"
             "但上行空间亦有限。地板价会随开箱成本、汇率与平台费率漂移。",
    },
    {
        "k": "流动性 成交量 volume 稀有 刀 手套",
        "v": "高价值低流动性饰品(刀/手套/极品图案)日内波动大、买卖价差宽,不适合大额短线。"
             "评估时优先看近期真实成交笔数与价差,而非仅看盘口最优价。",
    },
    {
        "k": "cover 隐蔽 classified 受限 mil-spec 稀有度",
        "v": "箱内稀有度大致为消费级→工业→军规(Mil-Spec)→受限(Restricted)→保密(Classified)→隐蔽(Covert)→金(刀/手套)。"
             "稀有度越高基础供给越低,但热度与是否停产共同决定价格,不能只看等级。",
    },
    {
        "k": "sticker 贴纸 holo foil gloss 金贴 位置",
        "v": "贴纸品级(纸贴/全息 Holo/闪光 Foil/闪亮等)与热门战队决定溢价;"
             "已贴在热门枪皮特定位置(枪口/准星)可显著加价,但会降低流动性且难以单独拆卖。",
    },
    {
        "k": "trade up 汰换合同 合约 10件",
        "v": "汰换合同用 10 件同品质皮肤合成更高一级,会消耗低级供给并影响目标皮肤预期供给。"
             "当合成成本低于目标皮肤市场价格时,套利需求可能短期抬升相关材料价格。",
    },
    # —— 平台 / 费率 / 风险 ——
    {
        "k": "buff steam 价差 溢价 平台 汇率",
        "v": "中国玩家常用 BUFF 等第三方;Steam 市场有手续费且提现受限,常出现平台价差。"
             "价差受汇率、手续费、提现摩擦与封禁风险影响,裸价差未必可无风险套利。",
    },
    {
        "k": "steam 手续费 市场税 15% 挂售",
        "v": "Steam 社区市场成交约含 15% 手续费(平台+游戏费),挂售需把税费计入成本。"
             "短期低波动品种若价差不足以覆盖税费与滑点,账面上的微利可能实际亏损。",
    },
    {
        "k": "封禁 交易限制 trade ban 账号风险 cashout",
        "v": "第三方交易存在锁区、冷却、API 延迟与盗号/封禁风险。"
             "大额变现应分散平台与时间,避免单一渠道挤兑;来路不明的低价货可能是黑产回流。",
    },
    {
        "k": "新箱子 新皮肤 发布 倾销 热度退潮",
        "v": "新箱子/新皮肤上线初期开箱供给大、热度高,价格常先冲高后随供给释放回落。"
             "除非后续停产或成为常用皮肤元,否则'上市即巅峰'是常见路径。",
    },
    {
        "k": "周末 成交 volume 工作日 波动",
        "v": "饰品市场成交常在周末与重大赛事期间更活跃,工作日流动性相对更弱。"
             "低流动性标的在周末更易出现跳价,用周末极值做成本锚可能高估可成交价格。",
    },
    {
        "k": "agent 探员 music kit 音乐盒 收藏品",
        "v": "探员、音乐盒等非武器饰品受众更窄,流动性通常弱于主流枪皮。"
             "定价更依赖收藏偏好与限定发行,不宜套用步枪皮的短线量价规律。",
    },
    {
        "k": "collection 收藏品 地图 掉落 停更",
        "v": "收藏品(Collection)供给与地图掉落/行动奖励绑定;地图轮换或收藏品停掉落后,"
             "存量皮肤可能转向通缩预期,但冷门收藏品即使停产也可能长期无人问津。",
    },
    {
        "k": "刀 手套 金皮 special item 波动率",
        "v": "刀与手套属于特殊稀有品,单价高、深度薄,新闻与情绪冲击下波动率显著高于步枪皮。"
             "仓位上更适合中长期配置或严格止损的波段,不适合按低价皮的高频思路交易。",
    },
    {
        "k": "名牌 name tag 改名 增值",
        "v": "名牌(Name Tag)改名对多数皮肤增值有限,极端梗/纪念意义个案除外。"
             "改名不可逆地消耗名牌,评估成交价时应剥离情绪溢价。",
    },
]


# ============================================================
# 关键词 / 分块
# ============================================================
_SPLIT_RE = re.compile(r"[\s,，。;；:：!！?？、()（）\[\]【】\"'`/\\|-]+")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])")


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


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """按字符近似分块:优先在句号/换行处切开,块间保留 overlap。"""
    size = int(chunk_size if chunk_size is not None else RAG_CHUNK_SIZE)
    ov = int(overlap if overlap is not None else RAG_CHUNK_OVERLAP)
    size = max(64, size)
    ov = max(0, min(ov, size // 2))

    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    if len(raw) <= size:
        return [raw]

    # 先按句子聚合,再在超长句内硬切
    sentences = [s.strip() for s in _SENT_SPLIT_RE.split(raw) if s and s.strip()]
    if not sentences:
        sentences = [raw]

    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(sent) > size:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            start = 0
            while start < len(sent):
                end = min(len(sent), start + size)
                chunks.append(sent[start:end].strip())
                if end >= len(sent):
                    break
                start = max(0, end - ov)
            continue
        candidate = f"{buf}{sent}" if buf else sent
        if len(candidate) <= size:
            buf = candidate
            continue
        if buf.strip():
            chunks.append(buf.strip())
        # overlap:从上一块尾部取后缀
        if chunks and ov > 0:
            tail = chunks[-1][-ov:]
            buf = f"{tail}{sent}"
            if len(buf) > size:
                buf = sent
        else:
            buf = sent
    if buf.strip():
        chunks.append(buf.strip())

    # 去空、合并过碎尾块
    chunks = [c for c in chunks if c]
    if len(chunks) >= 2 and len(chunks[-1]) < max(32, size // 5):
        merged = (chunks[-2] + chunks[-1])[: size + ov]
        chunks = chunks[:-2] + [merged]
    return chunks or [raw[:size]]


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
        "mode": "hybrid" if ok else "keyword",
        "provider": "dashscope" if ok else None,
        "model": RAG_EMBED_MODEL if ok else None,
        "dim": RAG_EMBED_DIM if ok else None,
        "enabled": RAG_USE_VECTOR,
        "hasKey": bool(DASHSCOPE_API_KEY),
        "chunkSize": RAG_CHUNK_SIZE,
        "chunkOverlap": RAG_CHUNK_OVERLAP,
        "newsLimit": RAG_NEWS_LIMIT,
        "kbSize": len(KB),
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
def _doc_base(
    *,
    uid: str,
    doc_type: str,
    title: str,
    snippet: str,
    source: str,
    text: str,
    date: str | None = None,
    sentiment: str | None = None,
    url: str | None = None,
    parent_uid: str | None = None,
    chunk_index: int = 0,
) -> dict[str, Any]:
    return {
        "uid": uid,
        "parent_uid": parent_uid or uid,
        "chunk_index": chunk_index,
        "type": doc_type,
        "title": title,
        "snippet": snippet,
        "source": source,
        "date": date,
        "sentiment": sentiment,
        "url": url,
        "text": text,
    }


def _append_chunked_docs(
    docs: list[dict[str, Any]],
    *,
    parent_uid: str,
    doc_type: str,
    title: str,
    body: str,
    source: str,
    date: str | None = None,
    sentiment: str | None = None,
    url: str | None = None,
    keyword_prefix: str = "",
) -> None:
    """将正文分块写入 docs;短文保持单块。"""
    prefix = (keyword_prefix or "").strip()
    full = f"{prefix} {body}".strip() if prefix else (body or "").strip()
    pieces = chunk_text(full)
    if not pieces:
        return
    for i, piece in enumerate(pieces):
        uid = parent_uid if len(pieces) == 1 else f"{parent_uid}:c{i}"
        docs.append(_doc_base(
            uid=uid,
            parent_uid=parent_uid,
            chunk_index=i,
            doc_type=doc_type,
            title=title,
            snippet=piece,
            source=source,
            date=date,
            sentiment=sentiment,
            url=url,
            text=piece if doc_type == "kb" else f"{title} {piece}".strip(),
        ))


def _collect_docs() -> list[dict[str, Any]]:
    """统一语料: 知识库 + news 表(长文分块)。"""
    docs: list[dict[str, Any]] = []
    for i, item in enumerate(KB):
        _append_chunked_docs(
            docs,
            parent_uid=f"kb:{i}",
            doc_type="kb",
            title="CS2 市场知识库",
            body=item["v"],
            source="内置知识库",
            keyword_prefix=item["k"],
        )

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM news ORDER BY published_at DESC LIMIT ?",
            (int(RAG_NEWS_LIMIT),),
        ).fetchall()
    for r in rows:
        body = (r["summary"] or "").strip()
        _append_chunked_docs(
            docs,
            parent_uid=f"news:{r['id']}",
            doc_type="news",
            title=r["title"],
            body=body or r["title"],
            source=r["source"],
            date=r["published_at"],
            sentiment=r["sentiment"],
            url=(r["url"] or None) if "url" in r.keys() else None,
        )
    return docs


def _fingerprint(docs: list[dict[str, Any]]) -> str:
    h = hashlib.sha1()
    h.update(
        f"{RAG_EMBED_MODEL}:{RAG_EMBED_DIM}:{RAG_CHUNK_SIZE}:{RAG_CHUNK_OVERLAP}".encode("utf-8")
    )
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
                    print(f"[rag] 向量索引命中磁盘缓存 ({len(docs)} chunks)")
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
        print(f"[rag] 向量索引已重建 ({len(docs)} chunks, dim={vectors.shape[1]})")
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
def _source_from_doc(
    d: dict[str, Any],
    *,
    score: float,
    method: str,
    vector_score: float | None = None,
    keyword_score: float | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": d["type"],
        "title": d["title"],
        "snippet": d["snippet"],
        "source": d["source"],
        "date": d["date"],
        "sentiment": d.get("sentiment"),
        "url": d.get("url"),
        "score": round(float(score), 4),
        "method": method,
        "uid": d["uid"],
        "parentUid": d.get("parent_uid"),
    }
    if vector_score is not None:
        out["vectorScore"] = round(float(vector_score), 4)
    if keyword_score is not None:
        out["keywordScore"] = float(keyword_score)
    return out


def _finalize_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按父文档去重(同新闻多块只留最高分),映射 relevance,重编号。"""
    best: dict[str, dict[str, Any]] = {}
    for s in sources:
        key = s.get("parentUid") or s.get("uid") or f"{s['type']}:{s['title']}"
        prev = best.get(key)
        if prev is None or float(s["score"]) > float(prev["score"]):
            best[key] = s
    merged = list(best.values())
    merged.sort(key=lambda x: -float(x["score"]))

    if not merged:
        return []

    method = merged[0].get("method")
    if method == "keyword":
        max_s = max((float(x["score"]) for x in merged), default=0) or 1.0
        for x in merged:
            x["relevance"] = round(min(1.0, float(x["score"]) / max_s), 2)
    else:
        # hybrid / vector: score 已在约 [0,1+] 或余弦映射
        for x in merged:
            base = float(x.get("vectorScore", x["score"]))
            # 余弦 [-1,1] → [0,1]; hybrid RRF 分数另用 min-max
            if method == "vector":
                x["relevance"] = round(max(0.0, min(1.0, (base + 1) / 2)), 2)
            else:
                pass
        if method != "vector":
            vals = [float(x["score"]) for x in merged]
            lo, hi = min(vals), max(vals)
            if hi <= lo:
                for x in merged:
                    x["relevance"] = 1.0
            else:
                span = hi - lo
                for x in merged:
                    x["relevance"] = round((float(x["score"]) - lo) / span, 2)

    for i, x in enumerate(merged):
        x["id"] = i + 1
    return merged


def _take_by_type(
    ranked: list[tuple[float, dict[str, Any], dict[str, Any]]],
    kb_k: int,
    news_k: int,
) -> list[dict[str, Any]]:
    """从已排序 (score, doc, meta) 中按类型配额选取;不做零分填充。"""
    kb_hits: list[dict[str, Any]] = []
    news_hits: list[dict[str, Any]] = []
    for score, d, meta in ranked:
        if d["type"] == "kb" and len(kb_hits) < kb_k:
            kb_hits.append(_source_from_doc(d, score=score, **meta))
        elif d["type"] == "news" and len(news_hits) < news_k:
            news_hits.append(_source_from_doc(d, score=score, **meta))
        if len(kb_hits) >= kb_k and len(news_hits) >= news_k:
            break
    return _finalize_sources(kb_hits + news_hits)


def _retrieve_keyword(query: str, kb_k: int, news_k: int) -> list[dict[str, Any]]:
    """纯关键词检索:只返回命中分 > 阈值的块,不零分灌水。"""
    qt = _tokens(query)
    if not qt:
        return []
    docs = _collect_docs()
    ranked: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for d in docs:
        s = _kw_score(qt, d["text"])
        if s < RAG_MIN_KEYWORD_SCORE:
            continue
        ranked.append((float(s), d, {"method": "keyword", "keyword_score": float(s)}))
    ranked.sort(key=lambda x: -x[0])
    return _take_by_type(ranked, kb_k, news_k)


def _retrieve_hybrid(query: str, kb_k: int, news_k: int) -> list[dict[str, Any]]:
    """向量 + 关键词 Reciprocal Rank Fusion;过滤低相似度/零命中。"""
    docs, vectors = _build_or_load_index()
    if not docs:
        return []

    qv = _embed_texts([query])[0]
    pool = min(len(docs), max(48, (kb_k + news_k) * 8))
    vec_ranked = _cosine_top(qv, vectors, top_k=pool)

    qt = _tokens(query)
    kw_pairs = [(_kw_score(qt, d["text"]), i) for i, d in enumerate(docs)]
    kw_pairs = [(s, i) for s, i in kw_pairs if s >= RAG_MIN_KEYWORD_SCORE]
    kw_pairs.sort(key=lambda x: -x[0])

    vec_rank: dict[int, int] = {}
    vec_score: dict[int, float] = {}
    for rank, (i, sim) in enumerate(vec_ranked, start=1):
        if sim < RAG_MIN_VECTOR_SCORE:
            continue
        vec_rank[i] = rank
        vec_score[i] = sim

    kw_rank: dict[int, int] = {}
    kw_score_map: dict[int, float] = {}
    for rank, (s, i) in enumerate(kw_pairs, start=1):
        kw_rank[i] = rank
        kw_score_map[i] = float(s)

    rrf_k = max(1, int(RAG_HYBRID_RRF_K))
    fused: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    candidates = set(vec_rank) | set(kw_rank)
    for i in candidates:
        score = 0.0
        if i in vec_rank:
            score += 1.0 / (rrf_k + vec_rank[i])
        if i in kw_rank:
            score += 1.0 / (rrf_k + kw_rank[i])
        if score <= 0:
            continue
        fused.append((
            score,
            docs[i],
            {
                "method": "hybrid",
                "vector_score": vec_score.get(i),
                "keyword_score": kw_score_map.get(i),
            },
        ))
    fused.sort(key=lambda x: -x[0])
    return _take_by_type(fused, kb_k, news_k)


def _retrieve_vector(query: str, kb_k: int, news_k: int) -> list[dict[str, Any]]:
    """兼容旧名:实际走混合检索。"""
    return _retrieve_hybrid(query, kb_k=kb_k, news_k=news_k)


def _retrieve_sources(query: str, kb_k: int = 3, news_k: int = 5) -> list[dict[str, Any]]:
    """优先混合检索,失败自动降级关键词。"""
    if RAG_EMBED_ENABLED:
        try:
            return _retrieve_hybrid(query, kb_k=kb_k, news_k=news_k)
        except Exception as e:
            print(f"[rag] 混合检索失败,降级关键词: {type(e).__name__}: {e}")
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
        "impact": None, "url": s.get("url"),
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
    """日报只用真实 news 表条目。

    不注入内置 KB：KB 里有 Major/Valve 等「常青」说明，LLM 会当成「今日事件」写进日报。
    """
    q = query or "CS2 饰品 市场价格 流动性 成交 磨损 StatTrak"
    return _retrieve_sources(q, kb_k=0, news_k=limit)


def _query_lang(q: str) -> str:
    """粗判用户问题语言: en / zh。"""
    letters = sum(1 for ch in q if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    cjk = sum(1 for ch in q if "\u4e00" <= ch <= "\u9fff")
    if cjk >= 2:
        return "zh"
    if letters >= 6:
        return "en"
    return "zh" if cjk > letters else "en"


def ask(query: str, top_k: int = 5) -> dict[str, Any]:
    """RAG 问答: 检索 → LLM 生成带 [编号] 引用的答案。"""
    q = (query or "").strip()
    if not q:
        return {"query": "", "answer": "请输入你的问题。 / Please enter a question.",
                "sources": [], "retrieval": vector_status()}

    sources = _retrieve_sources(q, kb_k=3, news_k=top_k)
    context_text = "\n".join(
        f"[{s['id']}] ({s['source']}) {s['snippet']}" for s in sources
    ) or "(no retrieval hits)"
    status = vector_status()
    lang = _query_lang(q)
    if lang == "en":
        lang_rule = (
            "OUTPUT LANGUAGE: English ONLY. "
            "The retrieved notes may be Chinese — translate facts into English. "
            "Do NOT write any Chinese characters in your answer."
        )
    else:
        lang_rule = "输出语言:仅中文。检索资料若为英文请译成中文作答。"

    if not sources:
        empty_hint = (
            "No retrieved notes matched this question. "
            "Say that evidence is insufficient; do not invent market facts."
            if lang == "en"
            else "当前没有检索到足够相关的资料。请明确说明证据不足,不要编造行情。"
        )
        context_msg = f"{lang_rule}\n\n{empty_hint}"
    else:
        context_msg = (
            f"{lang_rule}\n\n"
            "You are a CS2 skin-market RAG assistant. Answer ONLY from the retrieved notes. "
            "Cite with [n]. Do not invent facts; if evidence is insufficient, say so. "
            "Keep 3-6 sentences and end with one risk disclaimer.\n\n"
            f"Retrieval mode: {status['mode']}\n"
            f"Retrieved notes:\n{context_text}"
        )
    # 用户原句单独作为最后一条,避免模型把英文指令当成「用户语言」
    answer = llm.chat_sync(
        [
            {"role": "user", "content": context_msg},
            {"role": "user", "content": q},
        ],
        temperature=0.3,
    )
    return {
        "query": q,
        "answer": answer,
        "sources": sources,
        "retrieval": status,
    }
