from __future__ import annotations

import unittest

from quicktranslate.settings import AppSettings
from quicktranslate.translator import (
    RequestFailure,
    TranslationResult,
    estimate_max_output_tokens,
    extract_output_text,
    load_cached_translation,
    prepare_request,
    should_retry_with_fallback,
    store_cached_translation,
)


class TranslatorTests(unittest.TestCase):
    def test_prepare_request_sets_reasoning_by_model(self) -> None:
        settings = AppSettings()

        qwen_payload = prepare_request("hello", settings, settings.primary_model)
        gemma_payload = prepare_request("hello", settings, settings.fallback_model)

        self.assertEqual(qwen_payload["reasoning"]["effort"], "none")
        self.assertEqual(gemma_payload["reasoning"]["effort"], "minimal")
        self.assertEqual(qwen_payload["text"]["format"]["type"], "text")

    def test_estimate_max_output_tokens_prefers_lower_limits_for_short_text(self) -> None:
        self.assertEqual(estimate_max_output_tokens("hello"), 120)
        self.assertEqual(estimate_max_output_tokens("a" * 120), 220)
        self.assertLessEqual(estimate_max_output_tokens("a" * 2000), 1800)

    def test_extract_output_text_uses_output_text_then_message_content(self) -> None:
        direct = extract_output_text({"output_text": "translated"})
        fallback = extract_output_text(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "fallback"}],
                    }
                ]
            }
        )

        self.assertEqual(direct, "translated")
        self.assertEqual(fallback, "fallback")

    def test_cache_uses_primary_model_key_and_ttl(self) -> None:
        settings = AppSettings(cache_ttl_seconds=300)
        result = TranslationResult(text="안녕하세요", model=settings.primary_model)

        store_cached_translation("hello", settings, result)
        cached = load_cached_translation("hello", settings)

        self.assertIsNotNone(cached)
        self.assertEqual(cached.text, "안녕하세요")

    def test_retry_policy_only_retries_retryable_failures(self) -> None:
        settings = AppSettings(fallback_on_provider_error_only=True)

        retryable = RequestFailure(
            user_message="retry",
            log_message="retry",
            retryable=True,
            status_code=503,
        )
        non_retryable = RequestFailure(
            user_message="stop",
            log_message="stop",
            retryable=False,
            status_code=401,
        )

        self.assertTrue(should_retry_with_fallback(retryable, settings))
        self.assertFalse(should_retry_with_fallback(non_retryable, settings))


if __name__ == "__main__":
    unittest.main()
