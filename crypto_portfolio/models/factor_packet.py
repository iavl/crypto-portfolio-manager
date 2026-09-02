"""Compact typed inputs for bounded semantic factor judgment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ..facts.models import FactBase
from .evidence import AssetAssessment


_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_TRENDS = {"IMPROVING", "STABLE", "DETERIORATING", "MIXED", "UNKNOWN", "NEUTRAL"}
_FORBIDDEN_KEYS = {
    "raw",
    "raw_ohlcv",
    "ohlcv",
    "candles",
    "raw_webpage",
    "raw_webpages",
    "raw_data",
    "raw_response",
    "source_content",
    "response_body",
    "page_content",
    "webpage",
    "webpages",
    "html",
    "full_history",
    "history",
    "metric_history",
    "metrics_history",
    "full_metrics_history",
    "volume_profile_bins",
    "bins",
    "chain_of_thought",
    "scratchpad",
}


def freeze_packet_value(value: Any, *, path: str = "packet") -> Any:
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path} contains an invalid key")
            normalized = key.strip().lower()
            if normalized in _FORBIDDEN_KEYS or normalized.startswith("raw_") or normalized.endswith("_html"):
                raise ValueError(f"{path} must not contain raw or full-history field {key}")
            frozen[key] = freeze_packet_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_packet_value(item, path=f"{path}[]") for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must contain finite values")
    return value


def thaw_packet_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_packet_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_packet_value(item) for item in value]
    return value


def _fact(value: Any, field_name: str) -> FactBase | Mapping[str, Any] | None:
    if value is None or isinstance(value, (FactBase, Mapping)):
        return value if isinstance(value, FactBase) else freeze_packet_value(value, path=field_name) if value is not None else None
    raise ValueError(f"{field_name} must be a FactBase, mapping, or null")


def _fact_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return value.as_dict() if isinstance(value, FactBase) else thaw_packet_value(value)


def _ids(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain strings")
    result = tuple(item.strip() for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must contain unique non-empty strings")
    return result


@dataclass(frozen=True)
class FactorJudgment:
    """Structured output accepted from a semantic factor model."""

    factor: str
    score: float
    confidence: str
    supporting_evidence_ids: tuple[str, ...] = ()
    contrary_evidence_ids: tuple[str, ...] = ()
    trend: str = "UNKNOWN"
    summary: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.factor, str) or not self.factor.strip():
            raise ValueError("judgment.factor must be non-empty")
        factor = self.factor.strip().lower()
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("judgment.score must be a number")
        score = float(self.score)
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("judgment.score must be finite and in [0, 100]")
        confidence = str(self.confidence).strip().upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("judgment.confidence must be HIGH, MEDIUM, or LOW")
        trend = str(self.trend).strip().upper()
        if trend not in _TRENDS:
            raise ValueError("judgment.trend is unsupported")
        if not isinstance(self.summary, str):
            raise ValueError("judgment.summary must be a string")
        object.__setattr__(self, "factor", factor)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "supporting_evidence_ids", _ids(self.supporting_evidence_ids, "supporting_evidence_ids"))
        object.__setattr__(self, "contrary_evidence_ids", _ids(self.contrary_evidence_ids, "contrary_evidence_ids"))
        object.__setattr__(self, "trend", trend)
        object.__setattr__(self, "summary", self.summary.strip())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FactorJudgment":
        if not isinstance(value, Mapping):
            raise ValueError("factor judgment must be an object")
        allowed = {
            "factor", "score", "confidence", "supporting_evidence_ids", "supporting_evidence",
            "contrary_evidence_ids", "contrary_evidence", "trend", "summary",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"factor judgment contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            factor=value.get("factor"),
            score=value.get("score"),
            confidence=value.get("confidence"),
            supporting_evidence_ids=tuple(value.get("supporting_evidence_ids", value.get("supporting_evidence", ()))),
            contrary_evidence_ids=tuple(value.get("contrary_evidence_ids", value.get("contrary_evidence", ()))),
            trend=value.get("trend", "UNKNOWN"),
            summary=value.get("summary", ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "score": self.score,
            "confidence": self.confidence,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contrary_evidence_ids": list(self.contrary_evidence_ids),
            "trend": self.trend,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class AssetFactorPacket:
    symbol: str
    trend_facts: FactBase | Mapping[str, Any] | None = None
    valuation_facts: FactBase | Mapping[str, Any] | None = None
    fundamental_facts: FactBase | Mapping[str, Any] | None = None
    onchain_facts: FactBase | Mapping[str, Any] | None = None
    flow_facts: FactBase | Mapping[str, Any] | None = None
    relative_strength_facts: FactBase | Mapping[str, Any] | None = None
    event_facts: FactBase | Mapping[str, Any] | None = None
    coverage: float | None = None
    previous_assessment: AssetAssessment | Mapping[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("factor packet symbol must be non-empty")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        fact_fields = (
            "trend_facts", "valuation_facts", "fundamental_facts", "onchain_facts",
            "flow_facts", "relative_strength_facts", "event_facts",
        )
        for field_name in fact_fields:
            fact = _fact(getattr(self, field_name), field_name)
            if isinstance(fact, FactBase):
                freeze_packet_value(fact.as_dict(), path=field_name)
                if fact.symbol != self.symbol:
                    raise ValueError(f"{field_name} symbol does not match packet symbol")
            if isinstance(fact, Mapping) and fact.get("symbol") is not None and str(fact["symbol"]).strip().upper() != self.symbol:
                raise ValueError(f"{field_name} symbol does not match packet symbol")
            object.__setattr__(self, field_name, fact)
        assessments = self.previous_assessment
        if assessments is not None and not isinstance(assessments, AssetAssessment):
            if not isinstance(assessments, Mapping):
                raise ValueError("previous_assessment must be an AssetAssessment, mapping, or null")
            assessments = AssetAssessment.from_mapping(self.symbol, assessments)
        object.__setattr__(self, "previous_assessment", assessments)
        coverage = self.coverage
        if coverage is None:
            fact_coverages = [getattr(value, "coverage", None) for value in (getattr(self, name) for name in fact_fields)]
            fact_coverages = [float(value) for value in fact_coverages if value is not None]
            coverage = sum(fact_coverages) / len(fact_coverages) if fact_coverages else 0.0
        if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
            raise ValueError("factor packet coverage must be a number")
        coverage = float(coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("factor packet coverage must be finite and in [0, 1]")
        object.__setattr__(self, "coverage", coverage)
        ids = list(_ids(self.evidence_ids, "evidence_ids"))
        for fact in (getattr(self, name) for name in fact_fields):
            if isinstance(fact, FactBase):
                ids.extend(fact.source_ids)
            elif isinstance(fact, Mapping):
                ids.extend(fact.get("source_ids", ()))
        object.__setattr__(self, "evidence_ids", _ids(tuple(dict.fromkeys(ids)), "evidence_ids"))

    @property
    def fundamentals_facts(self):
        return self.fundamental_facts

    @property
    def relative_facts(self):
        return self.relative_strength_facts

    @property
    def facts(self) -> Mapping[str, FactBase | Mapping[str, Any] | None]:
        return MappingProxyType({
            "trend": self.trend_facts,
            "valuation": self.valuation_facts,
            "fundamentals": self.fundamental_facts,
            "onchain": self.onchain_facts,
            "capital_flows": self.flow_facts,
            "relative_strength_btc": self.relative_strength_facts,
            "event_risk": self.event_facts,
        })

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"symbol": self.symbol, "coverage": self.coverage}
        names = (
            ("trend_facts", self.trend_facts),
            ("valuation_facts", self.valuation_facts),
            ("fundamental_facts", self.fundamental_facts),
            ("onchain_facts", self.onchain_facts),
            ("flow_facts", self.flow_facts),
            ("relative_strength_facts", self.relative_strength_facts),
            ("event_facts", self.event_facts),
        )
        for name, value in names:
            if value is not None:
                result[name] = _fact_dict(value)
        if self.previous_assessment is not None:
            result["previous_assessment"] = self.previous_assessment.as_dict() if isinstance(self.previous_assessment, AssetAssessment) else thaw_packet_value(self.previous_assessment)
        if self.evidence_ids:
            result["evidence_ids"] = list(self.evidence_ids)
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AssetFactorPacket":
        if not isinstance(value, Mapping):
            raise ValueError("factor packet must be an object")
        return cls(**dict(value))

    semantic_input = as_dict


__all__ = [
    "AssetFactorPacket",
    "FactorJudgment",
    "freeze_packet_value",
    "thaw_packet_value",
]
