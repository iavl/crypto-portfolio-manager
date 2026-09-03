"""Configurable model routing with explicit runtime capability resolution."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, TextIO


class RoutingError(ValueError):
    """Raised for invalid or unsafe model-routing configuration."""


LUNA_MAX = "LUNA_MAX"
TERRA = "TERRA"
SOL = "SOL"
PYTHON = "PYTHON"

REASONING_EFFORTS = ("inherit", "none", "low", "medium", "high", "xhigh", "max")
RUNTIMES = ("AUTO", "CODEX", "CHATGPT", "OPENAI_API", "GENERIC")
PYTHON_STAGES = frozenset(
    {
        "history",
        "facts",
        "metrics_math",
        "technical",
        "scoring_math",
        "regime",
        "allocation",
        "risk",
        "rebalance",
        "execution",
    }
)
LLM_STAGES = frozenset(
    {
        "screenshot_extraction",
        "metric_collection",
        "normal_source_retrieval",
        "source_conflict_resolution",
        "factor_semantic_analysis",
        "report_generation",
        "major_event_analysis",
        "high_impact_final_review",
    }
)
REQUIRED_STAGES = PYTHON_STAGES | LLM_STAGES

_DEFAULT_SOL_THRESHOLDS = {"material_reduce_pp": 5.0, "material_target_change_pp": 10.0}
_DEFAULT_RUNTIME_FALLBACK = {
    "unavailable_model": "current_session",
    "unsupported_reasoning": "inherit",
    "log_fallback": True,
}
_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "model-routing.json"
_V1_PRESETS = {
    LUNA_MAX: "luna_max",
    TERRA: "terra_medium",
    SOL: "sol_medium",
    PYTHON: "python",
}
_V1_MODELS = set(_V1_PRESETS)


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


def _effort(value: Any, field_name: str, *, allow_null: bool = False) -> str | None:
    if value is None and allow_null:
        return None
    value = _text(value, field_name).lower()
    if value not in REASONING_EFFORTS:
        raise RoutingError(f"{field_name} must be one of {list(REASONING_EFFORTS)}")
    return value


def _runtime(value: Any, field_name: str = "runtime") -> str:
    value = _text(value, field_name).upper()
    if value not in RUNTIMES:
        raise RoutingError(f"{field_name} must be one of {list(RUNTIMES)}")
    return value


def _legacy_model(value: Any, field_name: str) -> str:
    model = _text(value, field_name).upper()
    if model.startswith("LUNA") and model != LUNA_MAX:
        raise RoutingError("Luna-family stages may target only LUNA_MAX")
    if model not in _V1_MODELS:
        raise RoutingError(f"{field_name} must be one of {sorted(_V1_MODELS)}")
    return model


@dataclass(frozen=True)
class ModelSpec:
    """A named model preset requested by a routing profile."""

    name: str
    provider: str
    family: str
    model: str | None
    reasoning_effort: str | None
    supported_reasoning_efforts: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        name = _text(self.name, "model preset")
        provider = _text(self.provider, f"models.{name}.provider")
        family = _text(self.family, f"models.{name}.family").upper()
        model = self.model
        if model is not None:
            model = _text(model, f"models.{name}.model")
        effort = _effort(
            self.reasoning_effort,
            f"models.{name}.reasoning_effort",
            allow_null=True,
        )
        if family == "PYTHON":
            if model is not None or effort is not None:
                raise RoutingError("PYTHON model presets must use model=null and reasoning_effort=null")
        elif family == "CURRENT_SESSION":
            if model != "CURRENT_SESSION":
                raise RoutingError("CURRENT_SESSION model presets must use model=CURRENT_SESSION")
            if effort is None:
                effort = "inherit"
            if effort != "inherit":
                raise RoutingError("CURRENT_SESSION model presets must use reasoning_effort=inherit")
        elif model is None:
            raise RoutingError(f"models.{name}.model is required for non-PYTHON presets")
        if family.startswith("LUNA") and effort != "max":
            raise RoutingError("Luna-family model must use reasoning_effort=max")
        supported = self.supported_reasoning_efforts
        if supported is not None:
            if not isinstance(supported, (tuple, list)):
                raise RoutingError(f"models.{name}.supported_reasoning_efforts must be an array")
            parsed = tuple(
                _effort(item, f"models.{name}.supported_reasoning_efforts")
                for item in supported
            )
            if len(parsed) != len(set(parsed)):
                raise RoutingError(f"models.{name}.supported_reasoning_efforts must be unique")
            if effort is not None and effort not in parsed:
                raise RoutingError(
                    f"models.{name}.reasoning_effort {effort} is not in supported_reasoning_efforts"
                )
            supported = parsed
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "reasoning_effort", effort)
        object.__setattr__(self, "supported_reasoning_efforts", supported)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": self.provider,
            "family": self.family,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
        }
        if self.supported_reasoning_efforts is not None:
            result["supported_reasoning_efforts"] = list(self.supported_reasoning_efforts)
        return result


@dataclass(frozen=True)
class StageRoute:
    stage: str
    preset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _text(self.stage, "stage"))
        object.__setattr__(self, "preset", _text(self.preset, f"stages.{self.stage}"))


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Capabilities supplied by the host; no host behavior is inferred."""

    runtime: str
    can_select_model_per_stage: bool
    can_set_reasoning_effort: bool
    available_models: tuple[str, ...] | None = None
    supported_reasoning_efforts: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        runtime = _runtime(self.runtime)
        if not isinstance(self.can_select_model_per_stage, bool):
            raise RoutingError("can_select_model_per_stage must be boolean")
        if not isinstance(self.can_set_reasoning_effort, bool):
            raise RoutingError("can_set_reasoning_effort must be boolean")
        available = self.available_models
        if available is not None:
            if not isinstance(available, (tuple, list)):
                raise RoutingError("available_models must be an array or null")
            available = tuple(_text(item, "available_models") for item in available)
            if len(available) != len(set(available)):
                raise RoutingError("available_models must be unique")
        supported = self.supported_reasoning_efforts
        if supported is not None:
            if not isinstance(supported, Mapping):
                raise RoutingError("supported_reasoning_efforts must be an object or null")
            parsed: dict[str, tuple[str, ...]] = {}
            for model, values in supported.items():
                model_name = _text(model, "supported_reasoning_efforts model")
                if not isinstance(values, (tuple, list)):
                    raise RoutingError(
                        f"supported_reasoning_efforts.{model_name} must be an array"
                    )
                efforts = tuple(
                    _effort(item, f"supported_reasoning_efforts.{model_name}")
                    for item in values
                )
                if len(efforts) != len(set(efforts)):
                    raise RoutingError(
                        f"supported_reasoning_efforts.{model_name} must be unique"
                    )
                parsed[model_name] = efforts
            supported = parsed
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "available_models", available)
        object.__setattr__(self, "supported_reasoning_efforts", supported)


@dataclass(frozen=True)
class ResolvedStageRoute:
    stage: str
    requested_preset: str
    requested_model: str | None
    requested_reasoning_effort: str | None
    effective_model: str | None
    effective_reasoning_effort: str | None
    runtime: str
    fallback_used: bool
    fallback_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _text(self.stage, "stage"))
        object.__setattr__(self, "requested_preset", _text(self.requested_preset, "requested_preset"))
        object.__setattr__(self, "runtime", _runtime(self.runtime))
        if not isinstance(self.fallback_used, bool):
            raise RoutingError("fallback_used must be boolean")
        if self.fallback_used:
            if not self.fallback_reason:
                raise RoutingError("fallback_reason is required when fallback_used is true")
        elif self.fallback_reason is not None:
            raise RoutingError("fallback_reason must be null when fallback_used is false")

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_preset": self.requested_preset,
            "requested_model": self.requested_model,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "effective_model": self.effective_model,
            "effective_reasoning_effort": self.effective_reasoning_effort,
            "runtime": self.runtime,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


def _builtin_models() -> dict[str, ModelSpec]:
    raw = {
        "luna_max": ("openai", "LUNA", "gpt-5.6-luna", "max"),
        "terra_low": ("openai", "TERRA", "gpt-5.6-terra", "low"),
        "terra_medium": ("openai", "TERRA", "gpt-5.6-terra", "medium"),
        "terra_high": ("openai", "TERRA", "gpt-5.6-terra", "high"),
        "sol_medium": ("openai", "SOL", "gpt-5.6-sol", "medium"),
        "sol_high": ("openai", "SOL", "gpt-5.6-sol", "high"),
        "sol_xhigh": ("openai", "SOL", "gpt-5.6-sol", "xhigh"),
        "current_session": ("runtime", "CURRENT_SESSION", "CURRENT_SESSION", "inherit"),
        "python": ("local", "PYTHON", None, None),
    }
    return {
        name: ModelSpec(name, provider, family, model, effort)
        for name, (provider, family, model, effort) in raw.items()
    }


def _profile_stages(stages: Mapping[str, Any], field_name: str) -> dict[str, str]:
    if not isinstance(stages, Mapping):
        raise RoutingError(f"{field_name} must be an object")
    result: dict[str, str] = {}
    for stage, preset in stages.items():
        name = _text(stage, "stage")
        if name in result:
            raise RoutingError(f"duplicate stage {name}")
        result[name] = _text(preset, f"{field_name}.{name}")
    return result


def _profiles(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise RoutingError("profiles must be a non-empty object")
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_profile in value.items():
        name = _text(raw_name, "profile")
        if not isinstance(raw_profile, Mapping):
            raise RoutingError(f"profiles.{name} must be an object")
        unknown = set(raw_profile) - {"description", "extends", "stages"}
        if unknown:
            raise RoutingError(
                f"profiles.{name} contains unknown fields: {', '.join(sorted(unknown))}"
            )
        profile: dict[str, Any] = {
            "stages": _profile_stages(raw_profile.get("stages", {}), f"profiles.{name}.stages")
        }
        if "description" in raw_profile:
            profile["description"] = _text(raw_profile["description"], f"profiles.{name}.description")
        if raw_profile.get("extends") is not None:
            profile["extends"] = _text(raw_profile["extends"], f"profiles.{name}.extends")
        result[name] = profile
    return result


def _resolve_profile_stages(
    profiles: Mapping[str, Mapping[str, Any]],
    profile_name: str,
    *,
    trail: tuple[str, ...] = (),
) -> dict[str, str]:
    if profile_name not in profiles:
        raise RoutingError(f"Unknown profile: {profile_name}")
    if profile_name in trail:
        cycle = " -> ".join((*trail, profile_name))
        raise RoutingError(f"Profile inheritance cycle: {cycle}")
    profile = profiles[profile_name]
    result: dict[str, str] = {}
    parent = profile.get("extends")
    if parent is not None:
        result.update(_resolve_profile_stages(profiles, parent, trail=(*trail, profile_name)))
    result.update(profile.get("stages", {}))
    return result


def _normalize_models(value: Mapping[str, Any]) -> dict[str, ModelSpec]:
    if not isinstance(value, Mapping) or not value:
        raise RoutingError("models must be a non-empty object")
    result: dict[str, ModelSpec] = {}
    for raw_name, raw_spec in value.items():
        name = _text(raw_name, "model preset")
        if isinstance(raw_spec, ModelSpec):
            spec = raw_spec
            if spec.name != name:
                raise RoutingError(f"model preset name mismatch for {name}")
        else:
            if not isinstance(raw_spec, Mapping):
                raise RoutingError(f"models.{name} must be an object")
            unknown = set(raw_spec) - {
                "provider",
                "family",
                "model",
                "reasoning_effort",
                "supported_reasoning_efforts",
            }
            if unknown:
                raise RoutingError(
                    f"models.{name} contains unknown fields: {', '.join(sorted(unknown))}"
                )
            missing = {"provider", "family", "model", "reasoning_effort"} - set(raw_spec)
            if missing:
                raise RoutingError(f"models.{name} is missing fields: {', '.join(sorted(missing))}")
            spec = ModelSpec(name=name, **dict(raw_spec))
        result[name] = spec
    return result


def _normalize_thresholds(value: Mapping[str, Any] | None) -> dict[str, float]:
    value = _DEFAULT_SOL_THRESHOLDS if value is None or not value else value
    if not isinstance(value, Mapping):
        raise RoutingError("sol_thresholds must be an object")
    parsed = {
        _text(key, "sol threshold"): _number(raw, f"sol_thresholds.{key}")
        for key, raw in value.items()
    }
    required = set(_DEFAULT_SOL_THRESHOLDS)
    if set(parsed) != required:
        raise RoutingError(
            "sol_thresholds must contain material_reduce_pp and material_target_change_pp"
        )
    return parsed


def _normalize_fallback(value: Mapping[str, Any] | None) -> dict[str, Any]:
    value = _DEFAULT_RUNTIME_FALLBACK if value is None or not value else value
    if not isinstance(value, Mapping):
        raise RoutingError("runtime_fallback must be an object")
    unknown = set(value) - set(_DEFAULT_RUNTIME_FALLBACK)
    if unknown:
        raise RoutingError(
            f"runtime_fallback contains unknown fields: {', '.join(sorted(unknown))}"
        )
    result = dict(_DEFAULT_RUNTIME_FALLBACK)
    result.update(value)
    result["unavailable_model"] = _text(
        result["unavailable_model"], "runtime_fallback.unavailable_model"
    )
    result["unsupported_reasoning"] = _text(
        result["unsupported_reasoning"], "runtime_fallback.unsupported_reasoning"
    ).lower()
    if not isinstance(result["log_fallback"], bool):
        raise RoutingError("runtime_fallback.log_fallback must be boolean")
    return result


def _logical_model(spec: ModelSpec) -> str:
    if spec.family.startswith("LUNA"):
        return LUNA_MAX
    if spec.family == "TERRA":
        return TERRA
    if spec.family == "SOL":
        return SOL
    if spec.family == "PYTHON":
        return PYTHON
    if spec.family == "CURRENT_SESSION":
        return "CURRENT_SESSION"
    return spec.family


@dataclass(frozen=True)
class ModelRouting:
    """Validated v2 routing, with the old constructor/fields retained."""

    routing_policy_version: int
    mode: str | None = None
    luna_policy: str = "LUNA_MAX_ONLY"
    stages: Mapping[str, str] | None = None
    sol_thresholds: Mapping[str, float] = field(default_factory=dict)
    runtime: str = "AUTO"
    default_profile: str = "balanced"
    models: Mapping[str, ModelSpec | Mapping[str, Any]] = field(default_factory=dict)
    profiles: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    runtime_fallback: Mapping[str, Any] = field(default_factory=dict)
    active_profile: str | None = None
    run_overrides: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.routing_policy_version, bool)
            or not isinstance(self.routing_policy_version, int)
            or self.routing_policy_version < 1
        ):
            raise RoutingError("routing_policy_version must be a positive integer")
        policy = _text(self.luna_policy, "luna_policy").upper()
        if policy != "LUNA_MAX_ONLY":
            raise RoutingError("luna_policy must be LUNA_MAX_ONLY")
        thresholds = _normalize_thresholds(self.sol_thresholds)
        fallback = _normalize_fallback(self.runtime_fallback)

        legacy = self.routing_policy_version == 1
        if legacy:
            if not isinstance(self.stages, Mapping) or not self.stages:
                raise RoutingError("stages must be a non-empty object")
            old_mode = _text(self.mode or "AUTO", "mode").upper()
            if old_mode not in {"AUTO", "MANUAL"}:
                raise RoutingError("mode must be AUTO or MANUAL")
            legacy_stages = {
                _text(stage, "stage"): _legacy_model(value, f"stages.{stage}")
                for stage, value in self.stages.items()
            }
            specs = _builtin_models()
            profile_name = _text(self.active_profile or "legacy", "active_profile")
            profile_map = {
                profile_name: {
                    "description": "Normalized v1 routing profile.",
                    "stages": {
                        stage: _V1_PRESETS[model] for stage, model in legacy_stages.items()
                    },
                }
            }
            object.__setattr__(self, "mode", old_mode)
            object.__setattr__(self, "runtime", "AUTO")
            object.__setattr__(self, "default_profile", profile_name)
            object.__setattr__(self, "active_profile", profile_name)
            object.__setattr__(self, "models", specs)
            object.__setattr__(self, "profiles", profile_map)
            object.__setattr__(self, "runtime_fallback", fallback)
            object.__setattr__(self, "sol_thresholds", thresholds)
            object.__setattr__(self, "luna_policy", policy)
            object.__setattr__(self, "stages", legacy_stages)
            object.__setattr__(self, "run_overrides", {})
            return

        if self.routing_policy_version != 2:
            raise RoutingError("routing_policy_version must be 1 or 2")
        runtime_name = _runtime(self.runtime or self.mode or "AUTO")
        specs = _normalize_models(self.models)
        profile_map = _profiles(self.profiles)
        default_profile = _text(self.default_profile, "default_profile")
        if default_profile not in profile_map:
            raise RoutingError(f"Unknown profile: {default_profile}")
        active_profile = _text(self.active_profile or default_profile, "active_profile")
        selected = _resolve_profile_stages(profile_map, active_profile)
        overrides = _profile_stages(self.run_overrides, "run_overrides") if self.run_overrides else {}
        selected.update(overrides)
        for name in profile_map:
            profile_stages = _resolve_profile_stages(profile_map, name)
            _validate_stages(profile_stages, specs, name)
        _validate_stages(selected, specs, active_profile)
        if fallback["unavailable_model"].upper() in _V1_PRESETS:
            fallback["unavailable_model"] = _V1_PRESETS[fallback["unavailable_model"].upper()]
        if fallback["unavailable_model"] not in specs:
            raise RoutingError(f"Unknown model preset: {fallback['unavailable_model']}")
        if fallback["unsupported_reasoning"] not in {"inherit", "error", "fail"}:
            fallback_name = fallback["unsupported_reasoning"]
            if fallback_name.upper() in _V1_PRESETS:
                fallback_name = _V1_PRESETS[fallback_name.upper()]
            if fallback_name not in specs:
                raise RoutingError(f"Unknown model preset: {fallback_name}")
            fallback["unsupported_reasoning"] = fallback_name
        object.__setattr__(self, "mode", runtime_name)
        object.__setattr__(self, "runtime", runtime_name)
        object.__setattr__(self, "default_profile", default_profile)
        object.__setattr__(self, "active_profile", active_profile)
        object.__setattr__(self, "models", specs)
        object.__setattr__(self, "profiles", profile_map)
        object.__setattr__(self, "runtime_fallback", fallback)
        object.__setattr__(self, "sol_thresholds", thresholds)
        object.__setattr__(self, "luna_policy", policy)
        object.__setattr__(self, "run_overrides", overrides)
        object.__setattr__(
            self,
            "stages",
            {stage: _logical_model(specs[preset]) for stage, preset in selected.items()},
        )

    @property
    def profile(self) -> str:
        return self.active_profile or self.default_profile

    def profile_stages(self, profile: str | None = None) -> dict[str, str]:
        name = _text(profile or self.profile, "profile")
        return _resolve_profile_stages(self.profiles, name)

    def preset_for_stage(self, stage: str, *, profile: str | None = None) -> str:
        name = _text(stage, "stage")
        stages = self.profile_stages(profile)
        if self.run_overrides and profile in (None, self.profile) and name in self.run_overrides:
            return self.run_overrides[name]
        try:
            return stages[name]
        except KeyError as exc:
            raise RoutingError(f"unknown routing stage: {name}") from exc

    def model_spec(self, preset: str) -> ModelSpec:
        name = _text(preset, "preset")
        try:
            return self.models[name]
        except KeyError as exc:
            raise RoutingError(f"Unknown model preset: {name}") from exc

    def model_for_stage(self, stage: str) -> str:
        name = _text(stage, "stage")
        try:
            return self.stages[name]
        except KeyError as exc:
            raise RoutingError(f"unknown routing stage: {name}") from exc

    @property
    def config_hash(self) -> str:
        payload = self.as_dict()
        payload["active_profile"] = self.profile
        if self.run_overrides:
            payload["run_overrides"] = dict(self.run_overrides)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        if self.routing_policy_version == 1:
            return {
                "routing_policy_version": 1,
                "mode": self.mode,
                "luna_policy": self.luna_policy,
                "stages": dict(self.stages or {}),
                "sol_thresholds": dict(self.sol_thresholds),
            }
        return {
            "routing_policy_version": 2,
            "runtime": self.runtime,
            "default_profile": self.default_profile,
            "luna_policy": self.luna_policy,
            "models": {name: spec.as_dict() for name, spec in self.models.items()},
            "profiles": copy.deepcopy(dict(self.profiles)),
            "runtime_fallback": dict(self.runtime_fallback),
            "sol_thresholds": dict(self.sol_thresholds),
        }


def _validate_stages(
    stages: Mapping[str, str],
    specs: Mapping[str, ModelSpec],
    profile_name: str,
) -> None:
    missing = REQUIRED_STAGES - set(stages)
    if missing:
        raise RoutingError(
            f"profile {profile_name} is missing required stages: {', '.join(sorted(missing))}"
        )
    for stage, preset in stages.items():
        _validate_stage_preset(stage, preset, specs)


def _validate_stage_preset(
    stage: str,
    preset: str,
    specs: Mapping[str, ModelSpec],
) -> None:
    if preset not in specs:
        raise RoutingError(f"Unknown model preset: {preset}")
    spec = specs[preset]
    if stage in PYTHON_STAGES and spec.family != "PYTHON":
        raise RoutingError(f"Stage {stage} must remain PYTHON")
    if stage in LLM_STAGES and spec.family == "PYTHON":
        raise RoutingError(f"LLM-owned stage {stage} cannot use PYTHON")


def _is_v1(value: Mapping[str, Any]) -> bool:
    return value.get("routing_policy_version") == 1 or "stages" in value or "mode" in value


def validate_model_routing(value: Mapping[str, Any] | ModelRouting) -> ModelRouting:
    if isinstance(value, ModelRouting):
        return value
    if not isinstance(value, Mapping):
        raise RoutingError("model routing must be an object")
    if _is_v1(value):
        allowed = {"routing_policy_version", "mode", "luna_policy", "stages", "sol_thresholds"}
        unknown = set(value) - allowed
        if unknown:
            raise RoutingError(f"model routing contains unknown fields: {', '.join(sorted(unknown))}")
        missing = {"routing_policy_version", "mode", "luna_policy", "stages"} - set(value)
        if missing:
            raise RoutingError(f"model routing is missing fields: {', '.join(sorted(missing))}")
        if value["routing_policy_version"] != 1:
            raise RoutingError("v1 routing_policy_version must be 1")
        return ModelRouting(
            routing_policy_version=1,
            mode=value["mode"],
            luna_policy=value["luna_policy"],
            stages=value["stages"],
            sol_thresholds=value.get("sol_thresholds", _DEFAULT_SOL_THRESHOLDS),
        )
    allowed = {
        "routing_policy_version",
        "runtime",
        "default_profile",
        "luna_policy",
        "models",
        "profiles",
        "runtime_fallback",
        "sol_thresholds",
        "active_profile",
        "run_overrides",
    }
    unknown = set(value) - allowed
    if unknown:
        raise RoutingError(f"model routing contains unknown fields: {', '.join(sorted(unknown))}")
    required = {"routing_policy_version", "runtime", "default_profile", "luna_policy", "models", "profiles"}
    missing = required - set(value)
    if missing:
        raise RoutingError(f"model routing is missing fields: {', '.join(sorted(missing))}")
    if value["routing_policy_version"] != 2:
        raise RoutingError("routing_policy_version must be 2 for v2 routing")
    return ModelRouting(
        routing_policy_version=2,
        runtime=value["runtime"],
        default_profile=value["default_profile"],
        luna_policy=value["luna_policy"],
        models=value["models"],
        profiles=value["profiles"],
        runtime_fallback=value.get("runtime_fallback", _DEFAULT_RUNTIME_FALLBACK),
        sol_thresholds=value.get("sol_thresholds", _DEFAULT_SOL_THRESHOLDS),
        active_profile=value.get("active_profile"),
        run_overrides=value.get("run_overrides", {}),
    )


def _read_json(source: Path) -> dict[str, Any]:
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError(f"unable to load model routing from {source}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RoutingError(f"model routing from {source} must be an object")
    return dict(data)


def _merge_mapping(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if key in {"models", "profiles", "runtime_fallback", "sol_thresholds"} and isinstance(value, Mapping):
            current = result.get(key, {})
            if not isinstance(current, Mapping):
                current = {}
            merged_section = copy.deepcopy(dict(current))
            for item_name, item_value in value.items():
                if (
                    item_name in merged_section
                    and isinstance(merged_section[item_name], Mapping)
                    and isinstance(item_value, Mapping)
                ):
                    merged_item = copy.deepcopy(dict(merged_section[item_name]))
                    if key == "profiles" and isinstance(item_value.get("stages"), Mapping):
                        merged_stages = dict(merged_item.get("stages", {}))
                        merged_stages.update(item_value["stages"])
                        item_value = {**dict(item_value), "stages": merged_stages}
                    merged_item.update(copy.deepcopy(dict(item_value)))
                    merged_section[item_name] = merged_item
                else:
                    merged_section[item_name] = copy.deepcopy(item_value)
            result[key] = merged_section
        elif key != "active_profile":
            result[key] = copy.deepcopy(value)
    if "active_profile" in override:
        result["active_profile"] = override["active_profile"]
    return result


def _validate_override(value: Mapping[str, Any], source: Path) -> None:
    allowed = {
        "routing_policy_version",
        "runtime",
        "default_profile",
        "luna_policy",
        "models",
        "profiles",
        "runtime_fallback",
        "sol_thresholds",
        "active_profile",
        "run_overrides",
    }
    unknown = set(value) - allowed
    if unknown:
        raise RoutingError(
            f"model routing override {source} contains unknown fields: {', '.join(sorted(unknown))}"
        )


def load_model_routing(
    path: str | Path | None = None,
    *,
    profile: str | None = None,
    run_overrides: Mapping[str, str] | None = None,
    runtime: str | None = None,
) -> ModelRouting:
    """Load repository defaults and optional user overrides without writing files."""
    base = _read_json(_DEFAULT_PATH)
    if path is not None:
        override_path: Path | None = Path(path)
    else:
        configured = os.environ.get("CRYPTO_PORTFOLIO_MODEL_CONFIG")
        override_path = (
            Path(configured)
            if configured
            else Path.home() / ".config" / "crypto-portfolio-manager" / "model-routing.json"
        )
    local_profile: str | None = None
    if override_path is not None and override_path.exists():
        override = _read_json(override_path)
        if _is_v1(override):
            routing = validate_model_routing(override)
            if profile is not None:
                raise RoutingError("v1 routing config does not support profile selection")
            return routing
        _validate_override(override, override_path)
        local_profile = override.get("active_profile")
        merged = _merge_mapping(base, override)
    elif path is not None or os.environ.get("CRYPTO_PORTFOLIO_MODEL_CONFIG"):
        raise RoutingError(f"unable to load model routing from {override_path}: file does not exist")
    else:
        merged = base
    selected_profile = (
        profile
        or os.environ.get("CRYPTO_PORTFOLIO_MODEL_PROFILE")
        or local_profile
        or merged.get("default_profile")
    )
    if runtime is not None:
        merged["runtime"] = runtime
    if run_overrides is not None:
        merged["run_overrides"] = dict(run_overrides)
    merged["active_profile"] = selected_profile
    return validate_model_routing(merged)


def default_runtime_capabilities(runtime: str = "AUTO") -> RuntimeCapabilities:
    """Return conservative capabilities when the host exposes no adapter."""
    # ponytail: no host discovery; inject capabilities until a runtime adapter exists.
    return RuntimeCapabilities(runtime, False, False)


def runtime_capabilities(runtime: str = "AUTO") -> RuntimeCapabilities:
    """Compatibility alias for the conservative default capability adapter."""
    return default_runtime_capabilities(runtime)


def _routing(value: ModelRouting | Mapping[str, Any] | None) -> ModelRouting:
    if value is None:
        return load_model_routing()
    return value if isinstance(value, ModelRouting) else validate_model_routing(value)


def _capabilities(value: RuntimeCapabilities | None, routing: ModelRouting) -> RuntimeCapabilities:
    if value is None:
        return default_runtime_capabilities(routing.runtime)
    if not isinstance(value, RuntimeCapabilities):
        raise RoutingError("runtime_capabilities must be RuntimeCapabilities or null")
    return value


def _fallback_spec(routing: ModelRouting, name: str) -> ModelSpec:
    normalized = _text(name, "fallback preset")
    if normalized.upper() in _V1_PRESETS:
        normalized = _V1_PRESETS[normalized.upper()]
    return routing.model_spec(normalized)


def _fallback_route(
    stage: str,
    requested_preset: str,
    requested: ModelSpec,
    routing: ModelRouting,
    capabilities: RuntimeCapabilities,
    reason: str,
) -> ResolvedStageRoute:
    fallback = _fallback_spec(routing, routing.runtime_fallback["unavailable_model"])
    return ResolvedStageRoute(
        stage,
        requested_preset,
        requested.model,
        requested.reasoning_effort,
        fallback.model,
        fallback.reasoning_effort,
        capabilities.runtime,
        True,
        reason,
    )


def _unsupported_reasoning_route(
    stage: str,
    requested_preset: str,
    requested: ModelSpec,
    routing: ModelRouting,
    capabilities: RuntimeCapabilities,
    reason: str,
) -> ResolvedStageRoute:
    policy = routing.runtime_fallback["unsupported_reasoning"]
    if policy in {"error", "fail"}:
        raise RoutingError(f"{stage}: {reason}")
    if policy == "inherit" and requested.family.startswith("LUNA"):
        return _fallback_route(stage, requested_preset, requested, routing, capabilities, reason)
    if policy == "inherit":
        return ResolvedStageRoute(
            stage,
            requested_preset,
            requested.model,
            requested.reasoning_effort,
            requested.model,
            "inherit",
            capabilities.runtime,
            True,
            reason,
        )
    fallback = _fallback_spec(routing, policy)
    return ResolvedStageRoute(
        stage,
        requested_preset,
        requested.model,
        requested.reasoning_effort,
        fallback.model,
        fallback.reasoning_effort,
        capabilities.runtime,
        True,
        reason,
    )


def resolve_stage_route(
    stage: str,
    *,
    profile: str | None = None,
    routing: ModelRouting | Mapping[str, Any] | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
    run_override: Mapping[str, str] | None = None,
) -> ResolvedStageRoute:
    """Resolve requested profile routing against explicitly supplied capabilities."""
    resolved = _routing(routing)
    name = _text(stage, "stage")
    preset = resolved.preset_for_stage(name, profile=profile)
    if run_override is not None:
        overrides = _profile_stages(run_override, "run_override")
        if name in overrides:
            preset = overrides[name]
    requested = resolved.model_spec(preset)
    _validate_stage_preset(name, preset, resolved.models)
    capabilities = _capabilities(runtime_capabilities, resolved)
    if requested.family in {"PYTHON", "CURRENT_SESSION"}:
        return ResolvedStageRoute(
            name,
            preset,
            requested.model,
            requested.reasoning_effort,
            requested.model,
            requested.reasoning_effort,
            capabilities.runtime,
            False,
            None,
        )
    if not capabilities.can_select_model_per_stage:
        return _fallback_route(
            name,
            preset,
            requested,
            resolved,
            capabilities,
            "runtime does not expose per-stage model selection",
        )
    if capabilities.available_models is not None and requested.model not in capabilities.available_models:
        return _fallback_route(
            name,
            preset,
            requested,
            resolved,
            capabilities,
            f"runtime does not provide requested model {requested.model}",
        )
    effort = requested.reasoning_effort
    if effort not in (None, "inherit"):
        supported = requested.supported_reasoning_efforts
        if supported is not None and effort not in supported:
            return _unsupported_reasoning_route(
                name,
                preset,
                requested,
                resolved,
                capabilities,
                f"model {requested.model} does not support reasoning effort {effort}",
            )
        if not capabilities.can_set_reasoning_effort:
            return _unsupported_reasoning_route(
                name,
                preset,
                requested,
                resolved,
                capabilities,
                "runtime does not expose reasoning-effort selection",
            )
        supported_by_runtime = (capabilities.supported_reasoning_efforts or {}).get(
            requested.model,
            (capabilities.supported_reasoning_efforts or {}).get(requested.family),
        )
        if supported_by_runtime is not None and effort not in supported_by_runtime:
            return _unsupported_reasoning_route(
                name,
                preset,
                requested,
                resolved,
                capabilities,
                f"runtime does not support reasoning effort {effort} for {requested.model}",
            )
    return ResolvedStageRoute(
        name,
        preset,
        requested.model,
        requested.reasoning_effort,
        requested.model,
        requested.reasoning_effort,
        capabilities.runtime,
        False,
        None,
    )


def resolve_all_routes(
    *,
    stages: Mapping[str, Any] | None = None,
    profile: str | None = None,
    routing: ModelRouting | Mapping[str, Any] | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
    run_override: Mapping[str, str] | None = None,
) -> dict[str, ResolvedStageRoute]:
    resolved = _routing(routing)
    names = tuple(stages) if stages is not None else tuple(resolved.profile_stages(profile))
    return {
        name: resolve_stage_route(
            name,
            profile=profile,
            routing=resolved,
            runtime_capabilities=runtime_capabilities,
            run_override=run_override,
        )
        for name in names
    }


def model_for_stage(
    stage: str,
    *,
    routing: ModelRouting | Mapping[str, Any] | None = None,
) -> str:
    resolved = _routing(routing)
    return resolved.model_for_stage(stage)


def _logical_for_value(value: Any, routing: ModelRouting, field_name: str) -> str:
    if isinstance(value, ModelSpec):
        return _logical_model(value)
    text = _text(value, field_name)
    upper = text.upper()
    if upper in _V1_MODELS:
        return _legacy_model(text, field_name)
    if text in routing.models:
        return _logical_model(routing.models[text])
    for spec in routing.models.values():
        if spec.model == text:
            return _logical_model(spec)
    raise RoutingError(f"Unknown model preset: {text}")


def validate_stage_model(
    stage: str,
    model: str,
    *,
    routing: ModelRouting | Mapping[str, Any] | None = None,
) -> bool:
    resolved = _routing(routing)
    expected = resolved.model_for_stage(stage)
    actual = _logical_for_value(model, resolved, f"model for {stage}")
    if actual != expected:
        raise RoutingError(f"stage {stage} is configured for {expected}, not {actual}")
    return True


def validate_historical_stage_model(stage: str, model: str) -> bool:
    """Validate a persisted stage model without comparing against current config.

    Append-only decision records carry ``stages_used`` metadata that was valid
    under the routing policy current when the record was written. The current
    config may later change those stage assignments, so historical records are
    checked only for a well-formed, permitted model name (including the
    Luna-family ``LUNA_MAX``-only rule) rather than for a match with the
    current stage mapping.
    """
    name = _text(stage, "stage")
    if name not in REQUIRED_STAGES:
        raise RoutingError(f"Unknown stage: {name}")
    value = _text(model, f"model for {name}")
    if value.upper() in _V1_MODELS:
        _legacy_model(value, f"model for {name}")
    else:
        _logical_for_value(value, _routing(None), f"model for {name}")
    return True


def luna_max_only(model: str) -> bool:
    text = _text(model, "model")
    if text.upper().startswith("LUNA") and text.upper() != LUNA_MAX:
        raise RoutingError("Luna-family stages may target only LUNA_MAX")
    return text.lower() == "luna_max"


def format_route_log(route: ResolvedStageRoute) -> str:
    fallback = route.fallback_reason or "none"
    return "\n".join(
        (
            f"[ROUTE] {route.stage}",
            f"        requested: {route.requested_model or 'PYTHON'} / {route.requested_reasoning_effort or 'none'}",
            f"        effective: {route.effective_model or 'PYTHON'} / {route.effective_reasoning_effort or 'none'}",
            f"        runtime: {route.runtime}",
            f"        fallback: {fallback}",
        )
    )


def log_route(route: ResolvedStageRoute, *, stream: TextIO | None = None) -> None:
    print(format_route_log(route), file=stream or sys.stderr)


def routing_metadata(
    stages_used: Mapping[str, Any] | ModelRouting | None = None,
    *,
    escalations: tuple[str, ...] | list[str] = (),
    sol_review_performed: bool = False,
    routing: ModelRouting | Mapping[str, Any] | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
    run_override: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if routing is None and isinstance(stages_used, ModelRouting):
        resolved = stages_used
    elif routing is None and isinstance(stages_used, Mapping) and {
        "routing_policy_version",
        "models",
        "profiles",
    }.issubset(stages_used):
        resolved = _routing(stages_used)
        stages_used = None
    else:
        resolved = _routing(routing)
    if not isinstance(sol_review_performed, bool):
        raise RoutingError("sol_review_performed must be boolean")
    if isinstance(stages_used, ModelRouting):
        used = dict(stages_used.stages or {})
    elif stages_used is None:
        used = {stage: resolved.model_for_stage(stage) for stage in resolved.profile_stages()}
    elif isinstance(stages_used, Mapping):
        used = dict(stages_used)
    else:
        raise RoutingError("stages_used must be an object, ModelRouting, or null")
    normalized_run_override = (
        _profile_stages(run_override, "run_override") if run_override is not None else {}
    )
    routes: dict[str, ResolvedStageRoute] = {}
    legacy_used: dict[str, str] = {}
    for stage, value in used.items():
        name = _text(stage, "stage")
        if isinstance(value, ResolvedStageRoute):
            route = value
            requested_preset = route.requested_preset
        else:
            if isinstance(value, Mapping):
                requested_preset = value.get("requested_preset")
                if requested_preset is None:
                    raise RoutingError(f"routing_metadata.stages_used.{name} must name a preset")
                requested_preset = _text(requested_preset, f"stages_used.{name}.requested_preset")
            else:
                text = _text(value, f"stages_used.{name}")
                requested_preset = text if text in resolved.models else resolved.preset_for_stage(name)
                if name in normalized_run_override:
                    requested_preset = normalized_run_override[name]
                expected = _logical_model(resolved.model_spec(requested_preset))
                expected_stage = _logical_model(
                    resolved.model_spec(resolved.preset_for_stage(name))
                )
                if name not in normalized_run_override and expected != expected_stage:
                    raise RoutingError(
                        f"stage {name} is configured for {expected_stage}, not {text}"
                    )
                if text not in resolved.models and _logical_for_value(text, resolved, f"stages_used.{name}") != expected:
                    raise RoutingError(f"stage {name} is configured for {expected}, not {text}")
            _validate_stage_preset(name, requested_preset, resolved.models)
            route = resolve_stage_route(
                name,
                routing=resolved,
                runtime_capabilities=runtime_capabilities,
                run_override={name: requested_preset},
            )
        _validate_stage_preset(name, requested_preset, resolved.models)
        if name not in normalized_run_override:
            expected_stage = _logical_model(
                resolved.model_spec(resolved.preset_for_stage(name))
            )
            if _logical_model(resolved.model_spec(requested_preset)) != expected_stage:
                raise RoutingError(
                    f"stage {name} is configured for {expected_stage}, not {requested_preset}"
                )
        routes[name] = route
        legacy_used[name] = _logical_model(resolved.model_spec(route.requested_preset))
    return {
        "routing_policy_version": resolved.routing_policy_version,
        "profile": resolved.profile,
        "runtime": (runtime_capabilities.runtime if runtime_capabilities else resolved.runtime),
        "config_hash": resolved.config_hash,
        "stages": {stage: route.as_dict() for stage, route in routes.items()},
        "stages_used": legacy_used,
        "escalations": list(escalations),
        "sol_review_performed": sol_review_performed,
        "fallback_count": sum(route.fallback_used for route in routes.values()),
    }


def routing_config_hash(routing: ModelRouting | Mapping[str, Any] | None = None) -> str:
    return _routing(routing).config_hash


load_routing_config = load_model_routing
validate_routing_config = validate_model_routing
stage_model = model_for_stage


__all__ = [
    "LLM_STAGES",
    "LUNA_MAX",
    "ModelRouting",
    "ModelSpec",
    "PYTHON",
    "PYTHON_STAGES",
    "REASONING_EFFORTS",
    "REQUIRED_STAGES",
    "ResolvedStageRoute",
    "RoutingError",
    "RuntimeCapabilities",
    "RUNTIMES",
    "SOL",
    "StageRoute",
    "TERRA",
    "default_runtime_capabilities",
    "format_route_log",
    "load_model_routing",
    "load_routing_config",
    "log_route",
    "luna_max_only",
    "model_for_stage",
    "resolve_all_routes",
    "resolve_stage_route",
    "routing_config_hash",
    "routing_metadata",
    "runtime_capabilities",
    "stage_model",
    "validate_model_routing",
    "validate_routing_config",
    "validate_stage_model",
    "validate_historical_stage_model",
]
