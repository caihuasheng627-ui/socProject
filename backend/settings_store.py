"""
运行时应用配置(管理员可在面板里改 API Key,无需重启进程)
=======================================================
持久化到 SQLite app_settings; 启动时加载并注入 config / llm / rag 模块属性。
"""
from __future__ import annotations

from typing import Any

from database import get_connection

# 允许管理员写入的键
SETTING_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "RAG_EMBED_MODEL",
    "RAG_EMBED_DIM",
    "RAG_USE_VECTOR",
)


def ensure_settings_table() -> None:
    with get_connection() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            )"""
        )
        conn.commit()


def get_all_settings() -> dict[str, str]:
    ensure_settings_table()
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_settings(updates: dict[str, Any]) -> dict[str, str]:
    """写入非空值; 空字符串表示清除该覆盖(删行,回退到 .env)。"""
    from datetime import datetime, timezone

    ensure_settings_table()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        for k, v in updates.items():
            if k not in SETTING_KEYS:
                continue
            if v is None:
                continue
            s = str(v).strip()
            if s == "":
                conn.execute("DELETE FROM app_settings WHERE key=?", (k,))
            else:
                conn.execute(
                    """INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (k, s, now),
                )
        conn.commit()
    apply_runtime_settings()
    return get_all_settings()


def _mask(key: str, value: str | None) -> str | None:
    if not value:
        return None
    if "API_KEY" in key or key.endswith("_KEY"):
        if len(value) <= 8:
            return "****"
        return value[:4] + "…" + value[-4:]
    return value


def public_config() -> dict[str, Any]:
    """给管理员面板的脱敏配置视图(合并 .env + DB 覆盖后的生效值)。"""
    import config as cfg

    deepseek_key = getattr(cfg, "DEEPSEEK_API_KEY", "") or ""
    dash_key = getattr(cfg, "DASHSCOPE_API_KEY", "") or ""
    return {
        "deepseek": {
            "hasKey": bool(deepseek_key),
            "keyMasked": _mask("DEEPSEEK_API_KEY", deepseek_key),
            "baseUrl": getattr(cfg, "DEEPSEEK_BASE_URL", ""),
            "model": getattr(cfg, "DEEPSEEK_MODEL", ""),
            "enabled": bool(getattr(cfg, "LLM_ENABLED", False)),
        },
        "dashscope": {
            "hasKey": bool(dash_key),
            "keyMasked": _mask("DASHSCOPE_API_KEY", dash_key),
            "baseUrl": getattr(cfg, "DASHSCOPE_BASE_URL", ""),
            "embedModel": getattr(cfg, "RAG_EMBED_MODEL", ""),
            "embedDim": int(getattr(cfg, "RAG_EMBED_DIM", 1024) or 1024),
            "useVector": bool(getattr(cfg, "RAG_USE_VECTOR", True)),
            "enabled": bool(getattr(cfg, "RAG_EMBED_ENABLED", False)),
        },
        "overrides": list(get_all_settings().keys()),
    }


def apply_runtime_settings() -> None:
    """把 DB 覆盖写回 config 模块,并同步到 llm / rag 已 import 的属性。"""
    import os

    import config as cfg
    from dotenv import dotenv_values

    env_file = cfg.BACKEND_DIR / ".env"
    file_vals = dotenv_values(env_file) if env_file.exists() else {}

    def _env(name: str, default: str = "") -> str:
        return (os.getenv(name) or file_vals.get(name) or default or "").strip()

    base = {
        "DEEPSEEK_API_KEY": _env("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "DEEPSEEK_MODEL": _env("DEEPSEEK_MODEL", "deepseek-chat"),
        "DASHSCOPE_API_KEY": _env("DASHSCOPE_API_KEY"),
        "DASHSCOPE_BASE_URL": _env(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/"),
        "RAG_EMBED_MODEL": _env("RAG_EMBED_MODEL", "text-embedding-v3"),
        "RAG_EMBED_DIM": _env("RAG_EMBED_DIM", "1024"),
        "RAG_USE_VECTOR": _env("RAG_USE_VECTOR", "1"),
    }
    overrides = get_all_settings()
    merged = {**base, **overrides}

    cfg.DEEPSEEK_API_KEY = merged["DEEPSEEK_API_KEY"]
    cfg.DEEPSEEK_BASE_URL = merged["DEEPSEEK_BASE_URL"].rstrip("/")
    cfg.DEEPSEEK_MODEL = merged["DEEPSEEK_MODEL"]
    cfg.LLM_ENABLED = bool(cfg.DEEPSEEK_API_KEY)

    cfg.DASHSCOPE_API_KEY = merged["DASHSCOPE_API_KEY"]
    cfg.DASHSCOPE_BASE_URL = merged["DASHSCOPE_BASE_URL"].rstrip("/")
    cfg.RAG_EMBED_MODEL = merged["RAG_EMBED_MODEL"]
    try:
        cfg.RAG_EMBED_DIM = int(merged["RAG_EMBED_DIM"] or 1024)
    except ValueError:
        cfg.RAG_EMBED_DIM = 1024
    cfg.RAG_USE_VECTOR = str(merged["RAG_USE_VECTOR"]).strip() not in ("0", "false", "False", "")
    cfg.RAG_EMBED_ENABLED = bool(cfg.DASHSCOPE_API_KEY) and cfg.RAG_USE_VECTOR

    try:
        import llm as llm_mod
        llm_mod.DEEPSEEK_API_KEY = cfg.DEEPSEEK_API_KEY
        llm_mod.DEEPSEEK_BASE_URL = cfg.DEEPSEEK_BASE_URL
        llm_mod.DEEPSEEK_MODEL = cfg.DEEPSEEK_MODEL
        llm_mod.LLM_ENABLED = cfg.LLM_ENABLED
    except Exception:
        pass

    try:
        import rag as rag_mod
        rag_mod.DASHSCOPE_API_KEY = cfg.DASHSCOPE_API_KEY
        rag_mod.DASHSCOPE_BASE_URL = cfg.DASHSCOPE_BASE_URL
        rag_mod.RAG_EMBED_MODEL = cfg.RAG_EMBED_MODEL
        rag_mod.RAG_EMBED_DIM = cfg.RAG_EMBED_DIM
        rag_mod.RAG_USE_VECTOR = cfg.RAG_USE_VECTOR
        rag_mod.RAG_EMBED_ENABLED = cfg.RAG_EMBED_ENABLED
        try:
            rag_mod.invalidate_index()
        except Exception:
            pass
    except Exception:
        pass
