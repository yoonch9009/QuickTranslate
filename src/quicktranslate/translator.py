from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import monotonic

import requests

from .codex_client import CodexProviderError, request_codex_translation
from .model_catalog import MODEL_CATALOG, EffectiveReasoning
from .model_profiles import recommended_parameters_for
from .settings import PARAMETER_MODE_AUTO, REASONING_MODE_AUTO, AppSettings

OPENROUTER_URL = "https://openrouter.ai/api/v1/responses"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_CODEX = "codex"
CONNECT_TIMEOUT_SECONDS = 3.0
SESSION = requests.Session()
LOGGER = logging.getLogger(__name__)
_CACHE_LOCK = Lock()
_CACHE_LIMIT = 128
_TRANSLATION_CACHE: OrderedDict[tuple[str, ...], CacheEntry] = OrderedDict()
_TARGET_LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
}


class TranslationError(RuntimeError):
    pass


@dataclass
class TranslationResult:
    text: str
    model: str


@dataclass
class CacheEntry:
    result: TranslationResult
    stored_at: float


@dataclass
class RequestFailure:
    user_message: str
    log_message: str
    retryable: bool
    status_code: int | None = None


@dataclass(frozen=True)
class EffectiveParameters:
    values: dict[str, object]
    omitted: frozenset[str]
    summary: str
    metadata_known: bool


def build_instructions(target_language_code: str) -> str:
    target_language = _TARGET_LANGUAGE_NAMES.get(
        target_language_code,
        target_language_code,
    )
    return (
        "You are a translation engine. Detect the source language automatically and "
        f"translate the user's text into {target_language}. "
        "Keep the formatting, line breaks, bullet points, and code blocks. "
        "Return only the translation with no explanation."
    )


def build_image_request_text(target_language_code: str, source_text: str = "") -> str:
    target_language = _TARGET_LANGUAGE_NAMES.get(
        target_language_code,
        target_language_code,
    )
    instruction = (
        "Read every piece of visible text in the image and translate it into "
        f"{target_language}. Do not merely transcribe the source text: translate it. "
        "Preserve the reading order, line breaks, headings, lists, labels, and table "
        "structure as closely as possible. Return only the translated text with no "
        "explanation."
    )
    if source_text.strip():
        instruction += f"\n\nAdditional clipboard context:\n{source_text.strip()}"
    return instruction


def split_model(model: str) -> tuple[str, str]:
    """Split an optional provider prefix from a model ID."""
    name = model.strip()
    prefix, slash, rest = name.partition("/")
    prefix_lower = prefix.lower()
    if slash and prefix_lower == PROVIDER_DEEPSEEK:
        return PROVIDER_DEEPSEEK, rest
    if slash and prefix_lower == PROVIDER_OPENROUTER:
        return PROVIDER_OPENROUTER, rest
    if slash and prefix_lower == PROVIDER_CODEX:
        return PROVIDER_CODEX, rest
    return PROVIDER_OPENROUTER, name


def provider_for_model(model: str) -> str:
    return split_model(model)[0]


def model_id_for_request(model: str) -> str:
    return split_model(model)[1]


def provider_label(provider: str) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return "DeepSeek"
    if provider == PROVIDER_CODEX:
        return "Codex 구독"
    return "OpenRouter"


def endpoint_for_provider(provider: str, *, image_request: bool = False) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return DEEPSEEK_URL
    if image_request:
        return OPENROUTER_CHAT_URL
    return OPENROUTER_URL


def api_key_for_provider(provider: str, settings: AppSettings) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return settings.deepseek_api_key.strip()
    return settings.api_key.strip()


def reasoning_settings_for_model(
    model: str,
    settings: AppSettings,
) -> tuple[str, dict | None]:
    if model.strip() == settings.primary_model.strip():
        return settings.primary_reasoning_mode, settings.primary_reasoning_config
    if model.strip() == settings.fallback_model.strip():
        return settings.fallback_reasoning_mode, settings.fallback_reasoning_config
    return REASONING_MODE_AUTO, None


def parameter_settings_for_model(
    model: str,
    settings: AppSettings,
) -> tuple[str, float, float, dict[str, object]]:
    if model.strip() == settings.fallback_model.strip():
        return (
            settings.fallback_parameter_mode,
            settings.fallback_temperature,
            settings.fallback_top_p,
            dict(settings.fallback_extra_parameters),
        )
    return (
        settings.primary_parameter_mode,
        settings.primary_temperature,
        settings.primary_top_p,
        dict(settings.primary_extra_parameters),
    )


def effective_reasoning_for_request(
    model: str,
    settings: AppSettings,
    *,
    ensure_metadata: bool = False,
) -> EffectiveReasoning:
    mode, manual_config = reasoning_settings_for_model(model, settings)
    if mode != REASONING_MODE_AUTO:
        config = dict(manual_config) if isinstance(manual_config, dict) else None
        summary = "수동" if config else "수동 → 비움"
        return EffectiveReasoning(config, summary, True)

    provider, _ = split_model(model)
    if provider == PROVIDER_CODEX:
        return EffectiveReasoning({"effort": "max"}, "자동 → max", True)
    if provider == PROVIDER_DEEPSEEK:
        return EffectiveReasoning({"effort": "none"}, "자동 → thinking 끄기", True)

    if ensure_metadata:
        MODEL_CATALOG.ensure_model(model)
    return MODEL_CATALOG.reasoning_for(model)


_DIRECT_DEEPSEEK_PARAMETERS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
)


def effective_parameters_for_request(
    model: str,
    settings: AppSettings,
    *,
    ensure_metadata: bool = False,
) -> EffectiveParameters:
    mode, temperature, top_p, extra = parameter_settings_for_model(model, settings)
    reasoning = effective_reasoning_for_request(
        model,
        settings,
        ensure_metadata=ensure_metadata,
    )
    if mode == PARAMETER_MODE_AUTO:
        recommended = recommended_parameters_for(model, reasoning.config)
        requested = dict(recommended.values)
        label = recommended.label
    else:
        requested = {"temperature": temperature, "top_p": top_p, **extra}
        label = "수동 파라미터"

    provider = provider_for_model(model)
    if provider == PROVIDER_CODEX:
        omitted = frozenset(requested)
        return EffectiveParameters(
            {},
            omitted,
            "Codex 구독 → reasoning만 적용",
            True,
        )
    if provider == PROVIDER_DEEPSEEK:
        supported = _DIRECT_DEEPSEEK_PARAMETERS
        metadata_known = True
    else:
        if ensure_metadata:
            MODEL_CATALOG.ensure_model(model)
        support = MODEL_CATALOG.supported_parameters_for(model)
        supported = support.supported
        metadata_known = support.metadata_known

    if metadata_known:
        values = {key: value for key, value in requested.items() if key in supported}
        omitted = frozenset(set(requested) - set(values))
    elif mode == PARAMETER_MODE_AUTO:
        values = {}
        omitted = frozenset(requested)
    else:
        values = requested
        omitted = frozenset()

    summary = label
    if omitted:
        summary += f" (미지원 제외: {', '.join(sorted(omitted))})"
    return EffectiveParameters(values, omitted, summary, metadata_known)


_OUTPUT_TOKEN_CEILING = {
    PROVIDER_OPENROUTER: 16_384,
    PROVIDER_DEEPSEEK: 65_536,
    PROVIDER_CODEX: 128_000,
}
_OUTPUT_TOKEN_FLOOR = 2_048


def estimate_max_output_tokens(
    source_text: str,
    provider: str = PROVIDER_OPENROUTER,
) -> int:
    text_length = len(source_text.strip())
    approx_input_tokens = text_length // 2 + 1
    generous = approx_input_tokens * 4 + _OUTPUT_TOKEN_FLOOR
    ceiling = _OUTPUT_TOKEN_CEILING.get(
        provider,
        _OUTPUT_TOKEN_CEILING[PROVIDER_OPENROUTER],
    )
    return min(generous, ceiling)


def extract_output_text(data: dict) -> str:
    output_text = str(data.get("output_text") or "").strip()
    if output_text:
        return output_text

    output = data.get("output") or []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                text = str(content.get("text") or "").strip()
                if text:
                    return text

    return ""


def extract_chat_output_text(data: dict) -> str:
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        text = str(message.get("content") or "").strip()
        if text:
            return text
    return ""


def extract_output_text_for(provider: str, data: dict) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return extract_chat_output_text(data)
    return extract_output_text(data) or extract_chat_output_text(data)


def cache_key_for(
    source_text: str,
    settings: AppSettings,
    image_data_url: str | None = None,
) -> tuple[str, ...]:
    effective = effective_reasoning_for_request(settings.primary_model, settings)
    reasoning_key = json.dumps(
        effective.config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parameters = effective_parameters_for_request(settings.primary_model, settings)
    parameter_key = json.dumps(
        parameters.values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_identity = source_text
    if image_data_url:
        digest = sha256(image_data_url.encode("ascii")).hexdigest()
        source_identity = f"image:{digest}:{source_text}"
    return (
        source_identity,
        settings.target_language_code,
        settings.primary_model,
        settings.primary_reasoning_mode,
        reasoning_key,
        settings.primary_parameter_mode,
        parameter_key,
    )


def load_cached_translation(
    source_text: str,
    settings: AppSettings,
    image_data_url: str | None = None,
) -> TranslationResult | None:
    key = cache_key_for(source_text, settings, image_data_url)
    now = monotonic()
    ttl = max(1, settings.cache_ttl_seconds)

    with _CACHE_LOCK:
        expired_keys = [
            cache_key
            for cache_key, entry in _TRANSLATION_CACHE.items()
            if now - entry.stored_at > ttl
        ]
        for expired_key in expired_keys:
            _TRANSLATION_CACHE.pop(expired_key, None)

        entry = _TRANSLATION_CACHE.get(key)
        if entry is None:
            return None

        _TRANSLATION_CACHE.move_to_end(key)
        return entry.result


def store_cached_translation(
    source_text: str,
    settings: AppSettings,
    result: TranslationResult,
    image_data_url: str | None = None,
) -> None:
    key = cache_key_for(source_text, settings, image_data_url)

    with _CACHE_LOCK:
        _TRANSLATION_CACHE[key] = CacheEntry(result=result, stored_at=monotonic())
        _TRANSLATION_CACHE.move_to_end(key)
        while len(_TRANSLATION_CACHE) > _CACHE_LIMIT:
            _TRANSLATION_CACHE.popitem(last=False)


def build_headers(
    api_key: str,
    app_name: str,
    provider: str = PROVIDER_OPENROUTER,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    if provider == PROVIDER_OPENROUTER:
        headers["HTTP-Referer"] = "https://localhost/quicktranslate"
        headers["X-Title"] = app_name
    return headers


def prepare_deepseek_request(
    source_text: str,
    settings: AppSettings,
    model: str,
    image_data_url: str | None = None,
) -> dict:
    full_model = f"{PROVIDER_DEEPSEEK}/{model}"
    parameters = effective_parameters_for_request(full_model, settings)
    user_content: str | list[dict]
    if image_data_url:
        user_content = [
            {
                "type": "text",
                "text": build_image_request_text(
                    settings.target_language_code,
                    source_text,
                ),
            },
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        user_content = source_text
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": build_instructions(settings.target_language_code),
            },
            {"role": "user", "content": user_content},
        ],
        "max_tokens": (
            8192
            if image_data_url
            else estimate_max_output_tokens(source_text, PROVIDER_DEEPSEEK)
        ),
        "stream": False,
    }
    payload.update(parameters.values)
    effective = effective_reasoning_for_request(
        full_model,
        settings,
    )
    reasoning = effective.config or {}
    if reasoning.get("effort") == "none" or reasoning.get("enabled") is False:
        payload["thinking"] = {"type": "disabled"}
    elif isinstance(reasoning.get("thinking"), dict):
        payload["thinking"] = reasoning["thinking"]
    elif reasoning:
        payload["thinking"] = {"type": "enabled"}
    return payload


def prepare_request(
    source_text: str,
    settings: AppSettings,
    model: str,
    image_data_url: str | None = None,
) -> dict:
    provider, model_id = split_model(model)
    if provider == PROVIDER_DEEPSEEK:
        return prepare_deepseek_request(
            source_text,
            settings,
            model_id,
            image_data_url,
        )

    parameters = effective_parameters_for_request(model, settings)
    effective = effective_reasoning_for_request(model, settings)
    if image_data_url:
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": build_instructions(settings.target_language_code),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": build_image_request_text(
                                settings.target_language_code,
                                source_text,
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                },
            ],
            "max_tokens": 8192,
            "provider": {"allow_fallbacks": True},
        }
        payload.update(parameters.values)
        if effective.config:
            payload["reasoning"] = effective.config
        return payload

    payload = {
        "model": model_id,
        "input": source_text,
        "instructions": build_instructions(settings.target_language_code),
        "text": {"format": {"type": "text"}},
        "max_output_tokens": estimate_max_output_tokens(
            source_text,
            PROVIDER_OPENROUTER,
        ),
        "provider": {
            "allow_fallbacks": True,
        },
        "store": False,
    }
    payload.update(parameters.values)
    if effective.config:
        payload["reasoning"] = effective.config
    return payload


def failure_from_status(
    status_code: int,
    detail: str,
    model: str,
    label: str = "OpenRouter",
) -> RequestFailure:
    if status_code in {401, 403}:
        user_message = f"{label} API Key를 확인해 주세요."
    elif status_code == 402:
        user_message = f"{label} 크레딧이 부족합니다."
    elif status_code == 404:
        user_message = "선택한 번역 모델을 찾을 수 없습니다."
    elif status_code == 422:
        user_message = "번역 요청 형식이 올바르지 않습니다."
    elif status_code == 429:
        if "temporarily rate-limited upstream" in detail:
            user_message = "선택한 모델 공급자가 일시적으로 사용량을 제한했습니다."
        else:
            user_message = "요청이 많아 잠시 후 다시 시도해 주세요."
    elif 500 <= status_code < 600:
        user_message = "번역 서버 오류가 발생했습니다."
    else:
        user_message = "번역 요청을 처리하지 못했습니다."

    retryable = status_code == 429 or 500 <= status_code < 600
    return RequestFailure(
        user_message=user_message,
        log_message=f"{model} HTTP {status_code}: {detail}",
        retryable=retryable,
        status_code=status_code,
    )


def read_timeout_for(source_text: str, settings: AppSettings) -> float:
    base = max(5, int(settings.request_timeout_seconds))
    text_length = len(source_text.strip())
    dynamic = base + text_length // 10
    return float(min(max(base, dynamic), 600))


def send_request(
    payload: dict,
    headers: dict[str, str],
    settings: AppSettings,
    *,
    url: str = OPENROUTER_URL,
    label: str = "OpenRouter",
    read_timeout: float | None = None,
) -> dict:
    model = payload["model"]
    effective_read_timeout = read_timeout or settings.request_timeout_seconds

    try:
        response = SESSION.post(
            url,
            headers=headers,
            json=payload,
            timeout=(CONNECT_TIMEOUT_SECONDS, effective_read_timeout),
        )
    except requests.Timeout as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="번역 서버 응답이 지연되고 있습니다.",
                log_message=f"{model} timeout: {exc}",
                retryable=True,
            )
        ) from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="번역 서버에 연결할 수 없습니다.",
                log_message=f"{model} connection error: {exc}",
                retryable=True,
            )
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="네트워크 오류가 발생했습니다.",
                log_message=f"{model} request error: {exc}",
                retryable=True,
            )
        ) from exc

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        status_code = exc.response.status_code if exc.response is not None else 0
        raise RuntimeError(
            failure_from_status(status_code, detail, model, label)
        ) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="번역 응답을 처리하지 못했습니다.",
                log_message=f"{model} invalid JSON response: {exc}",
                retryable=False,
            )
        ) from exc


def send_streaming_request(
    payload: dict,
    headers: dict[str, str],
    settings: AppSettings,
    on_delta: Callable[[str], None],
    *,
    url: str = OPENROUTER_URL,
    label: str = "OpenRouter",
    read_timeout: float | None = None,
) -> dict:
    model = payload["model"]
    effective_read_timeout = read_timeout or settings.request_timeout_seconds
    streaming_payload = dict(payload)
    streaming_payload["stream"] = True
    try:
        response = SESSION.post(
            url,
            headers=headers,
            json=streaming_payload,
            timeout=(CONNECT_TIMEOUT_SECONDS, effective_read_timeout),
            stream=True,
        )
    except requests.Timeout as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="번역 서버 응답이 지연되고 있습니다.",
                log_message=f"{model} timeout: {exc}",
                retryable=True,
            )
        ) from exc
    except requests.ConnectionError as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="번역 서버에 연결할 수 없습니다.",
                log_message=f"{model} connection error: {exc}",
                retryable=True,
            )
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            RequestFailure(
                user_message="네트워크 오류가 발생했습니다.",
                log_message=f"{model} request error: {exc}",
                retryable=True,
            )
        ) from exc

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text
        status_code = response.status_code
        response.close()
        raise RuntimeError(
            failure_from_status(status_code, detail, model, label)
        ) from exc

    response.encoding = "utf-8"
    try:
        output_text = parse_openrouter_stream_lines(
            response.iter_lines(decode_unicode=True),
            on_delta,
        )
    finally:
        response.close()
    return {"output_text": output_text}


def parse_openrouter_stream_lines(
    lines: Iterable[str],
    on_delta: Callable[[str], None],
) -> str:
    chunks: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        if data_text == "[DONE]":
            break
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError:
            LOGGER.warning("Ignoring malformed OpenRouter stream event")
            continue
        if not isinstance(event, dict):
            continue

        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = str(event.get("delta") or "")
            if delta:
                chunks.append(delta)
                on_delta(delta)
        elif event_type in {"error", "response.failed"}:
            detail = event.get("error") or event
            raise RuntimeError(
                RequestFailure(
                    user_message="번역 스트리밍 중 오류가 발생했습니다.",
                    log_message=f"stream error: {detail}",
                    retryable=True,
                )
            )
    return "".join(chunks).strip()


def should_retry_with_fallback(
    failure: RequestFailure,
    settings: AppSettings,
    *,
    image_request: bool = False,
) -> bool:
    if image_request:
        return True
    if not settings.fallback_on_provider_error_only:
        return True
    return failure.retryable


def log_failure(model: str, failure: RequestFailure) -> None:
    LOGGER.warning("Translation request failed for %s: %s", model, failure.log_message)


def request_translation(
    source_text: str,
    settings: AppSettings,
    *,
    image_data_url: str | None = None,
    app_name: str = "QuickTranslate",
    on_delta: Callable[[str], None] | None = None,
    only_model: str | None = None,
) -> TranslationResult:
    source_text = source_text.strip()
    if not source_text and not image_data_url:
        raise TranslationError("번역할 텍스트나 이미지가 비어 있습니다.")

    if only_model is not None:
        requested_model = only_model.strip()
        if not requested_model:
            raise TranslationError("비교할 폴백 모델이 설정되지 않았습니다.")
        models = [requested_model]
    else:
        models = [settings.primary_model.strip()]
        fallback_model = settings.fallback_model.strip()
        if fallback_model and fallback_model != models[0]:
            models.append(fallback_model)

    for model in models:
        if provider_for_model(model) == PROVIDER_OPENROUTER:
            MODEL_CATALOG.ensure_model(model)

    if only_model is None:
        cached = load_cached_translation(source_text, settings, image_data_url)
        if cached is not None:
            return cached

    last_failure: RequestFailure | None = None
    read_timeout = read_timeout_for(source_text, settings)
    if image_data_url:
        read_timeout = max(read_timeout, 90.0)
    for index, model in enumerate(models):
        provider = provider_for_model(model)
        label = provider_label(provider)
        api_key = "" if provider == PROVIDER_CODEX else api_key_for_provider(
            provider, settings
        )
        if provider != PROVIDER_CODEX and not api_key:
            failure = RequestFailure(
                user_message=f"{label} API Key가 설정되지 않았습니다.",
                log_message=f"{model} missing {label} API key",
                retryable=False,
            )
            log_failure(model, failure)
            last_failure = failure
            if index == 0 and len(models) > 1:
                continue
            break

        effective = effective_reasoning_for_request(model, settings)
        parameters = effective_parameters_for_request(model, settings)
        LOGGER.info(
            "Translation request: provider=%s model=%s media=%s reasoning=%s "
            "parameters=%s",
            provider,
            model,
            "image" if image_data_url else "text",
            effective.summary,
            parameters.summary,
        )

        try:
            if provider == PROVIDER_CODEX:
                effort = str((effective.config or {}).get("effort") or "max")
                codex_timeout = read_timeout
                if effort == "max":
                    codex_timeout = max(codex_timeout, 180.0)
                elif effort == "xhigh":
                    codex_timeout = max(codex_timeout, 120.0)
                else:
                    codex_timeout = max(codex_timeout, 60.0)
                try:
                    translated_text = request_codex_translation(
                        source_text,
                        image_data_url=image_data_url,
                        model=model_id_for_request(model),
                        effort=effort,
                        instructions=(
                            build_image_request_text(settings.target_language_code)
                            if image_data_url
                            else build_instructions(settings.target_language_code)
                        ),
                        timeout=codex_timeout,
                        on_delta=on_delta,
                    )
                except CodexProviderError as exc:
                    raise RuntimeError(
                        RequestFailure(
                            user_message=exc.user_message,
                            log_message=exc.detail,
                            retryable=exc.retryable,
                        )
                    ) from exc
            else:
                headers = build_headers(api_key, app_name, provider)
                payload = prepare_request(source_text, settings, model, image_data_url)
                if (
                    provider == PROVIDER_OPENROUTER
                    and on_delta is not None
                    and not image_data_url
                ):
                    response_data = send_streaming_request(
                        payload,
                        headers,
                        settings,
                        on_delta,
                        url=endpoint_for_provider(provider),
                        label=label,
                        read_timeout=read_timeout,
                    )
                else:
                    response_data = send_request(
                        payload,
                        headers,
                        settings,
                        url=endpoint_for_provider(
                            provider,
                            image_request=image_data_url is not None,
                        ),
                        label=label,
                        read_timeout=read_timeout,
                    )
                translated_text = extract_output_text_for(provider, response_data)
            if not translated_text:
                failure = RequestFailure(
                    user_message="번역 응답이 비어 있습니다.",
                    log_message=f"{model} empty output",
                    retryable=False,
                )
                log_failure(model, failure)
                raise TranslationError(failure.user_message)

            result = TranslationResult(text=translated_text, model=model)
            if only_model is None and model == settings.primary_model.strip():
                store_cached_translation(source_text, settings, result, image_data_url)
            return result
        except RuntimeError as exc:
            failure = exc.args[0]
            if not isinstance(failure, RequestFailure):
                raise TranslationError("번역 요청 중 오류가 발생했습니다.") from exc

            log_failure(model, failure)
            last_failure = failure
            if (
                index == 0
                and len(models) > 1
                and should_retry_with_fallback(
                    failure,
                    settings,
                    image_request=image_data_url is not None,
                )
            ):
                continue
            break

    if last_failure is not None:
        raise TranslationError(last_failure.user_message)
    raise TranslationError("사용 가능한 번역 모델이 없습니다.")
