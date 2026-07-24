import unittest
import json
import re

from agents.bear_agent import BearAgent
from agents.bull_agent import BullAgent
from agents.conversation import classify_session_input
from agents.presentation import infer_user_profile
from agents.risk_policy import apply_risk_policy, score_market, select_action
from agents.judge_agent import JudgeAgent
from agents.schemas import (
    BearInput,
    BullInput,
    Evidence,
    HybridPrediction,
    JudgeInput,
    MarketSnapshot,
    UserProfile,
)


def snapshot(**overrides):
    values = {
        "skin_id": "skin-1",
        "skin_name": "Test Skin",
        "current_price": 100,
        "change_7d": 4,
        "change_30d": 8,
        "volatility_30d": 3,
        "max_drawdown_30d": 6,
        "liquidity_score": 75,
        "hybrid_prediction": HybridPrediction(
            model="test",
            predicted_price=108,
            change_pct=8,
            confidence=70,
            degraded=False,
        ),
        "evidence": (
            Evidence(
                evidence_id="positive",
                source="test",
                title="positive",
                content="positive evidence",
                direction="positive",
            ),
            Evidence(
                evidence_id="negative",
                source="test",
                title="negative",
                content="negative evidence",
                direction="negative",
            ),
        ),
    }
    values.update(overrides)
    return MarketSnapshot(**values)


class RiskPolicyTests(unittest.TestCase):
    def test_english_mock_debate_contains_no_chinese_user_facing_text(self):
        item = snapshot()
        profile = UserProfile(risk_level="medium", locale="en-US")
        bull = BullAgent._mock_opinion(BullInput(snapshot=item, user_profile=profile))
        bear = BearAgent._mock_opinion(BearInput(snapshot=item, user_profile=profile))
        judge_input = JudgeInput(
            snapshot=item,
            user_profile=profile,
            bull_history=[bull],
            bear_history=[bear],
        )
        judge = JudgeAgent._complete_strategy(
            JudgeAgent._mock_decision(judge_input), judge_input
        )
        public_text = json.dumps(
            {
                "bull": bull.model_dump(),
                "bear": bear.model_dump(),
                "judge": judge.model_dump(),
            },
            ensure_ascii=False,
        )
        self.assertIsNone(re.search(r"[\u4e00-\u9fff]", public_text))

    def test_plain_aggressive_preference_is_parsed_and_rerun_worthy(self):
        message = "我希望更激进一些"
        self.assertEqual(classify_session_input(message), "preference")
        profile, changes = infer_user_profile(message, UserProfile(risk_level="medium"))
        self.assertEqual(profile.risk_level, "high")
        self.assertTrue(changes)

    def test_english_aggressive_preference_is_parsed(self):
        message = "I want a more aggressive strategy"
        self.assertEqual(classify_session_input(message), "preference")
        profile, changes = infer_user_profile(
            message, UserProfile(risk_level="medium", locale="en-US")
        )
        self.assertEqual(profile.risk_level, "high")
        self.assertTrue(changes)
        self.assertIn("Risk preference", changes[0])

    def test_objective_scores_do_not_change_with_user_risk(self):
        item = snapshot()
        scores = score_market(item)
        for risk in ("low", "medium", "high"):
            result = apply_risk_policy(item, UserProfile(risk_level=risk))
            self.assertEqual(
                (result.opportunity_score, result.risk_score, result.decision_score),
                scores[:3],
            )

    def test_risk_threshold_changes_action_for_same_scores(self):
        item = snapshot()
        low, _ = select_action(68, 40, item, UserProfile(risk_level="low"))
        medium, _ = select_action(68, 40, item, UserProfile(risk_level="medium"))
        high, _ = select_action(68, 40, item, UserProfile(risk_level="high"))
        self.assertEqual(low, "scale_in")
        self.assertEqual(medium, "scale_in")
        self.assertEqual(high, "buy_now")

    def test_position_size_increases_with_risk_when_actionable(self):
        item = snapshot()
        results = [
            apply_risk_policy(item, UserProfile(risk_level=risk))
            for risk in ("low", "medium", "high")
        ]
        self.assertLessEqual(results[0].position_size_pct, results[1].position_size_pct)
        self.assertLessEqual(results[1].position_size_pct, results[2].position_size_pct)

    def test_budget_is_a_hard_constraint(self):
        item = snapshot()
        for risk in ("low", "medium", "high"):
            action, reason = select_action(
                95, 5, item, UserProfile(risk_level=risk, budget=50)
            )
            self.assertEqual(action, "avoid")
            self.assertIn("预算", reason)

    def test_bull_and_bear_confidence_are_profile_independent(self):
        item = snapshot()
        bull = BullAgent()
        bear = BearAgent()
        bull_values = {
            bull._mock_opinion(BullInput(snapshot=item, user_profile=UserProfile(risk_level=risk))).confidence
            for risk in ("low", "medium", "high")
        }
        bear_values = {
            bear._mock_opinion(BearInput(snapshot=item, user_profile=UserProfile(risk_level=risk))).confidence
            for risk in ("low", "medium", "high")
        }
        self.assertEqual(len(bull_values), 1)
        self.assertEqual(len(bear_values), 1)


if __name__ == "__main__":
    unittest.main()
