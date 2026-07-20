"""
SkinVision AI — 组合诊断(组员 3 第 7 步 · 🆕 方案 B 核心创新)
==============================================================
固定三块输出(策划书 §方案B):
  1. 7/30 天库存总值预估区间(逐件 Hybrid 预测 → 汇总 ± 置信带)
  2. 逐件调仓建议(卖/持/加)+ RAG 一句理由
  3. 风险贡献 Top N(波动率 × 市值 → 风险预算占比;含最大回撤)

技术点(策划书):
  - 推理走 model_loader.predict_hybrid(单窗口,快);库存 >20 件分批
  - 冷启动:新物品用 price_history 回填(已由 database.py 从 CSV 导入)
  - LLM 汇总三块(无 Key 时规则模板)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import llm
from database import (
    get_connection, resolve_skin, latest_price, change_pct, _utcnow,
)
from model_loader import get_loader
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
    """调仓建议:基于预测涨幅 + 波动率。"""
    if pred_change >= 3.0 and vol30 < 0.05:
        return "加仓", f"模型预测 +{pred_change:.1f}%,低波动,趋势稳健"
    if pred_change >= 1.5:
        return "持有", f"预测 +{pred_change:.1f}%,趋势偏多,继续持有"
    if pred_change <= -3.0:
        return "卖出", f"预测 {pred_change:.1f}%,下行风险显著"
    if vol30 >= 0.08:
        return "持有(减仓观察)", f"预测 {pred_change:+.1f}% 但波动率高({vol30:.1%}),宜减仓"
    return "持有", f"预测 {pred_change:+.1f}%,信号不明确,持有观望"


def diagnose() -> dict:
    """/api/portfolio/diagnose 主入口。"""
    loader = get_loader()
    items_out: list[dict] = []
    risk_rows: list[dict] = []
    total_cur = 0.0
    total_pred7_low = 0.0
    total_pred7_high = 0.0
    total_pred30_low = 0.0
    total_pred30_high = 0.0

    with get_connection() as conn:
        positions = conn.execute(
            """SELECT p.*, s.market_hash_name, s.slug, s.category
               FROM portfolio p JOIN skins s ON s.id=p.skin_id
               ORDER BY p.id"""
        ).fetchall()
        if not positions:
            return {"error": "portfolio 为空,请先添加持仓"}

        for pos in positions:
            name = pos["market_hash_name"]
            cur, _ = latest_price(conn, pos["skin_id"])
            if cur is None:
                continue
            qty = pos["quantity"] or 1
            mv = cur * qty
            total_cur += mv

            pred = loader.predict_hybrid(name)
            if pred is None:
                continue
            p7 = pred["predicted_price"]
            chg = pred["change_pct"] / 100.0
            conf = pred["confidence"] / 100.0
            band = max(0.02, (1 - conf) * 0.06)   # 置信越低带越宽
            p7_low = p7 * (1 - band)
            p7_high = p7 * (1 + band)
            # 30 天外推(7 天 ×3.5)
            p30 = max(cur * (1 + chg * 3.5), 0.01)
            p30_low = p30 * (1 - band * 1.5)
            p30_high = p30 * (1 + band * 1.5)

            total_pred7_low += p7_low * qty
            total_pred7_high += p7_high * qty
            total_pred30_low += p30_low * qty
            total_pred30_high += p30_high * qty

            m = _item_metrics(conn, pos["skin_id"])
            action, reason = _adjust_action(pred["change_pct"], m["vol30"])
            buy_price = pos["buy_price"]
            pnl_pct = round((cur - buy_price) / buy_price * 100, 2) if buy_price else None

            items_out.append({
                "id": pos["id"],
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
                "predChange7d": pred["change_pct"],
                "action": action,
                "reason": reason,
                "vol30": m["vol30"],
                "maxDrawdown30": m["max_dd"],
            })

            risk_rows.append({
                "name": name, "marketValue": mv,
                "vol30": m["vol30"], "max_dd": m["max_dd"],
                "risk_contrib": mv * m["vol30"],   # 简化:市值 × 波动率
            })

    # ---- 块 1:总值区间 ----
    value_range = {
        "current": round(total_cur, 2),
        "pred7d_low": round(total_pred7_low, 2),
        "pred7d_high": round(total_pred7_high, 2),
        "pred30d_low": round(total_pred30_low, 2),
        "pred30d_high": round(total_pred30_high, 2),
        "expected7d_change_pct": round((total_pred7_low + total_pred7_high) / 2 / total_cur * 100 - 100, 2)
            if total_cur else 0.0,
    }

    # ---- 块 3:风险贡献 Top N ----
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

    # ---- LLM 汇总 ----
    summary = _summarize(items_out, value_range, risk_top)

    return {
        "generatedAt": _utcnow().isoformat(),
        "totalItems": len(items_out),
        "valueRange": value_range,
        "adjustments": items_out,
        "riskTopN": risk_top,
        "aiSummary": summary,
    }


def _summarize(items, value_range, risk_top) -> str:
    if not llm.LLM_ENABLED:
        up = value_range["expected7d_change_pct"]
        top_risk = risk_top[0]["name"] if risk_top else "—"
        return (
            f"(Mock)组合当前总值 ${value_range['current']},"
            f"7 天预计 {up:+.1f}%(区间 ${value_range['pred7d_low']}~${value_range['pred7d_high']})。"
            f"最大风险贡献来自 {top_risk},建议关注其波动。⚠ 不构成投资建议。"
        )
    prompt = (
        f"组合共 {len(items)} 件,当前总值 ${value_range['current']},"
        f"7天预测区间 ${value_range['pred7d_low']}~${value_range['pred7d_high']}"
        f"({value_range['expected7d_change_pct']:+.1f}%)。"
        f"调仓动作:{ {i['name']+'='+i['action'] for i in items} }。"
        f"风险 Top:{ [r['name']+'('+str(r['riskSharePct'])+'%)' for r in risk_top] }。"
        f"用 3 句话给组合诊断总结,含风险提示。"
    )
    return llm.chat_sync([{"role": "user", "content": prompt}], temperature=0.4)
