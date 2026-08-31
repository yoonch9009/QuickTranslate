from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from quicktranslate.settings import (
    PARAMETER_MODE_MANUAL,
    REASONING_MODE_MANUAL,
    AppSettings,
)
from quicktranslate.settings_dialog import SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_edited_model_text_wins_over_stale_combo_item_data(self) -> None:
        current = AppSettings(always_pin_new_popups=True)
        dialog = SettingsDialog(current)
        primary = "openrouter/z-ai/glm-5.3-flash"
        fallback = "openrouter/z-ai/glm-5.3"

        dialog.primary_model_combo.setCurrentIndex(0)
        dialog.fallback_model_combo.setCurrentIndex(1)
        dialog.primary_model_combo.setEditText(primary)
        dialog.fallback_model_combo.setEditText(fallback)
        dialog.primary_temperature_input.setValue(1.0)
        dialog.primary_top_p_input.setValue(0.95)
        dialog.fallback_temperature_input.setValue(0.4)
        dialog.fallback_top_p_input.setValue(0.8)

        self.assertNotEqual(dialog.primary_model_combo.currentData(), primary)
        self.assertNotEqual(dialog.fallback_model_combo.currentData(), fallback)

        saved = dialog.build_settings(current)

        self.assertEqual(saved.primary_model, primary)
        self.assertEqual(saved.fallback_model, fallback)
        self.assertEqual(saved.primary_temperature, 1.0)
        self.assertEqual(saved.primary_top_p, 0.95)
        self.assertEqual(saved.fallback_temperature, 0.4)
        self.assertEqual(saved.fallback_top_p, 0.8)
        self.assertTrue(saved.always_pin_new_popups)
        dialog.deleteLater()

    def test_swap_moves_model_and_all_parameters_together(self) -> None:
        current = AppSettings(
            primary_model="qwen/qwen3.8-flash",
            primary_reasoning_mode=REASONING_MODE_MANUAL,
            primary_reasoning_config={"effort": "none"},
            primary_parameter_mode=PARAMETER_MODE_MANUAL,
            primary_temperature=0.7,
            primary_top_p=0.8,
            primary_extra_parameters={"top_k": 20},
            fallback_model="openrouter/z-ai/glm-5.3-flash",
            fallback_reasoning_mode=REASONING_MODE_MANUAL,
            fallback_reasoning_config={"effort": "low"},
            fallback_parameter_mode=PARAMETER_MODE_MANUAL,
            fallback_temperature=1.0,
            fallback_top_p=0.95,
        )
        dialog = SettingsDialog(current)

        dialog._swap_model_slots()
        saved = dialog.build_settings(current)

        self.assertEqual(saved.primary_model, "openrouter/z-ai/glm-5.3-flash")
        self.assertEqual(saved.primary_temperature, 1.0)
        self.assertEqual(saved.primary_top_p, 0.95)
        self.assertEqual(saved.primary_reasoning_config, {"effort": "low"})
        self.assertEqual(saved.fallback_model, "qwen/qwen3.8-flash")
        self.assertEqual(saved.fallback_temperature, 0.7)
        self.assertEqual(saved.fallback_top_p, 0.8)
        self.assertEqual(saved.fallback_reasoning_config, {"effort": "none"})
        self.assertEqual(saved.fallback_extra_parameters, {"top_k": 20})
        dialog.deleteLater()

    def test_named_profile_saves_and_loads_complete_slot(self) -> None:
        current = AppSettings(
            primary_model="qwen/qwen3.8-flash",
            primary_parameter_mode=PARAMETER_MODE_MANUAL,
            primary_temperature=0.7,
            primary_top_p=0.8,
            primary_extra_parameters={"top_k": 20},
        )
        dialog = SettingsDialog(current)
        dialog.profile_name_combo.setCurrentText("Qwen 번역")

        with patch.object(current, "save") as save:
            dialog._save_model_profile("primary")

        self.assertEqual(save.call_count, 1)
        self.assertEqual(dialog.profile_name_combo.currentText(), "")
        dialog.primary_model_combo.setEditText("openrouter/z-ai/glm-5.3-flash")
        dialog.profile_name_combo.setCurrentText("Qwen 번역")
        dialog._load_model_profile("primary")
        restored = dialog._capture_model_slot("primary")
        self.assertEqual(restored["model"], "qwen/qwen3.8-flash")
        self.assertEqual(restored["temperature"], 0.7)
        self.assertEqual(restored["top_p"], 0.8)
        self.assertEqual(restored["extra_parameters"], {"top_k": 20})
        dialog.deleteLater()

    def test_primary_then_fallback_save_creates_two_distinct_profiles(self) -> None:
        current = AppSettings(
            primary_model="qwen/qwen3.8-flash",
            primary_temperature=0.7,
            fallback_model="openrouter/z-ai/glm-5.3-flash",
            fallback_temperature=1.0,
        )
        dialog = SettingsDialog(current)

        with patch.object(current, "save"):
            dialog._save_model_profile("primary")
            self.assertEqual(dialog.profile_name_combo.currentText(), "")
            dialog._save_model_profile("fallback")

        profiles = dialog._saved_model_profiles
        self.assertEqual(len(profiles), 2)
        self.assertEqual(
            profiles["qwen/qwen3.8-flash"]["model"],
            "qwen/qwen3.8-flash",
        )
        self.assertEqual(
            profiles["openrouter/z-ai/glm-5.3-flash"]["model"],
            "openrouter/z-ai/glm-5.3-flash",
        )
        self.assertEqual(profiles["qwen/qwen3.8-flash"]["temperature"], 0.7)
        self.assertEqual(
            profiles["openrouter/z-ai/glm-5.3-flash"]["temperature"],
            1.0,
        )
        dialog.deleteLater()

    def test_codex_subscription_shows_max_and_disables_sampling_controls(self) -> None:
        dialog = SettingsDialog(AppSettings(primary_model="codex/gpt-5.6-luna"))

        self.assertEqual(dialog.primary_reasoning_status.text(), "자동 → max")
        self.assertFalse(dialog.primary_parameter_mode_combo.isEnabled())
        self.assertFalse(dialog.primary_temperature_input.isEnabled())
        self.assertFalse(dialog.primary_top_p_input.isEnabled())
        self.assertIn("reasoning만 적용", dialog.primary_parameter_status.text())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
