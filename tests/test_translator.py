from __future__ import annotations

import unittest

from quicktranslate.settings import AppSettings, migrate_model_name
from quicktranslate.translator import (
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENROUTER,
    RequestFailure,
    TranslationResult,
    api_key_for_provider,
    endpoint_for_provider,
    estimate_max_output_tokens,
    extract_deepseek_output_text,
    extract_output_text,
    extract_output_text_for,
    load_cached_translation,
    prepare_request,
    provider_for_model,
    should_retry_with_fallback,
    split_model,
    store_cached_translation,
)


class TranslatorTests(unittest.TestCase):
    def test_prepare_request_passes_reasoning_config_by_model(self) -> None:
        settings = AppSettings(
            primary_model="openrouter/qwen/qwen3.5-flash-02-23",
            primary_reasoning_config={"effort": "high", "exclude": True},
            fallback_model="openrouter/google/gemma-4-26b-a4b-it",
            fallback_reasoning_config={"max_tokens": 2048, "enabled": True},
        )

        qwen_payload = prepare_request("hello", settings, settings.primary_model)
        gemma_payload = prepare_request("hello", settings, settings.fallback_model)

        # The "<provider>/" prefix is stripped before reaching OpenRouter.
        self.assertEqual(qwen_payload["model"], "qwen/qwen3.5-flash-02-23")
        self.assertEqual(qwen_payload["reasoning"]["effort"], "high")
        self.assertTrue(qwen_payload["reasoning"]["exclude"])
        self.assertEqual(gemma_payload["reasoning"]["max_tokens"], 2048)
        self.assertTrue(gemma_payload["reasoning"]["enabled"])
        self.assertEqual(qwen_payload["text"]["format"]["type"], "text")

    def test_prepare_request_omits_reasoning_when_not_configured(self) -> None:
        settings = AppSettings(
            primary_model="openrouter/qwen/qwen3.5-flash-02-23",
            primary_reasoning_config=None,
            fallback_reasoning_config=None,
        )

        payload = prepare_request("hello", settings, settings.primary_model)

        self.assertNotIn("reasoning", payload)

    def test_split_model_parses_provider_prefix(self) -> None:
        self.assertEqual(
            split_model("deepseek/deepseek-v4-flash"),
            (PROVIDER_DEEPSEEK, "deepseek-v4-flash"),
        )
        self.assertEqual(
            split_model("openrouter/tencent/hy3-preview"),
            (PROVIDER_OPENROUTER, "tencent/hy3-preview"),
        )
        self.assertEqual(
            split_model("openrouter/deepseek/deepseek-v4-flash"),
            (PROVIDER_OPENROUTER, "deepseek/deepseek-v4-flash"),
        )
        # Unknown / unprefixed ids pass through to OpenRouter unchanged.
        self.assertEqual(
            split_model("tencent/hy3-preview"),
            (PROVIDER_OPENROUTER, "tencent/hy3-preview"),
        )

    def test_provider_for_model_routes_deepseek_and_openrouter(self) -> None:
        self.assertEqual(provider_for_model("deepseek/deepseek-v4-flash"), PROVIDER_DEEPSEEK)
        self.assertEqual(provider_for_model("deepseek/deepseek-v4-pro"), PROVIDER_DEEPSEEK)
        self.assertEqual(provider_for_model("openrouter/tencent/hy3-preview"), PROVIDER_OPENROUTER)
        self.assertEqual(
            provider_for_model("openrouter/deepseek/deepseek-v4-flash"), PROVIDER_OPENROUTER
        )

    def test_prepare_request_builds_chat_payload_for_deepseek(self) -> None:
        settings = AppSettings(target_language_code="ko")

        payload = prepare_request("hello", settings, "deepseek/deepseek-v4-flash")

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["messages"][1]["content"], "hello")
        self.assertIn("max_tokens", payload)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("provider", payload)

    def test_openrouter_prefix_routes_deepseek_model_to_openrouter(self) -> None:
        settings = AppSettings(target_language_code="ko")

        payload = prepare_request("hello", settings, "openrouter/deepseek/deepseek-v4-flash")

        # Routed through OpenRouter's responses API, keeping "deepseek/..." as the id.
        self.assertEqual(payload["model"], "deepseek/deepseek-v4-flash")
        self.assertIn("input", payload)
        self.assertNotIn("messages", payload)

    def test_endpoint_and_key_selection_per_provider(self) -> None:
        settings = AppSettings(api_key="or-key", deepseek_api_key="ds-key")

        self.assertIn("deepseek", endpoint_for_provider(PROVIDER_DEEPSEEK))
        self.assertIn("openrouter", endpoint_for_provider(PROVIDER_OPENROUTER))
        self.assertEqual(api_key_for_provider(PROVIDER_DEEPSEEK, settings), "ds-key")
        self.assertEqual(api_key_for_provider(PROVIDER_OPENROUTER, settings), "or-key")

    def test_extract_output_text_for_deepseek_reads_choices(self) -> None:
        data = {"choices": [{"message": {"role": "assistant", "content": "안녕"}}]}

        self.assertEqual(extract_deepseek_output_text(data), "안녕")
        self.assertEqual(extract_output_text_for(PROVIDER_DEEPSEEK, data), "안녕")
        self.assertEqual(
            extract_output_text_for(PROVIDER_OPENROUTER, {"output_text": "hi"}),
            "hi",
        )

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

    def test_migrate_model_name_upgrades_legacy_ids(self) -> None:
        self.assertEqual(migrate_model_name("deepseek-v4-flash"), "deepseek/deepseek-v4-flash")
        self.assertEqual(migrate_model_name("tencent/hy3-preview"), "openrouter/tencent/hy3-preview")
        # Already-prefixed values are left untouched.
        self.assertEqual(
            migrate_model_name("openrouter/tencent/hy3-preview"),
            "openrouter/tencent/hy3-preview",
        )
        self.assertEqual(
            migrate_model_name("deepseek/deepseek-v4-pro"),
            "deepseek/deepseek-v4-pro",
        )

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
