from __future__ import annotations

import ctypes
from math import ceil
from time import monotonic

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QShortcut,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class TranslationPopup(QWidget):
    closed = Signal()
    _MIN_WIDTH = 320
    _MIN_HEIGHT = 180
    _EDGE_MARGIN = 6
    _TEXT_CHROME_WIDTH = 72
    _TEXT_CHROME_HEIGHT = 96
    _LOADING_WIDTH = 220
    _LOADING_HEIGHT = 92
    _GLOBAL_INPUT_POLL_MS = 25
    _OUTSIDE_CLICK_GRACE_SECONDS = 0.3
    _VK_ESCAPE = 0x1B
    _VK_LBUTTON = 0x01
    _VK_RBUTTON = 0x02

    def __init__(self, auto_max_width: int, auto_max_height: int) -> None:
        super().__init__(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setWindowTitle("QuickTranslate")
        self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)
        self.setMouseTracking(True)
        self.setObjectName("popup")
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._auto_max_width = max(auto_max_width, self._MIN_WIDTH)
        self._auto_max_height = max(auto_max_height, self._MIN_HEIGHT)
        self._resize_edges = Qt.Edges()
        self._resize_origin = QPoint()
        self._resize_geometry = QRect()
        self._moving = False
        self._move_offset = QPoint()
        self._loading = False
        self._pinned = False
        self._content_revision = 0
        self._outside_clicks_enabled_at = 0.0
        self._global_esc_down = False
        self._global_left_down = False
        self._global_right_down = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.panel = QWidget()
        self.panel.setObjectName("panel")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        self.panel.setMouseTracking(True)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(18, 16, 18, 12)
        panel_layout.setSpacing(10)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_edit.setFrameStyle(0)
        self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.text_edit.setObjectName("resultEdit")

        self.action_bar = QWidget()
        self.action_bar.setObjectName("actionBar")
        self.action_bar.setCursor(Qt.OpenHandCursor)

        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.model_label = QLabel()
        self.model_label.setObjectName("modelLabel")
        action_layout.addWidget(self.model_label)
        action_layout.addStretch()

        self.pin_button = QPushButton("고정")
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip("다른 곳을 클릭해도 번역창을 유지합니다.")
        self.copy_button = QPushButton("복사")
        self.close_button = QPushButton("닫기")
        self.pin_button.setObjectName("pinButton")
        self.copy_button.setObjectName("actionButton")
        self.close_button.setObjectName("actionButton")
        action_layout.addWidget(self.pin_button)
        action_layout.addWidget(self.copy_button)
        action_layout.addWidget(self.close_button)

        panel_layout.addWidget(self.action_bar)
        panel_layout.addWidget(self.text_edit)
        outer.addWidget(self.panel)

        self.pin_button.toggled.connect(self.set_pinned)
        self.copy_button.clicked.connect(self.copy_text)
        self.close_button.clicked.connect(self.dismiss)
        self.close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.close_shortcut.activated.connect(self.dismiss)

        self.installEventFilter(self)
        self.panel.installEventFilter(self)
        self.text_edit.viewport().installEventFilter(self)
        self.action_bar.installEventFilter(self)

        self.global_input_timer = QTimer(self)
        self.global_input_timer.setInterval(self._GLOBAL_INPUT_POLL_MS)
        self.global_input_timer.timeout.connect(self._poll_global_input)

        self.resize(
            min(self._auto_max_width, 520),
            min(self._auto_max_height, 260),
        )
        self._apply_style()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched in (self, self.panel, self.text_edit.viewport(), self.action_bar):
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                local_pos = self._event_pos_in_self(watched, event)
                edges = self._resize_edges_for_pos(local_pos)
                if edges:
                    self._start_resize(edges, event.globalPosition().toPoint())
                    return True

                if watched is self.action_bar:
                    self._start_move(event.globalPosition().toPoint())
                    return True

            if event.type() == QEvent.Type.MouseMove:
                local_pos = self._event_pos_in_self(watched, event)
                if self._resize_edges and event.buttons() & Qt.LeftButton:
                    self._resize_window(event.globalPosition().toPoint())
                    return True
                if self._moving and event.buttons() & Qt.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._move_offset)
                    return True
                self._update_cursor(local_pos)

            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                if self._resize_edges:
                    self._resize_edges = Qt.Edges()
                    self.unsetCursor()
                    return True
                if self._moving:
                    self._moving = False
                    self.action_bar.setCursor(Qt.OpenHandCursor)
                    return True

            if (
                event.type() == QEvent.Type.Leave
                and not self._resize_edges
                and not self._moving
            ):
                self.unsetCursor()
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:
        self._arm_outside_click_guard()
        self.global_input_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.global_input_timer.stop()
        super().hideEvent(event)

    def dismiss(self) -> None:
        self.closed.emit()
        self.hide()

    def set_auto_size_limits(self, auto_max_width: int, auto_max_height: int) -> None:
        self._auto_max_width = max(auto_max_width, self._MIN_WIDTH)
        self._auto_max_height = max(auto_max_height, self._MIN_HEIGHT)

    @property
    def is_pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_button.setText("고정됨" if pinned else "고정")
        if self.pin_button.isChecked() != pinned:
            self.pin_button.setChecked(pinned)

    def show_translation(
        self,
        text: str,
        model_name: str,
        *,
        used_fallback: bool = False,
    ) -> None:
        self._loading = False
        self.copy_button.setEnabled(True)
        display_model = model_name.removeprefix("openrouter/")
        prefix = "폴백 · " if used_fallback else ""
        self.model_label.setText(prefix + display_model)
        self.model_label.setToolTip(model_name)
        self._set_text_and_auto_resize(text)
        self._arm_outside_click_guard()
        self._show_popup(reposition=not self.isVisible())

    def show_loading(self, message: str = "번역 중...") -> None:
        self._loading = True
        self.copy_button.setEnabled(False)
        self.model_label.setText("")
        self._content_revision += 1
        self.text_edit.setPlainText(message)
        self.resize(self._LOADING_WIDTH, self._LOADING_HEIGHT)
        self._arm_outside_click_guard()
        self._show_popup(reposition=not self.isVisible())

    def show_partial_translation(self, text: str) -> None:
        self._loading = True
        self.copy_button.setEnabled(False)
        self._set_text_and_auto_resize(text, grow_only=True)
        self._show_popup(reposition=False)

    def show_status(self, title: str, message: str) -> None:
        self._loading = False
        self.copy_button.setEnabled(False)
        self.model_label.setText("")
        text = f"{title}\n\n{message}" if title else message
        self._set_text_and_auto_resize(text)
        self._arm_outside_click_guard()
        self._show_popup(reposition=not self.isVisible())

    def copy_text(self) -> None:
        QGuiApplication.clipboard().setText(self.text_edit.toPlainText())

    def _set_text_and_auto_resize(self, text: str, *, grow_only: bool = False) -> None:
        self._content_revision += 1
        revision = self._content_revision
        self.text_edit.setPlainText(text)
        self._auto_resize_to_content(text, grow_only=grow_only)
        QTimer.singleShot(
            0,
            lambda: self._finish_deferred_auto_resize(revision, grow_only),
        )

    def _auto_resize_to_content(self, text: str, *, grow_only: bool = False) -> None:
        lines = text.splitlines() or [text]
        metrics = QFontMetrics(self.text_edit.font())
        longest_line_width = max(
            (metrics.horizontalAdvance(line) for line in lines), default=0
        )

        target_width = max(
            self._MIN_WIDTH,
            min(longest_line_width + self._TEXT_CHROME_WIDTH, self._auto_max_width),
        )

        doc = QTextDocument(self)
        doc.setDefaultFont(self.text_edit.font())
        doc.setPlainText(text)
        doc.setTextWidth(max(220, target_width - self._TEXT_CHROME_WIDTH))

        target_height = max(
            self._MIN_HEIGHT,
            min(
                int(doc.size().height()) + self._TEXT_CHROME_HEIGHT,
                self._auto_max_height,
            ),
        )
        target_size = QSize(target_width, target_height)
        if grow_only:
            target_size = target_size.expandedTo(self.size())
        self.resize(target_size)
        self._keep_inside_available_screen()

    def _finish_deferred_auto_resize(self, revision: int, grow_only: bool) -> None:
        if revision != self._content_revision:
            return
        document = self.text_edit.document()
        document.setTextWidth(max(1, self.text_edit.viewport().width()))
        target_height = max(
            self._MIN_HEIGHT,
            min(
                ceil(document.size().height()) + self._TEXT_CHROME_HEIGHT,
                self._auto_max_height,
            ),
        )
        if grow_only:
            target_height = max(self.height(), target_height)
        if target_height != self.height():
            self.resize(self.width(), target_height)
        self._keep_inside_available_screen()

    def _show_popup(self, *, reposition: bool) -> None:
        if reposition:
            self._move_near_cursor()

        self.show()
        self.raise_()

    def _arm_outside_click_guard(self) -> None:
        self._reset_global_input_state()
        self._outside_clicks_enabled_at = (
            monotonic() + self._OUTSIDE_CLICK_GRACE_SECONDS
        )

    def _keep_inside_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.screenAt(self.frameGeometry().center())
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = min(max(self.x(), available.left()), available.right() - self.width() + 1)
        y = min(max(self.y(), available.top()), available.bottom() - self.height() + 1)
        self.move(x, y)

    def _move_near_cursor(self) -> None:
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        fallback_screen = self.screen() or QGuiApplication.primaryScreen()
        geometry = (
            screen.availableGeometry()
            if screen
            else fallback_screen.availableGeometry()
        )
        pos = cursor_pos + QPoint(18, 18)

        if pos.x() + self.width() > geometry.right():
            pos.setX(max(geometry.left(), geometry.right() - self.width()))
        if pos.y() + self.height() > geometry.bottom():
            pos.setY(max(geometry.top(), geometry.bottom() - self.height()))

        self.move(pos)

    def _resize_edges_for_pos(self, pos: QPoint) -> Qt.Edges:
        rect = self.rect()
        edges = Qt.Edges()

        if pos.x() <= self._EDGE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= rect.width() - self._EDGE_MARGIN:
            edges |= Qt.RightEdge

        if pos.y() <= self._EDGE_MARGIN:
            edges |= Qt.TopEdge
        elif pos.y() >= rect.height() - self._EDGE_MARGIN:
            edges |= Qt.BottomEdge

        return edges

    def _event_pos_in_self(self, watched: object, event: QEvent) -> QPoint:
        pos = event.position().toPoint()
        if watched is self:
            return pos
        return watched.mapTo(self, pos)

    def _start_move(self, global_pos: QPoint) -> None:
        handle = self.windowHandle()
        if handle is not None and handle.startSystemMove():
            return
        self._moving = True
        self._move_offset = global_pos - self.frameGeometry().topLeft()
        self.action_bar.setCursor(Qt.ClosedHandCursor)

    def _start_resize(self, edges: Qt.Edges, global_pos: QPoint) -> None:
        handle = self.windowHandle()
        if handle is not None and handle.startSystemResize(edges):
            return
        self._resize_edges = edges
        self._resize_origin = global_pos
        self._resize_geometry = self.geometry()

    def _update_cursor(self, pos: QPoint) -> None:
        edges = self._resize_edges_for_pos(pos)
        if edges in (Qt.LeftEdge, Qt.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif edges in (Qt.TopEdge, Qt.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        elif edges in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in (Qt.TopEdge | Qt.RightEdge, Qt.BottomEdge | Qt.LeftEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.unsetCursor()

    def _resize_window(self, global_pos: QPoint) -> None:
        delta = global_pos - self._resize_origin
        geometry = QRect(self._resize_geometry)

        if self._resize_edges & Qt.LeftEdge:
            new_left = min(
                geometry.right() - self.minimumWidth(),
                geometry.left() + delta.x(),
            )
            geometry.setLeft(new_left)
        if self._resize_edges & Qt.RightEdge:
            geometry.setRight(
                max(geometry.left() + self.minimumWidth(), geometry.right() + delta.x())
            )
        if self._resize_edges & Qt.TopEdge:
            new_top = min(
                geometry.bottom() - self.minimumHeight(),
                geometry.top() + delta.y(),
            )
            geometry.setTop(new_top)
        if self._resize_edges & Qt.BottomEdge:
            geometry.setBottom(
                max(
                    geometry.top() + self.minimumHeight(), geometry.bottom() + delta.y()
                )
            )

        self.setGeometry(geometry.normalized())

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
                color: #f7f7f5;
            }
            #popup {
                background: transparent;
                border: none;
            }
            #panel {
                background: #292929;
                border: none;
                border-radius: 12px;
            }
            #resultEdit {
                background: transparent;
                border: none;
                padding: 0;
                font-size: 17px;
                line-height: 1.45;
                selection-background-color: rgba(95, 160, 255, 0.35);
            }
            #actionBar {
                background: transparent;
                border: none;
                min-height: 28px;
            }
            #actionButton {
                background: transparent;
                border: none;
                color: rgba(247, 247, 245, 0.74);
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            #actionButton:hover {
                color: rgba(255, 255, 255, 0.98);
            }
            #modelLabel {
                color: rgba(247, 247, 245, 0.52);
                font-size: 11px;
            }
            #pinButton {
                background: transparent;
                border: none;
                color: rgba(247, 247, 245, 0.74);
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            #pinButton:hover, #pinButton:checked {
                color: #8fc7ff;
            }
            """
        )

    def _poll_global_input(self) -> None:
        if not self.isVisible():
            return

        esc_down = self._is_virtual_key_down(self._VK_ESCAPE)
        if esc_down and not self._global_esc_down:
            self.dismiss()
            self._global_esc_down = True
            return
        self._global_esc_down = esc_down

        left_down = self._is_virtual_key_down(self._VK_LBUTTON)
        right_down = self._is_virtual_key_down(self._VK_RBUTTON)

        if self._should_hide_for_global_click(left_down, self._global_left_down):
            self.dismiss()
            self._global_left_down = left_down
            self._global_right_down = right_down
            return

        if self._should_hide_for_global_click(right_down, self._global_right_down):
            self.dismiss()
            self._global_left_down = left_down
            self._global_right_down = right_down
            return

        self._global_left_down = left_down
        self._global_right_down = right_down

    def _should_hide_for_global_click(
        self, current_down: bool, previous_down: bool
    ) -> bool:
        if not current_down or previous_down:
            return False
        if self._pinned or monotonic() < self._outside_clicks_enabled_at:
            return False
        if self._moving or self._resize_edges:
            return False
        return not self.frameGeometry().contains(QCursor.pos())

    def _reset_global_input_state(self) -> None:
        self._global_esc_down = self._is_virtual_key_down(self._VK_ESCAPE)
        self._global_left_down = self._is_virtual_key_down(self._VK_LBUTTON)
        self._global_right_down = self._is_virtual_key_down(self._VK_RBUTTON)

    @staticmethod
    def _is_virtual_key_down(virtual_key: int) -> bool:
        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
