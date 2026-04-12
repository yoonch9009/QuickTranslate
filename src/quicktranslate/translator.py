from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

import requests

from .settings import AppSettings

OPENROUTER_URL = "https://openrouter.ai/api/v1/responses"
SESSION = requests.Session()
_CACHE_LOCK = Lock()
_CACHE_LIMIT = 128
_TRANSLATION_CACHE: OrderedDict[tuple[str, str, str, str], "TranslationResult"] = OrderedDict()


class TranslationError(RuntimeError):
    pass


@dataclass
class TranslationResult:
    text: str
    model: str


def build_instructions(target_language_code: str) -> str:
    return (
        "You are a translation engine. Detect the source language automatically and "
        f"translate the user's text into {target_language_code}. "
        "Keep the formatting, line breaks, bullet points, and code blocks. "
        "Return only the translation with no explanation."
    )


def reasoning_effort_for_model(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("qwen/"):
        return "none"
    if normalized.startswith("google/gemma"):
        return "minimal"
    return "none"


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


def estimate_max_output_tokens(source_text: str) -> int:
    rough = max(120, min(int(len(source_text) * 2.2), 3200))
    return rough


def load_cached_translation(
    source_text: str,
    settings: AppSettings,
) -> TranslationResult | None:
    key = (
        source_text,
        settings.target_language_code,
        settings.primary_model,
        settings.fallback_model,
    )
    with _CACHE_LOCK:
        cached = _TRANSLATION_CACHE.get(key)
        if cached is None:
            return None
        _TRANSLATION_CACHE.move_to_end(key)
        return cached


def store_cached_translation(
    source_text: str,
    settings: AppSettings,
    result: TranslationResult,
) -> None:
    key = (
        source_text,
        settings.target_language_code,
        settings.primary_model,
        settings.fallback_model,
    )
    with _CACHE_LOCK:
        _TRANSLATION_CACHE[key] = result
        _TRANSLATION_CACHE.move_to_end(key)
        while len(_TRANSLATION_CACHE) > _CACHE_LIMIT:
            _TRANSLATION_CACHE.popitem(last=False)


def request_translation(
    source_text: str,
    settings: AppSettings,
    *,
    app_name: str = "QuickTranslate",
) -> TranslationResult:
    if not settings.api_key.strip():
        raise TranslationError("OpenRouter API Key가 설정되지 않았습니다.")

    if not source_text.strip():
        raise TranslationError("번역할 텍스트가 비어 있습니다.")

    cached = load_cached_translation(source_text, settings)
    if cached is not None:
        return cached

    headers = {
        "Authorization": f"Bearer {settings.api_key.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/quicktranslate",
        "X-Title": app_name,
    }

    models = [settings.primary_model.strip(), settings.fallback_model.strip()]
    seen = set()
    last_error = "사용 가능한 번역 모델이 없습니다."

    for model in models:
        if not model or model in seen:
            continue
        seen.add(model)

        payload = {
            "model": model,
            "input": source_text,
            "instructions": build_instructions(settings.target_language_code),
            "reasoning": {"effort": reasoning_effort_for_model(model)},
            "text": {
                "format": {"type": "text"},
                "verbosity": "low",
            },
            "max_output_tokens": estimate_max_output_tokens(source_text),
            "provider": {
                "allow_fallbacks": True,
                "sort": "latency",
            },
            "store": False,
            "temperature": 0.0,
        }

        try:
            response = SESSION.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=settings.request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            content = extract_output_text(data)
            if not content:
                raise TranslationError(f"{model} 응답이 비어 있습니다.")
            result = TranslationResult(text=content, model=model)
            store_cached_translation(source_text, settings, result)
            return result
        except requests.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            last_error = f"{model} 요청 실패: {detail}"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = f"{model} 응답 해석 실패: {exc}"
        except requests.RequestException as exc:
            last_error = f"{model} 연결 실패: {exc}"

    raise TranslationError(last_error)
