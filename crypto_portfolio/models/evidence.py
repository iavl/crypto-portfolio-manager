"""Structured evidence and factor-assessment models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .time import normalize_timestamp


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
_ASSET_TYPES = {"core", "satellite", "stablecoin", "cash", "other"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _score(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{field} must be finite and in [0, 100]")
    return value


def _confidence(value: Any, field: str = "confidence") -> str:
    value = _text(value, field).upper()
    if value not in _CONFIDENCE:
        raise ValueError(f"{field} must be one of {sorted(_CONFIDENCE)}")
    return value


@dataclass(frozen=True)
class Evidence:
    id: str
    asset: str
    factor: str
    source: str
    observed_at: str
    fetched_at: str
    freshness: str
    confidence: str
    value: Any = None
    summary: str | None = None

    def __post_init__(self) -> None:
        for field in ("id", "factor", "source", "observed_at", "fetched_at"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        object.__setattr__(self, "observed_at", normalize_timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at"))
        object.__setattr__(self, "freshness", _text(self.freshness, "freshness").upper())
        if self.freshness not in _FRESHNESS:
            raise ValueError(f"freshness must be one of {sorted(_FRESHNESS)}")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if isinstance(self.value, bool):
            raise ValueError("evidence.value must not be boolean")
        if isinstance(self.value, (int, float)) and not math.isfinite(float(self.value)):
            raise ValueError("evidence.value must be finite")
        if self.summary is not None:
            object.__setattr__(self, "summary", _text(self.summary, "summary"))

    def as_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "asset": self.asset,
            "factor": self.factor,
            "source": self.source,
            "observed_at": self.observed_at,
            "fetched_at": self.fetched_at,
            "freshness": self.freshness,
            "confidence": self.confidence,
        }
        if self.value is not None:
            result["value"] = self.value
        if self.summary is not None:
            result["summary"] = self.summary
        return result


@dataclass(frozen=True)
class FactorScore:
    factor: str
    score: float
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor", _text(self.factor, "factor"))
        object.__setattr__(self, "score", _score(self.score, f"factor {self.factor}.score"))
        ids = tuple(_text(item, "evidence_id") for item in self.evidence_ids)
        if len(ids) != len(set(ids)):
            raise ValueError(f"factor {self.factor} contains duplicate evidence IDs")
        object.__setattr__(self, "evidence_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "score": self.score,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class AssetAssessment:
    symbol: str
    factor_scores: Mapping[str, FactorScore | float | None]
    weighted_score: float | None = None
    confidence: str = "LOW"
    asset_type: str = "other"
    relative_strength_vs_btc: float | str | None = None
    severe_event: bool = False
    risk_tier: str = "normal"
    thesis_broken: bool = False
    critical_data_complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        if not isinstance(self.factor_scores, Mapping):
            raise ValueError("factor_scores must be an object")
        parsed: dict[str, FactorScore | None] = {}
        for factor, value in self.factor_scores.items():
            factor = _text(factor, "factor")
            if value is None:
                parsed[factor] = None
            elif isinstance(value, FactorScore):
                if value.factor != factor:
                    raise ValueError(f"factor score key {factor!r} does not match {value.factor!r}")
                parsed[factor] = value
            elif isinstance(value, Mapping):
                factor_name = value.get("factor", factor)
                if factor_name != factor or "score" not in value:
                    raise ValueError(f"factor score key {factor!r} is malformed")
                parsed[factor] = FactorScore(
                    factor,
                    value["score"],
                    tuple(value.get("evidence_ids", ())),
                )
            else:
                parsed[factor] = FactorScore(factor, value)
        object.__setattr__(self, "factor_scores", parsed)
        if self.weighted_score is not None:
            object.__setattr__(
                self,
                "weighted_score",
                _score(self.weighted_score, f"asset {self.symbol}.weighted_score"),
            )
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if self.asset_type not in _ASSET_TYPES:
            raise ValueError(f"asset_type must be one of {sorted(_ASSET_TYPES)}")
        if isinstance(self.relative_strength_vs_btc, str):
            object.__setattr__(self, "relative_strength_vs_btc", self.relative_strength_vs_btc.upper())
        elif self.relative_strength_vs_btc is not None:
            value = float(self.relative_strength_vs_btc)
            if not math.isfinite(value):
                raise ValueError("relative_strength_vs_btc must be finite")
            object.__setattr__(self, "relative_strength_vs_btc", value)
        if not isinstance(self.severe_event, bool):
            raise ValueError("severe_event must be boolean")
        if not isinstance(self.thesis_broken, bool):
            raise ValueError("thesis_broken must be boolean")
        if not isinstance(self.critical_data_complete, bool):
            raise ValueError("critical_data_complete must be boolean")
        object.__setattr__(self, "risk_tier", _text(self.risk_tier, "risk_tier").lower())

    @classmethod
    def from_mapping(
        cls, symbol: str, value: Mapping[str, Any] | "AssetAssessment"
    ) -> "AssetAssessment":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError(f"assessment {symbol} must be an object")
        data = dict(value)
        data.setdefault("symbol", symbol)
        return cls(**data)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "factor_scores": {
                factor: value.as_dict() if isinstance(value, FactorScore) else None
                for factor, value in self.factor_scores.items()
            },
            "weighted_score": self.weighted_score,
            "confidence": self.confidence,
            "asset_type": self.asset_type,
            "relative_strength_vs_btc": self.relative_strength_vs_btc,
            "severe_event": self.severe_event,
            "thesis_broken": self.thesis_broken,
            "critical_data_complete": self.critical_data_complete,
            "risk_tier": self.risk_tier,
        }


__all__ = ["AssetAssessment", "Evidence", "FactorScore"]
