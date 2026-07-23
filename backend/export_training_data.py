"""
导出 price_history → 训练 CSV(组员 3 · 任务 2 "作为训练数据源")
==============================================================
把爬到的 BUFF 价格 + skins 元数据 + exogenous 默认值,导成与 ml/data/train.csv
同 schema 的面板 CSV,供组员 1/2 用 build_features 重训模型。

输出: ml/data/buff_training_panel.csv
列:  date, market_hash_name, price, daily_volume, weapon_type, rarity, wear,
     is_stattrak, is_floor_price, days_to_next_major, days_since_last_major,
     is_major_active, days_since_cs2_announce, steam_ccu

运行: python export_training_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database import get_connection
from config import ML_DIR, BUFF_HISTORY_DAYS

OUTPUT = ML_DIR / "data" / "buff_training_panel.csv"


def export() -> Path:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.date, s.market_hash_name, p.price, p.daily_volume,
                      s.weapon_type, s.rarity, s.wear, s.is_stattrak, s.is_floor_price
               FROM price_history p
               JOIN skins s ON s.id = p.skin_id
               ORDER BY s.market_hash_name, p.date"""
        ).fetchall()
    if not rows:
        print("[export] price_history 为空,先跑 scraper_buff.py")
        return OUTPUT

    df = pd.DataFrame([dict(r) for r in rows])
    # exogenous 默认值(BUFF 不提供;与 model_loader._skin_window_from_db 一致)
    df["days_to_next_major"] = 0
    df["days_since_last_major"] = 0
    df["is_major_active"] = 0
    df["days_since_cs2_announce"] = 0
    df["steam_ccu"] = 0.0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT, index=False)
    print(f"[export] 已导出 {len(df):,} 行 × {len(df.columns)} 列 → {OUTPUT}")
    print(f"  物品: {df['market_hash_name'].nunique()} 件")
    print(f"  日期: {df['date'].min()} ~ {df['date'].max()}")
    return OUTPUT


if __name__ == "__main__":
    export()
