from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from threading import Lock, RLock
from time import time
from typing import Any

import requests

from .settings import MODEL_METADATA_PATH

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
MODEL_METADATA_TTL_SECONDS = 6 * 60 * 60
CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 20.0

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EffectiveReasoning:
    config: dict[str, Any] | None
    summary: str
    metadata_known: bool


@dataclass(frozen=True)
class ParameterSupport:
    supported: frozenset[str]
    metadata_known: bool


class ModelCatalog:
    def __init__(self) -> None:
        self._models: dict[str, dict[str, Any]] = {}
        self._fetched_at = 0.0
        self._data_lock = RLock()
        self._refresh_lock = Lock()
        self._load_cache()

    @staticmethod
    def normalize_model_id(model: str) -> str:
        model_id = model.strip()
        if model_id.lower().startswith("openrouter/"):
            return model_id.split("/", 1)[1]
        return model_id

    def is_stale(self) -> bool:
        with self._data_lock:
            return time() - self._fetched_at > MODEL_METADATA_TTL_SECONDS

    def contains(self, model: str) -> bool:
        model_id = self.normalize_model_id(model)
        with self._data_lock:
            return model_id in self._models

    def ensure_model(self, model: str) -> None:
        if self.is_stale() or not self.contains(model):
            self.refresh()

    def refresh(self, *, force: bool = False) -> bool:
        if not force and not self.is_stale() and self._models:
            return True

        with self._refresh_lock:
            if not force and not self.is_stale() and self._models:
                return True
            try:
                response = requests.get(
                    OPENROUTER_MODELS_URL,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                    headers={"User-Agent": "QuickTranslate/1.0"},
                )
                response.raise_for_status()
                models = _parse_models_payload(response.json())
                if not models:
                    raise ValueError("OpenRouter model list was empty")
            except (requests.RequestException, ValueError, TypeError) as exc:
                LOGGER.warning("OpenRouter model metadata refresh failed: %s", exc)
                return False

            fetched_at = time()
            with self._data_lock:
                self._models = models
                self._fetched_at = fetched_at
            self._save_cache()
            LOGGER.info("OpenRouter model metadata refreshed: %d models", len(models))
            return True

    def reasoning_for(self, model: str) -> EffectiveReasoning:
        model_id = self.normalize_model_id(model)
        with self._data_lock:
            if model_id not in self._models:
                return EffectiveReasoning(None, "모델 정보 없음", False)
            model_info = self._models[model_id]

        reasoning = model_info.get("reasoning")

        if not isinstance(reasoning, dict):
            return EffectiveReasoning(None, "reasoning 미지원", True)
        return select_lowest_reasoning(reasoning)

    def supported_parameters_for(self, model: str) -> ParameterSupport:
        model_id = self.normalize_model_id(model)
        with self._data_lock:
            model_info = self._models.get(model_id)
        if model_info is None:
            return ParameterSupport(frozenset(), False)
        raw_supported = model_info.get("supported_parameters")
        if not isinstance(raw_supported, list):
            return ParameterSupport(frozenset(), True)
        return ParameterSupport(
            frozenset(str(value) for value in raw_supported if str(value)),
            True,
        )

    def _load_cache(self) -> None:
        try:
            payload = json.loads(MODEL_METADATA_PATH.read_text(encoding="utf-8"))
            if int(payload.get("schema_version") or 0) != 2:
                return
            raw_models = payload.get("models")
            fetched_at = float(payload.get("fetched_at") or 0)
            if not isinstance(raw_models, dict):
                return
            models = {
                str(model_id): model_info
                for model_id, model_info in raw_models.items()
                if isinstance(model_info, dict)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

        with self._data_lock:
            self._models = models
            self._fetched_at = fetched_at

    def _save_cache(self) -> None:
        MODEL_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._data_lock:
            payload = {
                "schema_version": 2,
                "fetched_at": self._fetched_at,
                "models": self._models,
            }
        temporary_path = MODEL_METADATA_PATH.with_suffix(".json.tmp")
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(MODEL_METADATA_PATH)
        except OSError as exc:
            LOGGER.warning("Could not save model metadata cache: %s", exc)


def _parse_models_payload(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TypeError("Invalid OpenRouter model metadata response")

    models: dict[str, dict[str, Any]] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        reasoning = item.get("reasoning")
        supported_parameters = item.get("supported_parameters")
        architecture = item.get("architecture")
        models[model_id] = {
            "reasoning": reasoning if isinstance(reasoning, dict) else None,
            "supported_parameters": (
                supported_parameters if isinstance(supported_parameters, list) else []
            ),
            "input_modalities": (
                architecture.get("input_modalities", [])
                if isinstance(architecture, dict)
                else []
            ),
        }
    return models


def select_lowest_reasoning(reasoning: dict[str, Any]) -> EffectiveReasoning:
    mandatory = bool(reasoning.get("mandatory"))
    supported = reasoning.get("supported_efforts")

    if supported is None:
        effort = "low" if mandatory else "none"
        return EffectiveReasoning({"effort": effort}, f"자동 → {effort}", True)

    if not isinstance(supported, list) or not supported:
        if not mandatory and reasoning.get("default_enabled") is False:
            return EffectiveReasoning(None, "자동 → 꺼짐(모델 기본값)", True)
        return EffectiveReasoning(None, "자동 조절 미지원", True)

    efforts = [str(value).strip() for value in supported if str(value).strip()]
    if not mandatory and "none" in efforts:
        return EffectiveReasoning({"effort": "none"}, "자동 → none", True)

    enabled_efforts = [effort for effort in efforts if effort != "none"]
    if not enabled_efforts:
        return EffectiveReasoning(None, "자동 조절 미지원", True)

    # OpenRouter returns supported_efforts from strongest to weakest. Choosing
    # the final item also supports effort names introduced after this release.
    effort = enabled_efforts[-1]
    return EffectiveReasoning({"effort": effort}, f"자동 → {effort}", True)


MODEL_CATALOG = ModelCatalog()
