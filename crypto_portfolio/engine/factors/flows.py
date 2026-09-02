"""Deterministic interpretation of numeric capital-flow observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...facts.models import FlowFacts
from ...models.metrics_history import MetricObservation
from ...models.policy import Policy, resolve_policy
from ..metric_history import build_factor_facts


@dataclass(frozen=True)
class FlowFactorResult:
    score: float | None
    state: str
    facts: FlowFacts
    confidence: str
    coverage: float
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.score is not None:
            score = float(self.score)
            if not math.isfinite(score) or not 0 <= score <= 100:
                raise ValueError("flow score must be finite and in [0, 100]")
            object.__setattr__(self, "score", score)
        state = str(self.state).strip().upper()
        if state not in {"POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"}:
            raise ValueError("flow state is unsupported")
        confidence = str(self.confidence).strip().upper()
        if confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("flow confidence is unsupported")
        coverage = float(self.coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("flow coverage must be in [0, 1]")
        if not isinstance(self.facts, FlowFacts):
            raise ValueError("flow facts must be FlowFacts")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        ids = tuple(str(item).strip() for item in self.evidence_ids)
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("flow evidence_ids must be unique non-empty strings")
        object.__setattr__(self, "evidence_ids", ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "state": self.state,
            "facts": self.facts.as_dict(),
            "confidence": self.confidence,
            "coverage": self.coverage,
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
        }


def _rules(policy: Policy) -> Mapping[str, float]:
    return {
        "positive_threshold": 0.0,
        "negative_threshold": 0.0,
        **policy.factor_rules.get("flows", {}),
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("flow value must be numeric or null")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("flow value must be finite")
    return value


def classify_flow_state(
    value: Any,
    *,
    policy: Policy | None = None,
    positive_threshold: float | None = None,
    negative_threshold: float | None = None,
) -> str:
    """Map one numeric flow to POSITIVE, NEUTRAL, NEGATIVE, or UNKNOWN."""
    if isinstance(value, FlowFacts):
        values = list(value.current.values())
        value = values[-1] if values else None
    elif isinstance(value, (list, tuple)):
        value = value[-1] if value else None
    elif isinstance(value, Mapping):
        current = value.get("current")
        if isinstance(current, Mapping):
            values = tuple(current.values())
            value = values[-1] if values else None
        else:
            value = value.get("value", value.get("flow"))
    number = _number(value)
    if number is None:
        return "UNKNOWN"
    rules = dict(_rules(policy or resolve_policy()))
    if positive_threshold is not None:
        rules["positive_threshold"] = _number(positive_threshold)
    if negative_threshold is not None:
        rules["negative_threshold"] = _number(negative_threshold)
    if rules["negative_threshold"] > rules["positive_threshold"] or rules["negative_threshold"] > 0 or rules["positive_threshold"] < 0:
        raise ValueError("flow thresholds must satisfy negative <= 0 <= positive")
    if number > rules["positive_threshold"]:
        return "POSITIVE"
    if number < rules["negative_threshold"]:
        return "NEGATIVE"
    return "NEUTRAL"


def calculate_flow_factor(
    value: FlowFacts | MetricObservation | Mapping[str, Any] | float | int | None = None,
    *,
    symbol: str = "MARKET",
    previous: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
    policy: Policy | None = None,
    observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
) -> FlowFactorResult:
    resolved = policy or resolve_policy()
    if value is not None and observations is not None:
        raise ValueError("provide only one of value or observations")
    if observations is not None:
        value = tuple(observations)
    if isinstance(value, FlowFacts):
        facts = value
    elif isinstance(value, MetricObservation) or (
        isinstance(value, Mapping) and "metric_key" in value
    ):
        if isinstance(value, MetricObservation):
            symbol = value.asset
        else:
            symbol = str(value.get("asset", symbol)).strip().upper()
        facts = build_factor_facts(
            (value,),
            symbol=symbol,
            factor="capital_flows",
            previous_observations=previous,
            fact_type=FlowFacts,
        )
    elif isinstance(value, (list, tuple)):
        if all(item is None or (isinstance(item, (int, float)) and not isinstance(item, bool)) for item in value):
            numbers = [_number(item) for item in value]
            latest = numbers[-1] if numbers else None
            prior = numbers[-2] if len(numbers) > 1 else None
            absolute = latest - prior if latest is not None and prior is not None else None
            percentage = absolute / prior if absolute is not None and prior else None
            facts = FlowFacts(
                symbol=symbol,
                current={"flow": latest},
                previous={"flow": prior},
                changes={"flow": {"absolute_change": absolute, "percentage_change": percentage}},
                trends={"flow": classify_flow_state(latest, policy=resolved)},
                coverage=1.0 if latest is not None else 0.0,
                freshness="CURRENT" if latest is not None else "UNKNOWN",
            )
        else:
            facts = build_factor_facts(
                value,
                symbol=(
                    next(iter({item.asset for item in value if isinstance(item, MetricObservation)}), symbol)
                    if symbol == "MARKET" and all(isinstance(item, MetricObservation) for item in value)
                    else symbol
                ),
                factor="capital_flows",
                previous_observations=previous,
                fact_type=FlowFacts,
            )
    else:
        number = _number(value)
        facts = FlowFacts(
            symbol=symbol,
            current={"flow": number},
            previous={},
            changes={},
            trends={},
            coverage=1.0 if number is not None else 0.0,
            freshness="CURRENT" if number is not None else "UNKNOWN",
        )
    latest = next(reversed(tuple(facts.current.values())), None) if facts.current else None
    state = classify_flow_state(latest, policy=resolved)
    score = {"POSITIVE": 100.0, "NEUTRAL": 50.0, "NEGATIVE": 0.0}.get(state)
    reason = "flow is unavailable" if state == "UNKNOWN" else f"flow state is {state}"
    return FlowFactorResult(
        score=score,
        state=state,
        facts=facts,
        confidence="HIGH" if facts.coverage >= 1 else "LOW",
        coverage=facts.coverage,
        reasons=(reason,),
        evidence_ids=facts.source_ids,
    )


flow_factor = calculate_flow_factor
deterministic_flow_state = classify_flow_state
calculate_flow_state = classify_flow_state
flow_state = classify_flow_state


__all__ = [
    "FlowFactorResult",
    "calculate_flow_factor",
    "calculate_flow_state",
    "classify_flow_state",
    "deterministic_flow_state",
    "flow_factor",
    "flow_state",
]
