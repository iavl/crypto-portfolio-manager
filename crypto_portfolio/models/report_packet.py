"""Immutable finalized values passed to the user-facing report writer."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .decision_packet import SolReview
from .factor_packet import freeze_packet_value, thaw_packet_value


_REVIEW_TYPES = {"SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"}
_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _weights(value: Mapping[str, Any] | None, field: str, *, require_sum: bool = False) -> Mapping[str, float]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for raw_symbol, raw_value in value.items():
        symbol = _text(raw_symbol, f"{field} symbol").upper()
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        number = float(raw_value)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        result[symbol] = number
    if require_sum and result and not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return MappingProxyType(result)


def _scores(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise ValueError("scores must be an object")
    def validate(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) or not key.strip() for key in item):
                raise ValueError(f"{path} contains an invalid key")
            return freeze_packet_value(
                {str(key): validate(value, f"{path}.{key}") for key, value in item.items()},
                path=path,
            )
        if item is None:
            return None
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{path} must contain numeric scores")
        number = float(item)
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError(f"{path} must contain scores in [0, 100]")
        return number
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("scores contains an invalid symbol")
        symbol = key.strip().upper()
        if symbol in normalized:
            raise ValueError(f"scores contains duplicate symbol {symbol}")
        normalized[symbol] = validate(item, f"scores.{key}")
    return MappingProxyType(normalized)


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a sequence")
    return tuple(
        freeze_packet_value(item.as_dict() if hasattr(item, "as_dict") else item, path=field)
        for item in value
    )


@dataclass(frozen=True)
class ReportPacket:
    review_type: str
    market_regime: str
    scores: Mapping[str, Any] = field(default_factory=dict)
    current_weights: Mapping[str, float] = field(default_factory=dict)
    target_weights: Mapping[str, float] = field(default_factory=dict)
    actions: tuple[Any, ...] = ()
    approved_amounts: Mapping[str, float] = field(default_factory=dict)
    execution_zones: Mapping[str, Any] = field(default_factory=dict)
    historical_changes: Mapping[str, Any] = field(default_factory=dict)
    risk_flags: tuple[str, ...] = ()
    sol_review: SolReview | Mapping[str, Any] | None = None
    critical_missing_data: tuple[str, ...] = ()
    data_quality: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        review = _text(self.review_type, "review_type").upper()
        regime = _text(self.market_regime, "market_regime").upper()
        if review not in _REVIEW_TYPES or regime not in _REGIMES:
            raise ValueError("report packet review_type or market_regime is unsupported")
        object.__setattr__(self, "review_type", review)
        object.__setattr__(self, "market_regime", regime)
        object.__setattr__(self, "scores", _scores(self.scores))
        object.__setattr__(self, "current_weights", _weights(self.current_weights, "current_weights"))
        object.__setattr__(self, "target_weights", _weights(self.target_weights, "target_weights", require_sum=True))
        if not self.target_weights:
            raise ValueError("target_weights must be non-empty")
        amounts: dict[str, float] = {}
        for raw_symbol, raw_amount in (self.approved_amounts or {}).items():
            symbol = _text(raw_symbol, "approved_amounts symbol").upper()
            amount = float(raw_amount)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("approved amounts must be finite and >= 0")
            amounts[symbol] = amount
        object.__setattr__(self, "approved_amounts", MappingProxyType(amounts))
        actions = _sequence(self.actions, "actions")
        for action in actions:
            if isinstance(action, Mapping):
                name = str(action.get("action", "")).strip().upper()
                if name not in _ACTIONS:
                    raise ValueError("report action is unsupported")
                raw_amount = action.get("amount_usd", action.get("approved_amount_usd", 0.0))
                if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
                    raise ValueError("report action amount must be a number")
                amount = float(raw_amount)
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("report action amount must be finite and >= 0")
                if name in {"HOLD", "WAIT", "NO_TRADE"} and amount != 0:
                    raise ValueError(f"{name} report actions must have zero amount")
        object.__setattr__(self, "actions", actions)
        for field_name in ("execution_zones", "historical_changes", "data_quality"):
            if not isinstance(getattr(self, field_name), Mapping):
                raise ValueError(f"{field_name} must be an object")
            object.__setattr__(self, field_name, freeze_packet_value(getattr(self, field_name), path=field_name))
        for field_name in ("risk_flags", "critical_missing_data"):
            values = tuple(_text(item, field_name) for item in getattr(self, field_name))
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique values")
            object.__setattr__(self, field_name, values)
        review = self.sol_review
        if review is not None and not isinstance(review, SolReview):
            review = SolReview(**dict(review))
        object.__setattr__(self, "sol_review", review)

    @property
    def finalized(self) -> bool:
        return True

    @property
    def prompt_rule(self) -> str:
        return "DO NOT recompute or alter numeric conclusions. Use the supplied structured outputs as authoritative."

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_type": self.review_type,
            "market_regime": self.market_regime,
            "scores": thaw_packet_value(self.scores),
            "current_weights": dict(self.current_weights),
            "target_weights": dict(self.target_weights),
            "actions": thaw_packet_value(self.actions),
            "approved_amounts": dict(self.approved_amounts),
            "execution_zones": thaw_packet_value(self.execution_zones),
            "historical_changes": thaw_packet_value(self.historical_changes),
            "risk_flags": list(self.risk_flags),
            "sol_review": self.sol_review.as_dict() if self.sol_review else None,
            "critical_missing_data": list(self.critical_missing_data),
            "data_quality": thaw_packet_value(self.data_quality),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReportPacket":
        if not isinstance(value, Mapping):
            raise ValueError("report packet must be an object")
        return cls(**dict(value))


ReportPacketModel = ReportPacket


__all__ = ["ReportPacket", "ReportPacketModel"]
