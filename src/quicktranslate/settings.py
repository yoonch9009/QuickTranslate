from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_DIR = Path.home() / "AppData" / "Roaming" / "QuickTranslate"
SETTINGS_PATH = APP_DIR / "settings.json"


@dataclass
class AppSettings:
    api_key: str = ""
    target_language_code: str = "ko"
    primary_model: str = "qwen/qwen3.5-flash-02-23"
    primary_reasoning_config: dict[str, Any] | None = field(default_factory=lambda: {"effort": "none"})
    fallback_model: str = "google/gemma-4-26b-a4b-it"
    fallback_reasoning_config: dict[str, Any] | None = field(default_factory=lambda: {"effort": "none"})
    trigger_interval_ms: int = 800
    request_timeout_seconds: int = 20
    fallback_on_provider_error_only: bool = True
    cache_ttl_seconds: int = 300
    clipboard_settle_poll_ms: int = 10
    clipboard_settle_timeout_ms: int = 80
    popup_auto_max_width: int = 620
    popup_auto_max_height: int = 520

    @classmethod
    def load(cls) -> "AppSettings":
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            settings = cls()
            settings.save()
            return settings

        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()

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

        defaults = asdict(cls())
        defaults.update(data)
        return cls(**defaults)

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


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
    ("qwen/qwen3.5-flash-02-23", "qwen/qwen3.5-flash-02-23"),
    ("google/gemma-4-26b-a4b-it", "google/gemma-4-26b-a4b-it"),
]
