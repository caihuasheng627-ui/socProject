"""
SkinVision AI — DeepSeek LLM 客户端(组员 3)
============================================
OpenAI 兼容接口:https://api.deepseek.com/chat/completions

提供:
  - chat_sync(messages)        : 同步一次性返回
  - chat_stream(messages)      : 生成器,逐 chunk 产出(SSE 用)
  - 无 DEEPSEEK_API_KEY 时降级 Mock(返回预设话术),保证服务可起、可联调

策划书 §4.3:BUFF Cookie 仅课程演示不公开;LLM 调用走 DeepSeek 官方 API。
"""
from __future__ import annotations

import json
from typing import Generator

import httpx

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_ENABLED

SYSTEM_PROMPT = (
    "你是 SkinVision AI,一个 CS2 饰品市场分析助手。"
    "你会基于 6 个回归模型(ARIMA/XGBoost/LightGBM/RandomForest/LSTM/GRU)+ "
    "RAG 知识库(Valve 公告/HLTV 赛事/历史日报)给出饰品价格分析与投资建议。"
    "回答用中文,简洁、有数据支撑,涉及预测时标注模型名称与置信度,"
    "并提示风险(饰品市场高波动,不构成投资建议)。"
)


def _err_text(exc: BaseException) -> str:
    """安全把异常转成可返回前端的短文本(避免二次 UnicodeEncodeError)。"""
    try:
        detail = str(exc)
    except Exception:
        detail = repr(exc)
    try:
        detail.encode("ascii")
        return f"{type(exc).__name__}: {detail}"
    except UnicodeEncodeError:
        # 响应体/消息含中文时,用 unicode_escape 保证任何终端都能显示
        safe = detail.encode("unicode_escape", errors="replace").decode("ascii")
        return f"{type(exc).__name__}: {safe}"


def _mock_reply(messages: list[dict], *, reason: str | None = None) -> str:
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    if reason:
        header = f"(Mock 模式 · {reason})"
    elif not LLM_ENABLED:
        header = "(Mock 模式 · 未配置 DEEPSEEK_API_KEY)"
    else:
        header = "(Mock 模式 · LLM 调用失败)"
    return (
        f"{header}\n"
        f"已收到你的问题:「{user_msg[:60]}」\n"
        f"基于 Hybrid 模型(LSTM-C/D 路由)与近 7 日行情,该饰品短期偏强震荡,"
        f"7 天预测涨幅约 +1.5%~+2.5%(置信度 ~78%)。"
        f"建议关注成交量与 Major 赛程节奏,设止损 -5%。"
        f"\n\n⚠ 饰品市场高波动,以上不构成投资建议。"
    )


def _post_chat(payload: dict, *, timeout: float | None, stream: bool):
    """显式 UTF-8 JSON 请求体,避免 Windows 默认 ascii 编码炸掉中文 prompt。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    return httpx.Client(timeout=timeout), url, body, headers


def chat_sync(messages: list[dict], temperature: float = 0.7, timeout: float = 30.0) -> str:
    """同步调用 DeepSeek。无 Key 走 Mock。"""
    if not LLM_ENABLED:
        return _mock_reply(messages)

    key = (DEEPSEEK_API_KEY or "").strip()
    if not key.isascii() or any(ch.isspace() for ch in key):
        return (
            "(LLM 调用失败,降级 Mock)\n"
            f"{_mock_reply(messages, reason='DEEPSEEK_API_KEY 含非 ASCII/空格,请在管理页重新粘贴纯 Key')}\n\n"
            "[error: InvalidApiKeyEncoding: Authorization header must be ASCII-only]"
        )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": temperature,
        "stream": False,
    }
    try:
        client, url, body, headers = _post_chat(payload, timeout=timeout, stream=False)
        with client:
            r = client.post(url, content=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return (
            f"(LLM 调用失败,降级 Mock)\n"
            f"{_mock_reply(messages)}\n\n"
            f"[error: {_err_text(e)}]"
        )


def chat_stream(messages: list[dict], temperature: float = 0.7) -> Generator[str, None, None]:
    """
    流式生成(SSE)。yield 文本 chunk。
    无 Key 时模拟逐句流式输出 Mock。
    """
    if not LLM_ENABLED:
        text = _mock_reply(messages)
        for ch in text:
            yield ch
        return

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": temperature,
        "stream": True,
    }
    try:
        client, url, body, headers = _post_chat(payload, timeout=None, stream=True)
        with client:
            with client.stream("POST", url, content=body, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                    except Exception:
                        continue
    except Exception as e:
        yield (
            f"\n\n(LLM 流式失败,降级 Mock: {type(e).__name__})\n"
            f"{_mock_reply(messages)}\n"
            f"[error: {_err_text(e)}]"
        )
