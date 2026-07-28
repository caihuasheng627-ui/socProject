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

# volume 内 sidecar：seed 覆盖运行库时由 entrypoint 恢复；保存时同步写入
def _settings_sidecar_path():
    try:
        from config import DATA_RUNTIME_DIR
        return DATA_RUNTIME_DIR / "app_settings_backup.json"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parent / "data" / "app_settings_backup.json"


def _restore_settings_from_sidecar_if_empty() -> int:
    """把 sidecar 里有、DB 里缺的键补回（seed 冲库或半次保存后丢 Key）。"""
    import json

    path = _settings_sidecar_path()
    if not path.exists():
        return 0
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(rows, list) or not rows:
        return 0
    current = get_all_settings()
    updates = {
        str(r.get("key")): r.get("value")
        for r in rows
        if isinstance(r, dict)
        and r.get("key") in SETTING_KEYS
        and r.get("value") not in (None, "")
        and str(r.get("key")) not in current
    }
    if not updates:
        return 0
    from datetime import datetime, timezone

    ensure_settings_table()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        for k, v in updates.items():
            conn.execute(
                """INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (k, str(v), now),
            )
        conn.commit()
    print(f"[settings] restored {len(updates)} missing keys from sidecar {path}")
    return len(updates)


def _write_settings_sidecar(settings: dict[str, str]) -> None:
    import json
    from datetime import datetime, timezone

    path = _settings_sidecar_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 合并旧 sidecar：本次保存可能只改了 model/url，不要把旧 Key 冲掉
    merged: dict[str, dict] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, list):
                for row in old:
                    if (
                        isinstance(row, dict)
                        and row.get("key") in SETTING_KEYS
                        and row.get("value") not in (None, "")
                    ):
                        merged[str(row["key"])] = {
                            "key": str(row["key"]),
                            "value": str(row["value"]),
                            "updated_at": row.get("updated_at") or now,
                        }
        except Exception:
            pass

    for k, v in (settings or {}).items():
        if k not in SETTING_KEYS or v is None or str(v) == "":
            continue
        merged[k] = {"key": k, "value": str(v), "updated_at": now}

    if not merged:
        return
    path.write_text(
        json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _sanitize_api_key(raw: str | None) -> str:
    """HTTP Authorization 头只能是 ASCII。去掉空白/零宽/非 ASCII 字符。"""
    if not raw:
        return ""
    s = (
        str(raw)
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\u00a0", "")
        .strip()
    )
    return "".join(ch for ch in s if ch.isascii() and not ch.isspace())


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
            # API Key 只允许 ASCII(httpx Authorization 头强制 ascii);去掉空白与零宽字符
            if k.endswith("_API_KEY") and s:
                cleaned = _sanitize_api_key(s)
                if not cleaned:
                    raise ValueError(
                        f"{k} 无效:粘贴内容清理后为空。请只粘贴纯 ASCII 的 Key(不要带中文说明或空格)。"
                    )
                s = cleaned
            if s == "":
                conn.execute("DELETE FROM app_settings WHERE key=?", (k,))
            else:
                conn.execute(
                    """INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (k, s, now),
                )
        conn.commit()
    # Persist first; runtime injection must not turn a successful write into HTTP 500.
    try:
        apply_runtime_settings()
    except Exception as exc:
        print(f"[settings] apply_runtime_settings failed after save: {type(exc).__name__}: {exc}")
    try:
        _write_settings_sidecar(get_all_settings())
    except Exception as exc:
        print(f"[settings] sidecar backup failed: {type(exc).__name__}: {exc}")
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


def _is_dashscope_llm(base_url: str) -> bool:
    from llm_routing import is_dashscope_llm
    return is_dashscope_llm(base_url)


def _normalize_llm_model(model: str, base_url: str) -> str:
    from llm_routing import normalize_llm_model
    return normalize_llm_model(model, base_url)


def apply_runtime_settings() -> None:
    """把 DB 覆盖写回 config 模块,并同步到 llm / rag 已 import 的属性。"""
    import os

    import config as cfg
    try:
        _restore_settings_from_sidecar_if_empty()
    except Exception as exc:
        print(f"[settings] sidecar restore skipped: {type(exc).__name__}: {exc}")
    try:
        from dotenv import dotenv_values
    except Exception:
        dotenv_values = lambda _path: {}  # type: ignore[misc, assignment]

    env_file = cfg.BACKEND_DIR / ".env"
    try:
        file_vals = dotenv_values(env_file) if env_file.exists() else {}
    except Exception:
        file_vals = {}

    def _env(name: str, default: str = "") -> str:
        return (os.getenv(name) or file_vals.get(name) or default or "").strip()

    dash_default = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    base = {
        "DEEPSEEK_API_KEY": _env("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": _env("DEEPSEEK_BASE_URL", dash_default),
        "DEEPSEEK_MODEL": _env("DEEPSEEK_MODEL", "deepseek-v3"),
        "DASHSCOPE_API_KEY": _env("DASHSCOPE_API_KEY"),
        "DASHSCOPE_BASE_URL": _env(
            "DASHSCOPE_BASE_URL",
            dash_default,
        ).rstrip("/"),
        "RAG_EMBED_MODEL": _env("RAG_EMBED_MODEL", "text-embedding-v3"),
        "RAG_EMBED_DIM": _env("RAG_EMBED_DIM", "1024"),
        "RAG_USE_VECTOR": _env("RAG_USE_VECTOR", "1"),
    }
    overrides = get_all_settings()
    merged = {**base, **overrides}

    llm_base = (merged.get("DEEPSEEK_BASE_URL") or dash_default).rstrip("/")
    llm_key = _sanitize_api_key(merged.get("DEEPSEEK_API_KEY"))
    dash_key = _sanitize_api_key(merged.get("DASHSCOPE_API_KEY"))
    # 阿里云 DeepSeek:无独立 LLM Key 时复用百炼 Key;坏 Key(非 sk- 开头)也回退
    if _is_dashscope_llm(llm_base) and dash_key:
        if not llm_key or not llm_key.startswith("sk-"):
            llm_key = dash_key

    cfg.DEEPSEEK_API_KEY = llm_key
    cfg.DEEPSEEK_BASE_URL = llm_base
    cfg.DEEPSEEK_MODEL = _normalize_llm_model(merged.get("DEEPSEEK_MODEL") or "deepseek-v3", llm_base)
    cfg.LLM_ENABLED = bool(cfg.DEEPSEEK_API_KEY)
    # Keep agent model aliases in sync when only the base model changed.
    cfg.BULL_MODEL = os.getenv("BULL_MODEL", cfg.DEEPSEEK_MODEL)
    cfg.BEAR_MODEL = os.getenv("BEAR_MODEL", cfg.DEEPSEEK_MODEL)
    cfg.JUDGE_MODEL = os.getenv("JUDGE_MODEL", cfg.DEEPSEEK_MODEL)

    cfg.DASHSCOPE_API_KEY = dash_key
    cfg.DASHSCOPE_BASE_URL = (merged.get("DASHSCOPE_BASE_URL") or dash_default).rstrip("/")
    embed_model = (merged.get("RAG_EMBED_MODEL") or "text-embedding-v3").strip()
    if "rerank" in embed_model.lower():
        # Rerank models are not embedding models; keep a safe default.
        print(f"[settings] reject rerank model for embedding: {embed_model}")
        embed_model = "text-embedding-v3"
    cfg.RAG_EMBED_MODEL = embed_model
    try:
        cfg.RAG_EMBED_DIM = int(merged.get("RAG_EMBED_DIM") or 1024)
    except ValueError:
        cfg.RAG_EMBED_DIM = 1024
    cfg.RAG_USE_VECTOR = str(merged.get("RAG_USE_VECTOR") or "1").strip() not in ("0", "false", "False", "")
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

    # 其余模块在文件顶层 `from config import LLM_ENABLED / *_MODEL`，
    # 形成的是本地旧绑定，必须显式回写，否则 admin 保存后它们仍读到旧值。
    try:
        import agent_debate as debate_mod
        debate_mod.LLM_ENABLED = cfg.LLM_ENABLED
    except Exception:
        pass

    try:
        import sys
        main_mod = sys.modules.get("main")
        if main_mod is not None:
            main_mod.LLM_ENABLED = cfg.LLM_ENABLED
            main_mod.DEEPSEEK_MODEL = cfg.DEEPSEEK_MODEL
            if hasattr(cfg, "BULL_MODEL"):
                main_mod.BULL_MODEL = cfg.BULL_MODEL
                main_mod.BEAR_MODEL = cfg.BEAR_MODEL
                main_mod.JUDGE_MODEL = cfg.JUDGE_MODEL
    except Exception:
        pass

    try:
        from agents import bear_agent, bull_agent, judge_agent
        bull_agent.BULL_MODEL = cfg.BULL_MODEL
        bear_agent.BEAR_MODEL = cfg.BEAR_MODEL
        judge_agent.JUDGE_MODEL = cfg.JUDGE_MODEL
    except Exception:
        pass
