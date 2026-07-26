from __future__ import annotations

import sys
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

import llm  # noqa: E402
from agents.schemas import BullOpinion  # noqa: E402


class StructuredLLMTests(unittest.TestCase):
    def test_custom_system_prompt_replaces_default(self):
        messages = llm._messages_with_system(
            [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hello"},
            ],
            "bull-only",
        )
        self.assertEqual(messages[0], {"role": "system", "content": "bull-only"})
        self.assertNotIn({"role": "system", "content": "old"}, messages)

    @patch.object(llm, "LLM_ENABLED", False)
    def test_structured_mock_is_validated(self):
        result = llm.chat_structured(
            [{"role": "user", "content": "analyze"}],
            output_schema=BullOpinion,
            system_prompt="bull",
            mock_data={
                "position": "watch",
                "arguments": [],
                "target_price": None,
                "confidence": 60,
                "assumptions": [],
            },
        )
        self.assertIsInstance(result, BullOpinion)
        self.assertEqual(result.position, "watch")

    @patch.object(llm, "LLM_ENABLED", False)
    def test_structured_mock_rejects_invalid_shape(self):
        with self.assertRaises(llm.StructuredOutputError):
            llm.chat_structured(
                [{"role": "user", "content": "analyze"}],
                output_schema=BullOpinion,
                system_prompt="bull",
                mock_data={"unexpected": True},
            )

    @patch.object(llm, "LLM_ENABLED", False)
    def test_structured_call_needs_explicit_mock(self):
        with self.assertRaises(llm.StructuredOutputError):
            llm.chat_structured(
                [{"role": "user", "content": "analyze"}],
                output_schema=BullOpinion,
                system_prompt="bull",
            )

    @patch.object(llm, "LLM_ENABLED", True)
    def test_english_structured_call_retries_invalid_schema(self):
        invalid = json.dumps({"unexpected": True})
        english = json.dumps({
            "position": "watch",
            "arguments": [{
                "claim": "Price is rising",
                "evidence_ids": ["e-1"],
                "explanation": "This explains the evidence.",
                "decision_impact": "Wait for confirmation.",
            }],
            "confidence": 60,
        })
        llm.reset_execution_status()
        with patch.object(llm, "_request_sync", side_effect=[invalid, english]) as request:
            result = llm.chat_structured(
                [{"role": "user", "content": "analyze"}],
                output_schema=BullOpinion,
                system_prompt="bull",
                output_locale="en-US",
                mock_data={
                    "position": "watch",
                    "arguments": [],
                    "confidence": 40,
                },
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result.arguments[0].claim, "Price is rising")
        self.assertEqual(llm.get_execution_status()["mode"], "live")

    @patch.object(llm, "LLM_ENABLED", True)
    def test_structured_failure_uses_explicit_degraded_fallback(self):
        llm.reset_execution_status()
        with patch.object(
            llm, "_request_sync", side_effect=httpx.ConnectError("offline")
        ):
            result = llm.chat_structured(
                [{"role": "user", "content": "analyze"}],
                output_schema=BullOpinion,
                system_prompt="bull",
                output_locale="en-US",
                max_retries=0,
                mock_data={
                    "position": "watch",
                    "arguments": [],
                    "confidence": 40,
                },
            )
        self.assertEqual(result.position, "watch")
        status = llm.get_execution_status()
        self.assertEqual(status["mode"], "degraded")
        self.assertEqual(status["fallbackCalls"], 1)


if __name__ == "__main__":
    unittest.main()
