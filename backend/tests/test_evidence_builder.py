from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from _support import bootstrap_llm_dependencies  # noqa: E402

bootstrap_llm_dependencies()

from agents.evidence import EvidenceBuilder  # noqa: E402


def sample_context(_skin_id: str):
    prices = [100 + index * 0.4 for index in range(31)]
    prices[20] = 101.0
    return {
        "slug": "ak-redline-ft",
        "name": "AK-47 | Redline (FT)",
        "current_price": prices[-1],
        "current_date": "2026-07-21",
        "change_7d": 2.5,
        "change_30d": 12.0,
        "prices": prices,
        "volumes": [1000 + index * 20 for index in range(31)],
        "kb": ["高流动性饰品通常买卖价差较小"],
        "news": [
            {
                "title": "Major相关成交升温",
                "summary": "相关饰品成交量增加",
                "source": "internal",
                "published_at": "2026-07-20",
                "sentiment": "positive",
            }
        ],
    }


def sample_prediction(_name: str):
    return {
        "model": "LSTM-D",
        "predicted_price": 116.0,
        "change_pct": 3.57,
        "confidence": 82,
        "date": "2026-07-21",
    }


class EvidenceBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = EvidenceBuilder(
            context_loader=sample_context,
            prediction_loader=sample_prediction,
        )

    def test_builds_shared_snapshot_from_real_contract_shapes(self):
        snapshot = self.builder.build("ak-redline-ft")

        self.assertEqual(snapshot.skin_id, "ak-redline-ft")
        self.assertEqual(snapshot.hybrid_prediction.model, "LSTM-D")
        self.assertIsNotNone(snapshot.volatility_30d)
        self.assertIsNotNone(snapshot.max_drawdown_30d)
        self.assertEqual(snapshot.liquidity_score, 30.8)
        self.assertIn("model:hybrid_7d", snapshot.evidence_ids())
        self.assertTrue(any(item.evidence_id.startswith("news:") for item in snapshot.evidence))

    def test_missing_prediction_is_explicitly_degraded(self):
        builder = EvidenceBuilder(
            context_loader=sample_context,
            prediction_loader=lambda _name: None,
        )
        snapshot = builder.build("ak-redline-ft")
        self.assertTrue(snapshot.hybrid_prediction.degraded)
        self.assertEqual(snapshot.hybrid_prediction.confidence, 0)

    def test_rejects_unsupported_horizon(self):
        with self.assertRaises(ValueError):
            self.builder.build("ak-redline-ft", horizon_days=30)

    def test_rejects_unknown_skin(self):
        builder = EvidenceBuilder(
            context_loader=lambda _skin_id: None,
            prediction_loader=sample_prediction,
        )
        with self.assertRaises(LookupError):
            builder.build("missing")


if __name__ == "__main__":
    unittest.main()
