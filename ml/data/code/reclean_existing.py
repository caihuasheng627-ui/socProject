"""
对已有 training_dataset.csv 重跑步骤 4.5 尖峰清洗 + 时序切分。
不依赖 Kaggle 源数据; 列格式保持不变。

用法 (在 ml/data/code/ 下):
  python reclean_existing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import (  # noqa: E402
    OUTPUT_CSV,
    OUTPUT_DIR,
    clean_price_spikes,
    quality_checks,
    save_and_report,
    split_timeseries,
)

EXPECTED_COLS = [
    "date", "market_hash_name", "price", "daily_volume",
    "weapon_type", "rarity", "wear", "is_stattrak", "is_floor_price",
    "days_to_next_major", "days_since_last_major", "is_major_active",
    "days_since_cs2_announce", "steam_ccu",
]


def main():
    # 优先读 ml/data/training_dataset.csv; 若不在 OUTPUT_DIR 则回退同级
    src = OUTPUT_CSV if OUTPUT_CSV.exists() else Path(__file__).resolve().parent.parent / "training_dataset.csv"
    if not src.exists():
        raise FileNotFoundError(f"找不到 {src}")

    print(f"读取: {src}")
    df = pd.read_csv(src)
    cols_before = list(df.columns)
    print(f"  行数={len(df):,}  列={cols_before}")

    if cols_before != EXPECTED_COLS:
        missing = [c for c in EXPECTED_COLS if c not in cols_before]
        extra = [c for c in cols_before if c not in EXPECTED_COLS]
        print(f"  [WARN] 列与期望不完全一致 missing={missing} extra={extra}")
        # 仍要求核心列齐全
        for c in ("date", "market_hash_name", "price"):
            if c not in df.columns:
                raise ValueError(f"缺少核心列: {c}")

    # 尖峰清洗前抽样: Bowie Doppler 尖峰
    bowie = "★ Bowie Knife | Doppler (Factory New)"
    if bowie in set(df["market_hash_name"]):
        sub = df[df["market_hash_name"] == bowie].sort_values("date")
        print(f"\n清洗前 {bowie}:")
        print(f"  price min/max = {sub['price'].min():.3f} / {sub['price'].max():.3f}")

    cleaned = clean_price_spikes(df)

    # 强制列顺序与清洗前一致 (格式不变)
    cleaned = cleaned[cols_before]

    if bowie in set(cleaned["market_hash_name"]):
        sub2 = cleaned[cleaned["market_hash_name"] == bowie].sort_values("date")
        print(f"\n清洗后 {bowie}:")
        print(f"  price min/max = {sub2['price'].min():.3f} / {sub2['price'].max():.3f}")
        # 展示原尖峰日附近
        sample = sub2[sub2["date"].between("2022-02-12", "2022-02-20")]
        print(sample[["date", "price", "daily_volume"]].to_string(index=False))

    assert list(cleaned.columns) == cols_before, "列格式被改变!"
    assert len(cleaned) == len(df), "行数不应变化 (只替换价格)"

    report_lines = quality_checks(cleaned)
    save_and_report(cleaned, report_lines)
    split_timeseries(cleaned)

    # 同步确认写出路径
    for name in ("training_dataset.csv", "train.csv", "val.csv", "test.csv"):
        p = OUTPUT_DIR / name
        print(f"  {'OK' if p.exists() else 'MISSING'}: {p} ({p.stat().st_size if p.exists() else 0:,} bytes)")

    print("\n完成: 尖峰已清洗, train/val/test 已重切, 列格式未变。")


if __name__ == "__main__":
    main()
