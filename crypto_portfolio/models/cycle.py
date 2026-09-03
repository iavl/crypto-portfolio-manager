"""Immutable BTC halving and market-cycle context."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .time import normalize_timestamp


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class HalvingContext(_ValueEnum):
    PRE_HALVING = "PRE_HALVING"
    EARLY_POST_HALVING = "EARLY_POST_HALVING"
    MID_EPOCH = "MID_EPOCH"
    LATE_EPOCH = "LATE_EPOCH"
    UNKNOWN = "UNKNOWN"


class MarketCycleState(_ValueEnum):
    RESET = "RESET"
    EXPANSION = "EXPANSION"
    MATURE = "MATURE"
    OVERHEATED = "OVERHEATED"
    CONTRACTION = "CONTRACTION"
    UNKNOWN = "UNKNOWN"


class CycleRisk(_ValueEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class CycleValuationState(_ValueEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class HolderBehaviorState(_ValueEnum):
    ACCUMULATION = "ACCUMULATION"
    NEUTRAL = "NEUTRAL"
    DISTRIBUTION = "DISTRIBUTION"
    UNKNOWN = "UNKNOWN"


_HALVING = {item.value for item in HalvingContext}
_MARKET = {item.value for item in MarketCycleState}
_RISK = {item.value for item in CycleRisk}
_VALUATION = {item.value for item in CycleValuationState}
_HOLDER = {item.value for item in HolderBehaviorState}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_NUMERIC_FIELDS = (
    "return_since_halving",
    "distance_from_ath",
    "drawdown",
    "mvrv",
    "mvrv_zscore",
    "realized_price",
    "market_to_realized_price",
    "sopr",
    "lth_supply_pct",
    "lth_net_position_change",
    "sth_realized_price",
    "lth_realized_price",
    "nupl",
)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_timestamp(value: Any, field_name: str) -> str | None:
    return None if value is None else normalize_timestamp(value, field_name)


def _state(value: Any, allowed: set[str], field_name: str) -> str:
    raw = value.value if isinstance(value, Enum) else value
    result = _text(raw, field_name).upper()
    if result not in allowed:
        raise ValueError(f"{field_name} is unsupported")
    return result


def _ids(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence of strings")
    result = tuple(_text(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must contain unique values")
    return result


def _freeze(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{field_name} contains an invalid key")
            normalized = key.strip().lower()
            if normalized in {"raw", "raw_data", "raw_posts", "full_history", "history", "dense_series", "candles"} or normalized.startswith("raw_"):
                raise ValueError(f"{field_name} must not contain raw or dense-history fields")
            result[key] = _freeze(item, f"{field_name}.{key}")
        try:
            json.dumps(_thaw(result), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be JSON serializable and finite") from exc
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, f"{field_name}[]") for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must contain finite values")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class BTCCycleContext:
    as_of: str

    last_halving_timestamp: str | None = None
    days_since_halving: int | None = None
    estimated_next_halving_timestamp: str | None = None
    estimated_days_to_next_halving: int | None = None
    halving_epoch_progress: float | None = None

    return_since_halving: float | None = None
    distance_from_ath: float | None = None
    drawdown: float | None = None

    mvrv: float | None = None
    mvrv_zscore: float | None = None
    realized_price: float | None = None
    market_to_realized_price: float | None = None
    sopr: float | None = None
    lth_supply_pct: float | None = None
    lth_net_position_change: float | None = None
    sth_realized_price: float | None = None
    lth_realized_price: float | None = None
    nupl: float | None = None

    halving_context: str = HalvingContext.UNKNOWN.value
    valuation_state: str = CycleValuationState.UNKNOWN.value
    holder_state: str = HolderBehaviorState.UNKNOWN.value
    market_cycle_state: str = MarketCycleState.UNKNOWN.value
    cycle_risk: str = CycleRisk.UNKNOWN.value
    confidence: str = "LOW"
    positioning_state: str | None = None
    positioning_risk: str | None = None

    evidence_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    data_quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", normalize_timestamp(self.as_of, "cycle as_of"))
        for field_name in ("last_halving_timestamp", "estimated_next_halving_timestamp"):
            object.__setattr__(self, field_name, _optional_timestamp(getattr(self, field_name), f"cycle {field_name}"))
        for field_name in ("days_since_halving", "estimated_days_to_next_halving"):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"cycle {field_name} must be a non-negative integer or null")
        for field_name in _NUMERIC_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"cycle {field_name} must be finite numeric or null")
            value = float(value)
            if field_name == "return_since_halving" and value < -1:
                raise ValueError("cycle return_since_halving must be >= -1")
            if field_name == "drawdown" and value > 0:
                raise ValueError("cycle drawdown must be <= 0")
            if field_name in {"mvrv", "realized_price", "market_to_realized_price", "sopr", "sth_realized_price", "lth_realized_price"} and value <= 0:
                raise ValueError(f"cycle {field_name} must be > 0")
            if field_name == "lth_supply_pct" and not 0 <= value <= 1:
                raise ValueError("cycle lth_supply_pct must be in [0, 1]")
            object.__setattr__(self, field_name, float(value))
        if self.halving_epoch_progress is not None:
            progress = self.halving_epoch_progress
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(float(progress)) or not 0 <= progress <= 1:
                raise ValueError("halving_epoch_progress must be finite and in [0, 1]")
            object.__setattr__(self, "halving_epoch_progress", float(progress))
        object.__setattr__(self, "halving_context", _state(self.halving_context, _HALVING, "halving_context"))
        object.__setattr__(self, "valuation_state", _state(self.valuation_state, _VALUATION, "valuation_state"))
        object.__setattr__(self, "holder_state", _state(self.holder_state, _HOLDER, "holder_state"))
        object.__setattr__(self, "market_cycle_state", _state(self.market_cycle_state, _MARKET, "market_cycle_state"))
        object.__setattr__(self, "cycle_risk", _state(self.cycle_risk, _RISK, "cycle_risk"))
        confidence = _text(self.confidence, "cycle confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("cycle confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "confidence", confidence)
        for field_name in ("positioning_state", "positioning_risk"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"cycle {field_name}").upper())
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))
        object.__setattr__(self, "reasons", _ids(self.reasons, "reasons"))
        object.__setattr__(self, "data_quality_flags", _ids(self.data_quality_flags, "data_quality_flags"))
        if not isinstance(self.source_metadata, Mapping):
            raise ValueError("cycle source_metadata must be an object")
        object.__setattr__(self, "source_metadata", _freeze(self.source_metadata, "source_metadata"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "last_halving_timestamp": self.last_halving_timestamp,
            "days_since_halving": self.days_since_halving,
            "estimated_next_halving_timestamp": self.estimated_next_halving_timestamp,
            "estimated_days_to_next_halving": self.estimated_days_to_next_halving,
            "halving_epoch_progress": self.halving_epoch_progress,
            **{field_name: getattr(self, field_name) for field_name in _NUMERIC_FIELDS},
            "halving_context": self.halving_context,
            "valuation_state": self.valuation_state,
            "holder_state": self.holder_state,
            "market_cycle_state": self.market_cycle_state,
            "cycle_risk": self.cycle_risk,
            "confidence": self.confidence,
            "positioning_state": self.positioning_state,
            "positioning_risk": self.positioning_risk,
            "evidence_ids": list(self.evidence_ids),
            "reasons": list(self.reasons),
            "source_metadata": _thaw(self.source_metadata),
            "data_quality_flags": list(self.data_quality_flags),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BTCCycleContext":
        if not isinstance(value, Mapping):
            raise ValueError("BTC cycle context must be an object")
        allowed = {
            "as_of", "last_halving_timestamp", "days_since_halving", "estimated_next_halving_timestamp",
            "estimated_days_to_next_halving", "halving_epoch_progress", *_NUMERIC_FIELDS,
            "halving_context", "valuation_state", "holder_state", "market_cycle_state", "cycle_risk",
            "confidence", "positioning_state", "positioning_risk", "evidence_ids", "reasons",
            "source_metadata", "data_quality_flags",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"BTC cycle context contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(**{key: value[key] for key in value if key in allowed})


BTCCycle = BTCCycleContext


__all__ = [
    "BTCCycleContext",
    "CycleRisk",
    "CycleValuationState",
    "HalvingContext",
    "HolderBehaviorState",
    "MarketCycleState",
    "BTCCycle",
]
