"""Structured evidence and factor-assessment models."""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .time import normalize_timestamp


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
AVAILABILITY_STATES = ("AVAILABLE", "MISSING", "NOT_APPLICABLE")
_EVENT_RISK_STATES = ("NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL")
_ASSET_TYPES = {"core", "satellite", "stablecoin", "cash", "other"}
_PRIVATE_REASONING_FIELDS = {"chain_of_thought", "scratchpad", "private_reasoning", "hidden_reasoning"}


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


def contains_private_reasoning(value: Any) -> bool:
    """Recursively detect forbidden private-reasoning fields."""
    if isinstance(value, Mapping):
        return any(
            str(key).strip().lower() in _PRIVATE_REASONING_FIELDS
            or contains_private_reasoning(item)
            for key, item in value.items()
        )
    if isinstance(value, (tuple, list)):
        return any(contains_private_reasoning(item) for item in value)
    return False


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
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field in ("id", "factor", "source", "observed_at", "fetched_at"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        object.__setattr__(self, "factor", self.factor.lower())
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
        if self.metadata is not None:
            if not isinstance(self.metadata, Mapping):
                raise ValueError("evidence.metadata must be an object or null")
            metadata = dict(self.metadata)
            if contains_private_reasoning(metadata):
                raise ValueError("evidence.metadata must not contain private reasoning")
            try:
                json.dumps(metadata, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("evidence.metadata must be JSON serializable and finite") from exc
            object.__setattr__(self, "metadata", metadata)

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
        if self.metadata is not None:
            result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True)
class EventRiskAssessment:
    state: str
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    unresolved: bool = False

    def __post_init__(self) -> None:
        state = _text(self.state, "event_risk.state").upper()
        if state not in _EVENT_RISK_STATES:
            raise ValueError(f"event_risk.state must be one of {list(_EVENT_RISK_STATES)}")
        if isinstance(self.reasons, (str, bytes)) or isinstance(self.evidence_ids, (str, bytes)):
            raise ValueError("event_risk reasons and evidence_ids must be sequences")
        reasons = tuple(_text(item, "event_risk.reason") for item in self.reasons)
        if len(reasons) != len(set(reasons)):
            raise ValueError("event_risk.reasons must be unique")
        evidence_ids = tuple(_text(item, "event_risk.evidence_id") for item in self.evidence_ids)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("event_risk.evidence_ids must be unique")
        if not isinstance(self.unresolved, bool):
            raise ValueError("event_risk.unresolved must be boolean")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "evidence_ids", evidence_ids)

    @property
    def blocks_new_risk(self) -> bool:
        return self.state in {"SEVERE", "CRITICAL"}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | str) -> "EventRiskAssessment":
        if isinstance(value, str):
            return cls(value)
        if not isinstance(value, Mapping):
            raise ValueError("event_risk must be an object, state, or null")
        allowed = {"state", "reasons", "evidence_ids", "unresolved"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"event_risk contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            state=value.get("state"),
            reasons=value.get("reasons", ()),
            evidence_ids=value.get("evidence_ids", ()),
            unresolved=value.get("unresolved", False),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
            "unresolved": self.unresolved,
        }


@dataclass(frozen=True)
class FactorScore:
    factor: str
    score: float | None
    evidence_ids: tuple[str, ...] = ()
    availability: str = "AVAILABLE"
    reliability: float | None = None

    def __post_init__(self) -> None:
        factor = _text(self.factor, "factor").lower()
        availability = _text(self.availability, f"factor {factor}.availability").upper()
        if availability not in AVAILABILITY_STATES:
            raise ValueError(
                f"factor {factor}.availability must be one of {list(AVAILABILITY_STATES)}"
            )
        if availability == "AVAILABLE":
            if self.score is None:
                raise ValueError(f"factor {factor}.score is required when available")
            score = _score(self.score, f"factor {factor}.score")
        else:
            if self.score is not None:
                raise ValueError(f"factor {factor}.score must be null when {availability}")
            score = None
        raw_reliability = self.reliability
        if raw_reliability is None:
            raw_reliability = 1.0 if availability == "AVAILABLE" else 0.0
        if isinstance(raw_reliability, bool) or not isinstance(raw_reliability, (int, float)):
            raise ValueError(f"factor {factor}.reliability must be a number")
        reliability = float(raw_reliability)
        if not math.isfinite(reliability) or not 0 <= reliability <= 1:
            raise ValueError(f"factor {factor}.reliability must be finite and in [0, 1]")
        if availability != "AVAILABLE" and reliability != 0.0:
            raise ValueError(f"factor {factor}.reliability must be 0 when {availability}")
        object.__setattr__(self, "factor", factor)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "availability", availability)
        object.__setattr__(self, "reliability", reliability)
        if isinstance(self.evidence_ids, (str, bytes)):
            raise ValueError(f"factor {factor}.evidence_ids must be a sequence")
        ids = tuple(_text(item, "evidence_id") for item in self.evidence_ids)
        if len(ids) != len(set(ids)):
            raise ValueError(f"factor {factor} contains duplicate evidence IDs")
        object.__setattr__(self, "evidence_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "score": self.score,
            "evidence_ids": list(self.evidence_ids),
            "availability": self.availability,
            "reliability": self.reliability,
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
    event_risk: EventRiskAssessment | Mapping[str, Any] | str | None = None
    scoring_profile_name: str | None = None
    scoring_model_version: int | None = None
    score_coverage: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        if not isinstance(self.factor_scores, Mapping):
            raise ValueError("factor_scores must be an object")
        parsed: dict[str, FactorScore | None] = {}
        for raw_factor, value in self.factor_scores.items():
            factor = _text(raw_factor, "factor").lower()
            if factor in parsed:
                raise ValueError(f"factor_scores contains duplicate key {factor}")
            if value is None:
                parsed[factor] = FactorScore(factor, None, availability="MISSING")
            elif isinstance(value, FactorScore):
                if value.factor != factor:
                    raise ValueError(f"factor score key {factor!r} does not match {value.factor!r}")
                parsed[factor] = value
            elif isinstance(value, Mapping):
                factor_name = str(value.get("factor", factor)).strip().lower()
                availability = str(
                    value.get(
                        "availability",
                        "MISSING" if value.get("score") is None else "AVAILABLE",
                    )
                ).strip().upper()
                if factor_name != factor or ("score" not in value and availability == "AVAILABLE"):
                    raise ValueError(f"factor score key {factor!r} is malformed")
                parsed[factor] = FactorScore(
                    factor,
                    value.get("score"),
                    tuple(value.get("evidence_ids", ())),
                    availability,
                    value.get(
                        "reliability",
                        1.0 if availability == "AVAILABLE" else 0.0,
                    ),
                )
            elif hasattr(value, "score"):
                availability = str(
                    getattr(
                        value,
                        "availability",
                        "MISSING" if value.score is None else "AVAILABLE",
                    )
                ).strip().upper()
                parsed[factor] = FactorScore(
                    factor,
                    value.score,
                    tuple(
                        getattr(value, "supporting_evidence_ids", ())
                    ) + tuple(getattr(value, "contrary_evidence_ids", ())),
                    availability,
                    getattr(
                        value,
                        "reliability",
                        1.0 if availability == "AVAILABLE" else 0.0,
                    ),
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
        event_risk = self.event_risk
        if event_risk is not None and not isinstance(event_risk, EventRiskAssessment):
            event_risk = EventRiskAssessment.from_mapping(event_risk)
        if event_risk is None and self.severe_event:
            event_risk = EventRiskAssessment(
                "SEVERE", reasons=("legacy severe_event flag",)
            )
        if event_risk is not None and event_risk.blocks_new_risk:
            object.__setattr__(self, "severe_event", True)
        object.__setattr__(self, "event_risk", event_risk)
        if self.scoring_profile_name is not None:
            object.__setattr__(
                self,
                "scoring_profile_name",
                _text(self.scoring_profile_name, "scoring_profile_name").lower(),
            )
        if self.scoring_model_version is not None and (
            isinstance(self.scoring_model_version, bool)
            or not isinstance(self.scoring_model_version, int)
            or self.scoring_model_version < 1
        ):
            raise ValueError("scoring_model_version must be a positive integer or null")
        if self.score_coverage is not None:
            coverage = float(self.score_coverage)
            if not math.isfinite(coverage) or not 0 <= coverage <= 1:
                raise ValueError("score_coverage must be finite and in [0, 1] or null")
            object.__setattr__(self, "score_coverage", coverage)
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
            "event_risk": self.event_risk.as_dict() if self.event_risk is not None else None,
            "scoring_profile_name": self.scoring_profile_name,
            "scoring_model_version": self.scoring_model_version,
            "score_coverage": self.score_coverage,
        }


__all__ = [
    "AVAILABILITY_STATES",
    "AssetAssessment",
    "EventRiskAssessment",
    "Evidence",
    "FactorScore",
    "contains_private_reasoning",
]
