from __future__ import annotations

import unittest

from quicktranslate.model_catalog import select_lowest_reasoning


class ModelReasoningSelectionTests(unittest.TestCase):
    def test_mandatory_model_uses_weakest_supported_effort(self) -> None:
        effective = select_lowest_reasoning(
            {
                "mandatory": True,
                "supported_efforts": ["max", "high", "low"],
                "default_effort": "max",
            }
        )

        self.assertEqual(effective.config, {"effort": "low"})

    def test_optional_model_is_explicitly_disabled_when_supported(self) -> None:
        effective = select_lowest_reasoning(
            {
                "mandatory": False,
                "supported_efforts": ["high", "low", "none"],
                "default_effort": "high",
            }
        )

        self.assertEqual(effective.config, {"effort": "none"})

    def test_future_effort_names_follow_openrouter_order(self) -> None:
        effective = select_lowest_reasoning(
            {
                "mandatory": True,
                "supported_efforts": ["future-strong", "future-light"],
            }
        )

        self.assertEqual(effective.config, {"effort": "future-light"})

    def test_null_effort_list_uses_gateway_low_for_mandatory_model(self) -> None:
        effective = select_lowest_reasoning(
            {"mandatory": True, "supported_efforts": None}
        )

        self.assertEqual(effective.config, {"effort": "low"})


if __name__ == "__main__":
    unittest.main()
