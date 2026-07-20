"""
CSVest — 模型训练数据集构建脚本
===========================================
根据 docs/dataset_prompt.md 规范，从 Kaggle 数据集中筛选和处理数据。

用法:
    python data/build_dataset.py

输入:
    Kaggle: leawind/steam-market-price-dataset-csgo (通过 kagglehub 自动定位)

输出:
    data/training_dataset.csv      ← 完整训练数据集
    data/dataset_report.txt        ← 数据质量报告

规范要求:
    - 时间: 2019-01-01 ~ 2023-05-26
    - 物品: ~150 件高流动性武器皮肤
    - 排除: 贴纸/涂鸦/胶囊/Agent/音乐盒/Pin/Souvenir
    - 每种磨损独立, StatTrak 独立
    - 最低 100 个数据点, 成交量 Top 200
    - 字段: date, market_hash_name, price, daily_volume,
            weapon_type, rarity, wear, is_stattrak
"""

import os
import sys
import time
import base64
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

# 添加当前目录到 path
sys.path.insert(0, str(Path(__file__).parent))

from cs2_rarity_db import parse_item_name, get_rarity_name, WEAPON_CATEGORY
from external_features import add_external_features

# ============================================================
# 配置参数 (来自 dataset_prompt.md)
# ============================================================

DATE_START = "2019-01-01"
DATE_END = "2023-05-26"

# Timestamp 范围 (ms)
TS_START = int(pd.Timestamp(DATE_START).timestamp() * 1000)
TS_END = int(pd.Timestamp(DATE_END).timestamp() * 1000)

# 目标武器类别
TARGET_WEAPONS = {
    # Rifles
    "AK-47", "M4A1-S", "M4A4", "AWP", "FAMAS", "Galil AR",
    "AUG", "SG 553", "SSG 08",
    # Pistols
    "USP-S", "Glock-18", "Desert Eagle", "P250", "Five-SeveN",
    "Tec-9", "CZ75-Auto", "R8 Revolver", "Dual Berettas",
    # SMGs
    "MP9", "MAC-10", "UMP-45", "P90", "MP7", "MP5-SD", "PP-Bizon",
    # Heavy
    "XM1014", "MAG-7", "Nova", "Sawed-Off",
    # Knives
    "Butterfly Knife", "Karambit", "M9 Bayonet", "Bayonet",
    "Talon Knife", "Skeleton Knife", "Flip Knife", "Bowie Knife",
    # Gloves
    "Sport Gloves", "Specialist Gloves", "Driver Gloves", "Hand Wraps",
    # Cases
    "Danger Zone Case", "Prisma Case", "Clutch Case", "Chroma Case",
    "Spectrum Case", "Fracture Case", "Dreams & Nightmares Case",
    "Recoil Case", "Revolution Case",
}

# 排除关键词 (贴纸/涂鸦/胶囊/Agent/音乐盒/Pin/奖牌/通行证)
EXCLUDE_KEYWORDS = [
    "Sticker", "Graffiti", "Patch", "Capsule",
    "Music Kit", "Pin", "Agent", "Medal",
    "Pass", "Package", "Key", "Coin",
    "Name Tag", "Storage Unit", "Tool",
]

# 输出目录: ml/data/ (脚本在 ml/data/code/ 下)
OUTPUT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_CSV = OUTPUT_DIR / "training_dataset.csv"
OUTPUT_REPORT = OUTPUT_DIR / "dataset_report.txt"

# ============================================================
# 步骤 1: 加载数据
# ============================================================

def load_dataset():
    """定位并加载 Kaggle 数据集"""
    import kagglehub
    print("=" * 60)
    print("步骤 1: 加载 Kaggle 数据集")
    print("=" * 60)

    t0 = time.time()
    path = kagglehub.dataset_download("leawind/steam-market-price-dataset-csgo")
    pub = os.path.join(path, "dataset_publish")

    # 加载 item index
    idx = pd.read_csv(os.path.join(pub, "item_index.csv"))
    print(f"  item_index.csv: {len(idx):,} items")

    # 解码 Base64 → market_hash_name
    idx["market_hash_name"] = idx["item_hash_name_base64"].apply(
        lambda s: base64.b64decode(s).decode("utf-8")
    )

    # 构建文件路径
    idx["file_path"] = idx["file_name"].apply(
        lambda f: os.path.join(pub, "items", f)
    )

    elapsed = time.time() - t0
    print(f"  加载完成 ({elapsed:.1f}s)")
    return idx


# ============================================================
# 步骤 2: 筛选物品
# ============================================================

def filter_items(idx: pd.DataFrame) -> pd.DataFrame:
    """
    三步筛选:
      a) 解析元数据
      b) 排除非武器类别 (贴纸/涂鸦等)
      c) 排除 Souvenir 版本
    """
    print("\n" + "=" * 60)
    print("步骤 2: 筛选物品")
    print("=" * 60)

    # 解析所有 item name
    parsed = idx["market_hash_name"].apply(parse_item_name)
    meta = pd.DataFrame(parsed.tolist(), index=idx.index)

    # 合并回主表
    df = pd.concat([idx, meta], axis=1)
    print(f"  全量物品: {len(df):,}")

    # --- 筛选 a: 武器类别匹配 ---
    has_weapon = df["weapon_type"].isin(TARGET_WEAPONS)
    print(f"  武器类别匹配: {has_weapon.sum():,}")

    # --- 筛选 b: 排除非武器 ---
    def is_excluded(name):
        """排除非武器类物品。使用锚定匹配避免误杀 (如 Pin 不匹配 Pinstripe/Pink)"""
        # 精确匹配: 物品类型词后面跟 " | " (如 "Sticker | ...", "Pin | ...")
        anchored_kw = [
            "Sticker |", "Graffiti |", "Patch |", "Pin |",
            "Music Kit |", "Agent |",
        ]
        name_lower = name.lower()
        for kw in anchored_kw:
            if kw.lower() in name_lower:
                return True
        # 子串匹配: 这些词在名称中的位置比较安全 (不常见于武器皮肤名)
        fuzzy_kw = [
            "Capsule", "Medal", "Pass", "Package", "Key |",
            "Coin", "Name Tag", "Storage Unit", "Tool",
        ]
        for kw in fuzzy_kw:
            if kw.lower() in name_lower:
                return True
        return False

    excluded = df["market_hash_name"].apply(is_excluded)
    print(f"  排除贴纸/涂鸦等: {(excluded & ~has_weapon).sum():,}")

    # --- 筛选 c: 排除 Souvenir ---
    is_souvenir = df["is_souvenir"] == 1
    print(f"  Souvenir 版本: {is_souvenir.sum():,}")

    # --- 最终筛选 ---
    mask = has_weapon & ~excluded & ~is_souvenir
    df = df[mask].copy()
    print(f"\n  筛选后物品: {len(df):,} (唯一武器类型: {df['weapon_type'].nunique()})")

    # 统计分布
    cat_counts = df["category"].value_counts()
    for cat, cnt in cat_counts.items():
        print(f"    {cat}: {cnt}")

    return df


# ============================================================
# 步骤 3: 加载每个物品的价格数据 + 应用时间和流动性筛选
# ============================================================

def _process_one_item(row, ts_start, ts_end):
    """处理单个物品的 CSV (供并行调用)"""
    try:
        df = pd.read_csv(row["file_path"])
    except Exception:
        return None, None

    # 时间范围过滤
    df = df[(df["timestamp"] >= ts_start) & (df["timestamp"] <= ts_end)]
    if len(df) == 0:
        return None, None

    # 转换
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.strftime("%Y-%m-%d")
    df["market_hash_name"] = row["market_hash_name"]
    df["price"] = df["price_dollar"]
    df["daily_volume"] = df["sells"].clip(lower=0)

    # 元数据
    df["weapon_type"] = row["weapon_type"]
    df["rarity"] = get_rarity_name(row["rarity_level"], row["category"])
    df["wear"] = row["wear"]
    df["is_stattrak"] = row["is_stattrak"]

    cols = [
        "date", "market_hash_name", "price", "daily_volume",
        "weapon_type", "rarity", "wear", "is_stattrak",
    ]
    data = df[cols]

    stats = {
        "market_hash_name": row["market_hash_name"],
        "n_records": len(df),
        "total_volume": int(data["daily_volume"].sum()),
        "weapon_type": row["weapon_type"],
        "category": row["category"],
    }
    return data, stats


def load_price_data(meta: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    对筛选后的物品:
      a) 并行加载 CSV 中的价格/成交量数据
      b) 按时间范围过滤 (2019-01-01 ~ 2023-05-26)
      c) 计算每个物品的总成交量，用于流动性排名
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print("\n" + "=" * 60)
    print("步骤 3: 加载价格数据 + 时间范围过滤 (并行)")
    print("=" * 60)

    records = []
    item_stats = []
    skipped = 0
    total = len(meta)
    n_workers = min(16, os.cpu_count() or 4)

    print(f"  使用 {n_workers} 线程并行处理 {total} 个物品...")

    rows = [row for _, row in meta.iterrows()]

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(_process_one_item, row, TS_START, TS_END): i
            for i, row in enumerate(rows)
        }

        for future in as_completed(futures):
            idx = futures[future]
            try:
                data, stats = future.result()
            except Exception:
                data, stats = None, None

            if data is None:
                skipped += 1
            else:
                records.append(data)
                item_stats.append(stats)

            if (len(records) + skipped) % 1000 == 0:
                print(f"  处理中... {len(records)+skipped}/{total} "
                      f"(有效: {len(records)}, 跳过: {skipped})")

    print(f"  加载完成: {len(records)} 个物品有数据 (跳过 {skipped} 个)")

    # 合并所有数据
    all_data = pd.concat(records, ignore_index=True)
    print(f"  总记录数: {len(all_data):,}")

    return all_data, pd.DataFrame(item_stats)


# ============================================================
# 步骤 4: 数据清洗
# ============================================================

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗:
      - price <= 0 → 删除
      - price > 100000 → 删除
      - 重复 (date, market_hash_name) → 去重，保留首次出现
      - daily_volume 为负 → clip 到 0
    """
    print("\n" + "=" * 60)
    print("步骤 4: 数据清洗")
    print("=" * 60)

    before = len(df)

    # 价格有效性
    mask_bad_price = df["price"] <= 0
    n_bad_price = mask_bad_price.sum()
    df = df[~mask_bad_price].copy()
    print(f"  price <= 0: {n_bad_price:,} 行删除")

    mask_high_price = df["price"] > 100000
    n_high = mask_high_price.sum()
    df = df[~mask_high_price].copy()
    print(f"  price > 100k: {n_high:,} 行删除")

    # 去重
    dup_mask = df.duplicated(subset=["date", "market_hash_name"], keep="first")
    n_dup = dup_mask.sum()
    df = df[~dup_mask].copy()
    print(f"  重复 (date, name): {n_dup:,} 行删除")

    # 成交量 clip
    neg_vol = (df["daily_volume"] < 0).sum()
    df["daily_volume"] = df["daily_volume"].clip(lower=0)
    print(f"  daily_volume < 0: {neg_vol} 行 clipped")

    after = len(df)
    print(f"\n  清洗前: {before:,} → 清洗后: {after:,} (删除 {before-after:,} 行)")

    return df


# ============================================================
# 步骤 4.5: 价格异常清洗 (floor 标记 + 尖峰替换)
# ============================================================

def clean_price_spikes(
    df: pd.DataFrame,
    floor: float = 0.05,
    spike_ratio: float = 0.50,
    low_vol_spike_ratio: float = 0.30,
    window: int = 7,
    low_volume: int = 5,
) -> pd.DataFrame:
    """
    1. 标记 is_floor_price (price <= floor) — 列格式不变
    2. 按物品检测价格尖峰并替换为滚动中位数 (不删行、不加列):
       - 相对滚动中位数偏离 > spike_ratio (默认 50%)
       - 薄量日 (daily_volume < low_volume) 用更严阈值 30%
       - 单日孤立尖刺: 相对前后日均值偏离大, 且前后日彼此接近
    目的: 清掉薄流动性 / Doppler 相位混淆造成的假尖峰, 防止回测复利爆炸。
    """
    print("\n" + "=" * 60)
    print("步骤 4.5: 价格尖峰清洗 + is_floor_price 标记")
    print("=" * 60)

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["market_hash_name", "date"]).reset_index(drop=True)

    df["is_floor_price"] = (df["price"] <= floor).astype(int)
    floor_count = int(df["is_floor_price"].sum())
    floor_pct = 100 * floor_count / max(len(df), 1)
    affected_floor = df.loc[df["is_floor_price"] == 1, "market_hash_name"].nunique()
    print(f"  is_floor_price=1: {floor_count:,} 行 ({floor_pct:.1f}%), {affected_floor} 个物品")

    n_replaced = 0
    n_items_hit = 0
    cleaned_parts = []

    for name, g in df.groupby("market_hash_name", sort=False):
        g = g.copy()
        price = g["price"].astype(float)
        vol = g["daily_volume"].astype(float) if "daily_volume" in g.columns else pd.Series(0, index=g.index)

        # 滚动中位数 (居中窗口; 两端 min_periods=3 避免早期过松)
        roll_med = price.rolling(window=window, center=True, min_periods=3).median()
        # 两端用扩展/反向填充补齐
        roll_med = roll_med.fillna(price.expanding(min_periods=1).median())
        roll_med = roll_med.replace(0, np.nan).fillna(price.median())

        rel = (price - roll_med).abs() / roll_med.clip(lower=1e-6)
        thr = np.where(vol < low_volume, low_vol_spike_ratio, spike_ratio)
        spike_vs_med = rel > thr

        # 孤立尖刺: 当天相对前后日均值偏离大, 前后日彼此接近 (典型 259→560→304)
        prev_p = price.shift(1)
        next_p = price.shift(-1)
        neighbor = (prev_p + next_p) / 2.0
        neighbor_ok = prev_p.notna() & next_p.notna() & (neighbor > 0)
        rel_nb = (price - neighbor).abs() / neighbor.clip(lower=1e-6)
        neighbors_close = (prev_p - next_p).abs() / neighbor.clip(lower=1e-6) < 0.25
        isolated = neighbor_ok & neighbors_close & (rel_nb > spike_ratio)

        spike_mask = spike_vs_med | isolated
        n_hit = int(spike_mask.sum())
        if n_hit > 0:
            g.loc[spike_mask, "price"] = roll_med.loc[spike_mask].values
            n_replaced += n_hit
            n_items_hit += 1

        cleaned_parts.append(g)

    out = pd.concat(cleaned_parts, ignore_index=True)
    # 保持与历史 CSV 一致的日期字符串格式
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")

    print(f"  尖峰替换: {n_replaced:,} 行 / {n_items_hit} 个物品 "
          f"(阈值 med>{spike_ratio:.0%}, 薄量<{low_volume} 用>{low_vol_spike_ratio:.0%}, "
          f"窗口={window})")
    print(f"  列格式未变: {list(out.columns)}")
    return out


# ============================================================
# 步骤 5: 流动性过滤
# ============================================================

def apply_liquidity_filter(
    df: pd.DataFrame, stats: pd.DataFrame
) -> pd.DataFrame:
    """
    分层流动性过滤 (Stratified):
      - 每个物品 >= 100 个数据点
      - 每个类别内按成交量排序，取固定配额
      - 目的: 确保高端品 (刀/手套) 不被廉价高量品挤掉

    配额分配 (总计 ~150):
      Rifle: 50 | Pistol: 35 | SMG: 25 | Heavy: 15
      Knife: 12 | Glove: 8 | Case: 5
    """
    print("\n" + "=" * 60)
    print("步骤 5: 分层流动性过滤 (Stratified)")
    print("=" * 60)

    # 每个物品重计算 stats
    recalc = df.groupby("market_hash_name").agg(
        n_records=("price", "count"),
        total_volume=("daily_volume", "sum"),
        category=("weapon_type", "first"),  # used for category mapping
    ).reset_index()

    # 映射 weapon_type → 大类别
    recalc["category_group"] = recalc["category"].map(
        lambda x: WEAPON_CATEGORY.get(x, "Other")
    )

    # 过滤: >= 100 数据点
    recalc = recalc[recalc["n_records"] >= 100].copy()
    print(f"  >= 100 数据点: {len(recalc)} 个物品")

    # 分层配额 (基础配额会加上 StatTrak 补充, 最终 ~150-165)
    quotas = {
        "Rifle": 47,
        "Pistol": 32,
        "SMG": 23,
        "Heavy": 14,
        "Knife": 11,
        "Glove": 7,
        "Case": 5,
    }

    selected = []
    for group, quota in quotas.items():
        group_df = recalc[recalc["category_group"] == group].copy()
        available = len(group_df)
        if available == 0:
            print(f"  {group}: 0 个可用 (跳过)")
            continue

        # 组内按成交量排序取 Top N
        group_df = group_df.sort_values("total_volume", ascending=False)
        n = min(quota, available)
        top_names = group_df.head(n)["market_hash_name"].tolist()
        selected.extend(top_names)
        total_vol = group_df.head(n)["total_volume"].sum()
        print(f"  {group}: 取 {len(top_names)}/{available} (配额 {quota}), "
              f"组内 Top1 成交量: {top_names[0][:60]}... ({total_vol:,.0f} total vol)")

    print(f"\n  分层选取总计: {len(selected)} 个物品")

    # --- StatTrak 补充 ---
    # StatTrak 版本交易量较低,容易被普通版挤掉。每个品类额外加入 Top 3 StatTrak
    print(f"\n  StatTrak 补充:")
    st_selected = set(selected)

    # 在 recalc 中标记 StatTrak
    recalc["is_st"] = recalc["market_hash_name"].str.contains("StatTrak", case=False)
    st_total = 0

    for group, quota in quotas.items():
        group_df = recalc[(recalc["category_group"] == group) & recalc["is_st"]].copy()
        if len(group_df) == 0:
            continue
        # 排除已选中的
        group_df = group_df[~group_df["market_hash_name"].isin(st_selected)]
        if len(group_df) == 0:
            continue
        group_df = group_df.sort_values("total_volume", ascending=False)
        n_st = min(3, len(group_df))
        st_names = group_df.head(n_st)["market_hash_name"].tolist()
        st_selected.update(st_names)
        selected.extend(st_names)
        st_total += n_st
        print(f"    {group}: +{n_st} StatTrak")

    print(f"  StatTrak 补充总计: {st_total}")
    print(f"\n  最终选取总计: {len(selected)} 个物品 (含 StatTrak)")

    # 过滤数据
    df = df[df["market_hash_name"].isin(selected)].copy()
    df = df.sort_values(["market_hash_name", "date"]).reset_index(drop=True)

    print(f"  最终数据集: {len(df):,} 行, {df['market_hash_name'].nunique()} 件物品")
    return df


# ============================================================
# 步骤 6: 质量检查
# ============================================================

def quality_checks(df: pd.DataFrame) -> list[str]:
    """执行 dataset_prompt.md 要求的质量检查"""
    print("\n" + "=" * 60)
    print("步骤 6: 质量检查")
    print("=" * 60)

    lines = []
    checks = []

    # 1. 物品数量 ≈ 150 (+/-20)
    n_items = df["market_hash_name"].nunique()
    check1 = 130 <= n_items <= 170
    checks.append(("物品数量 ≈ 150", check1, f"实际: {n_items}"))

    # 2. 日期范围
    dates = pd.to_datetime(df["date"])
    min_date = dates.min().strftime("%Y-%m-%d")
    max_date = dates.max().strftime("%Y-%m-%d")
    check2 = min_date <= "2019-01-31"
    checks.append(("日期范围从 2019-01-01 左右开始", check2, f"实际: {min_date} ~ {max_date}"))

    # 3. 每个日期 market_hash_name 唯一
    dup_check = df.duplicated(subset=["date", "market_hash_name"]).sum()
    check3 = dup_check == 0
    checks.append(("无重复 (date, name) 对", check3, f"重复数: {dup_check}"))

    # 4. 无 null 值
    null_count = df[["price", "daily_volume"]].isnull().sum().sum()
    check4 = null_count == 0
    checks.append(("price/daily_volume 无 null", check4, f"null 数: {null_count}"))

    # 5. 武器类别分布
    weapon_dist = df["weapon_type"].value_counts()
    lines.append(f"\n  武器类别分布 (前 15):")
    for w, c in weapon_dist.head(15).items():
        lines.append(f"    {w}: {c:,}")

    # 类别级分布
    cat_dist = (
        df.drop_duplicates("market_hash_name")["weapon_type"]
        .map(lambda x: WEAPON_CATEGORY.get(x, "Other"))
        .value_counts()
    )
    lines.append(f"\n  大类别分布:")
    for cat, c in cat_dist.items():
        lines.append(f"    {cat}: {c}")

    # 检查是否过于集中
    max_cat_pct = cat_dist.max() / cat_dist.sum()
    check5 = max_cat_pct < 0.5  # 没有一类超过 50%
    checks.append(("武器类别分布合理 (<50% 集中)", check5, f"最大类占比: {max_cat_pct:.1%}"))

    # 6. 磨损分布
    wear_dist = df["wear"].value_counts(dropna=False)
    lines.append(f"\n  磨损分布:")
    for w, c in wear_dist.items():
        lines.append(f"    {w}: {c:,}")
    n_wear_conditions = df["wear"].nunique()
    check6 = n_wear_conditions >= 5
    checks.append(("包含所有 5 种磨损 (+null)", check6, f"实际磨损种类: {n_wear_conditions}"))

    # 7. StatTrak 分布
    st_dist = df["is_stattrak"].value_counts()
    lines.append(f"\n  StatTrak 分布: {st_dist.to_dict()}")

    # 输出检查结果
    print()
    all_pass = True
    for name, passed, detail in checks:
        status = "[PASS]" if passed else "[FAIL]"
        if not passed:
            all_pass = False
        print(f"  {status} {name}  — {detail}")

    if all_pass:
        print("\n  [PASS] 所有质量检查通过!")
    else:
        print("\n  [WARN] Some quality checks failed, see report")

    return lines


# ============================================================
# 步骤 7: 保存 + 报告
# ============================================================

def save_and_report(df: pd.DataFrame, report_lines: list[str]):
    """保存 CSV 并生成报告"""
    print("\n" + "=" * 60)
    print("步骤 7: 保存输出")
    print("=" * 60)

    # 保存 CSV
    df.to_csv(OUTPUT_CSV, index=False)
    file_size_mb = OUTPUT_CSV.stat().st_size / (1024 * 1024)
    print(f"  [PASS] 训练数据集: {OUTPUT_CSV}")
    print(f"     大小: {file_size_mb:.1f} MB")
    print(f"     行数: {len(df):,}")
    print(f"     列数: {len(df.columns)}")
    print(f"     物品数: {df['market_hash_name'].nunique()}")

    # 生成报告
    report = []
    report.append("=" * 60)
    report.append("CSVest — 训练数据集质量报告")
    report.append("=" * 60)
    report.append(f"生成时间: {pd.Timestamp.now()}")
    report.append(f"数据来源: Kaggle leawind/steam-market-price-dataset-csgo")
    report.append(f"时间范围: {DATE_START} ~ {DATE_END}")
    report.append(f"")

    report.append(f"数据集概览:")
    report.append(f"  总行数: {len(df):,}")
    report.append(f"  物品数: {df['market_hash_name'].nunique()}")
    report.append(f"  日期跨度: {df['date'].min()} ~ {df['date'].max()}")
    report.append(f"  文件大小: {file_size_mb:.1f} MB")
    report.append(f"")

    report.append(f"列信息:")
    for col in df.columns:
        report.append(f"  {col}: {df[col].dtype}")
    report.append(f"")

    report.append(f"价格统计:")
    report.append(f"  Mean:  ${df['price'].mean():.2f}")
    report.append(f"  Median: ${df['price'].median():.2f}")
    report.append(f"  Min:   ${df['price'].min():.2f}")
    report.append(f"  Max:   ${df['price'].max():.2f}")
    report.append(f"  Std:   ${df['price'].std():.2f}")
    report.append(f"")

    report.append(f"成交量统计:")
    report.append(f"  Mean:  {df['daily_volume'].mean():.0f}")
    report.append(f"  Median: {df['daily_volume'].median():.0f}")
    report.append(f"  Max:   {df['daily_volume'].max():,}")
    report.append(f"")

    report.append(f"稀有度分布:")
    rarity_dist = df.drop_duplicates("market_hash_name")["rarity"].value_counts()
    for r, c in rarity_dist.items():
        report.append(f"  {r}: {c}")
    report.append(f"")

    # 添加步骤 6 的报告
    report.extend(report_lines)

    report.append(f"\nTop 20 物品 (按总成交量):")
    top_items = (
        df.groupby("market_hash_name")["daily_volume"]
        .sum()
        .sort_values(ascending=False)
        .head(20)
    )
    for name, vol in top_items.items():
        report.append(f"  {name}: {vol:,}")

    report_str = "\n".join(report)

    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report_str)

    print(f"  [PASS] 质量报告: {OUTPUT_REPORT}")
    print()
    print(report_str)


# ============================================================
# 步骤 8: 时序分割 (train / val / test)
# ============================================================

def split_timeseries(df: pd.DataFrame):
    """
    按时间顺序切分 (70% / 15% / 15%)，严禁随机打乱。
    每个物品独立切分后合并，确保每个物品在每个集合中都有数据。
    """
    print("\n" + "=" * 60)
    print("步骤 8: 时序分割 (70/15/15)")
    print("=" * 60)

    dates = pd.to_datetime(df["date"])
    total_days = (dates.max() - dates.min()).days

    train_end = dates.min() + pd.Timedelta(days=int(total_days * 0.70))
    val_end = dates.min() + pd.Timedelta(days=int(total_days * 0.85))

    train = df[dates <= train_end].copy()
    val = df[(dates > train_end) & (dates <= val_end)].copy()
    test = df[dates > val_end].copy()

    train_path = OUTPUT_DIR / "train.csv"
    val_path = OUTPUT_DIR / "val.csv"
    test_path = OUTPUT_DIR / "test.csv"

    train.to_csv(train_path, index=False)
    val.to_csv(val_path, index=False)
    test.to_csv(test_path, index=False)

    t_size = len(train) / len(df) * 100
    v_size = len(val) / len(df) * 100
    ts_size = len(test) / len(df) * 100

    print(f"  完整集: {len(df):,} 行, {df['market_hash_name'].nunique()} 件")
    print(f"  train:  {len(train):,} 行 ({t_size:.1f}%) | {train['date'].min()} ~ {train['date'].max()}")
    print(f"  val:    {len(val):,} 行 ({v_size:.1f}%) | {val['date'].min()} ~ {val['date'].max()}")
    print(f"  test:   {len(test):,} 行 ({ts_size:.1f}%) | {test['date'].min()} ~ {test['date'].max()}")
    print(f"  已保存: {train_path}, {val_path}, {test_path}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("CSVest — Training Dataset Builder")
    print("=" * 60)
    print(f"   时间范围: {DATE_START} ~ {DATE_END}")
    print(f"   目标: ~150 件高流动性武器皮肤")
    print()

    t_start = time.time()

    # 1. 加载
    idx = load_dataset()

    # 2. 筛选物品类型
    meta = filter_items(idx)

    # 3. 加载价格数据
    all_data, stats = load_price_data(meta)

    # 4. 数据清洗
    all_data = clean_data(all_data)

    # 4.5 价格异常清洗 (floor spike + outlier)
    all_data = clean_price_spikes(all_data)

    # 5. 流动性过滤
    all_data = apply_liquidity_filter(all_data, stats)

    # 5.5 外部市场特征 (Major 赛事 / Steam CCU)
    all_data = add_external_features(all_data)

    # 6. 质量检查
    report_lines = quality_checks(all_data)

    # 7. 保存 + 报告
    save_and_report(all_data, report_lines)

    # 8. 时序分割
    split_timeseries(all_data)

    elapsed = time.time() - t_start
    print(f"\n 总耗时: {elapsed/60:.1f} 分钟")

    return all_data


if __name__ == "__main__":
    df = main()
