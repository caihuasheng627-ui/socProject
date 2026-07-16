"""
外部市场事件特征
=================
提供给 add_external_features(df) 调用，按 date 列左连接外部特征。

特征:
  days_to_next_major      — 距下一个 Major 天数 (>0未来, <0已过去, =0赛期内)
  days_since_last_major   — 距上一个 Major 结束天数
  is_major_active         — 是否 Major 赛期 (0/1)
  days_since_cs2_announce — 距 CS2 公布天数 (>0公布后)
  steam_ccu               — Steam 当日在线峰值 (月级近似)
"""

import pandas as pd

# ============================================================
# CS:GO/CS2 Major Championships 2019-2023
# 来源: Liquipedia (https://liquipedia.net/counterstrike/Majors)
# 格式: (start_date, end_date, name)
# ============================================================
MAJORS = [
    ("2019-02-13", "2019-03-03", "IEM Katowice 2019"),
    ("2019-08-23", "2019-09-08", "StarLadder Berlin 2019"),
    ("2021-10-26", "2021-11-07", "PGL Stockholm 2021"),
    ("2022-05-09", "2022-05-22", "PGL Antwerp 2022"),
    ("2022-10-31", "2022-11-13", "IEM Rio 2022"),
    ("2023-05-08", "2023-05-21", "BLAST Paris 2023"),
]

# ============================================================
# Steam CCU — 每月近似值 (steamcharts.com)
# 精确日数据需下载 CSV, 这里先放月级近似值
# 来源: https://steamcharts.com/app/730
# ============================================================
STEAM_CCU_MONTHLY = {
    # 2019
    "2019-01": 14000000, "2019-02": 14500000, "2019-03": 15000000,
    "2019-04": 14000000, "2019-05": 13500000, "2019-06": 14200000,
    "2019-07": 14500000, "2019-08": 15000000, "2019-09": 14800000,
    "2019-10": 14000000, "2019-11": 14200000, "2019-12": 15500000,
    # 2020 (COVID 推动在线人数暴增)
    "2020-01": 16000000, "2020-02": 17000000, "2020-03": 22000000,
    "2020-04": 24000000, "2020-05": 22000000, "2020-06": 21000000,
    "2020-07": 20500000, "2020-08": 20000000, "2020-09": 19500000,
    "2020-10": 19000000, "2020-11": 18800000, "2020-12": 21000000,
    # 2021
    "2021-01": 21500000, "2021-02": 22000000, "2021-03": 22500000,
    "2021-04": 23000000, "2021-05": 22000000, "2021-06": 21500000,
    "2021-07": 21000000, "2021-08": 20800000, "2021-09": 20500000,
    "2021-10": 22000000, "2021-11": 23500000, "2021-12": 24000000,
    # 2022
    "2022-01": 25000000, "2022-02": 25500000, "2022-03": 25000000,
    "2022-04": 24500000, "2022-05": 26000000, "2022-06": 24000000,
    "2022-07": 23000000, "2022-08": 23500000, "2022-09": 24000000,
    "2022-10": 24500000, "2022-11": 26000000, "2022-12": 27000000,
    # 2023 (CS2 发布年)
    "2023-01": 27500000, "2023-02": 28000000, "2023-03": 30000000,
    "2023-04": 29000000, "2023-05": 28000000, "2023-06": 27500000,
}


def add_external_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入: 含 date 列 (YYYY-MM-DD) 的 DataFrame
    输出: 添加了外部特征列的 DataFrame

    特征:
      days_to_next_major   — >0 未来 X 天, <0 已过去 |X| 天, =0 赛期内
      days_since_last_major — 距上一个 Major 结束天数
      is_major_active      — 是否 Major 赛期 (0/1)
      steam_ccu            — Steam 当日在线峰值 (月级近似)
      days_since_cs2_announce — 距 CS2 公布天数 (>0 公布后, <0 公布前)
    """
    df = df.copy()
    dates = pd.to_datetime(df["date"])

    # --- Major 赛事特征 ---
    major_starts = pd.to_datetime([m[0] for m in MAJORS])
    major_ends = pd.to_datetime([m[1] for m in MAJORS])

    days_to_next = []
    days_since_last_end = []
    is_active = []

    for d in dates:
        # days_to_next_major:
        #   赛期内 → 0
        #   赛期外 → 正数(距下一个 start) / 负数(距上一个 start 已过去)
        in_major = 0
        current_major_end = None
        for start, end in zip(major_starts, major_ends):
            if start <= d <= end:
                in_major = 1
                current_major_end = end
                break

        is_active.append(in_major)

        if in_major:
            days_to = 0
        else:
            future_starts = major_starts[major_starts > d]
            if len(future_starts) > 0:
                days_to = (future_starts[0] - d).days  # 正数
            else:
                # 所有 Major 都已结束
                days_to = -(d - major_starts[-1]).days  # 负数

        days_to_next.append(days_to)

        # days_since_last_major_end: 距离上一个 Major 结束多少天
        past_ends = major_ends[major_ends <= d]
        if len(past_ends) > 0:
            days_since = (d - past_ends[-1]).days
        else:
            days_since = (d - major_starts[0]).days  # 第一个 Major 之前, 返回负数
        days_since_last_end.append(days_since)

    df["days_to_next_major"] = days_to_next
    df["days_since_last_major"] = days_since_last_end
    df["is_major_active"] = is_active

    # --- CS2 重大事件 ---
    cs2_announce = pd.Timestamp("2023-03-22")
    df["days_since_cs2_announce"] = (dates - cs2_announce).dt.days  # 负=公布前, 正=公布后

    # --- Steam CCU (月级近似值) ---
    month_keys = dates.dt.strftime("%Y-%m")
    df["steam_ccu"] = month_keys.map(STEAM_CCU_MONTHLY)
    df["steam_ccu"] = df["steam_ccu"].ffill().bfill().astype(int)

    print(f"\n  外部特征已添加:")
    print(f"    days_to_next_major: {df['days_to_next_major'].min()} ~ {df['days_to_next_major'].max()}")
    print(f"    days_since_last_major: {df['days_since_last_major'].min()} ~ {df['days_since_last_major'].max()}")
    print(f"    is_major_active: {df['is_major_active'].sum():,} 行")
    print(f"    days_since_cs2_announce: {df['days_since_cs2_announce'].min()} ~ {df['days_since_cs2_announce'].max()}")
    print(f"    steam_ccu: {df['steam_ccu'].min():,} ~ {df['steam_ccu'].max():,}")

    return df
