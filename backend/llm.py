"""
SkinVision AI — DeepSeek LLM 客户端(组员 3)
============================================
OpenAI 兼容接口:https://api.deepseek.com/chat/completions

提供:
  - chat_sync(messages)        : 同步一次性返回(兼容旧调用)
  - chat_stream(messages)      : 生成器,逐 chunk 产出(SSE 用)
  - chat_structured(...)       : 按 Pydantic Schema 返回结构化结果
  - 无 DEEPSEEK_API_KEY 时降级 Mock(返回预设话术),保证服务可起、可联调

策划书 §4.3:BUFF Cookie 仅课程演示不公开;LLM 调用走 DeepSeek 官方 API。
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Generator

import httpx
from pydantic import BaseModel, ValidationError

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLM_ENABLED

DEFAULT_SYSTEM_PROMPT = (
    "你是 SkinVision AI,一个 CS2 饰品市场分析助手。"
    "你会基于 6 个回归模型(ARIMA/XGBoost/LightGBM/RandomForest/LSTM/GRU)+ "
    "RAG 知识库(Valve 公告/HLTV 赛事/历史日报)给出饰品价格分析与投资建议。"
    "回答用中文,简洁、有数据支撑,涉及预测时标注模型名称与置信度,"
    "并提示风险(饰品市场高波动,不构成投资建议)。"
)
# 历史兼容:旧模块若引用 SYSTEM_PROMPT 仍可工作。
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT


class StructuredOutputError(RuntimeError):
    """The model failed to return data matching the requested schema."""


@dataclass
class _ExecutionTracker:
    calls: int = 0
    live_calls: int = 0
    fallback_calls: int = 0
    last_error: str | None = None
    lock: Lock = field(default_factory=Lock)


_EXECUTION_STATUS: ContextVar[_ExecutionTracker] = ContextVar(
    "llm_execution_status", default=_ExecutionTracker()
)


def reset_execution_status() -> None:
    """Start per-request LLM accounting used by API runtime metadata."""

    _EXECUTION_STATUS.set(_ExecutionTracker())


def _record_execution(*, live: bool, error: Exception | None = None) -> None:
    tracker = _EXECUTION_STATUS.get()
    with tracker.lock:
        tracker.calls += 1
        if live:
            tracker.live_calls += 1
        else:
            tracker.fallback_calls += 1
        if error is not None:
            tracker.last_error = type(error).__name__


def get_execution_status() -> dict[str, Any]:
    tracker = _EXECUTION_STATUS.get()
    with tracker.lock:
        current = {
            "calls": tracker.calls,
            "liveCalls": tracker.live_calls,
            "fallbackCalls": tracker.fallback_calls,
            "lastError": tracker.last_error,
        }
    if current["fallbackCalls"]:
        mode = "degraded" if LLM_ENABLED else "mock"
    elif current["liveCalls"]:
        mode = "live"
    else:
        mode = "configured" if LLM_ENABLED else "mock"
    return {**current, "mode": mode}


def _messages_with_system(
    messages: list[dict], system_prompt: str | None
) -> list[dict]:
    prompt = DEFAULT_SYSTEM_PROMPT if system_prompt is None else system_prompt
    clean = [m for m in messages if m.get("role") != "system"]
    return ([{"role": "system", "content": prompt}] if prompt else []) + clean


def _build_payload(
    messages: list[dict],
    *,
    system_prompt: str | None,
    model: str | None,
    temperature: float,
    stream: bool,
    json_mode: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model or DEEPSEEK_MODEL,
        "messages": _messages_with_system(messages, system_prompt),
        "temperature": temperature,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _request_sync(payload: dict[str, Any], timeout: float) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def _schema_json(output_schema: type[BaseModel]) -> str:
    if hasattr(output_schema, "model_json_schema"):
        schema = output_schema.model_json_schema()  # type: ignore[attr-defined]
    else:
        schema = output_schema.schema()
    return json.dumps(schema, ensure_ascii=False)


def _schema_required_fields(output_schema: type[BaseModel]) -> list[str]:
    if hasattr(output_schema, "model_json_schema"):
        schema = output_schema.model_json_schema()  # type: ignore[attr-defined]
    else:
        schema = output_schema.schema()
    return [str(item) for item in schema.get("required", [])]


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL)
    return match.group(1).strip() if match else value


def _validate_structured(
    output_schema: type[BaseModel], value: str | dict[str, Any] | BaseModel
) -> BaseModel:
    if isinstance(value, output_schema):
        return value
    if isinstance(value, str):
        value = json.loads(_strip_json_fence(value))
    if hasattr(output_schema, "model_validate"):
        return output_schema.model_validate(value)  # type: ignore[attr-defined]
    return output_schema.parse_obj(value)


def _contains_cjk(value: Any) -> bool:
    if isinstance(value, BaseModel):
        value = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else value.dict()
        )
    if isinstance(value, dict):
        return any(_contains_cjk(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_cjk(item) for item in value)
    return isinstance(value, str) and bool(re.search(r"[\u3400-\u9fff]", value))


def _mock_reply(messages: list[dict]) -> str:
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    english = any(
        m.get("role") == "system"
        and "answer in english" in str(m.get("content", "")).lower()
        for m in messages
    )
    if english:
        return (
            "The live LLM request failed, so CSVest used a local fallback for this reply. "
            f"Your question was: “{user_msg[:60]}”. No live model conclusion is available; "
            "retry the request before making a decision."
        )
    return (
        f"(Mock 模式 · 未配置 DEEPSEEK_API_KEY)\n"
        f"已收到你的问题:「{user_msg[:60]}」\n"
        f"基于 Hybrid 模型(LSTM-C/D 路由)与近 7 日行情,该饰品短期偏强震荡,"
        f"7 天预测涨幅约 +1.5%~+2.5%(置信度 ~78%)。"
        f"建议关注成交量与 Major 赛程节奏,设止损 -5%。"
        f"\n\n⚠ 饰品市场高波动,以上不构成投资建议。"
    )


def chat_sync(
    messages: list[dict],
    temperature: float = 0.7,
    timeout: float = 30.0,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
) -> str:
    """同步调用 DeepSeek；支持调用方提供独立 system prompt。"""
    if not LLM_ENABLED:
        _record_execution(live=False)
        return _mock_reply(messages)

    payload = _build_payload(
        messages,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        stream=False,
    )
    try:
        result = _request_sync(payload, timeout)
        _record_execution(live=True)
        return result
    except Exception as e:
        _record_execution(live=False, error=e)
        return (
            f"(LLM 调用失败,降级 Mock)\n{_mock_reply(messages)}"
            f"\n\n[error: {type(e).__name__}]"
        )


def chat_structured(
    messages: list[dict],
    *,
    output_schema: type[BaseModel],
    system_prompt: str,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 30.0,
    max_retries: int = 2,
    mock_data: Any | Callable[[], Any] | None = None,
    output_locale: str | None = None,
) -> BaseModel:
    """Return a validated Pydantic object for an isolated agent call.

    Unlike ``chat_sync``, malformed output is never returned as prose.  Mock
    mode also requires explicit structured data so a demo cannot masquerade
    as a real, schema-valid agent decision.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    if not LLM_ENABLED:
        _record_execution(live=False)
        if mock_data is None:
            raise StructuredOutputError(
                "structured LLM call requires mock_data when LLM is disabled"
            )
        value = mock_data() if callable(mock_data) else mock_data
        try:
            return _validate_structured(output_schema, value)
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise StructuredOutputError("mock_data does not match output schema") from exc

    english_output = str(output_locale or "").lower().startswith("en")
    required_fields = ", ".join(_schema_required_fields(output_schema))
    schema_instruction = {
        "role": "user",
        "content": (
            (
                "Return exactly one JSON object without Markdown. Every user-facing "
                "string value must be English; do not output Chinese characters. "
                "Use the Schema properties directly at the top level; never wrap the "
                "object under bull_case, bear_case, judge_case, result, or data. "
                f"The required top-level keys are: {required_fields}. "
                "The object must match this JSON Schema: "
            ) if english_output else (
                "严格返回一个 JSON 对象，不要使用 Markdown。所有面向用户的字符串值"
                "必须使用简体中文。直接在顶层使用 Schema 字段，禁止包装在 bull_case、"
                "bear_case、judge_case、result 或 data 下。"
                f"顶层必填字段为：{required_fields}。输出必须符合此 JSON Schema："
            )
            + _schema_json(output_schema)
        ),
    }
    attempt_messages = [*messages, schema_instruction]
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        raw = ""
        payload = _build_payload(
            attempt_messages,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            stream=False,
            json_mode=True,
        )
        try:
            raw = _request_sync(payload, timeout)
            validated = _validate_structured(output_schema, raw)
            _record_execution(live=True)
            return validated
        except (httpx.HTTPError, KeyError, TypeError, ValueError,
                json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            if attempt < max_retries:
                attempt_messages.extend(
                    [
                        {"role": "assistant", "content": raw or "{}"},
                        {
                            "role": "user",
                            "content": (
                                "The previous object failed validation or contained "
                                "Chinese text. Return only a corrected English JSON object "
                                f"with these keys at the top level: {required_fields}. "
                                f"Validation error: {str(exc)[:800]}"
                                if english_output else
                                "上一个输出无法通过 Schema 校验。请仅返回修正后的简体中文 JSON 对象。"
                                f"校验错误：{str(exc)[:800]}"
                            ),
                        },
                    ]
                )

    if mock_data is not None:
        value = mock_data() if callable(mock_data) else mock_data
        try:
            fallback = _validate_structured(output_schema, value)
            _record_execution(live=False, error=last_error)
            return fallback
        except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
    _record_execution(live=False, error=last_error)
    raise StructuredOutputError(
        f"model failed structured output after {max_retries + 1} attempt(s): "
        f"{type(last_error).__name__ if last_error else 'unknown error'}"
    ) from last_error


def chat_stream(
    messages: list[dict],
    temperature: float = 0.7,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
) -> Generator[str, None, None]:
    """
    流式生成(SSE)。yield 文本 chunk。
    无 Key 时模拟逐句流式输出 Mock。
    """
    if not LLM_ENABLED:
        text = _mock_reply(messages)
        for ch in text:
            yield ch
        return

    payload = _build_payload(
        messages,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        stream=True,
    )
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=None) as client:
            with client.stream("POST", f"{DEEPSEEK_BASE_URL}/chat/completions",
                                json=payload, headers=headers) as r:
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
        yield f"\n\n(LLM 流式失败,降级 Mock: {type(e).__name__})\n{_mock_reply(messages)}"
