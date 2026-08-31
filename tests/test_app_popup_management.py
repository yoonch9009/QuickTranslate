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

    def isVisible(self) -> bool:
        return self._visible

    def begin_new_translation(self, *, pinned: bool = False) -> None:
        self.is_pinned = pinned
        self.begin_count += 1

    def set_always_pin_mode(self, enabled: bool) -> None:
        self.always_pin_mode = enabled

    def set_pinned(self, pinned: bool) -> None:
        self.is_pinned = pinned

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
        )

        QuickTranslateApp._handle_popup_closed(app, retained)

        self.assertEqual(app._retained_popups, [])
        retained.delete_later.assert_called_once_with()
        cancel.assert_called_once_with(retained)

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
        )

        QuickTranslateApp._handle_translation_success(app, 1, "완료", "model")

        self.assertEqual(pinned.translations, [("완료", "model", False, "low")])
        self.assertEqual(newer.translations, [])
        self.assertIn(2, app._task_popups)
        record_success.assert_called_once_with("first")

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
