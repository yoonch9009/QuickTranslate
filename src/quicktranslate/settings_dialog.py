from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .settings import AppSettings, LANGUAGE_OPTIONS, MODEL_OPTIONS

EFFORT_PRESET_OPTIONS: list[tuple[str, str]] = [
    ("자동/비움", ""),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("custom", "__custom__"),
]


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle("QuickTranslate 설정")
        self.setModal(True)
        self.resize(620, 520)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.api_key_input = QLineEdit(settings.api_key)
        self.api_key_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.api_key_input.setPlaceholderText("OpenRouter API Key")

        self.deepseek_api_key_input = QLineEdit(settings.deepseek_api_key)
        self.deepseek_api_key_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.deepseek_api_key_input.setPlaceholderText("DeepSeek API Key")

        self.target_language_combo = QComboBox()
        self.primary_model_combo = QComboBox()
        self.fallback_model_combo = QComboBox()
        self.primary_effort_combo = QComboBox()
        self.fallback_effort_combo = QComboBox()
        self.primary_model_combo.setEditable(True)
        self.fallback_model_combo.setEditable(True)

        for label, code in LANGUAGE_OPTIONS:
            self.target_language_combo.addItem(label, code)

        for label, model in MODEL_OPTIONS:
            self.primary_model_combo.addItem(label, model)
            self.fallback_model_combo.addItem(label, model)

        for label, value in EFFORT_PRESET_OPTIONS:
            self.primary_effort_combo.addItem(label, value)
            self.fallback_effort_combo.addItem(label, value)

        self.primary_reasoning_input = QPlainTextEdit()
        self.primary_reasoning_input.setPlaceholderText(
            '{\n  "effort": "none"\n}'
        )
        self.primary_reasoning_input.setFixedHeight(92)
        self.primary_reasoning_input.setPlainText(
            self._format_reasoning_json(settings.primary_reasoning_config)
        )

        self.fallback_reasoning_input = QPlainTextEdit()
        self.fallback_reasoning_input.setPlaceholderText(
            '{\n  "effort": "none"\n}'
        )
        self.fallback_reasoning_input.setFixedHeight(92)
        self.fallback_reasoning_input.setPlainText(
            self._format_reasoning_json(settings.fallback_reasoning_config)
        )

        self.trigger_interval_input = QSpinBox()
        self.trigger_interval_input.setRange(300, 2000)
        self.trigger_interval_input.setSuffix(" ms")
        self.trigger_interval_input.setValue(settings.trigger_interval_ms)

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 120)
        self.timeout_input.setSuffix(" s")
        self.timeout_input.setValue(settings.request_timeout_seconds)

        self.popup_width_input = QSpinBox()
        self.popup_width_input.setRange(360, 900)
        self.popup_width_input.setValue(settings.popup_auto_max_width)

        self.popup_height_input = QSpinBox()
        self.popup_height_input.setRange(220, 700)
        self.popup_height_input.setValue(settings.popup_auto_max_height)

        self._select_data(self.target_language_combo, settings.target_language_code)
        self._select_or_set_text(self.primary_model_combo, settings.primary_model)
        self._select_or_set_text(self.fallback_model_combo, settings.fallback_model)
        self._sync_effort_combo_from_config(
            self.primary_effort_combo,
            settings.primary_reasoning_config,
        )
        self._sync_effort_combo_from_config(
            self.fallback_effort_combo,
            settings.fallback_reasoning_config,
        )

        self.primary_effort_combo.currentIndexChanged.connect(
            lambda: self._apply_effort_preset(
                self.primary_effort_combo,
                self.primary_reasoning_input,
            )
        )
        self.fallback_effort_combo.currentIndexChanged.connect(
            lambda: self._apply_effort_preset(
                self.fallback_effort_combo,
                self.fallback_reasoning_input,
            )
        )
        self.primary_reasoning_input.textChanged.connect(
            lambda: self._sync_effort_combo_from_editor(
                self.primary_effort_combo,
                self.primary_reasoning_input,
            )
        )
        self.fallback_reasoning_input.textChanged.connect(
            lambda: self._sync_effort_combo_from_editor(
                self.fallback_effort_combo,
                self.fallback_reasoning_input,
            )
        )

        form.addRow("OpenRouter API Key", self.api_key_input)
        form.addRow("DeepSeek API Key", self.deepseek_api_key_input)
        form.addRow("대상 언어", self.target_language_combo)
        form.addRow("기본 모델", self.primary_model_combo)
        form.addRow("기본 effort", self.primary_effort_combo)
        form.addRow("기본 reasoning JSON", self.primary_reasoning_input)
        form.addRow("폴백 모델", self.fallback_model_combo)
        form.addRow("폴백 effort", self.fallback_effort_combo)
        form.addRow("폴백 reasoning JSON", self.fallback_reasoning_input)
        form.addRow("`Ctrl+C+C` 간격", self.trigger_interval_input)
        form.addRow("API 타임아웃", self.timeout_input)
        form.addRow("자동 최대 너비", self.popup_width_input)
        form.addRow("자동 최대 높이", self.popup_height_input)

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel_button = QPushButton("취소")
        save_button = QPushButton("저장")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)

        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)

        root.addLayout(form)
        root.addLayout(buttons)

    def accept(self) -> None:
        try:
            self._parse_reasoning_input(self.primary_reasoning_input)
            self._parse_reasoning_input(self.fallback_reasoning_input)
        except ValueError as exc:
            QMessageBox.warning(self, "설정 오류", str(exc))
            return
        super().accept()

    def build_settings(self, current: AppSettings) -> AppSettings:
        return AppSettings(
            api_key=self.api_key_input.text().strip(),
            deepseek_api_key=self.deepseek_api_key_input.text().strip(),
            target_language_code=self.target_language_combo.currentData(),
            primary_model=(self.primary_model_combo.currentData() or self.primary_model_combo.currentText()).strip(),
            primary_reasoning_config=self._parse_reasoning_input(self.primary_reasoning_input),
            fallback_model=(self.fallback_model_combo.currentData() or self.fallback_model_combo.currentText()).strip(),
            fallback_reasoning_config=self._parse_reasoning_input(self.fallback_reasoning_input),
            trigger_interval_ms=self.trigger_interval_input.value(),
            request_timeout_seconds=self.timeout_input.value(),
            fallback_on_provider_error_only=current.fallback_on_provider_error_only,
            cache_ttl_seconds=current.cache_ttl_seconds,
            clipboard_settle_poll_ms=current.clipboard_settle_poll_ms,
            clipboard_settle_timeout_ms=current.clipboard_settle_timeout_ms,
            popup_auto_max_width=self.popup_width_input.value(),
            popup_auto_max_height=self.popup_height_input.value(),
        )

    @staticmethod
    def _select_data(combo_box: QComboBox, value: str) -> None:
        index = combo_box.findData(value)
        if index >= 0:
            combo_box.setCurrentIndex(index)

    @staticmethod
    def _select_or_set_text(combo_box: QComboBox, value: str) -> None:
        index = combo_box.findData(value)
        if index >= 0:
            combo_box.setCurrentIndex(index)
            return
        combo_box.setEditText(value)

    @staticmethod
    def _format_reasoning_json(config: dict | None) -> str:
        if not config:
            return ""
        return json.dumps(config, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_reasoning_input(editor: QPlainTextEdit) -> dict | None:
        raw = editor.toPlainText().strip()
        if not raw:
            return None

        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Reasoning JSON 형식이 올바르지 않습니다: {exc.msg}") from exc

        if not isinstance(value, dict):
            raise ValueError("Reasoning 설정은 JSON 객체여야 합니다.")

        return value

    def _apply_effort_preset(
        self,
        combo_box: QComboBox,
        editor: QPlainTextEdit,
    ) -> None:
        preset = str(combo_box.currentData() or "")
        if preset == "__custom__":
            return

        current = self._safe_parse_reasoning_input(editor)
        if current is not None and any(key != "effort" for key in current):
            return

        if not preset:
            editor.blockSignals(True)
            editor.setPlainText("")
            editor.blockSignals(False)
            return

        editor.blockSignals(True)
        editor.setPlainText(json.dumps({"effort": preset}, ensure_ascii=False, indent=2))
        editor.blockSignals(False)

    def _sync_effort_combo_from_editor(
        self,
        combo_box: QComboBox,
        editor: QPlainTextEdit,
    ) -> None:
        config = self._safe_parse_reasoning_input(editor)
        self._sync_effort_combo_from_config(combo_box, config)

    def _sync_effort_combo_from_config(
        self,
        combo_box: QComboBox,
        config: dict | None,
    ) -> None:
        value = ""
        if config:
            if set(config.keys()) == {"effort"} and isinstance(config.get("effort"), str):
                value = str(config["effort"])
            else:
                value = "__custom__"

        index = combo_box.findData(value)
        combo_box.blockSignals(True)
        combo_box.setCurrentIndex(max(index, 0))
        combo_box.blockSignals(False)

    @staticmethod
    def _safe_parse_reasoning_input(editor: QPlainTextEdit) -> dict | None:
        raw = editor.toPlainText().strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
