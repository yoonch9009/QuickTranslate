from __future__ import annotations

import os
import unittest
from time import monotonic
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt
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

    def test_model_label_displays_reasoning_effort(self) -> None:
        self.popup.show_translation(
            "번역",
            "qwen/qwen3.8-flash",
            reasoning_effort="none",
        )

        self.assertEqual(
            self.popup.model_label.text(),
            "qwen/qwen3.8-flash · none",
        )

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

    def test_manual_resize_is_preserved_when_translation_finishes(self) -> None:
        self.popup.show_loading()
        self.popup._start_resize(Qt.RightEdge | Qt.BottomEdge, QPoint())
        self.popup.resize(580, 420)

        self.popup.show_partial_translation("작성 중")
        self.popup.show_translation("완성", "model")
        self.application.processEvents()
        self.application.processEvents()

        self.assertEqual(self.popup.size(), QSize(580, 420))

    def test_new_translation_reenables_automatic_sizing(self) -> None:
        self.popup._start_resize(Qt.RightEdge | Qt.BottomEdge, QPoint())
        self.popup.resize(580, 420)
        self.popup.show_translation("첫 번째", "model")
        self.popup.begin_new_translation()

        self.popup.show_translation("짧은 새 번역", "model")
        self.application.processEvents()

        self.assertLess(self.popup.width(), 580)
        self.assertLess(self.popup.height(), 420)

    def test_streaming_updates_preserve_user_scroll_position(self) -> None:
        first = "\n".join(f"번역 줄 {index}" for index in range(80))
        self.popup.show_partial_translation(first)
        self.application.processEvents()
        scroll_bar = self.popup.text_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum() // 3)
        reading_position = scroll_bar.value()

        self.popup.show_partial_translation(first + "\n새 번역 줄")
        self.application.processEvents()
        self.application.processEvents()

        self.assertGreater(reading_position, 0)
        self.assertEqual(scroll_bar.value(), reading_position)

    def test_final_translation_preserves_streaming_scroll_position(self) -> None:
        text = "\n".join(f"번역 줄 {index}" for index in range(80))
        self.popup.show_partial_translation(text)
        self.application.processEvents()
        scroll_bar = self.popup.text_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum() // 3)
        reading_position = scroll_bar.value()

        self.popup.show_translation(text + "\n마지막 줄", "model")
        self.application.processEvents()
        self.application.processEvents()

        self.assertGreater(reading_position, 0)
        self.assertEqual(scroll_bar.value(), reading_position)

    def test_streaming_continues_following_when_user_is_at_bottom(self) -> None:
        first = "\n".join(f"번역 줄 {index}" for index in range(80))
        self.popup.show_partial_translation(first)
        self.application.processEvents()
        scroll_bar = self.popup.text_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

        self.popup.show_partial_translation(first + "\n새 번역 줄")
        self.application.processEvents()
        self.application.processEvents()

        self.assertGreater(scroll_bar.maximum(), 0)
        self.assertEqual(scroll_bar.value(), scroll_bar.maximum())

    def test_popup_padding_is_reduced_by_half(self) -> None:
        margins = self.popup.panel.layout().contentsMargins()

        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (9, 8, 9, 6),
        )
        self.assertEqual(self.popup.panel.layout().spacing(), 5)

    def test_header_buttons_have_reliable_vertical_click_targets(self) -> None:
        self.popup.show_translation("번역", "model")
        self.application.processEvents()

        for button in (
            self.popup.compare_button,
            self.popup.always_pin_button,
            self.popup.pin_button,
            self.popup.copy_button,
            self.popup.close_button,
        ):
            self.assertGreaterEqual(button.height(), 24)

    def test_compare_button_is_left_of_always_pin_and_emits_request(self) -> None:
        requests: list[bool] = []
        self.popup.comparison_requested.connect(lambda: requests.append(True))
        self.popup.show_translation("기본 번역", "primary", reasoning_effort="low")
        self.popup.set_comparison_available(True)
        self.application.processEvents()

        self.popup.compare_button.click()

        self.assertEqual(requests, [True])
        self.assertLess(
            self.popup.compare_button.geometry().left(),
            self.popup.always_pin_button.geometry().left(),
        )

    def test_comparison_view_keeps_primary_and_streams_fallback(self) -> None:
        self.popup.show_translation("기본 번역", "primary", reasoning_effort="low")
        self.popup.set_comparison_available(True)

        self.popup.show_comparison_loading("fallback", "none")
        self.popup.show_comparison_partial("폴백 작성 중")
        self.popup.show_comparison_result("폴백 완료", "fallback", "none")

        self.assertTrue(self.popup.text_edit.isHidden())
        self.assertFalse(self.popup.comparison_panel.isHidden())
        self.assertEqual(self.popup.comparison_primary_label.text(), "primary · low")
        self.assertEqual(self.popup.comparison_primary_edit.toPlainText(), "기본 번역")
        self.assertEqual(
            self.popup.comparison_fallback_label.text(), "폴백 · fallback · none"
        )
        self.assertEqual(self.popup.comparison_fallback_edit.toPlainText(), "폴백 완료")
        self.assertFalse(self.popup.compare_button.isEnabled())
        self.popup.copy_text()
        self.assertEqual(
            self.application.clipboard().text(),
            "primary · low\n기본 번역\n\n폴백 · fallback · none\n폴백 완료",
        )
        self.application.processEvents()
        self.assertLessEqual(
            abs(
                self.popup.comparison_primary_edit.width()
                - self.popup.comparison_fallback_edit.width()
            ),
            2,
        )

    def test_comparison_does_not_override_manual_window_size(self) -> None:
        self.popup.show_translation("기본 번역", "primary")
        self.popup._start_resize(Qt.RightEdge | Qt.BottomEdge, QPoint())
        self.popup.resize(580, 420)

        self.popup.show_comparison_loading("fallback")
        self.popup.show_comparison_result("폴백 번역", "fallback")

        self.assertEqual(self.popup.size(), QSize(580, 420))

    def test_new_translation_resets_comparison_view(self) -> None:
        self.popup.show_translation("기본 번역", "primary")
        self.popup.show_comparison_loading("fallback")

        self.popup.begin_new_translation()

        self.assertFalse(self.popup.text_edit.isHidden())
        self.assertTrue(self.popup.comparison_panel.isHidden())

    def test_pin_buttons_only_turn_blue_when_checked(self) -> None:
        style = " ".join(self.popup.styleSheet().split())

        self.assertIn(
            "#pinButton:hover, #alwaysPinButton:hover "
            "{ color: rgba(255, 255, 255, 0.98); }",
            style,
        )
        self.assertIn(
            "#pinButton:checked, #alwaysPinButton:checked { color: #8fc7ff; }",
            style,
        )

    def test_copy_button_stays_active_while_visible_text_changes(self) -> None:
        self.popup.show_loading()
        self.assertTrue(self.popup.copy_button.isEnabled())

        self.popup.show_partial_translation("작성 중인 번역")
        self.assertTrue(self.popup.copy_button.isEnabled())

        self.popup.show_status("안내", "상태 메시지")
        self.assertTrue(self.popup.copy_button.isEnabled())

    def test_new_translation_always_starts_unpinned(self) -> None:
        self.popup.set_pinned(True)

        self.popup.begin_new_translation()

        self.assertFalse(self.popup.is_pinned)
        self.assertFalse(self.popup.pin_button.isChecked())
        self.assertEqual(self.popup.pin_button.text(), "고정")

    def test_always_pin_mode_can_make_new_translation_start_pinned(self) -> None:
        self.popup.set_pinned(False)

        self.popup.begin_new_translation(pinned=True)

        self.assertTrue(self.popup.is_pinned)
        self.assertTrue(self.popup.pin_button.isChecked())

    def test_always_pin_control_is_left_of_manual_pin_and_emits_user_changes(
        self,
    ) -> None:
        changes: list[bool] = []
        self.popup.always_pin_changed.connect(changes.append)
        self.popup.show_translation("번역", "model")
        self.application.processEvents()

        self.popup.always_pin_button.click()
        self.popup.set_always_pin_mode(True)

        self.assertEqual(changes, [True])
        self.assertEqual(self.popup.always_pin_button.text(), "상시")
        self.assertLess(
            self.popup.always_pin_button.geometry().left(),
            self.popup.pin_button.geometry().left(),
        )
        self.assertFalse(self.popup.is_pinned)

    def test_popup_grows_wide_enough_for_model_and_all_header_buttons(self) -> None:
        self.popup.set_always_pin_mode(True)
        self.popup.show_translation(
            "짧음",
            "qwen/qwen3.8-flash",
            reasoning_effort="low",
        )
        self.application.processEvents()

        self.assertGreaterEqual(
            self.popup.model_label.width(),
            self.popup.model_label.sizeHint().width(),
        )

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
