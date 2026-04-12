from __future__ import annotations

from time import monotonic

import keyboard
from PySide6.QtCore import QObject, Signal


class GlobalCopyTrigger(QObject):
    triggered = Signal()
    error = Signal(str)

    def __init__(self, interval_ms: int) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self._last_copy_press = 0.0
        self._hook = None

    def start(self) -> None:
        if self._hook is not None:
            return

        try:
            self._hook = keyboard.on_press(self._handle_key_press)
        except Exception as exc:
            self.error.emit(f"글로벌 단축키 감지 시작 실패: {exc}")

    def stop(self) -> None:
        if self._hook is None:
            return
        keyboard.unhook(self._hook)
        self._hook = None

    def update_interval(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms

    def _handle_key_press(self, event: keyboard.KeyboardEvent) -> None:
        if event.name != "c":
            return

        try:
            ctrl_pressed = keyboard.is_pressed("ctrl")
        except Exception:
            ctrl_pressed = False

        if not ctrl_pressed:
            return

        now = monotonic()
        if (now - self._last_copy_press) * 1000 <= self.interval_ms:
            self._last_copy_press = 0.0
            self.triggered.emit()
            return

        self._last_copy_press = now
