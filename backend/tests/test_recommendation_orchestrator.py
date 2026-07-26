from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

from agents.orchestrator import AIOrchestrator, detect_intent  # noqa: E402
from agents.recommendation_agent import RecommendationAgent  # noqa: E402


CANDIDATES = [
    {
        "skinId": "ak-redline-ft",
        "name": "AK-47 | Redline (FT)",
        "category": "步枪",
        "price": 100,
        "change7d": 3,
        "change30d": 6,
        "volume": 8000,
        "volatility": 1.5,
        "rarity": 4,
    },
    {
        "skinId": "awp-risky-ft",
        "name": "AWP | Risky (FT)",
        "category": "狙击枪",
        "price": 120,
        "change7d": 12,
        "change30d": 20,
        "volume": 300,
        "volatility": 9,
        "rarity": 5,
    },
    {
        "skinId": "ak-expensive-fn",
        "name": "AK-47 | Expensive (FN)",
        "category": "步枪",
        "price": 900,
        "change7d": 8,
        "change30d": 12,
        "volume": 6000,
        "volatility": 2,
        "rarity": 6,
    },
]


class FakeSessionService:
    def create(self, skin_id, *, user_profile, rounds):
        return {"sessionId": "session-1", "skinId": skin_id, "messages": []}

    def get(self, session_id):
        return {
            "sessionId": session_id,
            "skinId": "ak-redline-ft",
            "marketSnapshot": {
                "skin_id": "ak-redline-ft",
                "skin_name": "AK-47 | Redline (FT)",
                "current_price": 100,
            },
        }

    def send_message(self, session_id, *, message, target_agent, locale=None):
        return {
            "sessionId": session_id,
            "messages": [{"role": "agent", "agentName": target_agent, "content": message}],
        }

    def run_round(self, session_id, *, message, locale=None):
        round_data = {
            "roundNo": 2,
            "userMessage": message,
            "bull": {"position": "buy"},
            "bear": {"position": "watch"},
            "judge": {"decision": "watch"},
        }
        return {
            "sessionId": session_id,
            "messages": [],
            "debateRounds": [round_data],
        }

    def answer_question(self, session_id, *, message, locale=None):
        return {
            "sessionId": session_id,
            "messages": [],
            "answer": f"基于当前裁决回答：{message}",
            "profileChanges": [],
        }

    def update_profile(self, session_id, *, message, locale=None):
        return {
            "sessionId": session_id,
            "messages": [],
            "profileChanges": ["预算：100"],
        }


def resolve_skin(_message, skin_id):
    if skin_id == "ak-redline-ft":
        return {"skinId": skin_id, "name": "AK-47 | Redline (FT)", "price": 100}
    return None


def resolve_ambiguous_weapon(_message, _skin_id):
    return {
        "ambiguous": True,
        "query": "M4A1-S",
        "candidates": [
            {"skinId": "m4a1-s-printstream-ft", "name": "M4A1-S | Printstream (FT)", "price": 120},
            {"skinId": "m4a1-s-decimator-ft", "name": "M4A1-S | Decimator (FT)", "price": 18},
        ],
    }


class RecommendationTests(unittest.TestCase):
    def test_english_recommendation_reasons_follow_locale(self):
        result = RecommendationAgent(lambda: CANDIDATES).recommend(
            "recommend an AK skin",
            budget=200,
            risk_level="medium",
            locale="en-US",
        )
        self.assertTrue(result)
        self.assertIn("7-day change", result[0]["reasons"][0])

    def test_budget_and_category_are_enforced(self):
        agent = RecommendationAgent(lambda: CANDIDATES)
        result = agent.recommend("推荐 AK-47 皮肤", budget=200, risk_level="medium")
        self.assertEqual([item["skinId"] for item in result], ["ak-redline-ft"])

        english_result = agent.recommend(
            "recommend an AK skin", budget=200, risk_level="medium"
        )
        self.assertEqual(
            [item["skinId"] for item in english_result], ["ak-redline-ft"]
        )

    def test_low_risk_prefers_liquid_stable_candidate(self):
        agent = RecommendationAgent(lambda: CANDIDATES)
        result = agent.recommend("推荐皮肤", budget=200, risk_level="low")
        self.assertEqual(result[0]["skinId"], "ak-redline-ft")
        self.assertTrue(result[0]["reasons"])


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.service = AIOrchestrator(
            recommender=RecommendationAgent(lambda: CANDIDATES),
            session_service=FakeSessionService(),  # type: ignore[arg-type]
            skin_resolver=resolve_skin,
            prediction_loader=lambda skin_id, horizon: {
                "skinId": skin_id,
                "horizon": horizon,
                "targetPrice": 106,
            },
            chat_loader=lambda _messages: "普通 AI 回答",
        )

    def test_intent_requires_specific_skin_for_automatic_debate(self):
        question = "\u8fd9\u4e2a\u503c\u5f97\u4e70\u5417"
        self.assertEqual(detect_intent(question, has_skin=False), "chat")
        self.assertEqual(detect_intent(question, has_skin=True), "debate")

    def test_recommendation_route(self):
        result = self.service.handle("推荐一个皮肤", budget=200)
        self.assertEqual(result["type"], "recommendation")
        self.assertEqual(len(result["recommendations"]), 2)

    def test_main_ai_receives_selected_english_locale(self):
        captured = []
        service = AIOrchestrator(
            recommender=RecommendationAgent(lambda: CANDIDATES),
            session_service=FakeSessionService(),  # type: ignore[arg-type]
            skin_resolver=lambda _message, _skin_id: None,
            chat_loader=lambda messages: captured.extend(messages) or "English reply",
        )
        result = service.handle("Explain the market", locale="en-US")
        self.assertEqual(result["message"], "English reply")
        self.assertEqual(captured[0]["role"], "system")
        self.assertIn("English", captured[0]["content"])

    def test_prediction_route_calls_hybrid_loader(self):
        result = self.service.handle(
            "预测价格", action="predict", skin_id="ak-redline-ft", horizon_days=7
        )
        self.assertEqual(result["type"], "prediction")
        self.assertEqual(result["prediction"]["targetPrice"], 106)

    def test_debate_route_creates_session(self):
        result = self.service.handle(
            "是否值得买", skin_id="ak-redline-ft", budget=150
        )
        self.assertEqual(result["type"], "debate")
        self.assertEqual(result["agentSession"]["sessionId"], "session-1")

    def test_ambiguous_weapon_requests_specific_skin_before_debate(self):
        service = AIOrchestrator(
            recommender=RecommendationAgent(lambda: CANDIDATES),
            session_service=FakeSessionService(),  # type: ignore[arg-type]
            skin_resolver=resolve_ambiguous_weapon,
            chat_loader=lambda _messages: "不应调用普通聊天",
        )
        result = service.handle("那我是否应该买入 M4A1-S")

        self.assertEqual(result["type"], "clarification")
        self.assertEqual(result["requestedAction"], "debate")
        self.assertEqual(len(result["skinCandidates"]), 2)
        self.assertIn("不会替你猜", result["message"])

    def test_followup_targets_one_agent(self):
        result = self.service.handle(
            "反驳流动性风险",
            session_id="session-1",
            target_agent="bull",
        )
        self.assertEqual(result["type"], "agent_followup")
        self.assertEqual(result["agentSession"]["messages"][0]["agentName"], "bull")

    def test_active_session_routes_main_ai_to_full_debate_round(self):
        result = self.service.handle(
            "我愿意更激进一些，请按这个条件再辩一轮",
            session_id="session-1",
        )
        self.assertEqual(result["type"], "debate_round")
        self.assertEqual(result["debateRound"]["roundNo"], 2)
        self.assertEqual(result["debateRound"]["judge"]["decision"], "watch")

    def test_active_session_question_is_answered_without_new_round(self):
        result = self.service.handle(
            "置信度45%是什么意思，是建议买入还是观望？",
            session_id="session-1",
        )
        self.assertEqual(result["type"], "debate_answer")
        self.assertEqual(result["message"], "普通 AI 回答")
        self.assertEqual(result["answerMode"], "llm_grounded")
        self.assertNotIn("debateRound", result)

    def test_active_session_prediction_bypasses_grounded_qa(self):
        result = self.service.handle(
            "现在能给我这个皮肤的未来价格预测吗",
            session_id="session-1",
        )
        self.assertEqual(result["type"], "prediction")
        self.assertEqual(result["skin"]["skinId"], "ak-redline-ft")
        self.assertEqual(result["prediction"]["targetPrice"], 106)
        self.assertNotIn("agentSession", result)

    def test_active_session_recommendation_bypasses_grounded_qa(self):
        result = self.service.handle(
            "再推荐几个预算内的皮肤",
            session_id="session-1",
            budget=200,
        )
        self.assertEqual(result["type"], "recommendation")
        self.assertTrue(result["recommendations"])

    def test_grounded_qa_prompt_requests_a_direct_natural_answer(self):
        captured = []
        service = AIOrchestrator(
            recommender=RecommendationAgent(lambda: CANDIDATES),
            session_service=FakeSessionService(),  # type: ignore[arg-type]
            skin_resolver=resolve_skin,
            prediction_loader=lambda _skin_id, _horizon: {},
            chat_loader=lambda messages: captured.extend(messages) or "直接回答",
        )
        result = service.handle(
            "Judge 的置信度是什么意思？",
            session_id="session-1",
        )
        self.assertEqual(result["message"], "直接回答")
        self.assertIn("Answer the user's exact question directly", captured[0]["content"])
        self.assertIn("Public debate facts", captured[1]["content"])

    def test_profile_statement_automatically_reruns_debate(self):
        result = self.service.handle(
            "我的预算100，主要用于自用",
            session_id="session-1",
        )
        self.assertEqual(result["type"], "debate_round")
        self.assertEqual(result["debateRound"]["roundNo"], 2)

    def test_plain_aggressive_preference_automatically_reruns_debate(self):
        result = self.service.handle(
            "我希望更激进一些",
            session_id="session-1",
        )
        self.assertEqual(result["type"], "debate_round")
        self.assertEqual(result["debateRound"]["roundNo"], 2)


if __name__ == "__main__":
    unittest.main()
