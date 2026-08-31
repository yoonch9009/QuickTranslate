from __future__ import annotations

import unittest
from unittest.mock import patch

from quicktranslate.model_catalog import EffectiveReasoning, ParameterSupport
from quicktranslate.settings import (
    PARAMETER_MODE_MANUAL,
    REASONING_MODE_MANUAL,
    AppSettings,
)
from quicktranslate.translator import (
    RequestFailure,
    TranslationResult,
    estimate_max_output_tokens,
    extract_output_text,
    load_cached_translation,
    parse_openrouter_stream_lines,
    prepare_request,
    request_translation,
    should_retry_with_fallback,
    split_model,
    store_cached_translation,
)


class TranslatorTests(unittest.TestCase):
    def test_prepare_request_passes_reasoning_config_by_model(self) -> None:
        settings = AppSettings(
            primary_model="openrouter/z-ai/glm-5.3-flash",
            fallback_model="openrouter/tencent/hy3-preview",
            primary_reasoning_config={"effort": "high", "exclude": True},
            fallback_reasoning_config={"max_tokens": 2048, "enabled": True},
            primary_reasoning_mode=REASONING_MODE_MANUAL,
            fallback_reasoning_mode=REASONING_MODE_MANUAL,
        )

        qwen_payload = prepare_request("hello", settings, settings.primary_model)
        gemma_payload = prepare_request("hello", settings, settings.fallback_model)

        self.assertEqual(qwen_payload["reasoning"]["effort"], "high")
        self.assertTrue(qwen_payload["reasoning"]["exclude"])
        self.assertEqual(gemma_payload["reasoning"]["max_tokens"], 2048)
        self.assertTrue(gemma_payload["reasoning"]["enabled"])
        self.assertEqual(qwen_payload["text"]["format"]["type"], "text")
        self.assertEqual(qwen_payload["model"], "z-ai/glm-5.3-flash")

    def test_prepare_request_uses_model_specific_sampling_settings(self) -> None:
        settings = AppSettings(
            primary_parameter_mode=PARAMETER_MODE_MANUAL,
            primary_temperature=1.0,
            primary_top_p=0.95,
            fallback_model="deepseek/deepseek-v4-flash-vision-exp",
            fallback_parameter_mode=PARAMETER_MODE_MANUAL,
            fallback_temperature=0.4,
            fallback_top_p=0.8,
        )

        primary = prepare_request("hello", settings, settings.primary_model)
        fallback = prepare_request("hello", settings, settings.fallback_model)

        self.assertEqual(primary["temperature"], 1.0)
        self.assertEqual(primary["top_p"], 0.95)
        self.assertEqual(fallback["temperature"], 0.4)
        self.assertEqual(fallback["top_p"], 0.8)

    @patch("quicktranslate.translator.MODEL_CATALOG.supported_parameters_for")
    @patch("quicktranslate.translator.MODEL_CATALOG.reasoning_for")
    def test_qwen_auto_profile_filters_unsupported_parameters(
        self,
        reasoning_for,
        supported_parameters_for,
    ) -> None:
        reasoning_for.return_value = EffectiveReasoning(
            {"effort": "none"},
            "자동 → none",
            True,
        )
        supported_parameters_for.return_value = ParameterSupport(
            frozenset(
                {"temperature", "top_p", "top_k", "presence_penalty", "reasoning"}
            ),
            True,
        )
        settings = AppSettings(primary_model="qwen/qwen3.8-flash")

        payload = prepare_request("hello", settings, settings.primary_model)

        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["top_p"], 0.8)
        self.assertEqual(payload["top_k"], 20)
        self.assertEqual(payload["presence_penalty"], 1.5)
        self.assertNotIn("min_p", payload)
        self.assertNotIn("repetition_penalty", payload)

    def test_prepare_request_builds_openrouter_vision_message(self) -> None:
        settings = AppSettings()
        image_data_url = "data:image/jpeg;base64,YWJj"

        payload = prepare_request(
            "",
            settings,
            settings.primary_model,
            image_data_url,
        )

        content = payload["messages"][1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("visible text", content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], image_data_url)
        self.assertEqual(payload["max_tokens"], 8192)

    def test_prepare_request_builds_deepseek_vision_message(self) -> None:
        settings = AppSettings()
        image_data_url = "data:image/jpeg;base64,YWJj"

        payload = prepare_request(
            "",
            settings,
            settings.fallback_model,
            image_data_url,
        )

        self.assertEqual(payload["model"], "deepseek-v4-flash-vision-exp")
        self.assertEqual(
            payload["messages"][1]["content"][1]["image_url"]["url"],
            image_data_url,
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_prepare_request_omits_reasoning_when_not_configured(self) -> None:
        settings = AppSettings(
            primary_model="openrouter/z-ai/glm-5.3-flash",
            primary_reasoning_config=None,
            fallback_reasoning_config=None,
            primary_reasoning_mode=REASONING_MODE_MANUAL,
            fallback_reasoning_mode=REASONING_MODE_MANUAL,
        )

        payload = prepare_request("hello", settings, settings.primary_model)

        self.assertNotIn("reasoning", payload)

    def test_estimate_max_output_tokens_prefers_lower_limits_for_short_text(
        self,
    ) -> None:
        self.assertEqual(estimate_max_output_tokens("hello"), 2060)
        self.assertLessEqual(estimate_max_output_tokens("a" * 100_000), 16_384)

    def test_split_model_removes_supported_provider_prefix(self) -> None:
        self.assertEqual(
            split_model("openrouter/z-ai/glm-5.3-flash"),
            ("openrouter", "z-ai/glm-5.3-flash"),
        )
        self.assertEqual(
            split_model("deepseek/deepseek-v4-flash"),
            ("deepseek", "deepseek-v4-flash"),
        )

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

    def test_image_cache_uses_image_content_identity(self) -> None:
        settings = AppSettings(cache_ttl_seconds=300)
        result = TranslationResult(text="이미지 번역", model=settings.primary_model)
        image_data_url = "data:image/jpeg;base64,YWJj"

        store_cached_translation("", settings, result, image_data_url)

        self.assertIsNotNone(load_cached_translation("", settings, image_data_url))
        self.assertIsNone(
            load_cached_translation("", settings, "data:image/jpeg;base64,ZGVm")
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

    def test_primary_rate_limit_falls_back_without_retry(self) -> None:
        settings = AppSettings(
            api_key="test-key",
            primary_model="qwen/qwen3.8-flash",
            fallback_model="openrouter/z-ai/glm-5.3-flash",
        )
        rate_limit = RuntimeError(
            RequestFailure(
                user_message="retry",
                log_message="rate limited",
                retryable=True,
                status_code=429,
            )
        )
        support = ParameterSupport(
            frozenset({"temperature", "top_p", "top_k", "presence_penalty"}),
            True,
        )
        with (
            patch("quicktranslate.translator.MODEL_CATALOG.ensure_model"),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.reasoning_for",
                return_value=EffectiveReasoning({"effort": "none"}, "none", True),
            ),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.supported_parameters_for",
                return_value=support,
            ),
            patch(
                "quicktranslate.translator.send_request",
                side_effect=[rate_limit, {"output_text": "폴백"}],
            ) as send,
        ):
            result = request_translation("no-retry-unique", settings)

        self.assertEqual(result.model, settings.fallback_model)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            [call.args[0]["model"] for call in send.call_args_list],
            ["qwen/qwen3.8-flash", "z-ai/glm-5.3-flash"],
        )

    def test_stream_rate_limit_falls_back_without_non_streaming_retry(self) -> None:
        settings = AppSettings(
            api_key="test-key",
            primary_model="qwen/qwen3.8-flash",
            fallback_model="openrouter/z-ai/glm-5.3-flash",
        )
        rate_limit = RuntimeError(
            RequestFailure("retry", "rate limited", True, status_code=429)
        )
        with (
            patch("quicktranslate.translator.MODEL_CATALOG.ensure_model"),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.reasoning_for",
                return_value=EffectiveReasoning({"effort": "none"}, "none", True),
            ),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.supported_parameters_for",
                return_value=ParameterSupport(frozenset(), True),
            ),
            patch(
                "quicktranslate.translator.send_streaming_request",
                side_effect=[rate_limit, {"output_text": "폴백"}],
            ) as streaming,
            patch(
                "quicktranslate.translator.send_request",
                return_value={"output_text": "호출되면 안 됨"},
            ) as non_streaming,
        ):
            result = request_translation(
                "stream-no-retry-unique",
                settings,
                on_delta=lambda _delta: None,
            )

        self.assertEqual(result.model, settings.fallback_model)
        self.assertEqual(streaming.call_count, 2)
        self.assertEqual(non_streaming.call_count, 0)

    def test_fallback_result_is_not_cached_as_primary_result(self) -> None:
        settings = AppSettings(
            api_key="test-key",
            primary_model="qwen/qwen3.8-flash",
            fallback_model="openrouter/z-ai/glm-5.3-flash",
        )
        rate_limit = RuntimeError(
            RequestFailure("retry", "rate limited", True, status_code=429)
        )
        with (
            patch("quicktranslate.translator.MODEL_CATALOG.ensure_model"),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.reasoning_for",
                return_value=EffectiveReasoning({"effort": "none"}, "none", True),
            ),
            patch(
                "quicktranslate.translator.MODEL_CATALOG.supported_parameters_for",
                return_value=ParameterSupport(frozenset(), True),
            ),
            patch(
                "quicktranslate.translator.send_request",
                side_effect=[rate_limit, {"output_text": "폴백"}],
            ),
        ):
            result = request_translation("fallback-cache-unique", settings)

        self.assertEqual(result.model, settings.fallback_model)
        self.assertIsNone(load_cached_translation("fallback-cache-unique", settings))

    def test_responses_stream_collects_and_emits_only_text_deltas(self) -> None:
        emitted: list[str] = []
        lines = [
            "event: response.output_text.delta",
            'data: {"type":"response.output_text.delta","delta":"안녕"}',
            'data: {"type":"response.reasoning.delta","delta":"ignored"}',
            'data: {"type":"response.output_text.delta","delta":"하세요"}',
            "data: [DONE]",
        ]

        text = parse_openrouter_stream_lines(lines, emitted.append)

        self.assertEqual(text, "안녕하세요")
        self.assertEqual(emitted, ["안녕", "하세요"])


if __name__ == "__main__":
    unittest.main()
