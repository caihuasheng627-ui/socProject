"""
SkinVision AI — 组合诊断(组员 3 第 7 步 · 🆕 方案 B 核心创新)
==============================================================
固定三块输出(策划书 §方案B):
  1. 7/30 天库存总值预估区间(逐件 Hybrid 预测 → 汇总 ± 置信带)
  2. 逐件调仓建议(卖/持/加)+ RAG 一句理由
  3. 风险贡献 Top N(波动率 × 市值 → 风险预算占比;含最大回撤)

技术点(策划书):
  - 推理走 prediction_service.predict_for_skin,与详情页和库存曲线共享 Hybrid V2 校准
  - 冷启动:新物品用 price_history 回填(已由 database.py 从 CSV 导入)
  - LLM 汇总三块(无 Key 时规则模板)
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

import llm
from database import (
    get_connection, resolve_skin, latest_price, change_pct, _utcnow,
)
from model_loader import get_loader
from prediction_service import predict_for_skin
from config import PRED_CACHE_TTL_HOURS, PREDICTION_CIRCUIT_BREAKER_ENABLED
import rag


def _item_metrics(conn: sqlite3.Connection, skin_id: int) -> dict:
    """近 30 日波动率 + 最大回撤 + 7/30 日涨跌。"""
    rows = conn.execute(
        "SELECT date, price FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT 31",
        (skin_id,),
    ).fetchall()
    if len(rows) < 2:
        return {"vol30": 0.0, "max_dd": 0.0, "change7d": None, "change30d": None}
    prices = np.array([r["price"] for r in rows][::-1], dtype=float)
    rets = np.diff(prices) / prices[:-1]
    vol = float(np.std(rets)) if len(rets) > 1 else 0.0
    # 最大回撤
    cummax = np.maximum.accumulate(prices)
    dd = (prices - cummax) / cummax
    max_dd = float(dd.min())
    return {
        "vol30": round(vol, 4),
        "max_dd": round(max_dd, 4),
        "change7d": change_pct(conn, skin_id, 7),
        "change30d": change_pct(conn, skin_id, 30),
    }


def _adjust_action(pred_change: float, vol30: float) -> tuple[str, str]:
    """Rebalance suggestion from predicted change + volatility (English)."""
    if pred_change >= 3.0 and vol30 < 0.05:
        return "Add", f"Model +{pred_change:.1f}% with low volatility — trend looks steady"
    if pred_change >= 1.5:
        return "Hold", f"Forecast +{pred_change:.1f}% — mildly bullish, keep holding"
    if pred_change <= -3.0:
        return "Sell", f"Forecast {pred_change:.1f}% — downside risk is material"
    if vol30 >= 0.08:
        return "Hold (trim)", f"Forecast {pred_change:+.1f}% but vol is high ({vol30:.1%}) — consider trimming"
    return "Hold", f"Forecast {pred_change:+.1f}% — mixed signal, hold and watch"


def diagnose(
    user_id: int | None = None,
    holding_type: str | None = "sim",
    locale: str = "zh-CN",
) -> dict:
    """/api/portfolio/diagnose entry.

    When user_id is set, only that user's holdings are diagnosed. holding_type
    defaults to 'sim' (paper portfolio page); pass None to skip type filter.
    Empty portfolio returns empty/error without raising.
    locale controls AI summary language (zh-CN / en-US).
    """
    english = str(locale or "").lower().startswith("en")
    loader = get_loader()
    items_out: list[dict] = []
    risk_rows: list[dict] = []
    total_cur = 0.0
    total_pred7_low = 0.0
    total_pred7_high = 0.0
    total_pred30_low = 0.0
    total_pred30_high = 0.0

    with get_connection() as conn:
        clauses = []
        params: list = []
        if user_id is not None:
            clauses.append("p.user_id=?")
            params.append(user_id)
        if holding_type is not None:
            clauses.append("p.holding_type=?")
            params.append(holding_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        positions = conn.execute(
            f"""SELECT s.*, p.id AS portfolio_id, p.skin_id AS portfolio_skin_id,
                       p.holding_type, p.buy_price, p.buy_date, p.quantity, p.note
                FROM portfolio p JOIN skins s ON s.id=p.skin_id
                {where} ORDER BY p.id""",
            tuple(params),
        ).fetchall()
        if not positions:
            msg = (
                "Paper portfolio is empty — add holdings first"
                if english
                else "模拟持仓为空，请先添加持仓"
            )
            return {"empty": True, "error": msg}

        for pos in positions:
            name = pos["market_hash_name"]
            cur, _ = latest_price(conn, pos["portfolio_skin_id"])
            if cur is None:
                continue
            qty = pos["quantity"] or 1
            mv = cur * qty
            total_cur += mv

            forecast = predict_for_skin(
                conn, pos, horizon=7, requested_models=None, loader=loader,
                now=_utcnow(), ttl_hours=PRED_CACHE_TTL_HOURS,
                circuit_breaker_enabled=PREDICTION_CIRCUIT_BREAKER_ENABLED,
            )
            pred = (forecast.get("predictions") or [None])[0]
            if forecast.get("status") != "available" or not isinstance(pred, dict):
                total_pred7_low += mv
                total_pred7_high += mv
                total_pred30_low += mv
                total_pred30_high += mv
                continue
            p7 = float(pred["price"])
            pred_change = float(pred["change"])
            conf = float(pred.get("confidence") or 0.0) / 100.0
            band = max(0.02, (1 - conf) * 0.06)   # wider band when confidence is low
            p7_low = p7 * (1 - band)
            p7_high = p7 * (1 + band)
            trend = forecast.get("trend30d") or {}
            p10, p50, p90 = trend.get("p10"), trend.get("p50"), trend.get("p90")
            if all(isinstance(path, list) and len(path) == 30 for path in (p10, p50, p90)):
                p30 = float(p50[-1])
                p30_low = float(p10[-1])
                p30_high = float(p90[-1])
            else:
                p30 = p30_low = p30_high = p7

            total_pred7_low += p7_low * qty
            total_pred7_high += p7_high * qty
            total_pred30_low += p30_low * qty
            total_pred30_high += p30_high * qty

            m = _item_metrics(conn, pos["portfolio_skin_id"])
            action, reason = _adjust_action(pred_change, m["vol30"])
            buy_price = pos["buy_price"]
            pnl_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None

            items_out.append({
                "id": pos["portfolio_id"],
                "skinId": pos["slug"],
                "name": name,
                "holdingType": pos["holding_type"],
                "quantity": qty,
                "currentPrice": round(cur, 2),
                "marketValue": round(mv, 2),
                "buyPrice": buy_price,
                "pnlPct": pnl_pct,
                "pred7d": round(p7, 2),
                "pred30d": round(p30, 2),
                "predChange7d": pred_change,
                "modelVersion": forecast.get("modelVersion"),
                "action": action,
                "reason": reason,
                "vol30": m["vol30"],
                "maxDrawdown30": m["max_dd"],
            })

            risk_rows.append({
                "name": name, "marketValue": mv,
                "vol30": m["vol30"], "max_dd": m["max_dd"],
                "risk_contrib": mv * m["vol30"],   # market value × volatility
            })

    # ---- block 1: portfolio value range ----
    value_range = {
        "current": round(total_cur, 2),
        "pred7d_low": round(total_pred7_low, 2),
        "pred7d_high": round(total_pred7_high, 2),
        "pred30d_low": round(total_pred30_low, 2),
        "pred30d_high": round(total_pred30_high, 2),
        "expected7d_change_pct": round((total_pred7_low + total_pred7_high) / 2 / total_cur * 100 - 100, 2)
            if total_cur else 0.0,
    }

    # ---- block 3: risk contribution Top N ----
    total_risk = sum(r["risk_contrib"] for r in risk_rows) or 1.0
    for r in risk_rows:
        r["risk_share_pct"] = round(r["risk_contrib"] / total_risk * 100, 2)
    risk_rows.sort(key=lambda x: -x["risk_share_pct"])
    risk_top = [
        {"name": r["name"], "marketValue": round(r["marketValue"], 2),
         "vol30": r["vol30"], "maxDrawdown30": r["max_dd"],
         "riskSharePct": r["risk_share_pct"]}
        for r in risk_rows[:5]
    ]

    # ---- LLM summary ----
    summary = _summarize(items_out, value_range, risk_top, locale=locale)

    return {
        "generatedAt": _utcnow().isoformat(),
        "totalItems": len(items_out),
        "valueRange": value_range,
        "adjustments": items_out,
        "riskTopN": risk_top,
        "aiSummary": summary,
        "locale": "en-US" if english else "zh-CN",
    }


def _summarize(items, value_range, risk_top, locale: str = "zh-CN") -> str:
    english = str(locale or "").lower().startswith("en")
    up = value_range["expected7d_change_pct"]
    top_risk = risk_top[0]["name"] if risk_top else "—"
    if not llm.LLM_ENABLED:
        if english:
            return (
                f"(Mock) Portfolio value ${value_range['current']}; "
                f"7-day outlook {up:+.1f}% "
                f"(range ${value_range['pred7d_low']}–${value_range['pred7d_high']}). "
                f"Largest risk contribution: {top_risk} — watch its volatility. "
                f"⚠ Not investment advice."
            )
        return (
            f"（模拟）组合总值 ${value_range['current']}；"
            f"7 日展望 {up:+.1f}% "
            f"（区间 ${value_range['pred7d_low']}–${value_range['pred7d_high']}）。"
            f"最大风险贡献：{top_risk}，请关注其波动。"
            f"⚠ 非投资建议。"
        )
    language = "English" if english else "Simplified Chinese"
    actions = ", ".join(f"{i['name']}={i['action']}" for i in items)
    risks = ", ".join(f"{r['name']}({r['riskSharePct']}%)" for r in risk_top)
    prompt = (
        f"You are a CS2 skin portfolio analyst. Reply in {language} only.\n"
        f"Holdings: {len(items)} items; current value ${value_range['current']}; "
        f"7-day forecast range ${value_range['pred7d_low']}–${value_range['pred7d_high']} "
        f"({value_range['expected7d_change_pct']:+.1f}%).\n"
        f"Suggested actions: {actions}.\n"
        f"Risk Top: {risks}.\n"
        "Write a 3-sentence portfolio diagnosis with a clear risk caveat. "
        "Do not claim certainty; this is not investment advice."
    )
    return llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.4)
