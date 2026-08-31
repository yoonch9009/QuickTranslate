from __future__ import annotations

import unittest

from quicktranslate.model_profiles import recommended_parameters_for


class ModelProfileTests(unittest.TestCase):
    def test_qwen_non_thinking_and_thinking_profiles_are_distinct(self) -> None:
        non_thinking = recommended_parameters_for(
            "qwen/qwen3.8-flash",
            {"effort": "none"},
        )
        thinking = recommended_parameters_for(
            "qwen/qwen3.8-flash",
            {"effort": "low"},
        )

        self.assertEqual(non_thinking.values["temperature"], 0.7)
        self.assertEqual(non_thinking.values["top_p"], 0.8)
        self.assertEqual(non_thinking.values["presence_penalty"], 1.5)
        self.assertEqual(thinking.values["temperature"], 1.0)
        self.assertEqual(thinking.values["top_p"], 0.95)
        self.assertEqual(thinking.values["presence_penalty"], 0.0)

    def test_unknown_model_has_no_speculative_defaults(self) -> None:
        profile = recommended_parameters_for("vendor/future-model", None)

        self.assertEqual(profile.values, {})


if __name__ == "__main__":
    unittest.main()
