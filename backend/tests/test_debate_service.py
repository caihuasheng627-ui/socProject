from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

from agents.bear_agent import BearAgent  # noqa: E402
from agents.bull_agent import BullAgent  # noqa: E402
from agents.debate_service import DebateService, outcome_to_api  # noqa: E402
from agents.evidence import EvidenceBuilder  # noqa: E402
from agents.judge_agent import JudgeAgent  # noqa: E402
from agents.schemas import UserProfile  # noqa: E402


def context_loader(skin_id: str):
    return {
        "slug": skin_id,
        "name": "AK-47 | Redline (FT)",
        "current_price": 100,
        "current_date": "2026-07-21",
        "change_7d": 3.0,
        "change_30d": 8.0,
        "prices": [95 + index * 0.2 for index in range(31)],
        "volumes": [4000 + index * 25 for index in range(31)],
        "kb": [],
        "news": [
            {
                "title": "成交量升温",
                "summary": "相关饰品成交活跃",
                "source": "internal",
                "published_at": "2026-07-20",
                "sentiment": "positive",
            }
        ],
    }


def prediction_loader(_name: str):
    return {
        "model": "LSTM-D",
        "predicted_price": 106,
        "change_pct": 6,
        "confidence": 82,
        "date": "2026-07-21",
    }


class DebateServiceTests(unittest.TestCase):
    def make_service(self, *, parallel: bool = False):
        bull = BullAgent()
        bear = BearAgent()
        judge = JudgeAgent()
        service = DebateService(
            evidence_builder=EvidenceBuilder(
                context_loader=context_loader,
                prediction_loader=prediction_loader,
            ),
            bull_agent=bull,
            bear_agent=bear,
            judge_agent=judge,
            parallel=parallel,
        )
        return service, bull, bear, judge

    def test_three_rounds_accumulate_isolated_histories(self):
        service, bull, bear, judge = self.make_service()
        outcome = service.run("ak-redline-ft", rounds=3)

        self.assertEqual(len(outcome.rounds), 3)
        self.assertEqual(len(bull.history), 6)
        self.assertEqual(len(bear.history), 6)
        self.assertEqual(len(judge.history), 2)
        self.assertIs(outcome.rounds[0].bull.__class__, outcome.rounds[1].bull.__class__)
        self.assertEqual(outcome.snapshot.skin_id, "ak-redline-ft")

    def test_parallel_round_execution_produces_complete_outcome(self):
        service, _, _, _ = self.make_service(parallel=True)
        outcome = service.run("ak-redline-ft", rounds=2)
        self.assertEqual(len(outcome.rounds), 2)
        self.assertIsNotNone(outcome.judge.decision)

    def test_api_shape_keeps_legacy_text_and_adds_structured_results(self):
        service, _, _, _ = self.make_service()
        outcome = service.run(
            "ak-redline-ft",
            user_profile=UserProfile(budget=150, risk_level="medium"),
            rounds=3,
        )
        payload = outcome_to_api(outcome, mode="structured_mock")

        self.assertEqual(payload["schemaVersion"], 2)
        self.assertIsInstance(payload["rounds"][0]["bull"], str)
        self.assertIsInstance(payload["rounds"][0]["bullOpinion"], dict)
        self.assertIn("decision", payload["judge"])
        self.assertNotEqual(payload["consensus"]["recommendation"], "见 Round 3 共识")
        self.assertTrue(payload["agentMeta"]["isolated"])

    def test_round_count_is_bounded(self):
        service, _, _, _ = self.make_service()
        with self.assertRaises(ValueError):
            service.run("ak-redline-ft", rounds=0)
        with self.assertRaises(ValueError):
            service.run("ak-redline-ft", rounds=6)


if __name__ == "__main__":
    unittest.main()
