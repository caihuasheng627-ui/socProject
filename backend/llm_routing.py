"""LLM provider/model id helpers (no heavy DB deps)."""
from __future__ import annotations

import re


def is_dashscope_llm(base_url: str) -> bool:
    """True for Alibaba Bailian / DashScope / Coding Plan / MaaS endpoints."""
    u = (base_url or "").lower()
    return any(
        token in u
        for token in (
            "dashscope",
            "maas.aliyuncs.com",
            "token-plan.",
        )
    )


def _canonicalize_typos(model_key: str) -> str:
    """Fix common no-hyphen IDs users type in the admin box.

    glm5 / GLM5 / glm5.1 / glm4.7  →  glm-5 / glm-5.1 / glm-4.7
    deepseekv3 / deepseekchat     →  deepseek-v3 / deepseek-chat
    """
    key = (model_key or "").strip().lower().replace("_", "-")
    if not key:
        return key

    m = re.fullmatch(r"glm(\d+(?:\.\d+)*)", key)
    if m:
        return f"glm-{m.group(1)}"

    m = re.fullmatch(r"deepseekv(\d+(?:\.\d+)*)", key)
    if m:
        return f"deepseek-v{m.group(1)}"

    if key in ("deepseekchat", "deepseek-chat"):
        return "deepseek-chat"

    # kimi k2.5 typed as kimik2.5 / kimi-k25 — leave alone unless exact
    m = re.fullmatch(r"kimi-?k(\d+)\.(\d+)", key)
    if m:
        return f"kimi-k{m.group(1)}.{m.group(2)}"

    return key


def normalize_llm_model(model: str, base_url: str) -> str:
    """按服务商纠正常见模型 ID 笔误，避免请求失败后被当成 Mock。"""
    raw = (model or "").strip() or "deepseek-v3"
    key = _canonicalize_typos(raw)

    if is_dashscope_llm(base_url):
        # 百炼 / Coding Plan：DeepSeek 对话模型 ID 是 deepseek-v3；GLM 是 glm-5
        alias = {
            "deepseek-chat": "deepseek-v3",
            "deepseek-reasoner": "deepseek-r1",
            "deepseek-v3.0": "deepseek-v3",
            "deepseek-v3": "deepseek-v3",
            "deepseek": "deepseek-v3",
            "deepseek-v3-chat": "deepseek-v3",
            "v3": "deepseek-v3",
            # Cursor/Coding Plan 文档里的别名 → 标准百炼 ID
            "glm-5-0": "glm-5",
            "glm-5-1": "glm-5.1",
            "glm-5-2": "glm-5.2",
            "glm-4-7": "glm-4.7",
        }
        return alias.get(key, key)

    # 官方 DeepSeek：对话模型是 deepseek-chat
    alias = {
        "deepseek-v3": "deepseek-chat",
        "deepseek-v3.0": "deepseek-chat",
        "deepseek": "deepseek-chat",
        "deepseek-v3-chat": "deepseek-chat",
        "v3": "deepseek-chat",
    }
    return alias.get(key, key if key != "deepseek-v3" else "deepseek-chat")
