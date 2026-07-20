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

# ---------- DeepSeek ----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_ENABLED = bool(DEEPSEEK_API_KEY)   # 无 Key → 全部走 Mock/预录回放

# ---------- 数据源开关 ----------
USE_BUFF_LIVE = os.getenv("USE_BUFF_LIVE", "0") == "1"   # 默认关:用已落库历史价
RSS_FEEDS = [
    # 课程演示用 RSS 源;失败不崩主进程
    "https://blog.counter-strike.net/index.php/feed/",
]

# ---------- 业务参数 ----------
PRED_CACHE_TTL_HOURS = int(os.getenv("PRED_CACHE_TTL_HOURS", "6"))   # predictions 缓存有效期
LOOKBACK = 60                       # LSTM 滑动窗口(与 train_lstm_c.py 一致)
FORECAST_HORIZON = 7                # 训练目标 = 7 天后 log_price
PORTFLOTTO_BATCH = 20               # 库存 >20 件时分批诊断

# 历史兼容:训练/展示统一为 USD,不再做 CNY 换算
USD_CNY_RATE = float(os.getenv("USD_CNY_RATE", "1.0"))


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODEL_DIR, PRED_DIR, OUTPUT_DIR, SEED_DIR, DATA_RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
