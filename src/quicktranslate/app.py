from __future__ import annotations

import sys

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
from .translator import TranslationError, request_translation


class TranslationSignals(QObject):
    success = Signal(str, str)
    failure = Signal(str)


class TranslationTask(QRunnable):
    def __init__(self, source_text: str, settings: AppSettings) -> None:
        super().__init__()
        self.source_text = source_text
        self.settings = settings
        self.signals = TranslationSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = request_translation(self.source_text, self.settings)
        except TranslationError as exc:
            self.signals.failure.emit(str(exc))
            return
        except Exception as exc:
            self.signals.failure.emit(f"알 수 없는 오류: {exc}")
            return

        self.signals.success.emit(result.text, result.model)


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
        self.thread_pool = QThreadPool.globalInstance()
        self.is_translating = False
        self.last_clipboard_text = ""

        self.tray_icon = QSystemTrayIcon(self._resolve_icon(), self.application)
        self.tray_icon.setToolTip("QuickTranslate")
        self.tray_icon.setContextMenu(self._build_menu())
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        self.copy_trigger = GlobalCopyTrigger(self.settings.trigger_interval_ms)
        self.copy_trigger.triggered.connect(self._queue_clipboard_translation)
        self.copy_trigger.error.connect(self._show_error)
        self.copy_trigger.start()

        self.tray_icon.showMessage(
            "QuickTranslate",
            "백그라운드 실행 중입니다. 텍스트를 선택한 뒤 Ctrl+C+C 를 누르세요.",
            QSystemTrayIcon.Information,
            3000,
        )

        if not self.settings.api_key:
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

        translate_action.triggered.connect(self._translate_clipboard_now)
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
        self.copy_trigger.update_interval(self.settings.trigger_interval_ms)
        self.tray_icon.showMessage(
            "QuickTranslate",
            "설정을 저장했습니다.",
            QSystemTrayIcon.Information,
            2000,
        )

    def _translate_clipboard_now(self) -> None:
        self._queue_clipboard_translation()

    def _queue_clipboard_translation(self) -> None:
        QTimer.singleShot(55, self._translate_from_clipboard)

    def _translate_from_clipboard(self) -> None:
        if self.is_translating:
            return

        clipboard_text = QGuiApplication.clipboard().text().strip()
        if not clipboard_text:
            return

        self.last_clipboard_text = clipboard_text
        self.is_translating = True

        task = TranslationTask(clipboard_text, self.settings)
        task.signals.success.connect(self._handle_translation_success)
        task.signals.failure.connect(self._handle_translation_failure)
        self.thread_pool.start(task)

    def _handle_translation_success(self, translated_text: str, model_name: str) -> None:
        self.is_translating = False
        self.popup.show_translation(translated_text, model_name)

    def _handle_translation_failure(self, message: str) -> None:
        self.is_translating = False
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self.popup.show_status("오류", message)

    def quit(self) -> None:
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
