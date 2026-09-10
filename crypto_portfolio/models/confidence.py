"""Bounded, auditable confidence contracts shared by the portfolio layers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping


CONFIDENCE_BANDS = ("LOW", "MEDIUM", "HIGH")
CONFIDENCE_STATUSES = ("AVAILABLE", "PROVISIONAL", "BLOCKED")
DEFAULT_MEDIUM_MIN = 0.60
DEFAULT_HIGH_MIN = 0.80
_PRIVATE_FIELDS = {"chain_of_thought", "scratchpad", "private_reasoning", "hidden_reasoning"}


def _finite_fraction(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be finite and in [0, 1]")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _unique_texts(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set)):
        raise ValueError(f"{field} must be a sequence of strings")
    result = tuple(sorted({_text(item, field) for item in value}))
    return result


def _reject_private(value: Any, field: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in _PRIVATE_FIELDS:
                raise ValueError(f"{field} must not contain private reasoning")
            _reject_private(item, field)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_private(item, field)


def confidence_band(
    score: Any,
    *,
    medium_min: float = DEFAULT_MEDIUM_MIN,
    high_min: float = DEFAULT_HIGH_MIN,
) -> str:
    """Return the configured band for a bounded score."""
    value = _finite_fraction(score, "score")
    medium = _finite_fraction(medium_min, "medium_min")
    high = _finite_fraction(high_min, "high_min")
    if not 0 < medium <= high <= 1:
        raise ValueError("confidence thresholds must satisfy 0 < medium_min <= high_min <= 1")
    if value >= high:
        return "HIGH"
    if value >= medium:
        return "MEDIUM"
    return "LOW"


@dataclass(frozen=True)
class ConfidenceDimension:
    name: str
    score: float
    weight: float
    reason_codes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "confidence dimension name").lower())
        object.__setattr__(self, "score", _finite_fraction(self.score, f"dimension {self.name}.score"))
        object.__setattr__(self, "weight", _finite_fraction(self.weight, f"dimension {self.name}.weight"))
        object.__setattr__(self, "reason_codes", _unique_texts(self.reason_codes, f"dimension {self.name}.reason_codes"))
        object.__setattr__(self, "evidence_ids", _unique_texts(self.evidence_ids, f"dimension {self.name}.evidence_ids"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, name: str | None = None) -> "ConfidenceDimension":
        if not isinstance(value, Mapping):
            raise ValueError("confidence dimension must be an object")
        data = dict(value)
        if name is not None:
            data.setdefault("name", name)
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "weight": self.weight,
            "reason_codes": list(self.reason_codes),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ConfidenceCap:
    code: str
    ceiling: float
    applies_to: str = "PORTFOLIO"
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, "confidence cap code").upper())
        object.__setattr__(self, "ceiling", _finite_fraction(self.ceiling, f"confidence cap {self.code}.ceiling"))
        object.__setattr__(self, "applies_to", _text(self.applies_to, f"confidence cap {self.code}.applies_to").upper())
        if self.reason is not None:
            object.__setattr__(self, "reason", _text(self.reason, f"confidence cap {self.code}.reason"))
        object.__setattr__(self, "evidence_ids", _unique_texts(self.evidence_ids, f"confidence cap {self.code}.evidence_ids"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConfidenceCap":
        if not isinstance(value, Mapping):
            raise ValueError("confidence cap must be an object")
        data = dict(value)
        allowed = {"code", "ceiling", "applies_to", "reason", "evidence_ids"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("confidence cap contains unknown fields: " + ", ".join(sorted(unknown)))
        data.setdefault("reason", "")
        data.setdefault("evidence_ids", ())
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "ceiling": self.ceiling,
            "applies_to": self.applies_to,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }


def merge_caps(caps: tuple[ConfidenceCap, ...] | list[ConfidenceCap] | None) -> tuple[ConfidenceCap, ...]:
    """Deduplicate caps deterministically and keep the strictest duplicate."""
    selected: dict[tuple[str, str], ConfidenceCap] = {}
    for cap in caps or ():
        item = cap if isinstance(cap, ConfidenceCap) else ConfidenceCap.from_mapping(cap)
        key = (item.code, item.applies_to)
        current = selected.get(key)
        if current is None or item.ceiling < current.ceiling:
            selected[key] = item
    return tuple(sorted(selected.values(), key=lambda item: (item.ceiling, item.code, item.applies_to)))


@dataclass(frozen=True)
class ConfidenceResult:
    raw_score: float
    score: float
    band: str
    dimensions: Mapping[str, ConfidenceDimension] = field(default_factory=dict)
    caps: tuple[ConfidenceCap, ...] = ()
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    status: str = "AVAILABLE"
    medium_min: float = DEFAULT_MEDIUM_MIN
    high_min: float = DEFAULT_HIGH_MIN

    def __post_init__(self) -> None:
        raw = _finite_fraction(self.raw_score, "raw_score")
        score = _finite_fraction(self.score, "score")
        if score > raw + 1e-12:
            raise ValueError("confidence score must not exceed raw_score")
        band = _text(self.band, "confidence band").upper()
        if band not in CONFIDENCE_BANDS:
            raise ValueError("confidence band must be LOW, MEDIUM, or HIGH")
        medium = _finite_fraction(self.medium_min, "medium_min")
        high = _finite_fraction(self.high_min, "high_min")
        if band != confidence_band(score, medium_min=medium, high_min=high):
            raise ValueError("confidence band must match the final score")
        status = _text(self.status, "confidence status").upper()
        if status not in CONFIDENCE_STATUSES:
            raise ValueError("confidence status is unsupported")
        if not isinstance(self.dimensions, Mapping):
            raise ValueError("confidence dimensions must be an object")
        dimensions: dict[str, ConfidenceDimension] = {}
        for raw_name, raw_dimension in self.dimensions.items():
            name = _text(raw_name, "confidence dimension key").lower()
            dimension = raw_dimension if isinstance(raw_dimension, ConfidenceDimension) else ConfidenceDimension.from_mapping(raw_dimension, name=name)
            if dimension.name != name:
                raise ValueError("confidence dimension key does not match its name")
            dimensions[name] = dimension
        object.__setattr__(self, "raw_score", raw)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "band", band)
        object.__setattr__(self, "dimensions", {key: dimensions[key] for key in sorted(dimensions)})
        object.__setattr__(self, "caps", merge_caps(self.caps))
        object.__setattr__(self, "reasons", _unique_texts(self.reasons, "confidence reasons"))
        object.__setattr__(self, "evidence_ids", _unique_texts(self.evidence_ids, "confidence evidence_ids"))
        object.__setattr__(self, "status", status)
        if medium > high:
            raise ValueError("confidence thresholds must be ordered")
        object.__setattr__(self, "medium_min", medium)
        object.__setattr__(self, "high_min", high)

    @property
    def cap_reasons(self) -> tuple[str, ...]:
        return tuple(cap.code for cap in self.caps)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConfidenceResult":
        if not isinstance(value, Mapping):
            raise ValueError("confidence result must be an object")
        _reject_private(value, "confidence result")
        data = dict(value)
        allowed = {
            "raw_score", "score", "band", "dimensions", "caps", "reasons",
            "evidence_ids", "status", "medium_min", "high_min",
        }
        if cls.__name__ == "DecisionConfidence":
            allowed |= {
                "components", "critical_blockers", "allowed_actions", "blocked_actions",
                "explanation", "scope", "soft_penalties",
            }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("confidence result contains unknown fields: " + ", ".join(sorted(unknown)))
        if "dimensions" in data:
            data["dimensions"] = {
                name: ConfidenceDimension.from_mapping(item, name=name)
                for name, item in data["dimensions"].items()
            }
        data["caps"] = tuple(ConfidenceCap.from_mapping(item) for item in data.get("caps", ()))
        data.setdefault("reasons", ())
        data.setdefault("evidence_ids", ())
        data.setdefault("status", "AVAILABLE")
        data.setdefault("medium_min", DEFAULT_MEDIUM_MIN)
        data.setdefault("high_min", DEFAULT_HIGH_MIN)
        if "band" not in data:
            data["band"] = confidence_band(data["score"])
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        value = {
            "raw_score": self.raw_score,
            "score": self.score,
            "band": self.band,
            "dimensions": {
                name: dimension.as_dict()
                for name, dimension in sorted(self.dimensions.items())
            },
            "caps": [cap.as_dict() for cap in self.caps],
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
            "status": self.status,
            "medium_min": self.medium_min,
            "high_min": self.high_min,
        }
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        return value


@dataclass(frozen=True)
class DecisionConfidence(ConfidenceResult):
    components: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    critical_blockers: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    blocked_actions: tuple[str, ...] = ()
    explanation: str = ""
    scope: Mapping[str, Any] | None = None
    soft_penalties: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.components, Mapping):
            raise ValueError("decision confidence components must be an object")
        _reject_private(self.components, "decision confidence components")
        components: dict[str, Mapping[str, Any]] = {}
        for key, value in self.components.items():
            name = _text(key, "decision confidence component").lower()
            if not isinstance(value, Mapping):
                raise ValueError(f"decision confidence component {name} must be an object")
            score = _finite_fraction(value.get("score"), f"component {name}.score")
            weight = _finite_fraction(value.get("weight"), f"component {name}.weight")
            components[name] = {"score": score, "weight": weight, **{
                field: item for field, item in value.items() if field not in {"score", "weight"}
            }}
        object.__setattr__(self, "components", {key: components[key] for key in sorted(components)})
        object.__setattr__(self, "critical_blockers", _unique_texts(self.critical_blockers, "critical_blockers"))
        object.__setattr__(self, "allowed_actions", tuple(sorted({_text(item, "allowed_action").upper() for item in self.allowed_actions})))
        object.__setattr__(self, "blocked_actions", tuple(sorted({_text(item, "blocked_action").upper() for item in self.blocked_actions})))
        if self.explanation:
            object.__setattr__(self, "explanation", _text(self.explanation, "decision confidence explanation"))
        if self.scope is not None:
            if not isinstance(self.scope, Mapping):
                raise ValueError("decision confidence scope must be an object or null")
            _reject_private(self.scope, "decision confidence scope")
            object.__setattr__(self, "scope", dict(self.scope))
        penalties: list[dict[str, Any]] = []
        for item in self.soft_penalties:
            if not isinstance(item, Mapping):
                raise ValueError("decision confidence soft penalties must be objects")
            penalty = _finite_fraction(item.get("penalty", item.get("amount", 0.0)), "soft penalty")
            value = {str(key): raw for key, raw in item.items() if key not in {"penalty", "amount"}}
            value["penalty"] = penalty
            _reject_private(value, "decision confidence soft penalties")
            penalties.append(value)
        object.__setattr__(self, "soft_penalties", tuple(penalties))

    def as_dict(self) -> dict[str, Any]:
        result = super().as_dict()
        result.update({
            "components": {key: dict(value) for key, value in self.components.items()},
            "critical_blockers": list(self.critical_blockers),
            "allowed_actions": list(self.allowed_actions),
            "blocked_actions": list(self.blocked_actions),
            "explanation": self.explanation,
            "scope": dict(self.scope) if self.scope is not None else None,
            "soft_penalties": [dict(item) for item in self.soft_penalties],
        })
        return result


__all__ = [
    "CONFIDENCE_BANDS",
    "CONFIDENCE_STATUSES",
    "ConfidenceCap",
    "ConfidenceDimension",
    "ConfidenceResult",
    "DecisionConfidence",
    "confidence_band",
    "merge_caps",
]
