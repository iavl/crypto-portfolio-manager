"""Validated decision-record model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .evidence import AssetAssessment, Evidence


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_STATUSES = {"PENDING", "CONFIRMED", "NOT_EXECUTED"}


def _weights(value: Mapping[str, Any], field: str, *, require_sum: bool = True) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result: dict[str, float] = {}
    for symbol, raw_weight in value.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"{field} contains an invalid symbol")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{field}.{symbol} must be a number")
        symbol = symbol.strip().upper()
        if symbol in result:
            raise ValueError(f"{field} contains duplicate symbol {symbol}")
        weight = float(raw_weight)
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{field}.{symbol} must be finite and in [0, 1]")
        result[symbol] = weight
    if require_sum and result and not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return result


@dataclass(frozen=True)
class Decision:
    timestamp: str
    market_regime: str
    policy_version: int
    current_weights: Mapping[str, float]
    target_weights: Mapping[str, float]
    actions: tuple[Any, ...] = ()
    risk_checks: tuple[Any, ...] = ()
    evidence: tuple[Evidence | str, ...] = ()
    factor_scores: Mapping[str, Any] = None
    status: str = "PENDING"
    constraints_applied: tuple[str, ...] = ()
    config: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        object.__setattr__(self, "timestamp", self.timestamp.strip())
        object.__setattr__(self, "market_regime", self.market_regime.upper())
        if self.market_regime not in _REGIMES:
            raise ValueError(f"market_regime must be one of {sorted(_REGIMES)}")
        if isinstance(self.policy_version, bool) or not isinstance(self.policy_version, int) or self.policy_version < 1:
            raise ValueError("policy_version must be a positive integer")
        object.__setattr__(self, "current_weights", _weights(self.current_weights, "current_weights"))
        object.__setattr__(self, "target_weights", _weights(self.target_weights, "target_weights"))
        if not self.current_weights or not self.target_weights:
            raise ValueError("current_weights and target_weights must be non-empty")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "risk_checks", tuple(self.risk_checks))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        evidence_ids = []
        for item in self.evidence:
            if isinstance(item, Evidence):
                evidence_ids.append(item.id)
            elif isinstance(item, str) and item.strip():
                evidence_ids.append(item.strip())
            else:
                raise ValueError("evidence must contain Evidence objects or IDs")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("decision evidence IDs must be unique")
        object.__setattr__(self, "factor_scores", dict(self.factor_scores or {}))
        object.__setattr__(self, "constraints_applied", tuple(self.constraints_applied))
        if self.config is not None:
            if not isinstance(self.config, Mapping):
                raise ValueError("config must be an object or null")
            object.__setattr__(self, "config", dict(self.config))
        status = self.status.upper()
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}")
        object.__setattr__(self, "status", status)

    def as_dict(self) -> dict[str, Any]:
        evidence = [item.as_dict() if isinstance(item, Evidence) else item for item in self.evidence]
        result = {
            "timestamp": self.timestamp,
            "policy_version": self.policy_version,
            "market_regime": self.market_regime,
            "current_weights": dict(self.current_weights),
            "target_weights": dict(self.target_weights),
            "actions": [item.as_dict() if hasattr(item, "as_dict") else item for item in self.actions],
            "risk_checks": [item.as_dict() if hasattr(item, "as_dict") else item for item in self.risk_checks],
            "constraints_applied": list(self.constraints_applied),
            "evidence": evidence,
            "evidence_ids": [item.id if isinstance(item, Evidence) else item for item in self.evidence],
            "factor_scores": {
                symbol: value.as_dict() if isinstance(value, AssetAssessment) else value
                for symbol, value in self.factor_scores.items()
            },
            "status": self.status,
        }
        if self.config is not None:
            result["config"] = dict(self.config)
        return result


__all__ = ["Decision"]
