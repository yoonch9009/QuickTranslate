from __future__ import annotations

import os
import unittest
from time import monotonic
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from quicktranslate.popup import TranslationPopup


class PopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.popup = TranslationPopup(620, 700)

    def tearDown(self) -> None:
        self.popup.hide()
        self.popup.deleteLater()
        self.application.processEvents()

    def test_loading_replaces_previous_translation_immediately(self) -> None:
        self.popup.show_translation("이전 번역", "model")

        self.popup.show_loading()

        self.assertEqual(self.popup.text_edit.toPlainText(), "번역 중...")

    def test_fallback_result_displays_actual_model(self) -> None:
        self.popup.show_translation(
            "번역",
            "openrouter/z-ai/glm-5.3-flash",
            used_fallback=True,
        )

        self.assertEqual(self.popup.model_label.text(), "폴백 · z-ai/glm-5.3-flash")

    def test_deferred_resize_fits_multiline_translation_without_scroll(self) -> None:
        text = "\n".join(f"번역 결과 {index}" for index in range(10))

        self.popup.show_translation(text, "model")
        self.application.processEvents()
        self.application.processEvents()

        self.assertGreater(self.popup.height(), self.popup.minimumHeight())
        self.assertEqual(self.popup.text_edit.verticalScrollBar().maximum(), 0)

    def test_stale_resize_does_not_override_new_loading_state(self) -> None:
        self.popup.show_translation("긴 번역 결과 " * 100, "model")
        self.popup.show_loading()

        self.application.processEvents()

        self.assertEqual(self.popup.text_edit.toPlainText(), "번역 중...")

    def test_pin_blocks_outside_click_but_close_controls_still_work(self) -> None:
        self.popup._outside_clicks_enabled_at = monotonic() - 1
        with patch("quicktranslate.popup.QCursor.pos", return_value=QPoint(9999, 9999)):
            self.assertTrue(self.popup._should_hide_for_global_click(True, False))
            self.popup.set_pinned(True)
            self.assertFalse(self.popup._should_hide_for_global_click(True, False))

        self.assertTrue(self.popup.is_pinned)
        self.assertEqual(self.popup.pin_button.text(), "고정됨")


if __name__ == "__main__":
    unittest.main()
