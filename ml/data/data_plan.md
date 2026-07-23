# CSVest — 模型训练数据集说明

> **更新:** 2026-07-15 | **版本:** V1.2 (外部特征 + BUFF 验证集 + 时序分割)

---

## 1. 数据集概览

| 数据集 | 文件 | 行数 | 件数 | 日期范围 | 作用 |
|--------|------|-----:|:---:|------|------|
| **训练集** | `train.csv` | 158,174 | 147 | 2019-01-01 ~ 2022-01-29 | 训练模型参数 |
| **验证集** | `val.csv` | 36,206 | 154 | 2022-01-30 ~ 2022-09-27 | 调超参 / 早停 |
| **测试集** | `test.csv` | 36,307 | 154 | 2022-09-28 ~ 2023-05-26 | 最终评估 (RMSE/MAE/MAPE) |
| **BUFF 验证集** | `buff_val.csv` | 12,551 | 87 | 2026-01-16 ~ 2026-07-10 | 跨时代验证: CS:GO 模型 → CS2 市场 |
| **完整合并集** | `training_dataset.csv` | 230,687 | 154 | 2019-01-01 ~ 2023-05-26 | 备用 (train+val+test 合集) |

| 属性 | 数值 |
|------|------|
| **主数据来源** | Kaggle `leawind/steam-market-price-dataset-csgo` |
| **验证数据来源** | BUFF API — 87 件物品, 180 天 USD 历史价格 |
| **物品总数** | **154** 件 (每种磨损独立 + StatTrak 独立) |
| **时序分割** | 70/15/15, 按日期切分, train/val/test 日期零重叠 |

---

## 2. 字段说明 (14 列)

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | str (YYYY-MM-DD) | 交易日期 |
| `market_hash_name` | str | 物品完整名称 (含磨损/StatTrak 标记) |
| `price` | float64 | USD 当日中位价 (未修改,原值) |
| `daily_volume` | int64 | 当日成交量 (sells) |
| `weapon_type` | str | 武器基类型 (AK-47, AWP, Butterfly Knife, ...) |
| `rarity` | str | CS2 稀有度等级 |
| `wear` | str | 磨损 (FN/MW/FT/WW/BS, 刀/箱为 null) |
| `is_stattrak` | int | 是否 StatTrak 版本 (0/1) |
| `is_floor_price` | int | Steam 底价标记 (price <= $0.05) |
| `days_to_next_major` | int | 距下一个 Major 天数 (>0 未来, <0 已过去, =0 赛期内) |
| `days_since_last_major` | int | 距上一个 Major 结束天数 |
| `is_major_active` | int | Major 赛期内为 1 (0/1) |
| `steam_ccu` | int | Steam 当日在线峰值 (月级近似, steamcharts) |
| `days_since_cs2_announce` | int | 距 CS2 公布天数 (>0 公布后, <0 公布前) |

---

## 3. 品类分布

| 大类别 | 物品数 | 说明 |
|--------|:------:|------|
| Rifle 步枪 | 50 | AK-47, AWP, M4A1-S, M4A4, FAMAS, Galil, AUG, SG 553, SSG 08 |
| Pistol 手枪 | 35 | USP-S, Glock-18, Desert Eagle, P250, Five-SeveN, Tec-9, CZ75, R8, Dual |
| SMG | 26 | MAC-10, MP9, UMP-45, P90, MP7, MP5-SD, PP-Bizon |
| Heavy 重型 | 17 | XM1014, MAG-7, Nova, Sawed-Off |
| Knife 刀具 | 14 | Butterfly, Karambit, M9, Bayonet, Talon, Skeleton, Flip, Bowie |
| Glove 手套 | 7 | Driver Gloves, Sport Gloves, Specialist Gloves, Hand Wraps |
| Case 箱子 | 5 | Danger Zone, Dreams & Nightmares, Chroma, Recoil, Revolution |
| **总计** | **154** | |

---

## 4. 稀有度分布

| 稀有度 | 颜色 | 物品数 |
|--------|:----:|:------:|
| Consumer Grade 消费级 | 白 | 5 |
| Industrial Grade 工业级 | 青 | 13 |
| Mil-Spec 军规级 | 蓝 | 44 |
| Restricted 受限级 | 紫 | 38 |
| Classified 保密级 | 粉 | 15 |
| Covert 隐秘级 | 红 | 13 |
| Rare 稀有 | 金 | 21 |
| Base Grade 基础 | — | 5 (箱子) |

---

## 5. 磨损 + StatTrak + 地板价

| 维度 | 分布 |
|------|------|
| **磨损** | FN 99,484 / FT 57,707 / MW 31,144 / BS 20,368 / WW 15,091 / null 6,893 (刀/箱) |
| **StatTrak** | 普通 208,738 行 / StatTrak 21,949 行 (15 件唯一物品) |
| **地板价** | `is_floor_price=1`: 75,682 行 (32.8%, ~55 件物品). 仅标记, 不修改 price |

---

## 6. 外部市场特征

| 特征 | 范围 | 说明 |
|------|------|------|
| `days_to_next_major` | -18 ~ 778 | 距下一个 Major start 的天数 |
| `days_since_last_major` | -43 ~ 790 | 距上一个 Major end 的天数 |
| `is_major_active` | 13,010 行 (5.6%) | 6 届 Major 赛期标记 |
| `steam_ccu` | 13.5M ~ 30M | Steam 月级同时在线人数 |
| `days_since_cs2_announce` | -1541 ~ 65 | CS2 2023-03-22 公布日偏移 |

**Major 数据:** IEM Katowice 2019, StarLadder Berlin 2019, PGL Stockholm 2021, PGL Antwerp 2022, IEM Rio 2022, BLAST Paris 2023

**Steam CCU:** 月级近似值 (steamcharts.com), 2019-01 ~ 2023-06

---

## 7. 价格统计

| 指标 | 数值 |
|------|------|
| 均值 | $35.59 |
| 中位数 | $0.12 |
| 最小值 | $0.03 |
| 最大值 | $1,976.89 |
| 标准差 | $120.36 |

> 价格分布严重右偏 — 大量低价消费品 + 少量高价刀/手套. `is_floor_price` 标记供下游模型使用, 不做强行修改.

---

## 8. 数据文件

```
socProject/ml/data/
├── data_plan.md               ← 本文件
├── train.csv                  ← 训练集 (16 MB, 158k 行, 70%)
├── val.csv                    ← 验证集 (3.6 MB, 36k 行, 15%)
├── test.csv                   ← 测试集 (3.6 MB, 36k 行, 15%)
├── training_dataset.csv       ← 完整合并集 (23 MB, 154 件, 14 列)
├── buff_val.csv               ← BUFF CS2 验证集 (715 KB, 87 件, 12k 行)
├── code/
│   ├── build_dataset.py       ← 数据集构建脚本 (可重复运行)
│   ├── cs2_rarity_db.py       ← CS2 稀有度映射数据库 (437 条目)
│   └── external_features.py   ← 外部特征 (Major/CCU/CS2 事件)
└── scraper/
    ├── fetch_buff_val.py      ← BUFF 历史价格采集 (断点续传)
    ├── fetch_live_prices.py   ← 多平台实时行情 CLI (Skinport/BUFF/Steam)
    ├── platforms.py           ← 平台适配器
    ├── requirements.txt       ← httpx + brotli (+ pandas)
    └── test_fetch_live_prices.py
```

---

## 9. 使用方法

### 重新生成 Kaggle 数据集
```bash
cd socProject/ml/data
python code/build_dataset.py
```

### 重新采集 BUFF 验证数据
```bash
cd socProject/ml/data
python scraper/fetch_buff_val.py
```

### 多平台实时行情
```bash
cd socProject/ml/data/scraper
pip install -r requirements.txt

# 免 Cookie 批量平台(推荐默认)
python fetch_live_prices.py --platforms skinport,waxpeer,marketcsgo,lootfarm,csgotrader --limit 10

# BUFF 需登录 Cookie; Steam/CSFloat 限流严格
export BUFF_SESSION='your_buff_session_cookie'
python fetch_live_prices.py --platforms skinport,buff,steam,csfloat --spread \\
    --items "AK-47 | Redline (Field-Tested)" "AWP | Asiimov (Field-Tested)"

# 轮询写入 live_quotes.csv
python fetch_live_prices.py --platforms waxpeer,marketcsgo --watch --interval 120 --append
```

支持平台: `skinport` / `buff` / `steam` / `waxpeer` / `marketcsgo` / `lootfarm` / `csgotrader` / `csfloat`。

输出字段: `fetched_at, platform, market_hash_name, currency, price, price_native, buy_price, sell_price, volume, ok, error`。
BUFF 价格按 `USD_CNY_RATE`(默认 7.2)换算为 USD 写入 `price`。

### 加载训练数据
```python
import pandas as pd
train = pd.read_csv("ml/data/train.csv")
val   = pd.read_csv("ml/data/val.csv")
test  = pd.read_csv("data/test.csv")
# CS2 市场验证
buff  = pd.read_csv("data/buff_val.csv")
```

---

## 10. 筛选逻辑 (与 dataset_prompt.md 对照)

| 要求 | 实现 |
|------|------|
| 时间 2019-2023 | timestamp 过滤: 2019-01-01 ~ 2023-05-26 |
| ~150 高流动性武器皮肤 | 分层采样 154 件: Rifle 50 + Pistol 35 + SMG 26 + Heavy 17 + Knife 14 + Glove 7 + Case 5 |
| 每种磨损独立 | 是 (FN/MW/FT/WW/BS 各自独立) |
| 排除 Souvenir | 是 (1,357 件 Souvenir 已排除) |
| StatTrak 独立 | 是 (15 件 StatTrak 通过补充配额入选) |
| >=100 数据点 | 是 (所有入选物品满足) |
| 品类内 Top N 成交量 | 是 (分层配额, 每品类按成交量排名) |
| 稀有度解析 | 437 条目映射 + 刀/手套自动 Rare + 箱子自动 Base Grade |
| 数据清洗 | 去重 254 万行, 价格区间过滤, 成交量 clip |
| 外部特征 | Major 赛事周期 + Steam CCU + CS2 公布日 |
| 时序分割 | 70/15/15 按全局时间线切分, 严禁随机打乱 |

---

## 11. BUFF 验证集

BUFF API 为 87/154 件训练集物品提供了 CS2 时期的实际交易数据:

| 指标 | 数值 |
|------|------|
| 物品 | 87 件 (覆盖所有品类) |
| 行数 | 12,551 |
| 日期 | 2026-01-16 ~ 2026-07-10 |
| 价格范围 | $0.03 ~ $2,057.81 |
| 采集方式 | `scraper/fetch_buff_val.py` (断点续传, 去重) |

**用途:** 模型在 Kaggle (CS:GO 时期) 上训练后, 用 BUFF 数据验证在 CS2 真实市场中的预测精度. 87 件物品同时出现在 train.csv 和 buff_val.csv 中, 可直接逐物品对比.

---

## 12. 已知限制

| 限制 | 说明 | 改进方向 |
|------|------|----------|
| **稀有度按 skin 名映射** | 同一 skin 在不同武器上可能有不同稀有度 | 接入 BUFF API 逐物品校验 |
| **Steam CCU 月级粒度** | 全月相同值, 丢失日间波动 | 下载 steamcharts 日级 CSV |
| **BUFF 覆盖率 56%** | 67 件物品 (刀/冷门磨损) 未在 BUFF 找到 | 扩大搜索或接受低覆盖率 |
| **价格底价占比高** | 32.8% 行价格 <= $0.05, 大部分为真实廉价品 | 已加 is_floor_price 标记供模型自行处理 |

---

*数据文档 — SWS3022 CSVest V1.2, 2026-07-15*
