"""
SkinVision AI — 双 Agent 辩论(组员 3 第 6 步 · 🆕 双模式)
==========================================================
🐂 Bull vs 🐻 Bear,3 轮迭代:
  Round 1:独立分析(并行)
  Round 2:互相质疑
  Round 3:达成共识

双模式(策划书 §方案B 核心变化):
  - 模式 1 预录回放(Expo 默认):读 docs/expo/seed_debate_*.json,1 秒展示,不耗额度
  - 模式 2 现场重跑:真调 DeepSeek(评委要求时切,?live=1)

无 Key + 无预录 → 规则模板生成(保证端点可用)。
"""
from __future__ import annotations

import json
from pathlib import Path

import llm
from config import LLM_ENABLED, SEED_DIR
from database import get_connection, resolve_skin, latest_price, change_pct
from model_loader import get_loader
import rag


def _load_seed_debate(slug: str) -> dict | None:
    """找该 skin 的预录辩论 JSON(docs/expo/seed_debate_<slug>.json)。"""
    if not SEED_DIR.exists():
        return None
    for p in SEED_DIR.glob("seed_debate_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("skinId") == slug or data.get("slug") == slug:
                return data
        except Exception:
            continue
    # 兜底:任一预录(Expo 演示)
    for p in SEED_DIR.glob("seed_debate_*.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _skin_context(skin_id: str) -> dict | None:
    with get_connection() as conn:
        skin = resolve_skin(conn, skin_id)
        if not skin:
            return None
        cur, cur_date = latest_price(conn, skin["id"])
        ch7 = change_pct(conn, skin["id"], 7)
        ch30 = change_pct(conn, skin["id"], 30)
        ctx = rag.retrieve_context(skin, skin["market_hash_name"])
    loader = get_loader()
    pred = loader.predict_hybrid(skin["market_hash_name"])
    return {
        "slug": skin["slug"],
        "name": skin["market_hash_name"],
        "current_price": cur,
        "change7d": ch7,
        "change30d": ch30,
        "prediction": pred,
        "rag": ctx,
    }


def _round_prompts(ctx: dict, round_no: int, prev: dict | None) -> tuple[str, str]:
    base = (
        f"饰品:{ctx['name']},当前价 ${ctx['current_price']},"
        f"7日 {ctx['change7d']}%,30日 {ctx['change30d']}%。"
        f"Hybrid 模型预测7天后 ${ctx['prediction']['predicted_price']}"
        f"(涨幅 {ctx['prediction']['change_pct']}%,置信度 {ctx['prediction']['confidence']})。"
        f"资讯:{'; '.join(n['title'] for n in ctx['rag']['news'][:2]) or '无'}。"
    )
    if round_no == 1:
        bull = f"你是看多 Agent(🐂)。基于以下信息,用 2-3 句给出看多理由与目标价。\n{base}"
        bear = f"你是看空 Agent(🐻)。基于以下信息,用 2-3 句给出看空理由与止损位。\n{base}"
    else:
        bull = (f"你是看多 Agent(🐂)。对方看空观点:「{prev['bear']}」\n"
                f"请反驳并修正你的目标价。2-3 句。\n{base}")
        bear = (f"你是看空 Agent(🐻)。对方看多观点:「{prev['bull']}」\n"
                f"请反驳并修正你的止损位。2-3 句。\n{base}")
    return bull, bear


def _live_debate(ctx: dict) -> dict:
    """现场重跑:3 轮,每轮 Bull/Bear 并行(同步串行实现,可并发优化)。"""
    rounds = []
    prev = None
    for rnd in (1, 2, 3):
        bp, ap = _round_prompts(ctx, rnd, prev)
        bull_text = llm.chat_sync([{"role": "user", "content": bp}], temperature=0.7)
        bear_text = llm.chat_sync([{"role": "user", "content": ap}], temperature=0.7)
        if rnd == 3:
            # 第 3 轮:共识
            consensus_prompt = (
                f"综合 3 轮辩论,Bull 最后观点:「{bull_text}」\nBear 最后观点:「{bear_text}」\n"
                f"给出共识结论:建议(买入/观望/卖出)、入场区间、止损、目标价、共识分(0-100)、主要风险(2条)。JSON 返回。"
            )
            cons = llm.chat_sync([{"role": "user", "content": consensus_prompt}], temperature=0.3)
            rounds.append({"round": rnd, "bull": bull_text, "bear": bear_text, "consensus_raw": cons})
            prev = {"bull": bull_text, "bear": bear_text}
        else:
            rounds.append({"round": rnd, "bull": bull_text, "bear": bear_text})
            prev = {"bull": bull_text, "bear": bear_text}

    pred = ctx["prediction"]
    return _build_result(ctx, rounds, live=True,
                         recommendation="见 Round 3 共识",
                         consensus_score=min(95, max(40, pred["confidence"])))


def _template_debate(ctx: dict) -> dict:
    """无 LLM 时的规则模板辩论(保证端点可用)。"""
    p = ctx["prediction"]
    chg = p["change_pct"]
    bull_target = round(p["predicted_price"] * 1.03, 2)
    bear_stop = round(p["current_price"] * 0.93, 2)
    rounds = [
        {"round": 1,
         "bull": f"模型预测 7 天涨幅 {chg}%,趋势偏多,目标价 ${bull_target}。成交量与 Major 节奏支撑短期上行。",
         "bear": f"近 30 日波动较大,若跌破 ${bear_stop} 则趋势破坏。高估值饰品流动性风险不容忽视。"},
        {"round": 2,
         "bull": f"止损 ${bear_stop} 可控,风险收益比尚可,维持看多。",
         "bear": f"模型置信度仅 {p['confidence']},预测涨幅有限,建议轻仓试探。"},
        {"round": 3,
         "bull": "综合看,温和看多,建议小仓位持有。",
         "bear": "同意观望偏多,严守止损。"},
    ]
    return _build_result(ctx, rounds, live=False,
                         recommendation="观望偏多(轻仓)",
                         consensus_score=round(min(80, 40 + abs(chg) * 3), 1))


def _build_result(ctx: dict, rounds: list, live: bool,
                  recommendation: str, consensus_score: float) -> dict:
    p = ctx["prediction"]
    cur = p["current_price"]
    return {
        "skinId": ctx["slug"],
        "mode": "live" if live else ("pre_recorded" if not live and _load_seed_debate(ctx["slug"]) else "template"),
        "rounds": rounds,
        "consensus": {
            "recommendation": recommendation,
            "entryRange": f"${round(cur * 0.97, 2)} ~ ${round(cur * 0.99, 2)}",
            "stopLoss": f"${round(cur * 0.93, 2)}",
            "targetPrice": f"${round(p['predicted_price'], 2)}",
            "consensusScore": consensus_score,
            "confidence": "medium",
            "risks": ["饰品市场高波动,模型预测存在误差", "流动性不足时滑点放大"],
        },
        "prediction": p,
    }


def debate(skin_id: str, live: bool = False, mode: str = "bull_bear") -> dict:
    """/api/debate/{skinId} 主入口。"""
    ctx = _skin_context(skin_id)
    if ctx is None:
        return {"error": "skin not found", "skinId": skin_id}

    # 模式 1:预录回放(默认,Expo)
    if not live:
        seed = _load_seed_debate(ctx["slug"])
        if seed:
            seed["mode"] = "pre_recorded"
            return seed

    # 模式 2:现场重跑
    if live and LLM_ENABLED:
        return _live_debate(ctx)

    # 兜底:模板
    return _template_debate(ctx)
