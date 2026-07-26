from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

from agents.base import BaseAgent  # noqa: E402
from agents.prompts import (  # noqa: E402
    BEAR_SYSTEM_PROMPT,
    BULL_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
)
from agents.schemas import (  # noqa: E402
    BullOpinion,
    Evidence,
    HybridPrediction,
    JudgeDecision,
    MarketSnapshot,
    UserProfile,
)


def validate(schema, value):
    if hasattr(schema, "model_validate"):
        return schema.model_validate(value)
    return schema.parse_obj(value)


class DummyAgent(BaseAgent[BullOpinion]):
    def build_user_payload(self, input_data):
        return {"skin_id": input_data}


def fake_structured_llm(messages, *, output_schema, **kwargs):
    return validate(
        output_schema,
        {
            "position": "watch",
            "arguments": [],
            "target_price": None,
            "confidence": 50,
            "assumptions": [],
        },
    )


class AgentSchemaTests(unittest.TestCase):
    def test_user_profile_rejects_invalid_risk(self):
        with self.assertRaises(ValidationError):
            UserProfile(risk_level="extreme")

    def test_judge_can_detect_unsupported_evidence(self):
        snapshot = MarketSnapshot(
            skin_id="ak-redline-ft",
            skin_name="AK-47 | Redline (FT)",
            current_price=42.0,
            hybrid_prediction=HybridPrediction(
                model="LSTM-Hybrid",
                predicted_price=44.0,
                change_pct=4.76,
                confidence=78,
            ),
            evidence=[
                Evidence(
                    evidence_id="price-001",
                    source="price_history",
                    title="Current price",
                    content="$42.00",
                )
            ],
        )
        decision = JudgeDecision(
            decision="watch",
            winner="draw",
            confidence=55,
            evidence_used=["price-001", "missing-999"],
        )
        self.assertEqual(decision.unsupported_evidence(snapshot), {"missing-999"})

    def test_market_snapshot_is_immutable(self):
        snapshot = MarketSnapshot(
            skin_id="ak-redline-ft",
            skin_name="AK-47 | Redline (FT)",
            current_price=42.0,
            hybrid_prediction=HybridPrediction(
                model="LSTM-Hybrid",
                predicted_price=44.0,
                change_pct=4.76,
                confidence=78,
            ),
        )
        with self.assertRaises(ValidationError):
            snapshot.current_price = 1.0


class AgentIsolationTests(unittest.TestCase):
    def make_agent(self, name: str, prompt: str, tools: set[str]) -> DummyAgent:
        return DummyAgent(
            name=name,
            system_prompt=prompt,
            output_schema=BullOpinion,
            allowed_tools=tools,
            llm_callable=fake_structured_llm,
        )

    def test_role_prompts_are_distinct(self):
        self.assertEqual(len({BULL_SYSTEM_PROMPT, BEAR_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT}), 3)

    def test_agents_do_not_share_history(self):
        bull = self.make_agent("bull", BULL_SYSTEM_PROMPT, {"positive_news"})
        other = self.make_agent("other", BEAR_SYSTEM_PROMPT, {"risk_metrics"})

        bull.run("ak-redline-ft")

        self.assertEqual(len(bull.history), 2)
        self.assertEqual(other.history, ())

        copied = bull.history
        copied[0]["content"] = "tampered"
        self.assertNotEqual(bull.history[0]["content"], "tampered")

    def test_tool_allowlist_is_enforced(self):
        bull = self.make_agent("bull", BULL_SYSTEM_PROMPT, {"positive_news"})
        self.assertTrue(bull.can_use_tool("positive_news"))
        with self.assertRaises(PermissionError):
            bull.require_tool("risk_metrics")


if __name__ == "__main__":
    unittest.main()
