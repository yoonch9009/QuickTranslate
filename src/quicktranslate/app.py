from __future__ import annotations

import hashlib
import sys
from time import monotonic

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
)

from .hotkey import GlobalCopyTrigger
from .popup import TranslationPopup
from .settings import AppSettings
from .settings_dialog import SettingsDialog
from .translator import TranslationError, load_cached_translation, request_translation

DUPLICATE_TRANSLATION_WINDOW_SECONDS = 0.8


class TranslationSignals(QObject):
    success = Signal(int, str, str)
    failure = Signal(int, str)


class TranslationTask(QRunnable):
    def __init__(self, task_id: int, source_text: str, settings: AppSettings) -> None:
        super().__init__()
        self.task_id = task_id
        self.source_text = source_text
        self.settings = settings
        self.signals = TranslationSignals()
        # We keep our own reference to the task (see QuickTranslateApp._active_tasks)
        # and release it in the result handler. Disabling autoDelete prevents the
        # thread pool from destroying the task (and its signals object) before the
        # queued result event is delivered to the GUI thread, which would silently
        # drop the result and leave the popup stuck on "번역 중...".
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = request_translation(self.source_text, self.settings)
        except TranslationError as exc:
            self.signals.failure.emit(self.task_id, str(exc))
            return
        except Exception as exc:
            self.signals.failure.emit(self.task_id, f"알 수 없는 오류: {exc}")
            return

        self.signals.success.emit(self.task_id, result.text, result.model)


class QuickTranslateApp(QObject):
    def __init__(self, application: QApplication) -> None:
        super().__init__()
        self.application = application
        self.application.setQuitOnLastWindowClosed(False)
        self.settings = AppSettings.load()
        self.popup = TranslationPopup(
            self.settings.popup_auto_max_width,
            self.settings.popup_auto_max_height,
        )
        self.popup.closed.connect(self._cancel_active_translation)
        self.thread_pool = QThreadPool.globalInstance()
        self.is_translating = False
        self.pending_clipboard_capture = False
        self._clipboard_baseline_text = ""
        self._clipboard_capture_started_at = 0.0
        self._pending_signature = ""
        self._pending_task_id = 0
        self._task_counter = 0
        self._active_tasks: dict[int, TranslationTask] = {}
        self._last_success_signature = ""
        self._last_success_at = 0.0

        self.clipboard_poll_timer = QTimer(self)
        self.clipboard_poll_timer.setSingleShot(True)
        self.clipboard_poll_timer.timeout.connect(self._poll_clipboard_capture)

        self.tray_icon = QSystemTrayIcon(self._resolve_icon(), self.application)
        self.tray_icon.setToolTip("QuickTranslate")
        self.tray_icon.setContextMenu(self._build_menu())
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        self.copy_trigger = GlobalCopyTrigger(self.settings.trigger_interval_ms)
        self.copy_trigger.triggered.connect(self._begin_clipboard_capture)
        self.copy_trigger.error.connect(self._show_error)
        self.copy_trigger.start()

        self.tray_icon.showMessage(
            "QuickTranslate",
            "백그라운드 실행 중입니다. 텍스트를 선택한 뒤 Ctrl+C+C 를 누르세요.",
            QSystemTrayIcon.Information,
            3000,
        )

        if not self.settings.api_key and not self.settings.deepseek_api_key:
            QTimer.singleShot(300, self.open_settings)

    def _resolve_icon(self) -> QIcon:
        icon = self.application.style().standardIcon(QStyle.SP_ComputerIcon)
        if icon.isNull():
            icon = self.application.windowIcon()
        return icon

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        translate_action = QAction("클립보드 번역", menu)
        settings_action = QAction("설정", menu)
        quit_action = QAction("종료", menu)

        translate_action.triggered.connect(self._begin_clipboard_capture)
        settings_action.triggered.connect(self.open_settings)
        quit_action.triggered.connect(self.quit)

        menu.addAction(translate_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.open_settings()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings)
        if dialog.exec() != SettingsDialog.Accepted:
            return

        self.settings = dialog.build_settings(self.settings)
        self.settings.save()
        self.popup.set_auto_size_limits(
            self.settings.popup_auto_max_width,
            self.settings.popup_auto_max_height,
        )
        self.clipboard_poll_timer.setInterval(self.settings.clipboard_settle_poll_ms)
        self.copy_trigger.update_interval(self.settings.trigger_interval_ms)
        self.tray_icon.showMessage(
            "QuickTranslate",
            "설정을 저장했습니다.",
            QSystemTrayIcon.Information,
            2000,
        )

    def _begin_clipboard_capture(self) -> None:
        if self.is_translating or self.pending_clipboard_capture:
            return

        self.pending_clipboard_capture = True
        self._clipboard_baseline_text = QGuiApplication.clipboard().text().strip()
        self._clipboard_capture_started_at = monotonic()
        self.clipboard_poll_timer.start(self.settings.clipboard_settle_poll_ms)

    def _poll_clipboard_capture(self) -> None:
        if not self.pending_clipboard_capture:
            return

        clipboard_text = QGuiApplication.clipboard().text().strip()
        clipboard_changed = bool(clipboard_text) and clipboard_text != self._clipboard_baseline_text
        elapsed_ms = (monotonic() - self._clipboard_capture_started_at) * 1000
        timed_out = elapsed_ms >= self.settings.clipboard_settle_timeout_ms

        if not clipboard_changed and not timed_out:
            self.clipboard_poll_timer.start(self.settings.clipboard_settle_poll_ms)
            return

        self.pending_clipboard_capture = False
        if not clipboard_text:
            return

        self._start_translation_for_text(clipboard_text)

    def _start_translation_for_text(self, source_text: str) -> None:
        signature = self._signature_for_text(source_text)
        now = monotonic()
        if (
            signature == self._last_success_signature
            and now - self._last_success_at <= DUPLICATE_TRANSLATION_WINDOW_SECONDS
        ):
            return

        cached = load_cached_translation(source_text, self.settings)
        if cached is not None:
            self._record_success(signature)
            self.popup.show_translation(cached.text, cached.model)
            return

        self.is_translating = True
        self._pending_signature = signature
        self._task_counter += 1
        task_id = self._task_counter
        self._pending_task_id = task_id
        self.popup.show_loading()

        task = TranslationTask(task_id, source_text, self.settings)
        # Connect to bound methods (persistent receiver) and keep a strong
        # reference to the task until its result arrives, so the queued result
        # event is never dropped before reaching the GUI thread.
        task.signals.success.connect(self._handle_translation_success)
        task.signals.failure.connect(self._handle_translation_failure)
        self._active_tasks[task_id] = task
        self.thread_pool.start(task)

    def _cancel_active_translation(self) -> None:
        # The user explicitly dismissed the popup (close button, Esc, or click
        # outside). Abandon any in-flight translation so its result no longer
        # reopens the popup. The background request itself cannot be force-killed,
        # but its result is discarded by the task-id guard in the handlers.
        if not self.is_translating:
            return
        self.is_translating = False
        self._pending_signature = ""
        self._pending_task_id = 0

    def _handle_translation_success(self, task_id: int, translated_text: str, model_name: str) -> None:
        self._active_tasks.pop(task_id, None)
        if task_id != self._pending_task_id:
            return
        self.is_translating = False
        self._record_success(self._pending_signature)
        self._pending_signature = ""
        self.popup.show_translation(translated_text, model_name)

    def _handle_translation_failure(self, task_id: int, message: str) -> None:
        self._active_tasks.pop(task_id, None)
        if task_id != self._pending_task_id:
            return
        self.is_translating = False
        self._pending_signature = ""
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self.popup.show_status("오류", message)

    def _record_success(self, signature: str) -> None:
        self._last_success_signature = signature
        self._last_success_at = monotonic()

    def _signature_for_text(self, text: str) -> str:
        payload = "|".join(
            [
                text,
                self.settings.target_language_code,
                self.settings.primary_model,
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def quit(self) -> None:
        self.pending_clipboard_capture = False
        self.clipboard_poll_timer.stop()
        self.copy_trigger.stop()
        self.tray_icon.hide()
        self.application.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("QuickTranslate")
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "QuickTranslate", "시스템 트레이를 사용할 수 없습니다.")
        return 1

    quick_translate = QuickTranslateApp(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
