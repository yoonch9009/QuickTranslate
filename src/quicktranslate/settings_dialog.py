from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .model_catalog import MODEL_CATALOG
from .model_profiles import recommended_parameters_for
from .settings import (
    LANGUAGE_OPTIONS,
    MODEL_OPTIONS,
    PARAMETER_MODE_AUTO,
    PARAMETER_MODE_MANUAL,
    REASONING_MODE_AUTO,
    REASONING_MODE_MANUAL,
    AppSettings,
)

AUTO_VALUE = "__auto__"
CUSTOM_VALUE = "__custom__"
EFFORT_PRESET_OPTIONS: list[tuple[str, str]] = [
    ("자동(속도·가격 우선)", AUTO_VALUE),
    ("none", "none"),
    ("minimal", "minimal"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("max", "max"),
    ("직접 입력", CUSTOM_VALUE),
]
PARAMETER_MODE_OPTIONS: list[tuple[str, str]] = [
    ("자동(모델 권장값)", PARAMETER_MODE_AUTO),
    ("수동", PARAMETER_MODE_MANUAL),
]


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._syncing_reasoning = False
        self._current_settings = settings
        self._saved_model_profiles = deepcopy(settings.saved_model_profiles)
        self.setWindowTitle("QuickTranslate 설정")
        self.setModal(True)
        self.resize(900, 760)

        root = QVBoxLayout(self)
        form_container = QWidget()
        form = QFormLayout(form_container)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_container)

        self.api_key_input = QLineEdit(settings.api_key)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.api_key_input.setPlaceholderText("OpenRouter API Key")

        self.deepseek_api_key_input = QLineEdit(settings.deepseek_api_key)
        self.deepseek_api_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.deepseek_api_key_input.setPlaceholderText("DeepSeek API Key")

        self.target_language_combo = QComboBox()
        self.primary_model_combo = QComboBox()
        self.fallback_model_combo = QComboBox()
        self.primary_reasoning_combo = QComboBox()
        self.fallback_reasoning_combo = QComboBox()
        self.primary_model_combo.setEditable(True)
        self.fallback_model_combo.setEditable(True)

        for label, code in LANGUAGE_OPTIONS:
            self.target_language_combo.addItem(label, code)
        for label, model in MODEL_OPTIONS:
            self.primary_model_combo.addItem(label, model)
            self.fallback_model_combo.addItem(label, model)
        for label, value in EFFORT_PRESET_OPTIONS:
            self.primary_reasoning_combo.addItem(label, value)
            self.fallback_reasoning_combo.addItem(label, value)

        self.primary_reasoning_input = self._new_reasoning_editor()
        self.fallback_reasoning_input = self._new_reasoning_editor()
        self.primary_reasoning_status = self._new_status_label()
        self.fallback_reasoning_status = self._new_status_label()
        self.primary_parameter_mode_combo = QComboBox()
        self.fallback_parameter_mode_combo = QComboBox()
        for label, value in PARAMETER_MODE_OPTIONS:
            self.primary_parameter_mode_combo.addItem(label, value)
            self.fallback_parameter_mode_combo.addItem(label, value)
        self.primary_parameter_status = self._new_status_label()
        self.fallback_parameter_status = self._new_status_label()
        self.primary_extra_parameters_input = self._new_parameters_editor()
        self.fallback_extra_parameters_input = self._new_parameters_editor()

        self.trigger_interval_input = QSpinBox()
        self.trigger_interval_input.setRange(300, 2000)
        self.trigger_interval_input.setSuffix(" ms")
        self.trigger_interval_input.setValue(settings.trigger_interval_ms)

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(5, 120)
        self.timeout_input.setSuffix(" s")
        self.timeout_input.setValue(settings.request_timeout_seconds)

        self.primary_temperature_input = self._new_sampling_input(
            settings.primary_temperature,
            maximum=2.0,
        )
        self.primary_top_p_input = self._new_sampling_input(
            settings.primary_top_p,
            maximum=1.0,
        )
        self.fallback_temperature_input = self._new_sampling_input(
            settings.fallback_temperature,
            maximum=2.0,
        )
        self.fallback_top_p_input = self._new_sampling_input(
            settings.fallback_top_p,
            maximum=1.0,
        )
        self._select_data(
            self.primary_parameter_mode_combo,
            settings.primary_parameter_mode,
        )
        self._select_data(
            self.fallback_parameter_mode_combo,
            settings.fallback_parameter_mode,
        )
        self.primary_extra_parameters_input.setPlainText(
            self._format_parameters_json(settings.primary_extra_parameters)
        )
        self.fallback_extra_parameters_input.setPlainText(
            self._format_parameters_json(settings.fallback_extra_parameters)
        )

        self.profile_name_combo = QComboBox()
        self.profile_name_combo.setEditable(True)
        self.profile_name_combo.setPlaceholderText("저장할 프리셋 이름")
        self._refresh_profile_names()

        self.popup_width_input = QSpinBox()
        self.popup_width_input.setRange(360, 900)
        self.popup_width_input.setValue(settings.popup_auto_max_width)

        self.popup_height_input = QSpinBox()
        self.popup_height_input.setRange(220, 700)
        self.popup_height_input.setValue(settings.popup_auto_max_height)

        self._select_data(self.target_language_combo, settings.target_language_code)
        self._select_or_set_text(self.primary_model_combo, settings.primary_model)
        self._select_or_set_text(self.fallback_model_combo, settings.fallback_model)
        self._initialize_reasoning_controls(
            self.primary_reasoning_combo,
            self.primary_reasoning_input,
            settings.primary_reasoning_mode,
            settings.primary_reasoning_config,
        )
        self._initialize_reasoning_controls(
            self.fallback_reasoning_combo,
            self.fallback_reasoning_input,
            settings.fallback_reasoning_mode,
            settings.fallback_reasoning_config,
        )

        self.primary_reasoning_combo.currentIndexChanged.connect(
            lambda: self._reasoning_selection_changed(
                self.primary_reasoning_combo,
                self.primary_reasoning_input,
                self.primary_model_combo,
                self.primary_reasoning_status,
            )
        )
        self.fallback_reasoning_combo.currentIndexChanged.connect(
            lambda: self._reasoning_selection_changed(
                self.fallback_reasoning_combo,
                self.fallback_reasoning_input,
                self.fallback_model_combo,
                self.fallback_reasoning_status,
            )
        )
        self.primary_reasoning_input.textChanged.connect(
            lambda: self._reasoning_text_changed(
                self.primary_reasoning_combo,
                self.primary_reasoning_input,
            )
        )
        self.fallback_reasoning_input.textChanged.connect(
            lambda: self._reasoning_text_changed(
                self.fallback_reasoning_combo,
                self.fallback_reasoning_input,
            )
        )
        self.primary_model_combo.lineEdit().textChanged.connect(
            lambda: self._update_reasoning_status(
                self.primary_model_combo,
                self.primary_reasoning_combo,
                self.primary_reasoning_status,
            )
        )
        self.fallback_model_combo.lineEdit().textChanged.connect(
            lambda: self._update_reasoning_status(
                self.fallback_model_combo,
                self.fallback_reasoning_combo,
                self.fallback_reasoning_status,
            )
        )
        self.primary_parameter_mode_combo.currentIndexChanged.connect(
            lambda: self._update_parameter_controls("primary")
        )
        self.fallback_parameter_mode_combo.currentIndexChanged.connect(
            lambda: self._update_parameter_controls("fallback")
        )
        self.primary_model_combo.lineEdit().textChanged.connect(
            lambda: self._update_parameter_controls("primary")
        )
        self.fallback_model_combo.lineEdit().textChanged.connect(
            lambda: self._update_parameter_controls("fallback")
        )
        self.primary_reasoning_combo.currentIndexChanged.connect(
            lambda: self._update_parameter_controls("primary")
        )
        self.fallback_reasoning_combo.currentIndexChanged.connect(
            lambda: self._update_parameter_controls("fallback")
        )

        form.addRow("OpenRouter API Key", self.api_key_input)
        form.addRow("DeepSeek API Key", self.deepseek_api_key_input)
        codex_note = QLabel(
            "codex/ 모델은 설치된 Codex의 ChatGPT 로그인을 사용하며 API Key가 필요 없습니다."
        )
        codex_note.setWordWrap(True)
        form.addRow("Codex 요금제", codex_note)
        form.addRow("대상 언어", self.target_language_combo)
        form.addRow("기본 모델", self.primary_model_combo)
        form.addRow("기본 reasoning", self.primary_reasoning_combo)
        form.addRow("기본 적용 상태", self.primary_reasoning_status)
        form.addRow("기본 reasoning JSON", self.primary_reasoning_input)
        form.addRow("기본 파라미터 모드", self.primary_parameter_mode_combo)
        form.addRow("기본 파라미터 상태", self.primary_parameter_status)
        form.addRow("기본 temperature", self.primary_temperature_input)
        form.addRow("기본 top_p", self.primary_top_p_input)
        form.addRow("기본 추가 파라미터 JSON", self.primary_extra_parameters_input)
        form.addRow("폴백 모델", self.fallback_model_combo)
        form.addRow("폴백 reasoning", self.fallback_reasoning_combo)
        form.addRow("폴백 적용 상태", self.fallback_reasoning_status)
        form.addRow("폴백 reasoning JSON", self.fallback_reasoning_input)
        form.addRow("폴백 파라미터 모드", self.fallback_parameter_mode_combo)
        form.addRow("폴백 파라미터 상태", self.fallback_parameter_status)
        form.addRow("폴백 temperature", self.fallback_temperature_input)
        form.addRow("폴백 top_p", self.fallback_top_p_input)
        form.addRow("폴백 추가 파라미터 JSON", self.fallback_extra_parameters_input)

        swap_button = QPushButton("기본 ↔ 폴백 전체 스왑")
        swap_button.clicked.connect(self._swap_model_slots)
        form.addRow("모델 위치", swap_button)

        profile_buttons = QWidget()
        profile_buttons_layout = QGridLayout(profile_buttons)
        profile_buttons_layout.setContentsMargins(0, 0, 0, 0)
        save_primary_profile = QPushButton("기본 저장")
        save_fallback_profile = QPushButton("폴백 저장")
        load_primary_profile = QPushButton("기본에 적용")
        load_fallback_profile = QPushButton("폴백에 적용")
        delete_profile = QPushButton("삭제")
        save_primary_profile.clicked.connect(
            lambda: self._save_model_profile("primary")
        )
        save_fallback_profile.clicked.connect(
            lambda: self._save_model_profile("fallback")
        )
        load_primary_profile.clicked.connect(
            lambda: self._load_model_profile("primary")
        )
        load_fallback_profile.clicked.connect(
            lambda: self._load_model_profile("fallback")
        )
        delete_profile.clicked.connect(self._delete_model_profile)
        for index, button in enumerate(
            (
            save_primary_profile,
            save_fallback_profile,
            load_primary_profile,
            load_fallback_profile,
            delete_profile,
            )
        ):
            profile_buttons_layout.addWidget(button, index // 3, index % 3)
        form.addRow("프리셋 이름", self.profile_name_combo)
        form.addRow("모델 설정 프리셋", profile_buttons)
        form.addRow("Ctrl+C+C 간격", self.trigger_interval_input)
        form.addRow("API 타임아웃", self.timeout_input)
        form.addRow("자동 최대 너비", self.popup_width_input)
        form.addRow("자동 최대 높이", self.popup_height_input)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("취소")
        save_button = QPushButton("전체 설정 저장")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)

        root.addWidget(scroll)
        root.addLayout(buttons)
        self._refresh_all_statuses()
        self._update_parameter_controls("primary")
        self._update_parameter_controls("fallback")

    @staticmethod
    def _new_reasoning_editor() -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setPlaceholderText('{\n  "effort": "low"\n}')
        editor.setFixedHeight(82)
        return editor

    @staticmethod
    def _new_parameters_editor() -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setPlaceholderText('{\n  "top_k": 20,\n  "presence_penalty": 1.5\n}')
        editor.setFixedHeight(82)
        return editor

    @staticmethod
    def _new_status_label() -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        return label

    @staticmethod
    def _new_sampling_input(value: float, *, maximum: float) -> QDoubleSpinBox:
        editor = QDoubleSpinBox()
        editor.setRange(0.0, maximum)
        editor.setSingleStep(0.05)
        editor.setDecimals(2)
        editor.setValue(value)
        return editor

    def accept(self) -> None:
        if not self.primary_model_combo.currentText().strip():
            QMessageBox.warning(self, "설정 오류", "기본 모델을 입력해 주세요.")
            return
        try:
            self._reasoning_value(
                self.primary_reasoning_combo,
                self.primary_reasoning_input,
            )
            self._reasoning_value(
                self.fallback_reasoning_combo,
                self.fallback_reasoning_input,
            )
            self._parse_parameters_input(self.primary_extra_parameters_input)
            self._parse_parameters_input(self.fallback_extra_parameters_input)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "설정 오류", str(exc))
            return
        super().accept()

    def build_settings(self, current: AppSettings) -> AppSettings:
        primary_mode, primary_config = self._reasoning_value(
            self.primary_reasoning_combo,
            self.primary_reasoning_input,
        )
        fallback_mode, fallback_config = self._reasoning_value(
            self.fallback_reasoning_combo,
            self.fallback_reasoning_input,
        )
        return AppSettings(
            api_key=self.api_key_input.text().strip(),
            deepseek_api_key=self.deepseek_api_key_input.text().strip(),
            target_language_code=self.target_language_combo.currentData(),
            primary_model=self.primary_model_combo.currentText().strip(),
            primary_reasoning_mode=primary_mode,
            primary_reasoning_config=primary_config,
            primary_temperature=self.primary_temperature_input.value(),
            primary_top_p=self.primary_top_p_input.value(),
            primary_parameter_mode=str(
                self.primary_parameter_mode_combo.currentData()
            ),
            primary_extra_parameters=self._parse_parameters_input(
                self.primary_extra_parameters_input
            ),
            fallback_model=self.fallback_model_combo.currentText().strip(),
            fallback_reasoning_mode=fallback_mode,
            fallback_reasoning_config=fallback_config,
            fallback_temperature=self.fallback_temperature_input.value(),
            fallback_top_p=self.fallback_top_p_input.value(),
            fallback_parameter_mode=str(
                self.fallback_parameter_mode_combo.currentData()
            ),
            fallback_extra_parameters=self._parse_parameters_input(
                self.fallback_extra_parameters_input
            ),
            saved_model_profiles=deepcopy(self._saved_model_profiles),
            trigger_interval_ms=self.trigger_interval_input.value(),
            request_timeout_seconds=self.timeout_input.value(),
            fallback_on_provider_error_only=current.fallback_on_provider_error_only,
            cache_ttl_seconds=current.cache_ttl_seconds,
            clipboard_settle_poll_ms=current.clipboard_settle_poll_ms,
            clipboard_settle_timeout_ms=current.clipboard_settle_timeout_ms,
            popup_auto_max_width=self.popup_width_input.value(),
            popup_auto_max_height=self.popup_height_input.value(),
            always_pin_new_popups=current.always_pin_new_popups,
        )

    def _initialize_reasoning_controls(
        self,
        combo: QComboBox,
        editor: QPlainTextEdit,
        mode: str,
        config: dict[str, Any] | None,
    ) -> None:
        editor.setPlainText(self._format_reasoning_json(config))
        if mode == REASONING_MODE_AUTO:
            self._select_data(combo, AUTO_VALUE)
            editor.setEnabled(False)
            return

        effort = config.get("effort") if isinstance(config, dict) else None
        is_simple_effort = isinstance(config, dict) and set(config) == {"effort"}
        if is_simple_effort and combo.findData(effort) >= 0:
            self._select_data(combo, str(effort))
        else:
            self._select_data(combo, CUSTOM_VALUE)
        editor.setEnabled(True)

    def _reasoning_selection_changed(
        self,
        combo: QComboBox,
        editor: QPlainTextEdit,
        model_combo: QComboBox,
        status: QLabel,
    ) -> None:
        if self._syncing_reasoning:
            return
        value = str(combo.currentData() or "")
        editor.setEnabled(value != AUTO_VALUE)
        if value not in {AUTO_VALUE, CUSTOM_VALUE}:
            self._syncing_reasoning = True
            editor.setPlainText(json.dumps({"effort": value}, indent=2))
            self._syncing_reasoning = False
        self._update_reasoning_status(model_combo, combo, status)

    def _reasoning_text_changed(
        self,
        combo: QComboBox,
        editor: QPlainTextEdit,
    ) -> None:
        if self._syncing_reasoning or not editor.isEnabled():
            return
        self._syncing_reasoning = True
        self._select_data(combo, CUSTOM_VALUE)
        self._syncing_reasoning = False

    def _refresh_all_statuses(self) -> None:
        self._update_reasoning_status(
            self.primary_model_combo,
            self.primary_reasoning_combo,
            self.primary_reasoning_status,
        )
        self._update_reasoning_status(
            self.fallback_model_combo,
            self.fallback_reasoning_combo,
            self.fallback_reasoning_status,
        )

    def _update_parameter_controls(self, slot: str) -> None:
        mode_combo = getattr(self, f"{slot}_parameter_mode_combo")
        temperature_input = getattr(self, f"{slot}_temperature_input")
        top_p_input = getattr(self, f"{slot}_top_p_input")
        extra_input = getattr(self, f"{slot}_extra_parameters_input")
        status = getattr(self, f"{slot}_parameter_status")
        model_combo = getattr(self, f"{slot}_model_combo")
        model = model_combo.currentText().strip()
        if model.lower().startswith("codex/"):
            mode_combo.setEnabled(False)
            temperature_input.setEnabled(False)
            top_p_input.setEnabled(False)
            extra_input.setEnabled(False)
            status.setText("Codex 구독 → temperature/top_p 미전송, reasoning만 적용")
            return

        mode_combo.setEnabled(True)
        manual = mode_combo.currentData() == PARAMETER_MODE_MANUAL
        temperature_input.setEnabled(manual)
        top_p_input.setEnabled(manual)
        extra_input.setEnabled(manual)
        if manual:
            status.setText("수동 → 모델 지원 목록에 있는 항목만 전송")
            return

        reasoning_combo = getattr(self, f"{slot}_reasoning_combo")
        reasoning_input = getattr(self, f"{slot}_reasoning_input")
        try:
            _, reasoning = self._reasoning_value(reasoning_combo, reasoning_input)
        except (TypeError, ValueError):
            reasoning = None
        model = model_combo.currentText().strip()
        if reasoning_combo.currentData() == AUTO_VALUE:
            if model.lower().startswith("deepseek/"):
                reasoning = {"effort": "none"}
            else:
                reasoning = MODEL_CATALOG.reasoning_for(model).config
        recommended = recommended_parameters_for(model, reasoning)
        support = MODEL_CATALOG.supported_parameters_for(model)
        values = recommended.values
        omitted: set[str] = set()
        if support.metadata_known and not model.lower().startswith("deepseek/"):
            omitted = set(values) - set(support.supported)
            values = {key: value for key, value in values.items() if key not in omitted}
        details = ", ".join(f"{key}={value}" for key, value in values.items())
        summary = f"자동 → {recommended.label}"
        if details:
            summary += f": {details}"
        if omitted:
            summary += f" (미지원 제외: {', '.join(sorted(omitted))})"
        elif not support.metadata_known and not model.lower().startswith("deepseek/"):
            summary += " (모델 정보 갱신 후 지원항목 필터)"
        status.setText(summary)

    def _swap_model_slots(self) -> None:
        try:
            primary = self._capture_model_slot("primary")
            fallback = self._capture_model_slot("fallback")
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "스왑 오류", str(exc))
            return
        self._apply_model_slot("primary", fallback)
        self._apply_model_slot("fallback", primary)

    def _capture_model_slot(self, slot: str) -> dict[str, Any]:
        reasoning_mode, reasoning_config = self._reasoning_value(
            getattr(self, f"{slot}_reasoning_combo"),
            getattr(self, f"{slot}_reasoning_input"),
        )
        return {
            "model": getattr(self, f"{slot}_model_combo").currentText().strip(),
            "reasoning_mode": reasoning_mode,
            "reasoning_config": reasoning_config,
            "parameter_mode": str(
                getattr(self, f"{slot}_parameter_mode_combo").currentData()
            ),
            "temperature": getattr(self, f"{slot}_temperature_input").value(),
            "top_p": getattr(self, f"{slot}_top_p_input").value(),
            "extra_parameters": self._parse_parameters_input(
                getattr(self, f"{slot}_extra_parameters_input")
            ),
        }

    def _apply_model_slot(self, slot: str, profile: dict[str, Any]) -> None:
        self._select_or_set_text(
            getattr(self, f"{slot}_model_combo"),
            str(profile.get("model") or ""),
        )
        self._syncing_reasoning = True
        try:
            self._initialize_reasoning_controls(
                getattr(self, f"{slot}_reasoning_combo"),
                getattr(self, f"{slot}_reasoning_input"),
                str(profile.get("reasoning_mode") or REASONING_MODE_AUTO),
                profile.get("reasoning_config"),
            )
        finally:
            self._syncing_reasoning = False
        self._select_data(
            getattr(self, f"{slot}_parameter_mode_combo"),
            str(profile.get("parameter_mode") or PARAMETER_MODE_AUTO),
        )
        getattr(self, f"{slot}_temperature_input").setValue(
            float(profile.get("temperature", 1.0))
        )
        getattr(self, f"{slot}_top_p_input").setValue(
            float(profile.get("top_p", 1.0))
        )
        extra = profile.get("extra_parameters")
        if not isinstance(extra, dict):
            extra = {}
        getattr(self, f"{slot}_extra_parameters_input").setPlainText(
            self._format_parameters_json(extra)
        )
        self._update_reasoning_status(
            getattr(self, f"{slot}_model_combo"),
            getattr(self, f"{slot}_reasoning_combo"),
            getattr(self, f"{slot}_reasoning_status"),
        )
        self._update_parameter_controls(slot)

    def _save_model_profile(self, slot: str) -> None:
        try:
            profile = self._capture_model_slot(slot)
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "프리셋 저장 오류", str(exc))
            return
        name = self.profile_name_combo.currentText().strip() or profile["model"]
        if not name:
            QMessageBox.warning(self, "프리셋 저장 오류", "프리셋 이름을 입력해 주세요.")
            return
        self._saved_model_profiles[name] = profile
        self._persist_saved_profiles()
        self._refresh_profile_names()

    def _load_model_profile(self, slot: str) -> None:
        name = self.profile_name_combo.currentText().strip()
        profile = self._saved_model_profiles.get(name)
        if not isinstance(profile, dict):
            QMessageBox.warning(self, "프리셋 불러오기 오류", "저장된 프리셋을 선택해 주세요.")
            return
        self._apply_model_slot(slot, deepcopy(profile))

    def _delete_model_profile(self) -> None:
        name = self.profile_name_combo.currentText().strip()
        if name not in self._saved_model_profiles:
            return
        self._saved_model_profiles.pop(name)
        self._persist_saved_profiles()
        self._refresh_profile_names()

    def _persist_saved_profiles(self) -> None:
        self._current_settings.saved_model_profiles = deepcopy(
            self._saved_model_profiles
        )
        self._current_settings.save()

    def _refresh_profile_names(self, selected: str = "") -> None:
        self.profile_name_combo.clear()
        self.profile_name_combo.addItems(sorted(self._saved_model_profiles))
        if selected:
            self.profile_name_combo.setCurrentText(selected)
            return
        self.profile_name_combo.setCurrentIndex(-1)
        self.profile_name_combo.clearEditText()

    @staticmethod
    def _update_reasoning_status(
        model_combo: QComboBox,
        reasoning_combo: QComboBox,
        status: QLabel,
    ) -> None:
        selected = str(reasoning_combo.currentData() or "")
        if selected != AUTO_VALUE:
            status.setText(f"수동 → {selected if selected != CUSTOM_VALUE else 'JSON'}")
            return

        model = model_combo.currentText().strip()
        if model.lower().startswith("codex/"):
            status.setText("자동 → max")
            return
        if model.lower().startswith("deepseek/"):
            status.setText("자동 → thinking 끄기")
            return

        effective = MODEL_CATALOG.reasoning_for(model)
        if effective.metadata_known:
            status.setText(effective.summary)
        else:
            status.setText("자동 → 모델 정보 갱신 후 결정")

    @staticmethod
    def _reasoning_value(
        combo: QComboBox,
        editor: QPlainTextEdit,
    ) -> tuple[str, dict[str, Any] | None]:
        selected = str(combo.currentData() or "")
        if selected == AUTO_VALUE:
            config = SettingsDialog._parse_reasoning_input(editor, allow_empty=True)
            return REASONING_MODE_AUTO, config
        if selected not in {CUSTOM_VALUE, ""}:
            return REASONING_MODE_MANUAL, {"effort": selected}
        return (
            REASONING_MODE_MANUAL,
            SettingsDialog._parse_reasoning_input(editor, allow_empty=False),
        )

    @staticmethod
    def _parse_reasoning_input(
        editor: QPlainTextEdit,
        *,
        allow_empty: bool,
    ) -> dict[str, Any] | None:
        text = editor.toPlainText().strip()
        if not text:
            if allow_empty:
                return None
            raise ValueError("reasoning JSON을 입력해 주세요.")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"reasoning JSON 형식이 올바르지 않습니다: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise TypeError("reasoning JSON은 객체 형식이어야 합니다.")
        return value

    @staticmethod
    def _parse_parameters_input(editor: QPlainTextEdit) -> dict[str, Any]:
        text = editor.toPlainText().strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"추가 파라미터 JSON 형식이 올바르지 않습니다: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise TypeError("추가 파라미터 JSON은 객체 형식이어야 합니다.")
        reserved = {
            "model",
            "messages",
            "input",
            "instructions",
            "stream",
            "provider",
            "reasoning",
            "thinking",
            "max_tokens",
            "max_output_tokens",
            "temperature",
            "top_p",
        }
        conflicts = reserved.intersection(value)
        if conflicts:
            raise ValueError(
                "추가 파라미터에서 사용할 수 없는 항목: "
                + ", ".join(sorted(conflicts))
            )
        return value

    @staticmethod
    def _format_reasoning_json(config: dict[str, Any] | None) -> str:
        if not config:
            return ""
        return json.dumps(config, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_parameters_json(config: dict[str, Any]) -> str:
        if not config:
            return ""
        return json.dumps(config, ensure_ascii=False, indent=2)

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _select_or_set_text(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(value)
