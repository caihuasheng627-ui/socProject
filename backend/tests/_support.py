"""Minimal dependency stubs for isolated unit tests.

The production dependencies remain declared in backend/requirements.txt.
These stubs let foundation tests run without installing TensorFlow and the
rest of the backend stack in the Codex verification runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def bootstrap_llm_dependencies() -> None:
    if "httpx" not in sys.modules and importlib.util.find_spec("httpx") is None:
        httpx = ModuleType("httpx")

        class HTTPError(Exception):
            pass

        class Client:
            def __init__(self, *args, **kwargs):
                raise AssertionError("HTTP client must not run in isolated unit tests")

        httpx.HTTPError = HTTPError  # type: ignore[attr-defined]
        httpx.Client = Client  # type: ignore[attr-defined]
        sys.modules["httpx"] = httpx

    if "config" not in sys.modules:
        config = ModuleType("config")
        config.DEEPSEEK_API_KEY = ""  # type: ignore[attr-defined]
        config.DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # type: ignore[attr-defined]
        config.DEEPSEEK_MODEL = "deepseek-chat"  # type: ignore[attr-defined]
        config.LLM_ENABLED = False  # type: ignore[attr-defined]
        config.BULL_MODEL = "deepseek-chat"  # type: ignore[attr-defined]
        config.BEAR_MODEL = "deepseek-chat"  # type: ignore[attr-defined]
        config.JUDGE_MODEL = "deepseek-chat"  # type: ignore[attr-defined]
        config.BULL_TEMPERATURE = 0.6  # type: ignore[attr-defined]
        config.BEAR_TEMPERATURE = 0.6  # type: ignore[attr-defined]
        config.JUDGE_TEMPERATURE = 0.2  # type: ignore[attr-defined]
        config.DEBATE_ROUNDS = 3  # type: ignore[attr-defined]
        sys.modules["config"] = config
