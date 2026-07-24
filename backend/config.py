"""
SkinVision AI 后端配置
======================
集中管理路径、DeepSeek Key、降级开关。
- DeepSeek Key 从环境变量 DEEPSEEK_API_KEY 或 .env 读取;缺失时 LLM 相关端点自动降级 Mock。
- BUFF/Skinport 实时爬虫默认关闭(课程演示用已落库历史价格 + 训练 CSV 兜底,见策划书 §13.2 降级预案)。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/ 目录 → 仓库根
BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# 加载 backend/.env(不进仓库)
load_dotenv(BACKEND_DIR / ".env")

# ---------- 路径 ----------
ML_DIR = REPO_ROOT / "ml"
DATA_DIR = ML_DIR / "data"          # train/val/test.csv
MODEL_DIR = ML_DIR / "models"       # .keras / .pkl / scaler
PRED_DIR = ML_DIR / "preds"         # 预录预测 CSV(回测/兜底用)
OUTPUT_DIR = ML_DIR / "outputs"     # 模型对比 / SHAP / 回测 JSON

# Docker volume 挂载 backend/data,本地与容器共用同一路径
DATA_RUNTIME_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_RUNTIME_DIR / "skinvision.db"
SEED_DIR = REPO_ROOT / "docs" / "expo"   # Expo 种子数据(预录辩论 JSON 等)

# ---------- DeepSeek / 通义兼容 LLM ----------
# 默认走阿里云百炼 OpenAI 兼容接口上的 DeepSeek(与 Embedding 共用 DASHSCOPE_API_KEY)。
# 若要用官方 DeepSeek,在 .env / 管理页改:
#   DEEPSEEK_BASE_URL=https://api.deepseek.com
#   DEEPSEEK_MODEL=deepseek-chat
#   DEEPSEEK_API_KEY=<官方 Key>
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v3")
# 无独立 DeepSeek Key 时,可回退到 DASHSCOPE_API_KEY(见 settings_store.apply_runtime_settings)
LLM_ENABLED = bool(DEEPSEEK_API_KEY)   # 启动后再由 apply_runtime_settings 校正

# ---------- JWT / 认证 ----------
JWT_SECRET = os.getenv("JWT_SECRET", "skinvision-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))
# 内置 demo 用户(拥有 6 件种子持仓,Expo 演示免注册)
DEMO_USERNAME = os.getenv("DEMO_USERNAME", "demo")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo123")

# 管理员账号(首次启动自动创建/提权; 默认 admin/admin123,并把 demo 提为管理员方便演示)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123").strip()
ADMIN_PROMOTE_DEMO = os.getenv("ADMIN_PROMOTE_DEMO", "1") == "1"

# ---------- 数据源开关 ----------
USE_BUFF_LIVE = os.getenv("USE_BUFF_LIVE", "0") == "1"   # 默认关:用已落库历史价
# BUFF 实时爬虫登录态(课程演示用,不进仓库;登录 buff.163.com → F12 → Cookies → session)
BUFF_COOKIE = os.getenv("BUFF_COOKIE", "").strip()
BUFF_BASE_URL = "https://buff.163.com"
BUFF_HISTORY_DAYS = int(os.getenv("BUFF_HISTORY_DAYS", "180"))   # 滚动窗口天数
# Steam 库存导入默认 cookie(必填;Steam 已限制匿名访问库存。steamcommunity.com → F12 → Cookies → steamLoginSecure)
# 弹窗里临时填的 cookie 优先于此值;此处留空则必须在弹窗里填写。
STEAM_COOKIE = os.getenv("STEAM_COOKIE", "").strip()
BUFF_REFRESH_HOURS = int(os.getenv("BUFF_REFRESH_HOURS", "6"))   # 定时刷新间隔(小时)
BUFF_REQUEST_DELAY = float(os.getenv("BUFF_REQUEST_DELAY", "1.5"))  # 礼貌限速(秒)
BUFF_BATCH_SIZE = int(os.getenv("BUFF_BATCH_SIZE", "50"))        # 分批每批件数
CATALOG_800_CSV = REPO_ROOT / "docs" / "catalog_800_buff_target.csv"
RSS_FEEDS = [
    # 较活跃的公开源;失败不崩主进程(Valve 博客更新频率低属正常)
    "https://blog.counter-strike.net/index.php/feed/",
    "https://www.hltv.org/rss/news",
]
# RSS 只入库近 N 天条目,避免旧闻刷屏
RSS_MAX_AGE_DAYS = int(os.getenv("RSS_MAX_AGE_DAYS", "14"))

# ---------- 业务参数 ----------
PRED_CACHE_TTL_HOURS = int(os.getenv("PRED_CACHE_TTL_HOURS", "6"))   # predictions 缓存有效期
LOOKBACK = 60                       # LSTM 滑动窗口(与 train_lstm_c.py 一致)
FORECAST_HORIZON = 7                # 训练目标 = 7 天后 log_price
PORTFLOTTO_BATCH = 20               # 库存 >20 件时分批诊断

# 历史兼容:训练/展示统一为 USD,不再做 CNY 换算
USD_CNY_RATE = float(os.getenv("USD_CNY_RATE", "1.0"))

# ---------- RAG 向量检索(阿里云百炼 DashScope Embedding) ----------
# 无 DASHSCOPE_API_KEY 时自动降级关键词检索
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).rstrip("/")
# text-embedding-v3 / v4; 维度仅 v3/v4 支持
RAG_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "text-embedding-v3")
RAG_EMBED_DIM = int(os.getenv("RAG_EMBED_DIM", "1024"))
RAG_USE_VECTOR = os.getenv("RAG_USE_VECTOR", "1") == "1"
RAG_INDEX_PATH = DATA_RUNTIME_DIR / "rag_vectors.npz"
RAG_EMBED_ENABLED = bool(DASHSCOPE_API_KEY) and RAG_USE_VECTOR


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODEL_DIR, PRED_DIR, OUTPUT_DIR, SEED_DIR, DATA_RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
