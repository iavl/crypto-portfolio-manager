"""Immutable, conclusion-free facts derived from normalized observations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("fact values must be finite")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return _freeze(value)


@dataclass(frozen=True)
class FactBase:
    symbol: str
    current: Mapping[str, Any] = field(default_factory=dict)
    previous: Mapping[str, Any] = field(default_factory=dict)
    changes: Mapping[str, Any] = field(default_factory=dict)
    trends: Mapping[str, str] = field(default_factory=dict)
    coverage: float = 0.0
    freshness: str = "UNKNOWN"
    source_ids: tuple[str, ...] = ()
    data_quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("fact symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        for name in ("current", "previous", "changes"):
            object.__setattr__(self, name, _mapping(getattr(self, name), f"fact {name}"))
        if not isinstance(self.trends, Mapping):
            raise ValueError("fact trends must be an object")
        if any(not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip() for key, value in self.trends.items()):
            raise ValueError("fact trends must contain non-empty keys and values")
        trends = {key.strip(): value.strip().upper() for key, value in self.trends.items()}
        object.__setattr__(self, "trends", MappingProxyType(trends))
        coverage = self.coverage
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
            raise ValueError("fact coverage must be a number")
        coverage = float(coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("fact coverage must be finite and in [0, 1]")
        object.__setattr__(self, "coverage", coverage)
        freshness = str(self.freshness).strip().upper()
        if freshness not in _FRESHNESS:
            raise ValueError(f"fact freshness must be one of {sorted(_FRESHNESS)}")
        object.__setattr__(self, "freshness", freshness)
        if any(not isinstance(item, str) or not item.strip() for item in self.source_ids):
            raise ValueError("fact source_ids must contain strings")
        source_ids = tuple(item.strip() for item in self.source_ids)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("fact source_ids must contain unique non-empty strings")
        object.__setattr__(self, "source_ids", source_ids)
        if any(not isinstance(item, str) or not item.strip() for item in self.data_quality_flags):
            raise ValueError("fact data_quality_flags must contain strings")
        flags = tuple(item.strip() for item in self.data_quality_flags)
        if len(flags) != len(set(flags)):
            raise ValueError("fact data_quality_flags must contain unique non-empty strings")
        object.__setattr__(self, "data_quality_flags", flags)

    @property
    def current_values(self) -> Mapping[str, Any]:
        return self.current

    @property
    def previous_values(self) -> Mapping[str, Any]:
        return self.previous

    @property
    def historical_changes(self) -> Mapping[str, Any]:
        return self.changes

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return self.source_ids

    @property
    def trend_classifications(self) -> Mapping[str, str]:
        return self.trends

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FactBase":
        if not isinstance(value, Mapping):
            raise ValueError("facts must be an object")
        data = dict(value)
        data.setdefault("current", data.pop("current_values", {}))
        data.setdefault("previous", data.pop("previous_values", {}))
        data.setdefault("changes", data.pop("historical_changes", {}))
        data.setdefault("trends", data.pop("trend_classifications", {}))
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current": _thaw(self.current),
            "previous": _thaw(self.previous),
            "changes": _thaw(self.changes),
            "trends": dict(self.trends),
            "coverage": self.coverage,
            "freshness": self.freshness,
            "source_ids": list(self.source_ids),
            "data_quality_flags": list(self.data_quality_flags),
        }


@dataclass(frozen=True)
class TrendFacts(FactBase):
    pass


@dataclass(frozen=True)
class ValuationFacts(FactBase):
    pass


@dataclass(frozen=True)
class FundamentalFacts(FactBase):
    pass


@dataclass(frozen=True)
class OnchainFacts(FactBase):
    pass


@dataclass(frozen=True)
class FlowFacts(FactBase):
    pass


@dataclass(frozen=True)
class RelativeStrengthFacts(FactBase):
    pass


@dataclass(frozen=True)
class EventFacts(FactBase):
    pass


FACT_TYPES = {
    "trend": TrendFacts,
    "valuation": ValuationFacts,
    "fundamentals": FundamentalFacts,
    "onchain": OnchainFacts,
    "capital_flows": FlowFacts,
    "flows": FlowFacts,
    "relative_strength_btc": RelativeStrengthFacts,
    "event_risk": EventFacts,
}


__all__ = [
    "EventFacts",
    "FACT_TYPES",
    "FactBase",
    "FlowFacts",
    "FundamentalFacts",
    "OnchainFacts",
    "RelativeStrengthFacts",
    "TrendFacts",
    "ValuationFacts",
]
