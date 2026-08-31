from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quicktranslate.app import QuickTranslateApp
from quicktranslate.model_catalog import EffectiveReasoning


class FakePopup:
    def __init__(self, *, visible: bool, pinned: bool) -> None:
        self._visible = visible
        self.is_pinned = pinned
        self.begin_count = 0
        self.delete_later = Mock()
        self.always_pin_mode = False
        self.partial_texts: list[str] = []
        self.translations: list[tuple[str, str, bool, str]] = []
        self.comparison_available = False
        self.comparison_loading: list[tuple[str, str, str, str]] = []
        self.comparison_partials: list[str] = []
        self.comparison_results: list[tuple[str, str, str]] = []
        self.comparison_errors: list[tuple[str, str]] = []
        self.translation_errors: list[tuple[str, str]] = []
        self.loading_count = 0

    def isVisible(self) -> bool:
        return self._visible

    def begin_new_translation(self, *, pinned: bool = False) -> None:
        self.is_pinned = pinned
        self.begin_count += 1

    def set_always_pin_mode(self, enabled: bool) -> None:
        self.always_pin_mode = enabled

    def set_pinned(self, pinned: bool) -> None:
        self.is_pinned = pinned

    def set_comparison_available(self, available: bool) -> None:
        self.comparison_available = available

    def show_loading(self) -> None:
        self.loading_count += 1

    def show_comparison_loading(
        self,
        primary_model: str,
        primary_reasoning_effort: str,
        fallback_model: str,
        fallback_reasoning_effort: str,
    ) -> None:
        self.comparison_loading.append(
            (
                primary_model,
                primary_reasoning_effort,
                fallback_model,
                fallback_reasoning_effort,
            )
        )

    def show_comparison_partial(self, text: str) -> None:
        self.comparison_partials.append(text)

    def show_comparison_result(
        self, text: str, model: str, reasoning_effort: str
    ) -> None:
        self.comparison_results.append((text, model, reasoning_effort))

    def show_comparison_error(self, message: str, model: str) -> None:
        self.comparison_errors.append((message, model))

    def show_translation_error(self, message: str, model: str) -> None:
        self.translation_errors.append((message, model))

    def show_partial_translation(self, text: str) -> None:
        self.partial_texts.append(text)

    def show_translation(
        self,
        text: str,
        model: str,
        *,
        used_fallback: bool,
        reasoning_effort: str,
    ) -> None:
        self.translations.append((text, model, used_fallback, reasoning_effort))

    def deleteLater(self) -> None:
        self.delete_later()


class AppPopupManagementTests(unittest.TestCase):
    def test_new_translation_enables_comparison_before_worker_finishes(self) -> None:
        popup = FakePopup(visible=False, pinned=False)
        settings = SimpleNamespace(primary_model="primary", fallback_model="fallback")
        signals = SimpleNamespace(success=Mock(), partial=Mock(), failure=Mock())
        task = SimpleNamespace(signals=signals)
        app = SimpleNamespace(
            settings=settings,
            popup=popup,
            _signature_for_source=Mock(return_value="signature"),
            _last_success_signature="",
            _last_success_at=0.0,
            _task_signatures={},
            _prepare_popup_for_new_translation=Mock(),
            _popup_sources={},
            _task_counter=0,
            _active_tasks={},
            _task_popups={},
            _handle_translation_success=Mock(),
            _handle_translation_partial=Mock(),
            _handle_translation_failure=Mock(),
            thread_pool=Mock(),
        )
        app._set_comparison_available = lambda target, model: (
            QuickTranslateApp._set_comparison_available(app, target, model)
        )

        with (
            patch("quicktranslate.app.load_cached_translation", return_value=None),
            patch("quicktranslate.app.TranslationTask", return_value=task),
        ):
            QuickTranslateApp._start_translation(app, "원문")

        self.assertEqual(popup.loading_count, 1)
        self.assertTrue(popup.comparison_available)
        self.assertEqual(app._popup_sources[popup], ("원문", None))
        app.thread_pool.start.assert_called_once_with(task)

    def test_pinned_popup_is_retained_and_new_translation_gets_new_window(self) -> None:
        pinned = FakePopup(visible=True, pinned=True)
        replacement = FakePopup(visible=False, pinned=False)
        app = SimpleNamespace(
            popup=pinned,
            _retained_popups=[],
            _create_popup=Mock(return_value=replacement),
            _always_pin_new_popups=False,
        )

        QuickTranslateApp._prepare_popup_for_new_translation(app)

        self.assertEqual(app._retained_popups, [pinned])
        self.assertIs(app.popup, replacement)
        self.assertEqual(replacement.begin_count, 1)
        self.assertFalse(replacement.is_pinned)

    def test_unpinned_popup_is_reused_for_next_translation(self) -> None:
        popup = FakePopup(visible=True, pinned=False)
        app = SimpleNamespace(
            popup=popup,
            _retained_popups=[],
            _create_popup=Mock(),
            _always_pin_new_popups=False,
        )

        QuickTranslateApp._prepare_popup_for_new_translation(app)

        app._create_popup.assert_not_called()
        self.assertEqual(popup.begin_count, 1)
        self.assertFalse(popup.is_pinned)

    def test_closing_retained_popup_releases_it_without_canceling_current(self) -> None:
        retained = FakePopup(visible=True, pinned=True)
        current = FakePopup(visible=True, pinned=False)
        cancel = Mock()
        app = SimpleNamespace(
            popup=current,
            _retained_popups=[retained],
            _cancel_popup_translations=cancel,
            _popup_sources={retained: ("원문", None)},
        )

        QuickTranslateApp._handle_popup_closed(app, retained)

        self.assertEqual(app._retained_popups, [])
        retained.delete_later.assert_called_once_with()
        cancel.assert_called_once_with(retained)
        self.assertNotIn(retained, app._popup_sources)

    def test_always_pin_mode_pins_future_popup_without_changing_old_popup(self) -> None:
        pinned = FakePopup(visible=True, pinned=True)
        replacement = FakePopup(visible=False, pinned=False)
        app = SimpleNamespace(
            popup=pinned,
            _retained_popups=[],
            _create_popup=Mock(return_value=replacement),
            _always_pin_new_popups=True,
        )

        QuickTranslateApp._prepare_popup_for_new_translation(app)

        self.assertTrue(pinned.is_pinned)
        self.assertTrue(replacement.is_pinned)

    def test_active_pinned_popup_allows_another_clipboard_capture(self) -> None:
        popup = FakePopup(visible=True, pinned=True)
        app = SimpleNamespace(
            popup=popup,
            pending_clipboard_capture=False,
            _task_popups={1: popup},
        )

        self.assertTrue(QuickTranslateApp._can_begin_clipboard_capture(app))

        popup.is_pinned = False
        self.assertFalse(QuickTranslateApp._can_begin_clipboard_capture(app))

    def test_active_comparison_obeys_the_same_pin_capture_rule(self) -> None:
        popup = FakePopup(visible=True, pinned=False)
        app = SimpleNamespace(
            popup=popup,
            pending_clipboard_capture=False,
            _task_popups={},
            _comparison_task_popups={1: popup},
        )

        self.assertFalse(QuickTranslateApp._can_begin_clipboard_capture(app))

        popup.is_pinned = True
        self.assertTrue(QuickTranslateApp._can_begin_clipboard_capture(app))

    def test_partial_results_are_routed_to_each_tasks_own_popup(self) -> None:
        first = FakePopup(visible=True, pinned=True)
        second = FakePopup(visible=True, pinned=False)
        app = SimpleNamespace(_task_popups={1: first, 2: second})

        QuickTranslateApp._handle_translation_partial(app, 1, "첫 작업")
        QuickTranslateApp._handle_translation_partial(app, 2, "둘째 작업")

        self.assertEqual(first.partial_texts, ["첫 작업"])
        self.assertEqual(second.partial_texts, ["둘째 작업"])

    def test_completed_pinned_task_updates_its_original_popup(self) -> None:
        pinned = FakePopup(visible=True, pinned=True)
        newer = FakePopup(visible=True, pinned=False)
        record_success = Mock()
        app = SimpleNamespace(
            popup=newer,
            _active_tasks={1: object(), 2: object()},
            _task_popups={1: pinned, 2: newer},
            _task_signatures={1: "first", 2: "second"},
            settings=SimpleNamespace(primary_model="model"),
            _record_success=record_success,
            _reasoning_effort_for_display=Mock(return_value="low"),
            _set_comparison_available=Mock(),
        )

        QuickTranslateApp._handle_translation_success(app, 1, "완료", "model")

        self.assertEqual(pinned.translations, [("완료", "model", False, "low")])
        self.assertEqual(newer.translations, [])
        self.assertIn(2, app._task_popups)
        record_success.assert_called_once_with("first")
        app._set_comparison_available.assert_called_once_with(pinned, "model")

    def test_comparison_requests_only_the_configured_fallback_model(self) -> None:
        popup = FakePopup(visible=True, pinned=False)
        fallback_model = "openrouter/deepseek/deepseek-v4-flash-vision-exp"
        settings = SimpleNamespace(
            primary_model="openrouter/z-ai/glm-5.3-flash",
            fallback_model=fallback_model,
        )
        signals = SimpleNamespace(success=Mock(), partial=Mock(), failure=Mock())
        task = SimpleNamespace(signals=signals)
        app = SimpleNamespace(
            settings=settings,
            _popup_sources={popup: ("원문", None)},
            _comparison_task_popups={},
            _active_tasks={},
            _task_counter=4,
            _reasoning_effort_for_display=Mock(return_value="none"),
            _handle_comparison_success=Mock(),
            _handle_comparison_partial=Mock(),
            _handle_comparison_failure=Mock(),
            thread_pool=Mock(),
        )

        with patch("quicktranslate.app.TranslationTask", return_value=task) as task_type:
            QuickTranslateApp._start_comparison(app, popup)

        task_type.assert_called_once_with(
            5,
            "원문",
            None,
            settings,
            only_model=fallback_model,
        )
        self.assertEqual(
            popup.comparison_loading,
            [
                (
                    settings.primary_model,
                    "none",
                    fallback_model,
                    "none",
                )
            ],
        )
        self.assertIs(app._comparison_task_popups[5], popup)
        app.thread_pool.start.assert_called_once_with(task)

    def test_comparison_results_are_routed_to_the_requesting_popup(self) -> None:
        popup = FakePopup(visible=True, pinned=True)
        app = SimpleNamespace(
            settings=SimpleNamespace(fallback_model="fallback"),
            _active_tasks={7: object()},
            _comparison_task_popups={7: popup},
            _reasoning_effort_for_display=Mock(return_value="none"),
        )

        QuickTranslateApp._handle_comparison_partial(app, 7, "작성 중")
        QuickTranslateApp._handle_comparison_success(app, 7, "완료", "fallback")

        self.assertEqual(popup.comparison_partials, ["작성 중"])
        self.assertEqual(popup.comparison_results, [("완료", "fallback", "none")])
        self.assertNotIn(7, app._active_tasks)
        self.assertNotIn(7, app._comparison_task_popups)

    def test_comparison_failure_stays_in_the_comparison_column(self) -> None:
        popup = FakePopup(visible=True, pinned=False)
        app = SimpleNamespace(
            settings=SimpleNamespace(fallback_model="fallback"),
            _active_tasks={8: object()},
            _comparison_task_popups={8: popup},
        )

        QuickTranslateApp._handle_comparison_failure(app, 8, "요청 실패")

        self.assertEqual(popup.comparison_errors, [("요청 실패", "fallback")])
        self.assertNotIn(8, app._active_tasks)
        self.assertNotIn(8, app._comparison_task_popups)

    def test_primary_failure_is_routed_without_closing_comparison(self) -> None:
        popup = FakePopup(visible=True, pinned=False)
        app = SimpleNamespace(
            settings=SimpleNamespace(primary_model="primary"),
            _active_tasks={9: object()},
            _task_popups={9: popup},
            _task_signatures={9: "signature"},
        )

        QuickTranslateApp._handle_translation_failure(app, 9, "기본 요청 실패")

        self.assertEqual(
            popup.translation_errors, [("기본 요청 실패", "primary")]
        )

    def test_always_pin_toggle_pins_current_and_preserves_older_windows(self) -> None:
        current = FakePopup(visible=True, pinned=False)
        retained = FakePopup(visible=True, pinned=True)
        settings = SimpleNamespace(always_pin_new_popups=False, save=Mock())
        app = SimpleNamespace(
            popup=current,
            _retained_popups=[retained],
            settings=settings,
            _always_pin_new_popups=False,
        )

        QuickTranslateApp._set_always_pin_new_popups(app, True)

        self.assertTrue(settings.always_pin_new_popups)
        settings.save.assert_called_once_with()
        self.assertTrue(current.always_pin_mode)
        self.assertTrue(retained.always_pin_mode)
        self.assertTrue(current.is_pinned)
        self.assertTrue(retained.is_pinned)

    def test_reasoning_level_is_extracted_for_popup_label(self) -> None:
        app = SimpleNamespace(settings=object())
        reasoning = EffectiveReasoning({"effort": "low"}, "자동 → low", True)

        with patch(
            "quicktranslate.app.effective_reasoning_for_request",
            return_value=reasoning,
        ):
            level = QuickTranslateApp._reasoning_effort_for_display(app, "model")

        self.assertEqual(level, "low")


if __name__ == "__main__":
    unittest.main()
