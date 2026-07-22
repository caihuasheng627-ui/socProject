"""
SkinVision AI — 数据库层
========================
SQLite 建表 + 导入 + 种子(组员 3 主线第 1 步)。

7 张表 + portfolio 转正(P0 核心表,加 holding_type):
  skins / price_history / predictions / news / model_registry / alerts / portfolio

数据来源(课程演示,策划书 §13.2 降级口径):
  - skins        : train.csv 去重导入(weapon_type/rarity/wear/is_stattrak)
  - price_history: train+val+test 回填(BUFF 实时爬虫关闭,训练 CSV 兜底)
  - portfolio    : Expo 种子 3-5 件预置持仓(real/sim 混合)
  - news         : 几条种子资讯(RSS 采集由 scheduler 增量补充)
  - model_registry: 8 模型指标(读 ml/outputs/*.json)

幂等:重复 init 不会重复导入(skins/price_history 走 INSERT OR IGNORE / 行数判断)。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import (
    DATA_DIR,
    DB_PATH,
    MODEL_DIR,
    OUTPUT_DIR,
    PRED_CACHE_TTL_HOURS,
    SEED_DIR,
    ensure_dirs,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ---------- 枚举映射 ----------
WEAR_FULL = {
    "FN": "Factory New",
    "MW": "Minimal Wear",
    "FT": "Field-Tested",
    "WW": "Well-Worn",
    "BS": "Battle-Scarred",
}

# weapon_type → 前端 category(中文)
def weapon_to_category(weapon: str) -> str:
    w = (weapon or "").lower()
    name = w
    if "knife" in name or "bayonet" in name or "karambit" in name \
       or "butterfly" in name or "talon" in name or "stiletto" in name \
       or "navaja" in name or "skeleton" in name or "falchion" in name \
       or "bowie" in name or "flip" in name or "gut " in name \
       or name.startswith("★") or name.startswith("m9"):
        return "刀具"
    if "gloves" in name or "glove" in name or "hand wraps" in name or "wraps" in name:
        return "手套"
    if "case" in name or "container" in name:
        return "箱子"
    if name.startswith("awp") or name.startswith("ssg") or name.startswith("scar") \
       or name.startswith("g3sg1"):
        return "狙击枪"
    if any(name.startswith(p) for p in (
        "ak-47", "ak47", "m4a1", "m4a4", "famas", "galil", "aug", "sg 553", "sg553"
    )):
        return "步枪"
    # 其余枪械(手枪 / SMG / 霰弹)暂归入手枪筛选项,避免丢失
    return "手枪"

# rarity → 1-7 等级(策划书 §3.4)
RARITY_RANK = {
    "consumer": 1, "industrial": 2, "milspec": 3, "mil-spec": 3,
    "restricted": 4, "classified": 5, "covert": 6,
    "contraband": 7, "knife": 7, "gloves": 7, "rare": 7,
}

def rarity_to_rank(rarity: str) -> int:
    key = (rarity or "").lower().replace(" ", "").replace("-", "")
    for k, v in RARITY_RANK.items():
        if k.replace("-", "") in key:
            return v
    return 4


def slugify(name: str) -> str:
    """market_hash_name → URL 友好 slug。AK-47 | Redline (FT) → ak-47-redline-ft"""
    import re
    s = (name or "").lower()
    # 把缩写 wear 也保留
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "skin"


# ============================================================
# 连接
# ============================================================
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ============================================================
# 建表
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS skins (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market_hash_name TEXT UNIQUE NOT NULL,
    slug             TEXT,
    weapon_type      TEXT,
    rarity           TEXT,
    rarity_rank      INTEGER DEFAULT 4,
    wear             TEXT,
    wear_full        TEXT,
    is_stattrak      INTEGER DEFAULT 0,
    is_floor_price   INTEGER DEFAULT 0,
    category         TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skin_id       INTEGER NOT NULL,
    date          TEXT NOT NULL,
    price         REAL NOT NULL,        -- USD 原价(与训练同口径)
    daily_volume  INTEGER DEFAULT 0,
    FOREIGN KEY (skin_id) REFERENCES skins(id),
    UNIQUE (skin_id, date)
);
CREATE INDEX IF NOT EXISTS idx_price_skin ON price_history(skin_id, date);

CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skin_id         INTEGER NOT NULL,
    horizon         INTEGER NOT NULL,
    model           TEXT NOT NULL,
    type            TEXT,               -- 统计 / ML / DL
    predicted_price REAL,
    current_price   REAL,
    change_pct      REAL,
    confidence      REAL,
    generated_at    TEXT,
    expires_at      TEXT,
    FOREIGN KEY (skin_id) REFERENCES skins(id)
);
CREATE INDEX IF NOT EXISTS idx_pred_skin ON predictions(skin_id, horizon, model);

CREATE TABLE IF NOT EXISTS news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT,
    summary       TEXT,
    source        TEXT,                 -- valve/steam/hltv/reddit/internal
    url           TEXT,
    published_at  TEXT,
    sentiment     TEXT DEFAULT 'neutral',  -- positive/negative/neutral
    impact        TEXT DEFAULT 'low',      -- high/medium/low
    related_skins TEXT                     -- 逗号分隔 market_hash_name
);
CREATE INDEX IF NOT EXISTS idx_news_time ON news(published_at);

CREATE TABLE IF NOT EXISTS model_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    type          TEXT,                 -- 统计/ML/DL
    version       TEXT DEFAULT 'v1',
    path          TEXT,
    incremental   INTEGER DEFAULT 0,
    metrics_json  TEXT,
    trained_at    TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,                -- 🆕 所属用户(NULL=旧数据,归 demo)
    skin_id      INTEGER NOT NULL,
    type         TEXT,                  -- above / below
    target_price REAL,
    note         TEXT,
    active       INTEGER DEFAULT 1,
    triggered    INTEGER DEFAULT 0,
    created_at   TEXT,
    FOREIGN KEY (skin_id) REFERENCES skins(id)
);

-- 🆕 portfolio 转正(P0 核心表),加 holding_type(real/sim)
CREATE TABLE IF NOT EXISTS portfolio (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,               -- 🆕 所属用户(NULL=旧数据,归 demo)
    skin_id       INTEGER NOT NULL,
    holding_type  TEXT DEFAULT 'real',   -- real=真实持仓 / sim=模拟持仓
    buy_price     REAL,                  -- 可空(模拟持仓可不填成本)
    buy_date      TEXT,
    quantity      INTEGER DEFAULT 1,
    note          TEXT,
    created_at    TEXT,
    FOREIGN KEY (skin_id) REFERENCES skins(id)
);

-- 🆕 用户表(注册/登录)
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,         -- bcrypt(sha256(p)) base64
    created_at    TEXT,
    is_demo       INTEGER DEFAULT 0      -- 1=内置 demo 用户
);
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return column in cols


def migrate_add_user_columns() -> None:
    """幂等:给已存在的 portfolio/alerts 表补 user_id 列 + 索引(新库由 CREATE TABLE 已带列)。"""
    with get_connection() as conn:
        if not _column_exists(conn, "portfolio", "user_id"):
            conn.execute("ALTER TABLE portfolio ADD COLUMN user_id INTEGER")
        if not _column_exists(conn, "alerts", "user_id"):
            conn.execute("ALTER TABLE alerts ADD COLUMN user_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id)")
        conn.commit()


def ensure_demo_user() -> int:
    """创建内置 demo 用户(若缺),并把 user_id IS NULL 的 portfolio/alerts 行归给它。"""
    from config import DEMO_USERNAME, DEMO_PASSWORD
    from auth import hash_password  # 延迟 import,避免循环依赖
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (DEMO_USERNAME,)).fetchone()
        if row:
            demo_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users(username, password_hash, created_at, is_demo) VALUES (?,?,?,1)",
                (DEMO_USERNAME, hash_password(DEMO_PASSWORD), _utcnow().isoformat()),
            )
            demo_id = cur.lastrowid
        # 把旧的无主持仓/预警归给 demo(仅对已有库生效一次)
        conn.execute("UPDATE portfolio SET user_id=? WHERE user_id IS NULL", (demo_id,))
        conn.execute("UPDATE alerts SET user_id=? WHERE user_id IS NULL", (demo_id,))
        conn.commit()
        return demo_id


def init_schema() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
    migrate_add_user_columns()


# ============================================================
# 导入 skins + price_history
# ============================================================
def import_skins_and_prices(force: bool = False) -> None:
    """从 train/val/test.csv 导入 skins(去重)+ price_history(全量回填)。"""
    ensure_dirs()
    with get_connection() as conn:
        n_skins = conn.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
        n_price = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        if not force and n_skins > 0 and n_price > 0:
            print(f"[db] skins={n_skins} price_history={n_price} 已存在,跳过导入")
            return

        # ---- 读三份 CSV 拼成全量面板 ----
        frames = []
        for split in ("train", "val", "test"):
            p = DATA_DIR / f"{split}.csv"
            if p.exists():
                df = pd.read_csv(p, parse_dates=["date"])
                df["_split"] = split
                frames.append(df)
        if not frames:
            print("[db] ⚠ 找不到 train/val/test.csv,跳过导入")
            return
        panel = pd.concat(frames, ignore_index=True)

        # ---- skins 去重 ----
        skin_cols = ["market_hash_name", "weapon_type", "rarity", "wear",
                     "is_stattrak", "is_floor_price"]
        skins = (panel[skin_cols]
                 .drop_duplicates(subset=["market_hash_name"])
                 .sort_values("market_hash_name"))
        skin_rows = []
        for _, r in skins.iterrows():
            name = str(r["market_hash_name"])
            skin_rows.append((
                name,
                slugify(name),
                str(r.get("weapon_type") or ""),
                str(r.get("rarity") or ""),
                rarity_to_rank(str(r.get("rarity") or "")),
                str(r.get("wear") or ""),
                WEAR_FULL.get(str(r.get("wear") or "").upper(), str(r.get("wear") or "")),
                int(bool(r.get("is_stattrak"))),
                int(bool(r.get("is_floor_price"))),
                weapon_to_category(str(r.get("weapon_type") or "")),
            ))
        conn.executemany(
            """INSERT OR IGNORE INTO skins
               (market_hash_name, slug, weapon_type, rarity, rarity_rank,
                wear, wear_full, is_stattrak, is_floor_price, category)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            skin_rows,
        )

        # ---- skin_id 映射 ----
        id_map = {row["market_hash_name"]: row["id"]
                  for row in conn.execute("SELECT id, market_hash_name FROM skins")}

        # ---- price_history 回填(分批 executemany)----
        if n_price == 0 or force:
            ph = panel[["market_hash_name", "date", "price", "daily_volume"]].copy()
            ph["skin_id"] = ph["market_hash_name"].map(id_map)
            ph = ph.dropna(subset=["skin_id"])
            ph["date"] = pd.to_datetime(ph["date"]).dt.strftime("%Y-%m-%d")
            ph["daily_volume"] = ph["daily_volume"].fillna(0).astype(int)
            rows = [(int(s), d, float(p), int(v)) for s, d, p, v in
                    zip(ph["skin_id"], ph["date"], ph["price"], ph["daily_volume"])]
            # 分批,避免单次事务过大
            BATCH = 20000
            for i in range(0, len(rows), BATCH):
                conn.executemany(
                    "INSERT OR IGNORE INTO price_history(skin_id, date, price, daily_volume) VALUES (?,?,?,?)",
                    rows[i:i + BATCH],
                )
            print(f"[db] 导入 skins={len(skin_rows)} price_history={len(rows)}")

        conn.commit()


# ============================================================
# 种子:portfolio / news / model_registry
# ============================================================
SEED_PORTFOLIO_NAMES = [
    # (market_hash_name 片段匹配, holding_type, buy_price 倍数相对最新价, quantity, note)
    ("AK-47 | Elite Build",      "real", 0.85, 1, "主力持仓"),
    ("AWP | Asiimov",            "real", 0.90, 1, "长线"),
    ("M4A1-S | Briefing",        "sim",  None, 2, "模拟跟踪"),
    ("USP-S | Cyrex",            "real", 1.05, 1, "短期"),
    ("★ Karambit | Doppler",     "sim",  None, 1, "高价值模拟"),
    ("★ Driver Gloves | Overtake","real",0.78, 1, "手套持仓"),
]

SEED_NEWS = [
    ("Valve 发布 CS2 春季更新,饰品市场活跃度提升", "更新涉及武器磨损与贴图重做,市场流动性短期上升。", "valve", "positive", "medium"),
    ("BLAST Major 巴黎站落幕,相关贴纸饰品需求回暖", "Major 后 7-14 天相关饰品成交量通常上升 15-30%。", "hltv", "positive", "high"),
    ("BUFF 平台部分高价值饰品挂单减少", "高价值饰品流动性下降,短期价格波动可能加大。", "internal", "neutral", "low"),
    ("社区热议新箱子掉落率调整", "若掉落率下调,箱子价格可能上行。", "reddit", "positive", "medium"),
]


def seed_portfolio() -> None:
    """Expo 预置 3-5 件持仓(real/sim 混合),归 demo 用户。仅在空库时执行。"""
    from config import DEMO_USERNAME
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0] > 0:
            return
        demo_row = conn.execute("SELECT id FROM users WHERE username=?", (DEMO_USERNAME,)).fetchone()
        if demo_row is None:
            return  # ensure_demo_user 应已创建;兜底跳过
        demo_id = demo_row["id"]
        today = _utcnow().strftime("%Y-%m-%d")
        for frag, htype, buy_mult, qty, note in SEED_PORTFOLIO_NAMES:
            row = conn.execute(
                "SELECT id, market_hash_name FROM skins WHERE market_hash_name LIKE ? LIMIT 1",
                (f"{frag}%",),
            ).fetchone()
            if not row:
                continue
            skin_id = row["id"]
            # 最新价
            p = conn.execute(
                "SELECT price FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT 1",
                (skin_id,),
            ).fetchone()
            cur = p["price"] if p else None
            buy_price = round(cur * buy_mult, 2) if (buy_mult and cur) else None
            buy_date = (_utcnow() - timedelta(days=45)).strftime("%Y-%m-%d")
            conn.execute(
                """INSERT INTO portfolio(skin_id, holding_type, buy_price, buy_date, quantity, note, created_at, user_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (skin_id, htype, buy_price, buy_date, qty, note, today, demo_id),
            )
        n = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        print(f"[db] 种子 portfolio={n} 件(归 demo 用户)")
        conn.commit()


def seed_news() -> None:
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] > 0:
            return
        now = _utcnow()
        for i, (title, summary, source, sent, impact) in enumerate(SEED_NEWS):
            conn.execute(
                """INSERT INTO news(title, summary, source, url, published_at, sentiment, impact, related_skins)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (title, summary, source, "", (now - timedelta(days=i)).isoformat(), sent, impact, ""),
            )
        conn.commit()
        print(f"[db] 种子 news={len(SEED_NEWS)} 条")


def seed_model_registry() -> None:
    """读 ml/outputs/*.json 填 8 模型指标。"""
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] > 0:
            return
        cmp = {}
        for fn in ("compare_results.json", "model_comparison.json"):
            p = OUTPUT_DIR / fn
            if p.exists():
                try:
                    cmp[fn] = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass

        # 名称 → (type, incremental, path, metrics 来源)
        registry = [
            ("ARIMA",        "统计", 1, None, "model_comparison.json"),
            ("XGBoost",      "ML",   1, "xgb_reg.pkl", None),
            ("LightGBM",     "ML",   1, "lightgbm_reg.pkl", None),
            ("RandomForest", "ML",   0, "rf_reg.pkl", None),
            ("LSTM-C",       "DL",   0, "lstm_c.keras", "compare_results.json"),
            ("LSTM-D",       "DL",   0, "lstm_d_*.keras", "compare_results.json"),
            ("GRU",          "DL",   0, "gru.keras", "compare_results.json"),
            ("XGBoost-CLS",  "ML",   1, "xgb_cls.pkl", None),
        ]
        now = _utcnow().isoformat()
        for name, mtype, incr, path, msrc in registry:
            metrics = {}
            if msrc and msrc in cmp:
                block = cmp[msrc].get(name, {})
                if block:
                    metrics = {k: v for k, v in block.items()
                               if k in ("rmse", "mae", "mape", "r2", "items", "direction", "backtest")}
            # model_comparison.json 里是 list
            if not metrics and "model_comparison.json" in cmp:
                for item in cmp["model_comparison.json"].get("regression", []):
                    if item.get("name", "").lower().replace(" ", "") == name.lower().replace("-", "").replace(" ", ""):
                        metrics = item
                        break
            conn.execute(
                """INSERT OR REPLACE INTO model_registry(name, type, version, path, incremental, metrics_json, trained_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, mtype, "v1", path, incr, json.dumps(metrics, ensure_ascii=False), now),
            )
        conn.commit()
        print(f"[db] 种子 model_registry={len(registry)} 个")


# ============================================================
# 查询辅助
# ============================================================
def resolve_skin(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """skinId 可能是 slug / market_hash_name / 数字 id,统一解析。"""
    if not key:
        return None
    # 数字 id
    if key.isdigit():
        r = conn.execute("SELECT * FROM skins WHERE id=?", (int(key),)).fetchone()
        if r:
            return r
    # slug
    r = conn.execute("SELECT * FROM skins WHERE slug=?", (key,)).fetchone()
    if r:
        return r
    # market_hash_name 精确
    r = conn.execute("SELECT * FROM skins WHERE market_hash_name=?", (key,)).fetchone()
    if r:
        return r
    # 模糊(LIKE)兜底
    r = conn.execute("SELECT * FROM skins WHERE market_hash_name LIKE ?", (f"%{key}%",)).fetchone()
    return r


def latest_price(conn: sqlite3.Connection, skin_id: int) -> tuple[float | None, str | None]:
    row = conn.execute(
        "SELECT price, date FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT 1",
        (skin_id,),
    ).fetchone()
    return ((row["price"], row["date"]) if row else (None, None))


def change_pct(conn: sqlite3.Connection, skin_id: int, days: int) -> float | None:
    rows = conn.execute(
        "SELECT price FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT ?",
        (skin_id, days + 1),
    ).fetchall()
    if len(rows) < 2:
        return None
    cur = rows[0]["price"]
    old = rows[-1]["price"]
    if not old:
        return None
    return round((cur - old) / old * 100, 2)


# ============================================================
# 启动入口
# ============================================================
def run_init() -> None:
    ensure_dirs()
    init_schema()
    import_skins_and_prices()
    ensure_demo_user()      # 创建 demo 用户 + 回填无主 portfolio/alerts(须在 seed_portfolio 前)
    seed_portfolio()
    seed_news()
    seed_model_registry()
    # 确保 Expo 种子目录存在(seed_data.py 会写入)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[db] 初始化完成 → {DB_PATH}")


if __name__ == "__main__":
    run_init()
