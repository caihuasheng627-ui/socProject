from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

from agents.debate_service import DebateService  # noqa: E402
from agents.evidence import EvidenceBuilder  # noqa: E402
from agents.session_service import AgentSessionService  # noqa: E402
from agents.session_store import SessionNotFoundError, SessionStore  # noqa: E402
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
        "news": [],
    }


def prediction_loader(_name: str):
    return {
        "model": "LSTM-D",
        "predicted_price": 106,
        "change_pct": 6,
        "confidence": 82,
        "date": "2026-07-21",
    }


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)

        def connection_factory():
            return sqlite3.connect(self.db_path)

        self.connection_factory = connection_factory
        self.store = SessionStore(connection_factory)
        debate = DebateService(
            evidence_builder=EvidenceBuilder(
                context_loader=context_loader,
                prediction_loader=prediction_loader,
            ),
            parallel=False,
        )
        self.service = AgentSessionService(store=self.store, debate_service=debate)

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def create_session(self):
        return self.service.create(
            "ak-redline-ft",
            user_profile=UserProfile(budget=150, risk_level="medium"),
            rounds=1,
        )

    def test_create_and_reload_session(self):
        created = self.create_session()
        reloaded = SessionStore(self.connection_factory).get(created["sessionId"])

        self.assertEqual(reloaded.skin_id, "ak-redline-ft")
        self.assertEqual(len(reloaded.bull_history), 1)
        self.assertEqual(len(reloaded.bear_history), 1)
        self.assertEqual(len(reloaded.judge_history), 1)
        self.assertEqual(len(reloaded.messages), 3)

    def test_user_can_target_bull_then_request_new_judge_decision(self):
        created = self.create_session()
        session_id = created["sessionId"]

        after_bull = self.service.send_message(
            session_id,
            message="请回应Bear关于回撤的质疑",
            target_agent="bull",
        )
        self.assertEqual(len(after_bull["bullHistory"]), 2)
        self.assertEqual(len(after_bull["bearHistory"]), 1)
        self.assertEqual(after_bull["messages"][-2]["agentName"], "bull")
        self.assertEqual(after_bull["messages"][-2]["role"], "user")

        after_judge = self.service.send_message(
            session_id,
            message="结合Bull的新回应重新裁决",
            target_agent="judge",
        )
        self.assertEqual(len(after_judge["judgeHistory"]), 2)
        self.assertEqual(after_judge["messages"][-1]["agentName"], "judge")

    def test_user_can_target_bear(self):
        created = self.create_session()
        updated = self.service.send_message(
            created["sessionId"],
            message="请重点分析流动性风险",
            target_agent="bear",
        )
        self.assertEqual(len(updated["bearHistory"]), 2)
        self.assertEqual(len(updated["bullHistory"]), 1)

    def test_user_message_runs_full_sequential_round_and_updates_risk(self):
        created = self.create_session()
        updated = self.service.run_round(
            created["sessionId"],
            message="我愿意更激进一些，但请双方解释回撤风险",
        )

        self.assertEqual(updated["userProfile"]["risk_level"], "high")
        self.assertEqual(len(updated["bullHistory"]), 2)
        self.assertEqual(len(updated["bearHistory"]), 2)
        self.assertEqual(len(updated["judgeHistory"]), 2)
        latest = updated["debateRounds"][-1]
        self.assertEqual(latest["roundNo"], 2)
        self.assertIn("激进", latest["userMessage"])
        self.assertTrue(latest["bull"]["arguments"][0]["explanation"])
        self.assertTrue(latest["bear"]["arguments"][0]["decision_impact"])
        self.assertIn("用户意见", latest["judge"]["user_view_considered"])
        self.assertTrue(latest["judge"]["change_summary"])
        self.assertTrue(updated["evidenceGuide"])
        self.assertTrue(latest["judge"]["summary"])
        self.assertTrue(latest["judge"]["recommendation"])
        self.assertEqual(len(latest["judge"]["confidence_basis"]), 6)
        self.assertIn("modelReliability", latest["judge"]["confidence_components"])
        self.assertIn("profileCompatibility", latest["judge"]["confidence_components"])
        self.assertIn(
            latest["judge"]["strategy_action"],
            {"buy_now", "scale_in", "wait_for_trigger", "avoid", "insufficient"},
        )
        self.assertTrue(latest["judge"]["agreed_facts"])
        self.assertTrue(latest["judge"]["complementary_views"])
        self.assertTrue(latest["judge"]["evidence_verdicts"])
        self.assertTrue(latest["judge"]["entry_strategy"])
        self.assertTrue(latest["judge"]["buy_triggers"])
        self.assertTrue(latest["judge"]["exit_triggers"])
        self.assertGreaterEqual(latest["judge"]["recheck_after_days"], 1)

        tail = updated["messages"][-4:]
        self.assertEqual(
            [(item["role"], item["agentName"]) for item in tail],
            [("user", "orchestrator"), ("agent", "bull"),
             ("agent", "bear"), ("agent", "judge")],
        )
        self.assertEqual({item["round"] for item in tail}, {2})

    def test_question_does_not_start_round_and_can_update_multiple_preferences(self):
        created = self.create_session()
        before_rounds = len(created["debateRounds"])
        answered = self.service.answer_question(
            created["sessionId"],
            message="我的预算100，主要自用，最多接受亏损5%，置信度是什么意思？",
        )

        self.assertEqual(len(answered["debateRounds"]), before_rounds)
        self.assertEqual(answered["userProfile"]["budget"], 100)
        self.assertEqual(answered["userProfile"]["purpose"], "use")
        self.assertEqual(answered["userProfile"]["loss_tolerance_pct"], 5)
        self.assertIn("上涨概率", answered["answer"])
        self.assertIn("Hybrid", answered["answer"])
        self.assertIn("Bull", answered["answer"])
        self.assertIn("Bear", answered["answer"])
        self.assertIn("Judge", answered["answer"])
        self.assertTrue(answered["profileChanges"])

    def test_budget_constraint_actually_changes_judge_decision(self):
        created = self.create_session()
        self.service.update_profile(
            created["sessionId"], message="我的预算50，主要用于投资"
        )
        updated = self.service.run_round(
            created["sessionId"], message="请按这些条件再辩一轮"
        )
        judge = updated["debateRounds"][-1]["judge"]

        self.assertEqual(judge["decision"], "avoid")
        self.assertIn("超过你的预算", judge["recommendation"])
        self.assertLess(judge["confidence_components"]["profileCompatibility"], 100)

    def test_rejects_unknown_session_and_target(self):
        with self.assertRaises(SessionNotFoundError):
            self.service.get("missing")

        created = self.create_session()
        with self.assertRaises(ValueError):
            self.service.send_message(
                created["sessionId"],
                message="hello",
                target_agent="unknown",  # type: ignore[arg-type]
            )

    def test_database_does_not_define_hidden_reasoning_column(self):
        connection = self.connection_factory()
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(agent_messages)").fetchall()
            }
        finally:
            connection.close()
        self.assertNotIn("chain_of_thought", columns)
        self.assertNotIn("private_reasoning", columns)


if __name__ == "__main__":
    unittest.main()
