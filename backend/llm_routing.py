"""LLM provider/model id helpers (no heavy DB deps)."""
from __future__ import annotations


def is_dashscope_llm(base_url: str) -> bool:
    return "dashscope" in (base_url or "").lower()


def normalize_llm_model(model: str, base_url: str) -> str:
    """按服务商纠正常见模型 ID 笔误，避免请求失败后被当成 Mock。"""
    m = (model or "").strip() or "deepseek-v3"
    key = m.lower().replace("_", "-")
    if is_dashscope_llm(base_url):
        # 百炼兼容模式：DeepSeek 对话模型 ID 是 deepseek-v3
        alias = {
            "deepseek-chat": "deepseek-v3",
            "deepseek-reasoner": "deepseek-r1",
            "deepseek-v3.0": "deepseek-v3",
            "deepseek-v3": "deepseek-v3",
            "deepseek": "deepseek-v3",
            "deepseek-v3-chat": "deepseek-v3",
            "v3": "deepseek-v3",
        }
        return alias.get(key, m)
    # 官方 DeepSeek：对话模型是 deepseek-chat
    alias = {
        "deepseek-v3": "deepseek-chat",
        "deepseek-v3.0": "deepseek-chat",
        "deepseek": "deepseek-chat",
        "deepseek-v3-chat": "deepseek-chat",
        "v3": "deepseek-chat",
    }
    return alias.get(key, m)
