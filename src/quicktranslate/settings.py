from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_DIR = Path.home() / "AppData" / "Roaming" / "QuickTranslate"
SETTINGS_PATH = APP_DIR / "settings.json"
MODEL_METADATA_PATH = APP_DIR / "openrouter-models.json"
LOG_PATH = APP_DIR / "quicktranslate.log"
LOCK_PATH = APP_DIR / "quicktranslate.lock"

REASONING_MODE_AUTO = "auto"
REASONING_MODE_MANUAL = "manual"
PARAMETER_MODE_AUTO = "auto"
PARAMETER_MODE_MANUAL = "manual"


@dataclass
class AppSettings:
    api_key: str = ""
    deepseek_api_key: str = ""
    target_language_code: str = "ko"
    primary_model: str = "openrouter/z-ai/glm-5.3-flash"
    primary_reasoning_mode: str = REASONING_MODE_AUTO
    primary_reasoning_config: dict[str, Any] | None = field(
        default_factory=lambda: {"effort": "low"}
    )
    primary_temperature: float = 1.0
    primary_top_p: float = 0.95
    primary_parameter_mode: str = PARAMETER_MODE_AUTO
    primary_extra_parameters: dict[str, Any] = field(default_factory=dict)
    fallback_model: str = "deepseek/deepseek-v4-flash-vision-exp"
    fallback_reasoning_mode: str = REASONING_MODE_AUTO
    fallback_reasoning_config: dict[str, Any] | None = field(
        default_factory=lambda: {"effort": "none"}
    )
    fallback_temperature: float = 1.0
    fallback_top_p: float = 0.95
    fallback_parameter_mode: str = PARAMETER_MODE_AUTO
    fallback_extra_parameters: dict[str, Any] = field(default_factory=dict)
    saved_model_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    trigger_interval_ms: int = 800
    request_timeout_seconds: int = 20
    fallback_on_provider_error_only: bool = True
    cache_ttl_seconds: int = 300
    clipboard_settle_poll_ms: int = 10
    clipboard_settle_timeout_ms: int = 80
    popup_auto_max_width: int = 620
    popup_auto_max_height: int = 520
    always_pin_new_popups: bool = False

    @classmethod
    def load(cls) -> AppSettings:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            settings = cls()
            settings.save()
            return settings

        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()

        if not isinstance(data, dict):
            return cls()

        _migrate_legacy_settings(data)
        defaults = asdict(cls())
        defaults.update({key: value for key, value in data.items() if key in defaults})
        return cls(**defaults)

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = SETTINGS_PATH.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(SETTINGS_PATH)


def _migrate_legacy_settings(data: dict[str, Any]) -> None:
    if "temperature" in data:
        legacy_temperature = data.pop("temperature")
        data.setdefault("primary_temperature", legacy_temperature)
        data.setdefault("fallback_temperature", legacy_temperature)
    data.setdefault("primary_top_p", 0.95)
    data.setdefault("fallback_top_p", 0.95)
    data.setdefault("primary_parameter_mode", PARAMETER_MODE_AUTO)
    data.setdefault("fallback_parameter_mode", PARAMETER_MODE_AUTO)
    data.setdefault("primary_extra_parameters", {})
    data.setdefault("fallback_extra_parameters", {})
    data.setdefault("saved_model_profiles", {})
    for key in (
        "primary_extra_parameters",
        "fallback_extra_parameters",
        "saved_model_profiles",
    ):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    for key in ("primary_parameter_mode", "fallback_parameter_mode"):
        if data.get(key) not in {PARAMETER_MODE_AUTO, PARAMETER_MODE_MANUAL}:
            data[key] = PARAMETER_MODE_AUTO
    if "popup_width" in data and "popup_auto_max_width" not in data:
        data["popup_auto_max_width"] = data.pop("popup_width")
    if "popup_height" in data and "popup_auto_max_height" not in data:
        data["popup_auto_max_height"] = data.pop("popup_height")
    if "primary_reasoning_effort" in data and "primary_reasoning_config" not in data:
        effort = str(data.pop("primary_reasoning_effort") or "").strip()
        data["primary_reasoning_config"] = {"effort": effort} if effort else None
    if "fallback_reasoning_effort" in data and "fallback_reasoning_config" not in data:
        effort = str(data.pop("fallback_reasoning_effort") or "").strip()
        data["fallback_reasoning_config"] = {"effort": effort} if effort else None

    # Older builds had no true automatic mode. Existing values remain available
    # as manual presets, while the new default follows live model metadata.
    data.setdefault("primary_reasoning_mode", REASONING_MODE_AUTO)
    data.setdefault("fallback_reasoning_mode", REASONING_MODE_AUTO)

    for key in ("primary_model", "fallback_model"):
        if key in data:
            data[key] = migrate_model_name(str(data[key]))
    if data.get("fallback_model") == "deepseek/deepseek-v4-flash":
        data["fallback_model"] = "deepseek/deepseek-v4-flash-vision-exp"


LANGUAGE_OPTIONS: list[tuple[str, str]] = [
    ("한국어", "ko"),
    ("English", "en"),
    ("日本語", "ja"),
    ("中文 (简体)", "zh-CN"),
    ("中文 (繁體)", "zh-TW"),
    ("Español", "es"),
    ("Français", "fr"),
    ("Deutsch", "de"),
    ("Português", "pt"),
    ("Italiano", "it"),
]

MODEL_OPTIONS: list[tuple[str, str]] = [
    ("codex/gpt-5.6-luna", "codex/gpt-5.6-luna"),
    ("qwen/qwen3.8-flash", "qwen/qwen3.8-flash"),
    ("openrouter/z-ai/glm-5.3-flash", "openrouter/z-ai/glm-5.3-flash"),
    (
        "openrouter/deepseek/deepseek-v4-flash-vision-exp",
        "openrouter/deepseek/deepseek-v4-flash-vision-exp",
    ),
    (
        "deepseek/deepseek-v4-flash-vision-exp",
        "deepseek/deepseek-v4-flash-vision-exp",
    ),
    ("openrouter/tencent/hy3-preview", "openrouter/tencent/hy3-preview"),
    ("deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
    ("deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
    ("openrouter/deepseek/deepseek-v4-flash", "openrouter/deepseek/deepseek-v4-flash"),
]

_LEGACY_MODEL_ALIASES: dict[str, str] = {
    "deepseek-v4-flash-vision-exp": "deepseek/deepseek-v4-flash-vision-exp",
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
    "tencent/hy3-preview": "openrouter/tencent/hy3-preview",
    "qwen/qwen3.5-flash-02-23": "openrouter/qwen/qwen3.5-flash-02-23",
    "google/gemma-4-26b-a4b-it": "openrouter/google/gemma-4-26b-a4b-it",
}


def migrate_model_name(model: str) -> str:
    name = model.strip()
    if name.startswith(("codex/", "deepseek/", "openrouter/")):
        return name
    return _LEGACY_MODEL_ALIASES.get(name, name)
