"""Base implementation that enforces per-agent isolation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

import llm


OutputT = TypeVar("OutputT", bound=BaseModel)
StructuredLLM = Callable[..., BaseModel]


def model_dump(value: BaseModel) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[attr-defined]
    return value.dict()


class BaseAgent(ABC, Generic[OutputT]):
    """Common Agent shell with private history and an explicit tool allowlist."""

    def __init__(
        self,
        *,
        name: str,
        system_prompt: str,
        system_prompt_en: str | None = None,
        output_schema: type[OutputT],
        model: str | None = None,
        temperature: float = 0.2,
        allowed_tools: set[str] | frozenset[str] | None = None,
        llm_callable: StructuredLLM | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("agent name must not be empty")
        if not system_prompt.strip():
            raise ValueError("agent system_prompt must not be empty")
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        self.name = name
        self.system_prompt = system_prompt
        self.system_prompt_en = system_prompt_en
        self.output_schema = output_schema
        self.model = model
        self.temperature = temperature
        self.allowed_tools = frozenset(allowed_tools or ())
        self._history: list[dict[str, str]] = []
        self._llm_callable = llm_callable or llm.chat_structured

    @property
    def history(self) -> tuple[dict[str, str], ...]:
        """Return a defensive snapshot; callers cannot mutate private history."""

        return tuple(deepcopy(self._history))

    def reset(self) -> None:
        self._history.clear()

    def can_use_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools

    def require_tool(self, tool_name: str) -> None:
        if not self.can_use_tool(tool_name):
            raise PermissionError(f"{self.name} is not allowed to use tool: {tool_name}")

    def validate_result(self, result: OutputT, input_data: Any) -> None:
        """Hook for semantic validation before a result enters Agent history."""

    @abstractmethod
    def build_user_payload(self, input_data: Any) -> dict[str, Any]:
        """Build the public, serializable input for this concrete agent."""

    def run(self, input_data: Any, *, mock_data: Any | None = None) -> OutputT:
        payload = self.build_user_payload(input_data)
        locale = str((payload.get("user_profile") or {}).get("locale") or "zh-CN")
        language_rule = (
            "\n\nCRITICAL OUTPUT LANGUAGE: Every user-facing string value in the JSON "
            "must be English. Do not output Chinese characters."
            if locale.lower().startswith("en")
            else "\n\n关键输出语言：JSON 中所有面向用户的字符串值必须使用简体中文。"
        )
        role_prompt = (
            self.system_prompt_en
            if locale.lower().startswith("en") and self.system_prompt_en
            else self.system_prompt
        )
        user_content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        messages = [*self._history, {"role": "user", "content": user_content}]

        result = self._llm_callable(
            messages,
            output_schema=self.output_schema,
            system_prompt=role_prompt + language_rule,
            model=self.model,
            temperature=self.temperature,
            mock_data=mock_data,
            output_locale=locale,
        )
        if not isinstance(result, self.output_schema):
            raise TypeError(
                f"{self.name} returned {type(result).__name__}; "
                f"expected {self.output_schema.__name__}"
            )

        self.validate_result(result, input_data)

        self._history.append({"role": "user", "content": user_content})
        self._history.append(
            {
                "role": "assistant",
                "content": json.dumps(model_dump(result), ensure_ascii=False, sort_keys=True),
            }
        )
        return result
