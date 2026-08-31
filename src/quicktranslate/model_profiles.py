from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecommendedParameters:
    values: dict[str, Any]
    label: str


def recommended_parameters_for(
    model: str,
    reasoning: dict[str, Any] | None,
) -> RecommendedParameters:
    model_id = model.strip().lower()
    if model_id.startswith("openrouter/"):
        model_id = model_id.split("/", 1)[1]

    if model_id == "qwen/qwen3.8-flash":
        if _thinking_enabled(reasoning):
            return RecommendedParameters(
                {
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "top_k": 20,
                    "min_p": 0.0,
                    "presence_penalty": 0.0,
                    "repetition_penalty": 1.0,
                },
                "Qwen thinking 권장값",
            )
        return RecommendedParameters(
            {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repetition_penalty": 1.0,
            },
            "Qwen non-thinking 권장값",
        )

    if model_id == "z-ai/glm-5.3-flash":
        return RecommendedParameters(
            {"temperature": 1.0, "top_p": 0.95},
            "GLM 5.3 Flash 권장값",
        )

    if model_id.endswith("deepseek-v4-flash-vision-exp"):
        return RecommendedParameters(
            {"temperature": 1.3},
            "DeepSeek 번역 권장값",
        )

    return RecommendedParameters({}, "등록된 권장값 없음")


def _thinking_enabled(reasoning: dict[str, Any] | None) -> bool:
    if not reasoning:
        return False
    if reasoning.get("enabled") is False:
        return False
    return str(reasoning.get("effort") or "").lower() != "none"
