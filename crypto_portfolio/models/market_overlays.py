"""Compact immutable container for positioning and BTC-cycle outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .cycle import BTCCycleContext
from .positioning import PositioningFacts


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _factors(value: Mapping[str, Any] | None) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("effective_deployment_caps must be an object")
    result: dict[str, float] = {}
    for key, raw in value.items():
        name = _text(key, "effective_deployment_caps key").upper()
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("effective_deployment_caps values must be numbers")
        number = float(raw)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError("effective_deployment_caps values must be finite and in [0, 1]")
        if name in result:
            raise ValueError(f"effective_deployment_caps contains duplicate key {name}")
        result[name] = number
    return MappingProxyType(result)


@dataclass(frozen=True)
class MarketOverlays:
    positioning_by_asset: Mapping[str, PositioningFacts | Mapping[str, Any]] = field(default_factory=dict)
    btc_cycle: BTCCycleContext | Mapping[str, Any] | None = None
    overlay_confidence: str = "LOW"
    warnings: tuple[str, ...] = ()
    effective_deployment_caps: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.positioning_by_asset, Mapping):
            raise ValueError("positioning_by_asset must be an object")
        parsed: dict[str, PositioningFacts] = {}
        for raw_symbol, value in self.positioning_by_asset.items():
            symbol = _text(raw_symbol, "positioning asset").upper()
            if symbol in parsed:
                raise ValueError(f"positioning_by_asset contains duplicate asset {symbol}")
            facts = value if isinstance(value, PositioningFacts) else PositioningFacts.from_mapping(value)
            if facts.symbol != symbol:
                raise ValueError(f"positioning facts for {symbol} do not match their mapping key")
            parsed[symbol] = facts
        object.__setattr__(self, "positioning_by_asset", MappingProxyType(parsed))
        cycle = self.btc_cycle
        if cycle is not None and not isinstance(cycle, BTCCycleContext):
            cycle = BTCCycleContext.from_mapping(cycle)
        object.__setattr__(self, "btc_cycle", cycle)
        confidence = _text(self.overlay_confidence, "overlay_confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("overlay_confidence must be HIGH, MEDIUM, or LOW")
        if confidence == "LOW":
            observed = [facts.confidence for facts in parsed.values()]
            if cycle is not None:
                observed.append(cycle.confidence)
            if "HIGH" in observed:
                confidence = "HIGH"
            elif "MEDIUM" in observed:
                confidence = "MEDIUM"
        object.__setattr__(self, "overlay_confidence", confidence)
        warnings = tuple(_text(item, "overlay warning") for item in self.warnings)
        if len(warnings) != len(set(warnings)):
            raise ValueError("warnings must contain unique values")
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "effective_deployment_caps", _factors(self.effective_deployment_caps))

    @property
    def positioning(self) -> Mapping[str, PositioningFacts]:
        return self.positioning_by_asset

    def compact_summary(self) -> dict[str, Any]:
        return {
            "positioning": {
                symbol: {
                    "leverage_state": facts.leverage_state,
                    "bias": facts.bias,
                    "risk": facts.risk,
                    "social_state": facts.social_state,
                    "confidence": facts.confidence,
                    "evidence_ids": list(facts.evidence_ids),
                    "notes": list(facts.notes),
                }
                for symbol, facts in self.positioning_by_asset.items()
            },
            "btc_cycle": self.btc_cycle.as_dict() if self.btc_cycle else None,
            "overlay_confidence": self.overlay_confidence,
            "warnings": list(self.warnings),
            "effective_deployment_caps": dict(self.effective_deployment_caps),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "positioning_by_asset": {
                symbol: facts.as_dict() for symbol, facts in self.positioning_by_asset.items()
            },
            "btc_cycle": self.btc_cycle.as_dict() if self.btc_cycle else None,
            "overlay_confidence": self.overlay_confidence,
            "warnings": list(self.warnings),
            "effective_deployment_caps": dict(self.effective_deployment_caps),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MarketOverlays":
        if not isinstance(value, Mapping):
            raise ValueError("market overlays must be an object")
        allowed = {
            "positioning_by_asset", "positioning", "positioning_summaries", "btc_cycle",
            "btc_cycle_summary", "overlay_confidence", "warnings", "effective_deployment_caps",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"market overlays contain unknown fields: {', '.join(sorted(unknown))}")
        positioning = value.get(
            "positioning_by_asset",
            value.get("positioning", value.get("positioning_summaries", {})),
        )
        return cls(
            positioning_by_asset=positioning,
            btc_cycle=value.get("btc_cycle", value.get("btc_cycle_summary")),
            overlay_confidence=value.get("overlay_confidence", "LOW"),
            warnings=tuple(value.get("warnings", ())),
            effective_deployment_caps=value.get("effective_deployment_caps", {}),
        )


__all__ = ["MarketOverlays"]
