"""Non-scoring structural-risk context for protocol assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


_STATES = {"NORMAL", "ELEVATED", "SEVERE", "CRITICAL", "UNKNOWN", "CONFLICT"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


@dataclass(frozen=True)
class StructuralRiskContext:
    symbol: str
    state: str = "UNKNOWN"
    metrics: Mapping[str, Any] = field(default_factory=dict)
    confidence: str = "LOW"
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        state = str(self.state).strip().upper()
        confidence = str(self.confidence).strip().upper()
        if not symbol:
            raise ValueError("structural-risk symbol must be non-empty")
        if state not in _STATES:
            raise ValueError("structural-risk state is unsupported")
        if confidence not in _CONFIDENCE:
            raise ValueError("structural-risk confidence is unsupported")
        if not isinstance(self.metrics, Mapping):
            raise ValueError("structural-risk metrics must be an object")
        ids = tuple(str(item).strip() for item in self.source_ids)
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("structural-risk source_ids must be unique non-empty strings")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "source_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "state": self.state,
            "metrics": dict(self.metrics),
            "confidence": self.confidence,
            "source_ids": list(self.source_ids),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StructuralRiskContext":
        if not isinstance(value, Mapping):
            raise ValueError("structural-risk context must be an object")
        return cls(**dict(value))


__all__ = ["StructuralRiskContext"]
