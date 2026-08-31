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

    def isVisible(self) -> bool:
        return self._visible

    def begin_new_translation(self) -> None:
        self.is_pinned = False
        self.begin_count += 1

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
            _cancel_active_translation=cancel,
        )

        QuickTranslateApp._handle_popup_closed(app, retained)

        self.assertEqual(app._retained_popups, [])
        retained.delete_later.assert_called_once_with()
        cancel.assert_not_called()

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
