from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quicktranslate.settings import AppSettings


class SettingsTests(unittest.TestCase):
    def test_custom_model_ids_survive_save_and_reload(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            app_dir = Path(temporary_directory)
            settings_path = app_dir / "settings.json"
            settings = AppSettings(
                primary_model="openrouter/z-ai/glm-5.3-flash",
                fallback_model="openrouter/z-ai/glm-5.3",
                saved_model_profiles={
                    "GLM": {
                        "model": "openrouter/z-ai/glm-5.3-flash",
                        "temperature": 1.0,
                    }
                },
                always_pin_new_popups=True,
            )

            with (
                patch("quicktranslate.settings.APP_DIR", app_dir),
                patch("quicktranslate.settings.SETTINGS_PATH", settings_path),
            ):
                settings.save()
                loaded = AppSettings.load()

            self.assertEqual(loaded.primary_model, settings.primary_model)
            self.assertEqual(loaded.fallback_model, settings.fallback_model)
            self.assertEqual(loaded.saved_model_profiles, settings.saved_model_profiles)
            self.assertTrue(loaded.always_pin_new_popups)

    def test_legacy_temperature_migrates_to_both_models(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            app_dir = Path(temporary_directory)
            settings_path = app_dir / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "temperature": 0.25,
                        "fallback_model": "deepseek/deepseek-v4-flash",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("quicktranslate.settings.APP_DIR", app_dir),
                patch("quicktranslate.settings.SETTINGS_PATH", settings_path),
            ):
                loaded = AppSettings.load()

            self.assertEqual(loaded.primary_temperature, 0.25)
            self.assertEqual(loaded.fallback_temperature, 0.25)
            self.assertEqual(loaded.primary_top_p, 0.95)
            self.assertEqual(loaded.fallback_top_p, 0.95)
            self.assertEqual(
                loaded.fallback_model,
                "deepseek/deepseek-v4-flash-vision-exp",
            )


if __name__ == "__main__":
    unittest.main()
