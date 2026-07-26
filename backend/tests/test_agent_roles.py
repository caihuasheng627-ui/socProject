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
from agents.judge_agent import EvidenceValidationError, JudgeAgent  # noqa: E402
from agents.schemas import (  # noqa: E402
    AgentArgument,
    BearInput,
    BearOpinion,
    BullInput,
    BullOpinion,
    Evidence,
    HybridPrediction,
    JudgeInput,
    MarketSnapshot,
    UserProfile,
)
from agents.tools import BEAR_FOCUS_TOOL, BULL_FOCUS_TOOL, JUDGE_EVIDENCE_TOOL  # noqa: E402


def make_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        skin_id="ak-redline-ft",
        skin_name="AK-47 | Redline (FT)",
        current_price=100,
        change_7d=3.0,
        change_30d=8.0,
        volatility_30d=2.0,
        max_drawdown_30d=4.0,
        liquidity_score=75,
        hybrid_prediction=HybridPrediction(
            model="LSTM-D",
            predicted_price=106,
            change_pct=6,
            confidence=82,
        ),
        evidence=(
            Evidence(
                evidence_id="positive-1",
                source="model",
                title="上涨预测",
                content="Hybrid预测上涨6%",
                direction="positive",
            ),
            Evidence(
                evidence_id="negative-1",
                source="price_history",
                title="最大回撤",
                content="30日最大回撤4%",
                direction="negative",
            ),
            Evidence(
                evidence_id="neutral-1",
                source="price_history",
                title="当前价格",
                content="当前价格$100",
                direction="neutral",
            ),
        ),
    )


class AgentRoleTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot()
        self.profile = UserProfile(budget=150, horizon_days=7, risk_level="medium")

    def test_agents_have_distinct_tools_and_histories(self):
        bull = BullAgent()
        bear = BearAgent()
        judge = JudgeAgent()

        self.assertEqual(bull.allowed_tools, frozenset({BULL_FOCUS_TOOL}))
        self.assertEqual(bear.allowed_tools, frozenset({BEAR_FOCUS_TOOL}))
        self.assertEqual(judge.allowed_tools, frozenset({JUDGE_EVIDENCE_TOOL}))

        bull.analyze(BullInput(snapshot=self.snapshot, user_profile=self.profile))
        self.assertEqual(len(bull.history), 2)
        self.assertEqual(bear.history, ())
        self.assertEqual(judge.history, ())

    def test_bull_and_bear_produce_cited_structured_opinions(self):
        bull = BullAgent().analyze(
            BullInput(snapshot=self.snapshot, user_profile=self.profile)
        )
        bear = BearAgent().analyze(
            BearInput(snapshot=self.snapshot, user_profile=self.profile)
        )

        self.assertIsInstance(bull, BullOpinion)
        self.assertIsInstance(bear, BearOpinion)
        self.assertEqual(bull.position, "buy")
        self.assertTrue(bull.arguments)
        self.assertEqual(bull.arguments[0].evidence_ids, ("positive-1",))
        self.assertTrue(bear.arguments)
        self.assertEqual(bear.arguments[0].evidence_ids, ("negative-1",))

    def test_english_agent_call_has_hard_language_contract(self):
        captured = {}

        def english_llm(messages, *, output_schema, system_prompt, output_locale, **kwargs):
            captured["system_prompt"] = system_prompt
            captured["output_locale"] = output_locale
            return output_schema(
                position="watch",
                arguments=[],
                confidence=50,
            )

        profile = UserProfile(locale="en-US")
        result = BullAgent(llm_callable=english_llm).analyze(
            BullInput(snapshot=self.snapshot, user_profile=profile)
        )
        self.assertEqual(captured["output_locale"], "en-US")
        self.assertIn("Every user-facing string value", captured["system_prompt"])
        self.assertTrue(result.arguments)

    def test_english_agent_repairs_mixed_language_public_fields(self):
        def mixed_llm(messages, *, output_schema, **kwargs):
            return output_schema(
                position="watch",
                arguments=[AgentArgument(
                    claim="价格上涨",
                    evidence_ids=("positive-1",),
                    explanation="这是中文解释",
                    decision_impact="需要继续观察",
                )],
                confidence=60,
                assumptions=("这是中文假设",),
            )

        result = BullAgent(llm_callable=mixed_llm).analyze(BullInput(
            snapshot=self.snapshot,
            user_profile=UserProfile(locale="en-US"),
        ))
        public_text = " ".join([
            result.arguments[0].claim,
            result.arguments[0].explanation,
            result.arguments[0].decision_impact,
            *result.assumptions,
        ])
        self.assertNotRegex(public_text, r"[\u3400-\u9fff]")

    def test_second_round_only_receives_public_opponent_opinion(self):
        first_bear = BearOpinion(
            position="watch",
            arguments=[
                AgentArgument(
                    claim="存在回撤风险",
                    evidence_ids=["negative-1"],
                )
            ],
            confidence=50,
            risks=["最大回撤"],
        )
        bull = BullAgent()
        payload = bull.build_user_payload(
            BullInput(
                snapshot=self.snapshot,
                user_profile=self.profile,
                round_no=2,
                bear_opinion=first_bear,
            )
        )
        self.assertEqual(payload["task"], "rebut_bear")
        self.assertEqual(payload["bear_public_opinion"]["position"], "watch")
        self.assertNotIn("history", payload["bear_public_opinion"])

    def test_judge_validates_and_returns_structured_decision(self):
        bull = BullAgent().analyze(BullInput(snapshot=self.snapshot))
        bear = BearAgent().analyze(BearInput(snapshot=self.snapshot))
        decision = JudgeAgent().decide(
            JudgeInput(
                snapshot=self.snapshot,
                user_profile=self.profile,
                bull_history=[bull],
                bear_history=[bear],
            )
        )
        self.assertFalse(decision.unsupported_evidence(self.snapshot))
        self.assertIn(decision.decision, {"buy", "watch", "avoid"})

    def test_role_recommendation_difference_is_not_a_factual_conflict(self):
        def judge_llm(messages, *, output_schema, **kwargs):
            return output_schema(
                decision="watch",
                winner="draw",
                confidence=60,
                evidence_used=("positive-1", "negative-1"),
                strategy_action="wait_for_trigger",
                true_conflicts=(
                    "Bull 建议买入，Bear 建议避免买入，双方行动建议相反。",
                ),
                buy_triggers=("价格动量转正。",),
                exit_triggers=("跌破止损价。",),
                recheck_after_days=7,
            )

        bull = BullAgent().analyze(BullInput(snapshot=self.snapshot))
        bear = BearAgent().analyze(BearInput(snapshot=self.snapshot))
        decision = JudgeAgent(llm_callable=judge_llm).decide(JudgeInput(
            snapshot=self.snapshot,
            user_profile=self.profile,
            bull_history=[bull],
            bear_history=[bear],
        ))

        self.assertEqual(decision.true_conflicts, ())
        self.assertTrue(decision.complementary_views)

    def test_judge_rejects_agent_claim_with_unknown_evidence(self):
        invalid_bull = BullOpinion(
            position="buy",
            arguments=[
                AgentArgument(claim="没有来源的上涨", evidence_ids=["invented-999"])
            ],
            confidence=90,
        )
        bear = BearAgent().analyze(BearInput(snapshot=self.snapshot))
        with self.assertRaises(EvidenceValidationError):
            JudgeAgent().decide(
                JudgeInput(
                    snapshot=self.snapshot,
                    bull_history=[invalid_bull],
                    bear_history=[bear],
                )
            )

    def test_invalid_judge_result_does_not_enter_history(self):
        def invalid_judge_llm(messages, *, output_schema, **kwargs):
            return output_schema(
                decision="buy",
                winner="bull",
                confidence=90,
                evidence_used=("invented-judge-evidence",),
            )

        bull = BullAgent().analyze(BullInput(snapshot=self.snapshot))
        bear = BearAgent().analyze(BearInput(snapshot=self.snapshot))
        judge = JudgeAgent(llm_callable=invalid_judge_llm)
        with self.assertRaises(EvidenceValidationError):
            judge.decide(
                JudgeInput(
                    snapshot=self.snapshot,
                    bull_history=[bull],
                    bear_history=[bear],
                )
            )
        self.assertEqual(judge.history, ())


if __name__ == "__main__":
    unittest.main()
