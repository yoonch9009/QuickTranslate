from __future__ import annotations

import hashlib
import json
import logging
import sys
from time import monotonic

from PySide6.QtCore import (
    QLockFile,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
)

from . import __version__, resources_rc  # noqa: F401
from .hotkey import GlobalCopyTrigger
from .image_input import encode_image_data_url
from .model_catalog import MODEL_CATALOG
from .popup import TranslationPopup
from .settings import APP_DIR, LOCK_PATH, LOG_PATH, AppSettings
from .settings_dialog import SettingsDialog
from .translator import TranslationError, load_cached_translation, request_translation

DUPLICATE_TRANSLATION_WINDOW_SECONDS = 0.8
LOGGER = logging.getLogger(__name__)


class MetadataRefreshTask(QRunnable):
    @Slot()
    def run(self) -> None:
        MODEL_CATALOG.refresh()


class TranslationSignals(QObject):
    success = Signal(int, str, str)
    partial = Signal(int, str)
    failure = Signal(int, str)


class TranslationTask(QRunnable):
    def __init__(
        self,
        task_id: int,
        source_text: str,
        image_data_url: str | None,
        settings: AppSettings,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.source_text = source_text
        self.image_data_url = image_data_url
        self.settings = settings
        self.signals = TranslationSignals()
        self._partial_text = ""
        self._last_partial_emit_at = 0.0
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = request_translation(
                self.source_text,
                self.settings,
                image_data_url=self.image_data_url,
                on_delta=self._handle_delta,
            )
        except TranslationError as exc:
            self.signals.failure.emit(self.task_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - keep the tray app alive on worker failures
            self.signals.failure.emit(self.task_id, f"알 수 없는 오류: {exc}")
            return

        self.signals.success.emit(self.task_id, result.text, result.model)

    def _handle_delta(self, delta: str) -> None:
        self._partial_text += delta
        now = monotonic()
        if now - self._last_partial_emit_at >= 0.05 or "\n" in delta:
            self._last_partial_emit_at = now
            self.signals.partial.emit(self.task_id, self._partial_text)


class QuickTranslateApp(QObject):
    def __init__(self, application: QApplication) -> None:
        super().__init__()
        self.application = application
        self.application.setQuitOnLastWindowClosed(False)
        self.settings = AppSettings.load()
        self.app_icon = self._resolve_icon()
        self.application.setWindowIcon(self.app_icon)
        self._retained_popups: list[TranslationPopup] = []
        self.popup = self._create_popup()
        self.thread_pool = QThreadPool.globalInstance()
        self.metadata_task = MetadataRefreshTask()
        self.thread_pool.start(self.metadata_task)
        self.is_translating = False
        self.pending_clipboard_capture = False
        self._clipboard_baseline_text = ""
        self._clipboard_baseline_image_key = 0
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

        self.tray_icon = QSystemTrayIcon(self.app_icon, self.application)
        self.tray_icon.setToolTip(f"QuickTranslate {__version__}")
        self.tray_icon.setContextMenu(self._build_menu())
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        self.copy_trigger = GlobalCopyTrigger(self.settings.trigger_interval_ms)
        self.copy_trigger.triggered.connect(self._begin_clipboard_capture)
        self.copy_trigger.error.connect(self._show_error)
        self.copy_trigger.start()

        self.tray_icon.showMessage(
            "QuickTranslate",
            "텍스트 또는 이미지를 복사한 뒤 Ctrl+C+C 를 누르세요.",
            QSystemTrayIcon.Information,
            3000,
        )

        if not self.settings.api_key and not self.settings.deepseek_api_key:
            QTimer.singleShot(300, self.open_settings)

    def _resolve_icon(self) -> QIcon:
        icon = QIcon(":/quicktranslate.ico")
        if icon.isNull():
            icon = self.application.style().standardIcon(QStyle.SP_ComputerIcon)
        if icon.isNull():
            icon = self.application.windowIcon()
        return icon

    def _create_popup(self) -> TranslationPopup:
        popup = TranslationPopup(
            self.settings.popup_auto_max_width,
            self.settings.popup_auto_max_height,
        )
        popup.setWindowIcon(self.app_icon)
        popup.closed.connect(
            lambda retained_popup=popup: self._handle_popup_closed(retained_popup)
        )
        return popup

    def _prepare_popup_for_new_translation(self) -> None:
        if self.popup.isVisible() and self.popup.is_pinned:
            self._retained_popups.append(self.popup)
            self.popup = self._create_popup()
        self.popup.begin_new_translation()

    def _handle_popup_closed(self, popup: TranslationPopup) -> None:
        if popup is self.popup:
            self._cancel_active_translation()
            return
        if popup in self._retained_popups:
            self._retained_popups.remove(popup)
            popup.deleteLater()

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
        LOGGER.info(
            "Settings saved: primary=%s fallback=%s",
            self.settings.primary_model,
            self.settings.fallback_model,
        )
        for popup in [self.popup, *self._retained_popups]:
            popup.set_auto_size_limits(
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
        if self.popup.isVisible() and not self.popup.is_pinned:
            self.popup.begin_new_translation()
            self.popup.show_loading("클립보드 확인 중...")
        clipboard = QGuiApplication.clipboard()
        self._clipboard_baseline_text = clipboard.text().strip()
        baseline_image = clipboard.image()
        self._clipboard_baseline_image_key = (
            int(baseline_image.cacheKey()) if not baseline_image.isNull() else 0
        )
        self._clipboard_capture_started_at = monotonic()
        self.clipboard_poll_timer.start(self.settings.clipboard_settle_poll_ms)

    def _poll_clipboard_capture(self) -> None:
        if not self.pending_clipboard_capture:
            return

        clipboard = QGuiApplication.clipboard()
        clipboard_text = clipboard.text().strip()
        clipboard_image = clipboard.image()
        image_key = int(clipboard_image.cacheKey()) if not clipboard_image.isNull() else 0
        clipboard_changed = (
            bool(clipboard_text) and clipboard_text != self._clipboard_baseline_text
        )
        image_changed = bool(image_key) and image_key != self._clipboard_baseline_image_key
        elapsed_ms = (monotonic() - self._clipboard_capture_started_at) * 1000
        timed_out = elapsed_ms >= self.settings.clipboard_settle_timeout_ms

        if not clipboard_changed and not image_changed and not timed_out:
            self.clipboard_poll_timer.start(self.settings.clipboard_settle_poll_ms)
            return

        self.pending_clipboard_capture = False
        if image_key:
            try:
                image_data_url = encode_image_data_url(clipboard_image)
            except ValueError as exc:
                self._show_error(str(exc))
                return
            self._start_translation("", image_data_url)
            return
        if not clipboard_text:
            return

        self._start_translation(clipboard_text)

    def _start_translation(
        self,
        source_text: str,
        image_data_url: str | None = None,
    ) -> None:
        signature = self._signature_for_source(source_text, image_data_url)
        cached = load_cached_translation(source_text, self.settings, image_data_url)
        if cached is not None:
            self._prepare_popup_for_new_translation()
            self._record_success(signature)
            self.popup.show_translation(
                cached.text,
                cached.model,
                used_fallback=cached.model != self.settings.primary_model,
            )
            return

        now = monotonic()
        if (
            signature == self._last_success_signature
            and now - self._last_success_at <= DUPLICATE_TRANSLATION_WINDOW_SECONDS
        ):
            return

        self._prepare_popup_for_new_translation()
        self.is_translating = True
        self._pending_signature = signature
        self._task_counter += 1
        task_id = self._task_counter
        self._pending_task_id = task_id
        self.popup.show_loading()

        task = TranslationTask(task_id, source_text, image_data_url, self.settings)
        task.signals.success.connect(self._handle_translation_success)
        task.signals.partial.connect(self._handle_translation_partial)
        task.signals.failure.connect(self._handle_translation_failure)
        self._active_tasks[task_id] = task
        self.thread_pool.start(task)

    def _cancel_active_translation(self) -> None:
        if not self.is_translating:
            return
        self.is_translating = False
        self._pending_signature = ""
        self._pending_task_id = 0

    def _handle_translation_success(
        self,
        task_id: int,
        translated_text: str,
        model_name: str,
    ) -> None:
        self._active_tasks.pop(task_id, None)
        if task_id != self._pending_task_id:
            return
        self.is_translating = False
        self._record_success(self._pending_signature)
        self._pending_signature = ""
        self._pending_task_id = 0
        self.popup.show_translation(
            translated_text,
            model_name,
            used_fallback=model_name != self.settings.primary_model,
        )

    def _handle_translation_partial(self, task_id: int, translated_text: str) -> None:
        if task_id != self._pending_task_id or not self.is_translating:
            return
        self.popup.show_partial_translation(translated_text)

    def _handle_translation_failure(self, task_id: int, message: str) -> None:
        self._active_tasks.pop(task_id, None)
        if task_id != self._pending_task_id:
            return
        self.is_translating = False
        self._pending_signature = ""
        self._pending_task_id = 0
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self.popup.show_status("오류", message)

    def _record_success(self, signature: str) -> None:
        self._last_success_signature = signature
        self._last_success_at = monotonic()

    def _signature_for_source(
        self,
        text: str,
        image_data_url: str | None,
    ) -> str:
        source_digest = text
        if image_data_url:
            source_digest = hashlib.sha256(image_data_url.encode("ascii")).hexdigest()
        payload = "|".join(
            [
                source_digest,
                self.settings.target_language_code,
                self.settings.primary_model,
                self.settings.primary_reasoning_mode,
                json.dumps(
                    self.settings.primary_reasoning_config,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                self.settings.primary_parameter_mode,
                str(self.settings.primary_temperature),
                str(self.settings.primary_top_p),
                json.dumps(
                    self.settings.primary_extra_parameters,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
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
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    LOGGER.info("QuickTranslate %s starting", __version__)

    app = QApplication(sys.argv)
    app.setApplicationName("QuickTranslate")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    instance_lock = QLockFile(str(LOCK_PATH))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        QMessageBox.information(
            None, "QuickTranslate", "QuickTranslate가 이미 실행 중입니다."
        )
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None, "QuickTranslate", "시스템 트레이를 사용할 수 없습니다."
        )
        return 1

    quick_translate = QuickTranslateApp(app)
    exit_code = app.exec()
    LOGGER.debug("Application object retained until shutdown: %r", quick_translate)
    instance_lock.unlock()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
