from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
from threading import Lock
from time import monotonic

import requests

from .settings import AppSettings

OPENROUTER_URL = "https://openrouter.ai/api/v1/responses"
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


def reasoning_config_for_request(model: str, settings: AppSettings) -> dict | None:
    if model.strip() == settings.primary_model.strip():
        return settings.primary_reasoning_config
    if model.strip() == settings.fallback_model.strip():
        return settings.fallback_reasoning_config
    return None


def estimate_max_output_tokens(source_text: str) -> int:
    text_length = len(source_text.strip())
    if text_length <= 80:
        return 120
    if text_length <= 240:
        return 220
    if text_length <= 800:
        return min(700, max(260, int(text_length * 0.9)))
    return min(1800, max(700, int(text_length * 0.75)))


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


def build_headers(api_key: str, app_name: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/quicktranslate",
        "X-Title": app_name,
    }


def prepare_request(
    source_text: str,
    settings: AppSettings,
    model: str,
) -> dict:
    payload = {
        "model": model,
        "input": source_text,
        "instructions": build_instructions(settings.target_language_code),
        "text": {"format": {"type": "text"}},
        "max_output_tokens": estimate_max_output_tokens(source_text),
        "provider": {
            "allow_fallbacks": True,
            "sort": "latency",
        },
        "store": False,
        "temperature": 0.0,
    }
    reasoning = reasoning_config_for_request(model, settings)
    if reasoning:
        payload["reasoning"] = reasoning
    return payload


def failure_from_status(status_code: int, detail: str, model: str) -> RequestFailure:
    if status_code in {401, 403}:
        user_message = "OpenRouter API Key를 확인해 주세요."
    elif status_code == 402:
        user_message = "OpenRouter 크레딧이 부족합니다."
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


def send_request(
    payload: dict,
    headers: dict[str, str],
    settings: AppSettings,
) -> dict:
    model = payload["model"]

    try:
        response = SESSION.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=(CONNECT_TIMEOUT_SECONDS, settings.request_timeout_seconds),
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
        raise RuntimeError(failure_from_status(status_code, detail, model)) from exc

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
    if not settings.api_key.strip():
        raise TranslationError("OpenRouter API Key가 설정되지 않았습니다.")

    source_text = source_text.strip()
    if not source_text:
        raise TranslationError("번역할 텍스트가 비어 있습니다.")

    cached = load_cached_translation(source_text, settings)
    if cached is not None:
        return cached

    headers = build_headers(settings.api_key, app_name)
    models = [settings.primary_model.strip()]
    fallback_model = settings.fallback_model.strip()
    if fallback_model and fallback_model != models[0]:
        models.append(fallback_model)

    last_failure: RequestFailure | None = None
    for index, model in enumerate(models):
        payload = prepare_request(source_text, settings, model)

        try:
            response_data = send_request(payload, headers, settings)
            translated_text = extract_output_text(response_data)
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
