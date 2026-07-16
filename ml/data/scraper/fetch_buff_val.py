"""
采集 BUFF 历史价格数据 (验证集/微调用)
=========================================
输入: data/training_dataset.csv (154 件物品)
输出: data/buff_val.csv

用法: python scraper/fetch_buff_val.py
预计耗时: ~5 分钟 (154 件 × 1.5s 间隔)
"""

import sys, io, time, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data"

# BUFF Cookie — 如果过期, 重新登录 buff.163.com → F12 → Cookies → session
COOKIE = "1-lYCsuEkMKBzL2S08NqguTaUV_byUQwq0SseVQg78I_Gy2025441204"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://buff.163.com/",
}

# 读取 Kaggle 训练的 154 件物品
TRAIN_CSV = DATA_DIR / "training_dataset.csv"
OUTPUT_CSV = DATA_DIR / "buff_val.csv"


def fetch_all():
    df_train = pd.read_csv(TRAIN_CSV)
    items = df_train["market_hash_name"].unique()
    print(f"目标物品: {len(items)} 件")
    print(f"Cookie: {COOKIE[:30]}...")

    client = httpx.Client(timeout=30, follow_redirects=True)
    client.cookies.set("session", COOKIE, domain="buff.163.com")

    # 断点续传: 如果输出文件已存在, 跳过已采集的物品
    already_done = set()
    if OUTPUT_CSV.exists():
        existing = pd.read_csv(OUTPUT_CSV)
        already_done = set(existing["market_hash_name"].unique())
        print(f"已有 {len(already_done)} 件已采集, 将跳过")

    all_history = []
    if OUTPUT_CSV.exists():
        all_history = existing.to_dict("records")
    found = len(already_done)
    not_found = 0
    errors = 0

    for i, name in enumerate(items, 1):
        if name in already_done:
            print(f"\n[{i}/{len(items)}] {name} — SKIP (already fetched)")
            continue
        print(f"\n[{i}/{len(items)}] {name}")

        # 搜索 (最多 3 次重试)
        goods_id = None
        for attempt in range(3):
            try:
                # 用 market_hash_name 的前 40 字符搜索 (太长了 BUFF 不认)
                search_query = name[:60]
                r = client.get(
                    "https://buff.163.com/api/market/goods",
                    params={"game": "csgo", "page_num": 1, "page_size": 5, "search": search_query},
                    headers=HEADERS,
                )
                data = r.json()
                if data.get("code") == "OK" and data["data"]["items"]:
                    # 精确匹配 market_hash_name
                    for item in data["data"]["items"]:
                        if item.get("market_hash_name") == name:
                            goods_id = item["id"]
                            break
                    if goods_id:
                        break
                    # 如果没有精确匹配, 取第一个 (可能是翻译差异)
                    goods_id = data["data"]["items"][0]["id"]
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(3)
                else:
                    print(f"  SEARCH ERROR: {e}")
                    errors += 1

        if not goods_id:
            print(f"  NOT FOUND on BUFF")
            not_found += 1
            continue

        # 获取历史价格
        hist_data = None
        for attempt in range(3):
            try:
                r2 = client.get(
                    "https://buff.163.com/api/market/goods/price_history",
                    params={"game": "csgo", "goods_id": goods_id, "days": 180},
                    headers=HEADERS,
                )
                hist_data = r2.json()
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(3)

        if hist_data and hist_data.get("code") == "OK":
            history = hist_data["data"]["price_history"]
            if not history:
                print(f"  EMPTY HISTORY (0 points)")
                not_found += 1
                time.sleep(1.5)
                continue
            for ts, price in history:
                date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                all_history.append({
                    "date": date_str,
                    "market_hash_name": name,
                    "price": price,
                    "source": "BUFF",
                })
            prices = [p[1] for p in history]
            print(f"  OK: {len(history)} pts, ${min(prices):.2f} ~ ${max(prices):.2f}")
            found += 1
        else:
            print(f"  HISTORY EMPTY: {hist_data.get('code') if hist_data else 'N/A'}")
            errors += 1

        time.sleep(1.5)  # 礼貌限速

    # 保存 (去重: BUFF 每天 ~2 个数据点, 保留每个物品每天最后一条)
    if all_history:
        df = pd.DataFrame(all_history)
        df = df.sort_values(["market_hash_name", "date"]).reset_index(drop=True)
        before = len(df)
        df = df.drop_duplicates(subset=["date", "market_hash_name"], keep="last")
        after = len(df)
        print(f"\n  去重: {before:,} -> {after:,} (删除 {before-after:,} 条日重复)")
        df.to_csv(OUTPUT_CSV, index=False)

        print(f"\n{'='*60}")
        print(f"BUFF 数据采集完成")
        print(f"  找到: {found}/{len(items)} 件")
        print(f"  未找到: {not_found}")
        print(f"  错误: {errors}")
        print(f"  总行数: {len(df):,}")
        print(f"  日期范围: {df['date'].min()} ~ {df['date'].max()}")
        print(f"  已保存: {OUTPUT_CSV}")
    else:
        print("\nERROR: 没有采集到任何数据! 检查 Cookie 是否过期。")


if __name__ == "__main__":
    fetch_all()
