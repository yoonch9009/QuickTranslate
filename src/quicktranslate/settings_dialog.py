from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .settings import AppSettings, LANGUAGE_OPTIONS, MODEL_OPTIONS


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle("QuickTranslate 설정")
        self.setModal(True)
        self.resize(520, 240)

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.api_key_input = QLineEdit(settings.api_key)
        self.api_key_input.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.api_key_input.setPlaceholderText("OpenRouter API Key")

        self.target_language_combo = QComboBox()
        self.primary_model_combo = QComboBox()
        self.fallback_model_combo = QComboBox()

        for label, code in LANGUAGE_OPTIONS:
            self.target_language_combo.addItem(label, code)

        for label, model in MODEL_OPTIONS:
            self.primary_model_combo.addItem(label, model)
            self.fallback_model_combo.addItem(label, model)

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
        self._select_data(self.primary_model_combo, settings.primary_model)
        self._select_data(self.fallback_model_combo, settings.fallback_model)

        form.addRow("API Key", self.api_key_input)
        form.addRow("대상 언어", self.target_language_combo)
        form.addRow("기본 모델", self.primary_model_combo)
        form.addRow("폴백 모델", self.fallback_model_combo)
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

    def build_settings(self, current: AppSettings) -> AppSettings:
        return AppSettings(
            api_key=self.api_key_input.text().strip(),
            target_language_code=self.target_language_combo.currentData(),
            primary_model=self.primary_model_combo.currentData(),
            fallback_model=self.fallback_model_combo.currentData(),
            trigger_interval_ms=self.trigger_interval_input.value(),
            request_timeout_seconds=self.timeout_input.value(),
            popup_auto_max_width=self.popup_width_input.value(),
            popup_auto_max_height=self.popup_height_input.value(),
        )

    @staticmethod
    def _select_data(combo_box: QComboBox, value: str) -> None:
        index = combo_box.findData(value)
        if index >= 0:
            combo_box.setCurrentIndex(index)
