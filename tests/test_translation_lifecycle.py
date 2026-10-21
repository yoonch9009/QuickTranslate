from __future__ import annotations

import logging
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quicktranslate.app import QuickTranslateApp, TranslationTask
from quicktranslate.codex_client import (
    CodexAppServerClient,
    TranslationCancelled,
    _classified_error,
    _TurnState,
)
from quicktranslate.settings import AppSettings
from quicktranslate.translator import (
    RequestFailure,
    TranslationResult,
    request_translation,
    should_retry_with_fallback,
)


def test_attempt_switch_resets_partial_text_and_logs_no_source(caplog):
    source = "PRIVATE_SOURCE_SHOULD_NOT_BE_LOGGED"
    settings = AppSettings(primary_model="codex/primary", fallback_model="openrouter/fallback")
    task = TranslationTask(5, source, None, settings)
    partials, attempts, successes = [], [], []
    task.signals.partial.connect(lambda _id, text: partials.append(text))
    task.signals.attempt.connect(lambda _id, model, reason: attempts.append((model, reason)))
    task.signals.success.connect(lambda _id, text, model: successes.append((text, model)))

    def request(*_args, on_attempt, on_delta, **_kwargs):
        on_attempt(settings.primary_model, "")
        on_delta("FIRST_MODEL\n")
        on_attempt(settings.fallback_model, "서버 혼잡")
        on_delta("SECOND_MODEL\n")
        return TranslationResult("SECOND_MODEL", settings.fallback_model)

    with (
        caplog.at_level(logging.INFO),
        patch("quicktranslate.app.request_translation", side_effect=request),
    ):
        task.run()
    assert partials == ["FIRST_MODEL\n", "SECOND_MODEL\n"]
    assert attempts == [(settings.primary_model, ""), (settings.fallback_model, "서버 혼잡")]
    assert successes == [("SECOND_MODEL", settings.fallback_model)]
    assert "translation_completed" in caplog.text and task.trace_id in caplog.text
    assert source not in caplog.text and "FIRST_MODEL" not in caplog.text


def test_settings_are_snapshotted_for_inflight_tasks():
    settings = AppSettings(primary_model="codex/first")
    task = TranslationTask(1, "source", None, settings)
    settings.primary_model = "codex/second"
    settings.primary_reasoning_config["effort"] = "max"
    assert task.settings.primary_model == "codex/first"
    assert task.settings.primary_reasoning_config["effort"] == "low"


def test_cancel_from_fallback_start_callback_prevents_network_call():
    event = Event()
    settings = AppSettings(primary_model="codex/a", fallback_model="codex/b")

    def attempt(model, reason):
        if model == "codex/b":
            event.set()

    with (
        patch(
            "quicktranslate.translator.request_codex_translation",
            side_effect=_classified_error("serverOverloaded", "busy"),
        ) as codex,
        pytest.raises(TranslationCancelled),
    ):
        request_translation("text", settings, cancel_event=event, on_attempt=attempt)
    assert codex.call_count == 1


def test_cancelled_queued_request_never_starts_model_or_fallback():
    event = Event()
    event.set()
    with (
        patch("quicktranslate.translator.request_codex_translation") as codex,
        pytest.raises(TranslationCancelled),
    ):
        request_translation("text", AppSettings(primary_model="codex/a"), cancel_event=event)
    codex.assert_not_called()


def test_cancel_after_primary_failure_does_not_attempt_fallback():
    event = Event()
    settings = AppSettings(primary_model="codex/a", fallback_model="codex/b")

    def fail(*_args, **_kwargs):
        event.set()
        raise _classified_error("serverOverloaded", "serverOverloaded")

    with (
        patch("quicktranslate.translator.request_codex_translation", side_effect=fail) as codex,
        pytest.raises(TranslationCancelled),
    ):
        request_translation("text", settings, cancel_event=event)
    assert codex.call_count == 1


@pytest.mark.parametrize("image", [False, True])
@pytest.mark.parametrize("error_only", [False, True])
def test_policy_refusal_never_falls_back(image, error_only):
    error = _classified_error("blocked cyberPolicy", "blocked")
    failure = RequestFailure(
        error.user_message, error.detail, error.retryable, allow_fallback=error.allow_fallback
    )
    assert not should_retry_with_fallback(
        failure, AppSettings(fallback_on_provider_error_only=error_only), image_request=image
    )


def test_model_reroute_is_exposed_and_reported_as_final_model():
    settings = AppSettings(primary_model="codex/requested", fallback_model="")
    attempts = []

    def codex(*_args, on_model_changed, **_kwargs):
        on_model_changed("served")
        return "translated"

    with patch("quicktranslate.translator.request_codex_translation", side_effect=codex):
        result = request_translation(
            "reroute-test", settings, on_attempt=lambda *a: attempts.append(a)
        )
    assert result.model == "codex/served"
    assert attempts[-1][0] == result.model


def test_reroute_notification_is_delivered_only_to_its_thread():
    callback = Mock()
    client = CodexAppServerClient()
    client._turns["a"] = _TurnState(on_model_changed=callback)
    client._turns["b"] = _TurnState(on_model_changed=Mock())
    client._handle_notification(
        {"method": "model/rerouted", "params": {"threadId": "a", "toModel": "new"}}
    )
    callback.assert_called_once_with("new")
    client._turns["b"].on_model_changed.assert_not_called()


def test_popup_close_signals_cancellation_for_primary_and_comparison():
    popup = object()
    first, second = Mock(), Mock()
    app = SimpleNamespace(
        _task_popups={1: popup},
        _task_signatures={1: "sig"},
        _comparison_task_popups={2: popup},
        _active_tasks={1: first, 2: second},
    )
    QuickTranslateApp._cancel_popup_translations(app, popup)
    first.cancel.assert_called_once()
    second.cancel.assert_called_once()
    assert not app._task_popups and not app._comparison_task_popups


def test_cancel_inflight_codex_interrupts_the_correct_turn():
    client = CodexAppServerClient()
    event = Event()

    def request(method, params, *, timeout):
        if method == "thread/start":
            return {"result": {"thread": {"id": "thread-a"}}}
        event.set()
        return {"result": {"turn": {"id": "turn-a"}}}

    with (
        patch.object(client, "_ensure_started"),
        patch.object(client, "_request", side_effect=request),
        patch.object(client, "_interrupt") as interrupt,
        pytest.raises(TranslationCancelled),
    ):
        client.translate(
            "source",
            model="a",
            effort="low",
            instructions="translate",
            image_data_url=None,
            timeout=1,
            cancel_event=event,
        )
    interrupt.assert_called_once_with("thread-a", "turn-a")
    assert not client._turns
