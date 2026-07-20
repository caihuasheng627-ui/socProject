"""
Expo 种子数据生成(组员 3 · D13 前冻结)
========================================
生成:
  - docs/expo/seed_debate_<slug>.json  : 3-5 件预录辩论(默认模板;有 DEEPSEEK_API_KEY 时可 --live 重生成)
  - docs/expo/seed_daily_report.json   : 1 篇预生成日报
  - docs/expo/seed_portfolio.json      : 预置持仓快照(供前端 Expo 兜底)

运行:
  python seed_data.py            # 模板生成
  python seed_data.py --live     # 用 DeepSeek 现场生成预录(有 Key 时)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import SEED_DIR
from database import get_connection, run_init
import agent_debate
import scheduler

# Expo 预置辩论的 5 件(覆盖各价位段)
SEED_DEBATE_SLUGS_LIKE = [
    "AK-47 | Elite Build",
    "AWP | Asiimov",
    "M4A1-S | Briefing",
    "★ Karambit | Doppler",
    "★ Driver Gloves | Overtake",
]


def gen_debates(live: bool = False) -> int:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with get_connection() as conn:
        for frag in SEED_DEBATE_SLUGS_LIKE:
            row = conn.execute(
                "SELECT slug, market_hash_name FROM skins WHERE market_hash_name LIKE ? LIMIT 1",
                (f"{frag}%",),
            ).fetchone()
            if not row:
                continue
            slug = row["slug"]
            result = agent_debate.debate(slug, live=live, mode="bull_bear")
            result["skinId"] = slug
            result["name"] = row["market_hash_name"]
            result["mode"] = "pre_recorded"
            out = SEED_DIR / f"seed_debate_{slug}.json"
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✅ {out.name}")
            n += 1
    return n


def gen_daily_report() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    report = scheduler.generate_daily_report()
    out = SEED_DIR / "seed_daily_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ {out.name}")


def gen_portfolio_snapshot() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.id, s.slug, s.market_hash_name, p.holding_type, p.buy_price,
                      p.quantity, p.buy_date, p.note
               FROM portfolio p JOIN skins s ON s.id=p.skin_id ORDER BY p.id"""
        ).fetchall()
    snapshot = [{"id": r["id"], "skinId": r["slug"], "name": r["market_hash_name"],
                 "holdingType": r["holding_type"], "buyPrice": r["buy_price"],
                 "quantity": r["quantity"], "buyDate": r["buy_date"], "note": r["note"]}
                for r in rows]
    out = SEED_DIR / "seed_portfolio.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ {out.name} ({len(snapshot)} 件)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="用 DeepSeek 现场生成辩论(需 Key)")
    args = parser.parse_args()

    run_init()
    print("[seed] 生成预录辩论...")
    n = gen_debates(live=args.live)
    print(f"[seed] 辩论 {n} 份 ({'live' if args.live else 'template'})")
    print("[seed] 生成日报...")
    gen_daily_report()
    print("[seed] 生成持仓快照...")
    gen_portfolio_snapshot()
    print("[seed] 完成 →", SEED_DIR)


if __name__ == "__main__":
    main()
