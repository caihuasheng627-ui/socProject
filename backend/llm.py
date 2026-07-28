"""
SkinVision AI — DeepSeek LLM 客户端(组员 3)
============================================
OpenAI 兼容接口: https://api.deepseek.com/chat/completions

提供:
  - chat_sync(messages)        : 同步一次性返回(兼容旧调用)
  - chat_stream(messages)      : 生成器,逐 chunk 产出(SSE 用)
  - chat_structured(...)       : 按 Pydantic Schema 返回结构化结果
  - 无 DEEPSEEK_API_KEY 时降级 Mock(返回预设话术),保证服务可起、可联调

策划书 §4.3: BUFF Cookie 仅课程演示不公开; LLM 调用走 DeepSeek 官方 API。
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

SYSTEM_PROMPT = (
    "You are CSVest AI, a CS2 skin market analysis assistant. "
    "Live 7-day price forecasts come only from the Hybrid-V2 model "
    "(calibrated LSTM ensemble). Do NOT invent per-skin outputs for ARIMA, "
    "XGBoost, LightGBM, RandomForest, GRU, or multi-model consensus tables — "
    "those lab models are offline comparison metrics, not live skin forecasts. "
    "You may use a RAG knowledge base (Valve announcements / HLTV / daily reports) "
    "only when real retrieved context is provided. "
    "Always reply in the same language as the user's latest message "
    "(English question → English answer; Chinese question → Chinese answer). "
    "Be concise and data-backed; when forecasting, cite only Hybrid-V2 figures "
    "that were supplied to you; always include a short risk disclaimer "
    "(volatile market, not investment advice)."
)


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
    prompt = SYSTEM_PROMPT if system_prompt is None else system_prompt
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
    max_tokens: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model or DEEPSEEK_MODEL,
        "messages": _messages_with_system(messages, system_prompt),
        "temperature": temperature,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
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


def _format_llm_error(exc: BaseException) -> str:
    """Prefer provider JSON error body so wrong model names are visible."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = ""
        try:
            body = (exc.response.text or "").strip()
        except Exception:
            body = ""
        if body:
            try:
                parsed = json.loads(body)
                err = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("msg") or err.get("code")
                    if msg:
                        return f"HTTP {status}: {msg}"
                if isinstance(err, str) and err.strip():
                    return f"HTTP {status}: {err.strip()}"
            except Exception:
                pass
            return f"HTTP {status}: {body[:280]}"
        return f"HTTP {status}: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _failure_reply(messages: list[dict], exc: BaseException, *, model: str | None) -> str:
    """When Key is configured but the provider rejects the call, explain why.

    Do not label this as Mock — that hid model-not-found errors behind a fake reply.
    """
    used_model = (model or DEEPSEEK_MODEL or "").strip() or "(unset)"
    detail = _format_llm_error(exc)
    english = any(
        m.get("role") == "system"
        and "answer in english" in str(m.get("content", "")).lower()
        for m in messages
    )
    if english:
        return (
            "Live LLM call failed — this is not a model answer.\n"
            f"model={used_model}\n"
            f"base={DEEPSEEK_BASE_URL}\n"
            f"error={detail}\n"
            "Check that Model and Base URL match the same provider "
            "(Bailian uses deepseek-v3 / qwen-*; official DeepSeek uses deepseek-chat)."
        )
    return (
        "实时 LLM 调用失败——本条不是模型回答。\n"
        f"model={used_model}\n"
        f"base={DEEPSEEK_BASE_URL}\n"
        f"error={detail}\n"
        "请确认 Model 与 Base URL 属于同一服务商："
        "阿里云百炼用 deepseek-v3 / glm-5 / qwen-*（GLM 须带连字符，不要写 glm5）；"
        "DeepSeek 官方 API 用 deepseek-chat + https://api.deepseek.com。"
    )


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
        f"实时 LLM 服务暂不可用（未配置或调用失败），本条为本地提示，不包含任何模型结论。\n"
        f"已收到你的问题:「{user_msg[:60]}」\n"
        "系统不会在离线状态下编造价格、涨幅或置信度；"
        "请稍后重试，或在行情中心查看真实价格数据后再做判断。"
    )


def chat_sync(
    messages: list[dict],
    temperature: float = 0.7,
    timeout: float = 60.0,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
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
        max_tokens=max_tokens,
    )
    try:
        result = _request_sync(payload, timeout)
        _record_execution(live=True)
        return result
    except Exception as e:
        _record_execution(live=False, error=e)
        return _failure_reply(messages, e, model=payload.get("model"))


def translate_content(content: Any, target_locale: str, timeout: float = 45.0) -> Any:
    """Translate a JSON-compatible chat payload using DeepSeek only.

    Unlike ordinary chat, this helper never returns a local fallback: an old
    conversation must remain in its original language when translation cannot
    be verified, rather than being replaced with fabricated text.
    """
    if not LLM_ENABLED:
        raise RuntimeError("DeepSeek is not configured")
    target = "Simplified Chinese" if str(target_locale).lower().startswith("zh") else "English"
    # JSON mode is more reliable when a scalar is wrapped in an object.  Keep
    # the public helper contract ergonomic by unwrapping that value again
    # below; structured chat/debate payloads retain their original shape.
    scalar_content = isinstance(content, str)
    payload_json = json.dumps({"content": content} if scalar_content else content, ensure_ascii=False)
    messages = [{
        "role": "user",
        "content": (
            f"Translate every natural-language string value in this JSON to {target}. "
            "Preserve the JSON structure, all keys, numbers, percentages, IDs, dates, "
            "skin names, model names, URLs, and the labels Bull, Bear, Judge exactly. "
            "Return only valid JSON with no Markdown.\n\n"
            + payload_json
        ),
    }]
    request_payload = _build_payload(
        messages,
        system_prompt="You are a precise UI localization service.",
        model=DEEPSEEK_MODEL,
        temperature=0.0,
        stream=False,
        json_mode=True,
    )
    try:
        raw = _request_sync(request_payload, timeout)
        translated = json.loads(_strip_json_fence(raw))
        if scalar_content:
            if not isinstance(translated, dict) or not isinstance(translated.get("content"), str):
                raise ValueError("translation response did not preserve scalar content")
            translated = translated["content"]
        _record_execution(live=True)
        return translated
    except Exception as exc:
        _record_execution(live=False, error=exc)
        raise RuntimeError(f"DeepSeek translation failed: {type(exc).__name__}") from exc


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
    """Return a validated Pydantic object for an isolated agent call."""
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
    max_tokens: int | None = None,
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
        max_tokens=max_tokens,
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
        yield (
            f"\n\n(LLM 流式失败)\n"
            f"{_failure_reply(messages, e, model=payload.get('model'))}"
        )
