"""Logical model routing with strict Luna Max-only enforcement."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class RoutingError(ValueError):
    """Raised for invalid or unsafe model-routing configuration."""


_MODELS = {"LUNA_MAX", "TERRA", "SOL", "PYTHON"}
LUNA_MAX = "LUNA_MAX"
TERRA = "TERRA"
SOL = "SOL"
PYTHON = "PYTHON"
_DEFAULT_SOL_THRESHOLDS = {"material_reduce_pp": 5.0, "material_target_change_pp": 10.0}
_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "model-routing.json"


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingError(f"{field_name} must be a non-empty string")
    return value.strip()


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoutingError(f"{field_name} must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise RoutingError(f"{field_name} must be finite and > 0")
    return value


def _model(value: Any, field_name: str) -> str:
    model = _text(value, field_name).upper()
    if model.startswith("LUNA") and model != "LUNA_MAX":
        raise RoutingError("Luna-family stages may target only LUNA_MAX")
    if model not in _MODELS:
        raise RoutingError(f"{field_name} must be one of {sorted(_MODELS)}")
    return model


@dataclass(frozen=True)
class ModelRouting:
    routing_policy_version: int
    mode: str
    luna_policy: str
    stages: Mapping[str, str]
    sol_thresholds: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.routing_policy_version, bool) or not isinstance(self.routing_policy_version, int) or self.routing_policy_version < 1:
            raise RoutingError("routing_policy_version must be a positive integer")
        mode = _text(self.mode, "mode").upper()
        if mode not in {"AUTO", "MANUAL"}:
            raise RoutingError("mode must be AUTO or MANUAL")
        policy = _text(self.luna_policy, "luna_policy").upper()
        if policy != "LUNA_MAX_ONLY":
            raise RoutingError("luna_policy must be LUNA_MAX_ONLY")
        if not isinstance(self.stages, Mapping) or not self.stages:
            raise RoutingError("stages must be a non-empty object")
        stages: dict[str, str] = {}
        for key, value in self.stages.items():
            name = _text(key, "stage")
            if name in stages:
                raise RoutingError(f"duplicate stage {name}")
            stages[name] = _model(value, f"stages.{name}")
        thresholds = self.sol_thresholds or _DEFAULT_SOL_THRESHOLDS
        if not isinstance(thresholds, Mapping):
            raise RoutingError("sol_thresholds must be an object")
        parsed_thresholds = {
            _text(key, "sol threshold"): _number(value, f"sol_thresholds.{key}")
            for key, value in thresholds.items()
        }
        required = {"material_reduce_pp", "material_target_change_pp"}
        if set(parsed_thresholds) != required:
            raise RoutingError("sol_thresholds must contain material_reduce_pp and material_target_change_pp")
        object.__setattr__(self, "routing_policy_version", self.routing_policy_version)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "luna_policy", policy)
        object.__setattr__(self, "stages", dict(stages))
        object.__setattr__(self, "sol_thresholds", dict(parsed_thresholds))

    def model_for_stage(self, stage: str) -> str:
        name = _text(stage, "stage")
        try:
            return self.stages[name]
        except KeyError as exc:
            raise RoutingError(f"unknown routing stage: {name}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "routing_policy_version": self.routing_policy_version,
            "mode": self.mode,
            "luna_policy": self.luna_policy,
            "stages": dict(self.stages),
            "sol_thresholds": dict(self.sol_thresholds),
        }


def validate_model_routing(value: Mapping[str, Any]) -> ModelRouting:
    if not isinstance(value, Mapping):
        raise RoutingError("model routing must be an object")
    allowed = {"routing_policy_version", "mode", "luna_policy", "stages", "sol_thresholds"}
    unknown = set(value) - allowed
    if unknown:
        raise RoutingError(f"model routing contains unknown fields: {', '.join(sorted(unknown))}")
    missing = (allowed - set(value)) - {"sol_thresholds"}
    if missing:
        raise RoutingError(f"model routing is missing fields: {', '.join(sorted(missing))}")
    return ModelRouting(
        routing_policy_version=value["routing_policy_version"],
        mode=value["mode"],
        luna_policy=value["luna_policy"],
        stages=value["stages"],
        sol_thresholds=value.get("sol_thresholds", _DEFAULT_SOL_THRESHOLDS),
    )


def load_model_routing(path: str | Path | None = None) -> ModelRouting:
    source = Path(path) if path is not None else _DEFAULT_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError(f"unable to load model routing from {source}: {exc}") from exc
    return validate_model_routing(data)


def model_for_stage(stage: str, *, routing: ModelRouting | None = None) -> str:
    return (routing or load_model_routing()).model_for_stage(stage)


def validate_stage_model(stage: str, model: str, *, routing: ModelRouting | None = None) -> bool:
    expected = model_for_stage(stage, routing=routing)
    actual = _model(model, f"model for {stage}")
    if actual != expected:
        raise RoutingError(f"stage {stage} is configured for {expected}, not {actual}")
    return True


def luna_max_only(model: str) -> bool:
    return _model(model, "model") == "LUNA_MAX"


def routing_metadata(
    stages_used: Mapping[str, str] | None = None,
    *,
    escalations: tuple[str, ...] | list[str] = (),
    sol_review_performed: bool = False,
    routing: ModelRouting | None = None,
) -> dict[str, Any]:
    resolved = routing or load_model_routing()
    used = dict(stages_used.stages if isinstance(stages_used, ModelRouting) else stages_used or {})
    for stage, model in used.items():
        validate_stage_model(stage, model, routing=resolved)
    if not isinstance(sol_review_performed, bool):
        raise RoutingError("sol_review_performed must be boolean")
    return {
        "routing_policy_version": resolved.routing_policy_version,
        "stages_used": used,
        "escalations": list(escalations),
        "sol_review_performed": sol_review_performed,
    }


load_routing_config = load_model_routing
validate_routing_config = validate_model_routing
stage_model = model_for_stage


__all__ = [
    "ModelRouting",
    "LUNA_MAX",
    "PYTHON",
    "RoutingError",
    "SOL",
    "TERRA",
    "load_model_routing",
    "load_routing_config",
    "luna_max_only",
    "model_for_stage",
    "routing_metadata",
    "stage_model",
    "validate_model_routing",
    "validate_routing_config",
    "validate_stage_model",
]
