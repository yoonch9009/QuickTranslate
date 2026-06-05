from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
from threading import Lock
from time import monotonic

import requests

from .settings import AppSettings

OPENROUTER_URL = "https://openrouter.ai/api/v1/responses"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_DEEPSEEK = "deepseek"
CONNECT_TIMEOUT_SECONDS = 3.0
SESSION = requests.Session()
LOGGER = logging.getLogger(__name__)
_CACHE_LOCK = Lock()
_CACHE_LIMIT = 128
_TRANSLATION_CACHE: OrderedDict[tuple[str, str, str], "CacheEntry"] = OrderedDict()


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


def build_instructions(target_language_code: str) -> str:
    return (
        "You are a translation engine. Detect the source language automatically and "
        f"translate the user's text into {target_language_code}. "
        "Keep the formatting, line breaks, bullet points, and code blocks. "
        "Return only the translation with no explanation."
    )


def split_model(model: str) -> tuple[str, str]:
    """Split a ``<provider>/<model>`` string into (provider, model_id).

    The first path segment selects the backend:
      - ``deepseek/deepseek-v4-flash``            -> ("deepseek", "deepseek-v4-flash")
      - ``openrouter/tencent/hy3-preview``        -> ("openrouter", "tencent/hy3-preview")
      - ``openrouter/deepseek/deepseek-v4-flash`` -> ("openrouter", "deepseek/deepseek-v4-flash")

    Anything without a recognized prefix is treated as an OpenRouter model id
    passed through unchanged (keeps older ``vendor/model`` ids working).
    """

    name = model.strip()
    prefix, slash, rest = name.partition("/")
    prefix_lower = prefix.lower()
    if slash and prefix_lower == PROVIDER_DEEPSEEK:
        return PROVIDER_DEEPSEEK, rest
    if slash and prefix_lower == PROVIDER_OPENROUTER:
        return PROVIDER_OPENROUTER, rest
    return PROVIDER_OPENROUTER, name


def provider_for_model(model: str) -> str:
    return split_model(model)[0]


def model_id_for_request(model: str) -> str:
    return split_model(model)[1]


def provider_label(provider: str) -> str:
    return "DeepSeek" if provider == PROVIDER_DEEPSEEK else "OpenRouter"


def endpoint_for_provider(provider: str) -> str:
    return DEEPSEEK_URL if provider == PROVIDER_DEEPSEEK else OPENROUTER_URL


def api_key_for_provider(provider: str, settings: AppSettings) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return settings.deepseek_api_key.strip()
    return settings.api_key.strip()


def reasoning_config_for_request(model: str, settings: AppSettings) -> dict | None:
    if model.strip() == settings.primary_model.strip():
        return settings.primary_reasoning_config
    if model.strip() == settings.fallback_model.strip():
        return settings.fallback_reasoning_config
    return None


# Generous output-token ceilings per backend. Billing is by actual tokens used,
# so a high cap only prevents truncation; it does not raise cost. OpenRouter is
# kept lower because individual models there have much smaller output limits and
# would reject an oversized request.
_OUTPUT_TOKEN_CEILING = {
    PROVIDER_DEEPSEEK: 65536,
    PROVIDER_OPENROUTER: 16384,
}
_OUTPUT_TOKEN_FLOOR = 2048


def estimate_max_output_tokens(
    source_text: str,
    provider: str = PROVIDER_OPENROUTER,
) -> int:
    text_length = len(source_text.strip())
    # ~2 chars/token is a conservative (over-)estimate of the input size; allow
    # several times that for translation expansion plus a comfortable floor, then
    # cap at the provider's safe maximum.
    approx_input_tokens = text_length // 2 + 1
    generous = approx_input_tokens * 4 + _OUTPUT_TOKEN_FLOOR
    ceiling = _OUTPUT_TOKEN_CEILING.get(provider, _OUTPUT_TOKEN_CEILING[PROVIDER_OPENROUTER])
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


def extract_deepseek_output_text(data: dict) -> str:
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        text = str(message.get("content") or "").strip()
        if text:
            return text
    return ""


def extract_output_text_for(provider: str, data: dict) -> str:
    if provider == PROVIDER_DEEPSEEK:
        return extract_deepseek_output_text(data)
    return extract_output_text(data)


def cache_key_for(source_text: str, settings: AppSettings) -> tuple[str, str, str]:
    return (
        source_text,
        settings.target_language_code,
        settings.primary_model,
    )


def load_cached_translation(
    source_text: str,
    settings: AppSettings,
) -> TranslationResult | None:
    key = cache_key_for(source_text, settings)
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
) -> None:
    key = cache_key_for(source_text, settings)

    with _CACHE_LOCK:
        _TRANSLATION_CACHE[key] = CacheEntry(result=result, stored_at=monotonic())
        _TRANSLATION_CACHE.move_to_end(key)
        while len(_TRANSLATION_CACHE) > _CACHE_LIMIT:
            _TRANSLATION_CACHE.popitem(last=False)


def build_headers(api_key: str, app_name: str, provider: str = PROVIDER_OPENROUTER) -> dict[str, str]:
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
) -> dict:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": build_instructions(settings.target_language_code),
            },
            {"role": "user", "content": source_text},
        ],
        # DeepSeek V4 enables thinking mode by default. Translation does not need
        # chain-of-thought, and leaving it on slows responses, raises cost, and
        # lets the CoT consume the tight max_tokens budget (risking empty/cut-off
        # translations). Disable it so temperature=0 also takes effect.
        "thinking": {"type": "disabled"},
        "max_tokens": estimate_max_output_tokens(source_text, PROVIDER_DEEPSEEK),
        "temperature": 0.0,
        "stream": False,
    }


def prepare_request(
    source_text: str,
    settings: AppSettings,
    model: str,
) -> dict:
    provider, model_id = split_model(model)
    if provider == PROVIDER_DEEPSEEK:
        return prepare_deepseek_request(source_text, settings, model_id)

    payload = {
        "model": model_id,
        "input": source_text,
        "instructions": build_instructions(settings.target_language_code),
        "text": {"format": {"type": "text"}},
        "max_output_tokens": estimate_max_output_tokens(source_text, PROVIDER_OPENROUTER),
        # No "sort"/"order" here: setting either disables OpenRouter's default
        # price-based load balancing. We keep the default strategy and only enable
        # automatic fallback to other providers on failure.
        "provider": {
            "allow_fallbacks": True,
        },
        "store": False,
        "temperature": 0.0,
    }
    reasoning = reasoning_config_for_request(model, settings)
    if reasoning:
        payload["reasoning"] = reasoning
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
    """Scale the read timeout with input length.

    Generation time grows with the amount of text, so a fixed short timeout
    truncates long translations. Use the configured timeout as a floor and add
    more time proportional to the input, capped at a generous ceiling.
    """

    base = max(5, int(settings.request_timeout_seconds))
    text_length = len(source_text.strip())
    dynamic = base + text_length // 10  # +1s per ~10 characters
    return float(min(max(base, dynamic), 600))


def send_request(
    payload: dict,
    headers: dict[str, str],
    settings: AppSettings,
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
        raise RuntimeError(failure_from_status(status_code, detail, model, label)) from exc

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


def should_retry_with_fallback(
    failure: RequestFailure,
    settings: AppSettings,
) -> bool:
    if not settings.fallback_on_provider_error_only:
        return True
    return failure.retryable


def log_failure(model: str, failure: RequestFailure) -> None:
    LOGGER.warning("Translation request failed for %s: %s", model, failure.log_message)


def request_translation(
    source_text: str,
    settings: AppSettings,
    *,
    app_name: str = "QuickTranslate",
) -> TranslationResult:
    source_text = source_text.strip()
    if not source_text:
        raise TranslationError("번역할 텍스트가 비어 있습니다.")

    cached = load_cached_translation(source_text, settings)
    if cached is not None:
        return cached

    models = [settings.primary_model.strip()]
    fallback_model = settings.fallback_model.strip()
    if fallback_model and fallback_model != models[0]:
        models.append(fallback_model)

    last_failure: RequestFailure | None = None
    read_timeout = read_timeout_for(source_text, settings)
    for index, model in enumerate(models):
        provider = provider_for_model(model)
        label = provider_label(provider)
        api_key = api_key_for_provider(provider, settings)
        if not api_key:
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

        headers = build_headers(api_key, app_name, provider)
        payload = prepare_request(source_text, settings, model)

        try:
            response_data = send_request(
                payload,
                headers,
                settings,
                url=endpoint_for_provider(provider),
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
            store_cached_translation(source_text, settings, result)
            return result
        except RuntimeError as exc:
            failure = exc.args[0]
            if not isinstance(failure, RequestFailure):
                raise TranslationError("번역 요청 중 오류가 발생했습니다.") from exc

            log_failure(model, failure)
            last_failure = failure
            if index == 0 and len(models) > 1 and should_retry_with_fallback(failure, settings):
                continue
            break

    if last_failure is not None:
        raise TranslationError(last_failure.user_message)
    raise TranslationError("사용 가능한 번역 모델이 없습니다.")
