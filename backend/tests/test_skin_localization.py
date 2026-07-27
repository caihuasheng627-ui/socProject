from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents import skin_localization as sl  # noqa: E402


SAMPLE_MAP = {
    "AWP | Asiimov (Field-Tested)": "AWP | 二西莫夫 (久经沙场)",
    "AWP | Asiimov (Minimal Wear)": "AWP | 二西莫夫 (略有磨损)",
    "AK-47 | Asiimov (Field-Tested)": "AK-47 | 二西莫夫 (久经沙场)",
    "AK-47 | Fire Serpent (Minimal Wear)": "AK-47 | 火蛇 (略有磨损)",
    "M4A1-S | Golden Coil (Factory New)": "M4A1消音版 | 金蛇缠绕 (崭新出厂)",
}


class SkinLocalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        sl.get_en_to_zh_map.cache_clear()

    @patch.object(sl, "get_en_to_zh_map", return_value=SAMPLE_MAP)
    def test_full_chinese_display_name(self, _mock_map):
        available = frozenset(SAMPLE_MAP)
        hits = sl.match_market_hash_names("帮我看看 AWP | 二西莫夫 (久经沙场) 走势", available)
        self.assertEqual(hits[0], "AWP | Asiimov (Field-Tested)")

    @patch.object(sl, "get_en_to_zh_map", return_value=SAMPLE_MAP)
    def test_partial_chinese_with_weapon(self, _mock_map):
        available = frozenset(SAMPLE_MAP)
        hits = sl.match_market_hash_names("AWP 二西莫夫最近怎么样", available)
        self.assertEqual(hits[0], "AWP | Asiimov (Field-Tested)")

    @patch.object(sl, "get_en_to_zh_map", return_value=SAMPLE_MAP)
    def test_nickname_only_resolves_weapon_family(self, _mock_map):
        available = frozenset(SAMPLE_MAP)
        hits = sl.match_market_hash_names("AK 火蛇值得买吗", available)
        self.assertEqual(hits[0], "AK-47 | Fire Serpent (Minimal Wear)")

    @patch.object(sl, "get_en_to_zh_map", return_value=SAMPLE_MAP)
    def test_ambiguous_nickname_without_weapon(self, _mock_map):
        available = frozenset(SAMPLE_MAP)
        hits = sl.match_market_hash_names("二西莫夫还能不能买", available)
        weapons = {sl._english_weapon(name) for name in hits}
        self.assertIn("AWP", weapons)
        self.assertIn("AK-47", weapons)

    @patch.object(sl, "get_en_to_zh_map", return_value=SAMPLE_MAP)
    def test_wear_hint_prefers_matching_float(self, _mock_map):
        available = frozenset(SAMPLE_MAP)
        hits = sl.match_market_hash_names("AWP 二西莫夫 略有磨损", available)
        self.assertEqual(hits[0], "AWP | Asiimov (Minimal Wear)")


if __name__ == "__main__":
    unittest.main()
