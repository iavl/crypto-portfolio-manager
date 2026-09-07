"""Deterministic interpretation of normalized capital-flow observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...facts.models import FlowFacts
from ...models.metrics_history import MetricObservation
from ...models.policy import Policy, resolve_policy
from ..metric_history import build_factor_facts


_DEFAULT_V1_RULES = {"positive_threshold": 0.0, "negative_threshold": 0.0}
_HORIZON_WEIGHTS = {"1d": 0.1, "7d": 0.3, "30d": 0.6}


@dataclass(frozen=True)
class FlowFactorResult:
    score: float | None
    state: str
    facts: FlowFacts
    confidence: str
    coverage: float
    reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    normalized_flow: float | None = None
    horizon_ratios: Mapping[str, float | None] | None = None
    source_confidence: str = "HIGH"

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
        if self.source_confidence not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("flow source confidence is unsupported")
        coverage = float(self.coverage)
        if not math.isfinite(coverage) or not 0 <= coverage <= 1:
            raise ValueError("flow coverage must be in [0, 1]")
        if not isinstance(self.facts, FlowFacts):
            raise ValueError("flow facts must be FlowFacts")
        if self.normalized_flow is not None:
            normalized = float(self.normalized_flow)
            if not math.isfinite(normalized):
                raise ValueError("normalized_flow must be finite or null")
            object.__setattr__(self, "normalized_flow", normalized)
        ratios = dict(self.horizon_ratios or {})
        if set(ratios) - set(_HORIZON_WEIGHTS):
            raise ValueError("horizon_ratios contains an unknown horizon")
        for key, value in ratios.items():
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(float(value))):
                raise ValueError(f"horizon_ratios.{key} must be finite or null")
        object.__setattr__(self, "horizon_ratios", ratios)
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
            "normalized_flow": self.normalized_flow,
            "horizon_ratios": dict(self.horizon_ratios),
            "source_confidence": self.source_confidence,
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


def _context_rules(policy: Policy) -> Mapping[str, float]:
    # Sign classification is regime context; v2 scoring uses normalized ratios.
    return {
        **_DEFAULT_V1_RULES,
        **(
            {key: value for key, value in policy.factor_rules.get("flows", {}).items() if key in _DEFAULT_V1_RULES}
        ),
    }


def classify_flow_state(
    value: Any,
    *,
    policy: Policy | None = None,
    positive_threshold: float | None = None,
    negative_threshold: float | None = None,
) -> str:
    """Map one numeric flow to a contextual POSITIVE/NEUTRAL/NEGATIVE state."""
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
    rules = dict(_context_rules(policy or resolve_policy()))
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


def _ratio(flow: Any, denominator: Any) -> float | None:
    flow = _number(flow)
    denominator = _number(denominator)
    if flow is None or denominator is None:
        return None
    if denominator <= 0:
        raise ValueError("flow denominator must be finite and > 0")
    return flow / denominator


def _normalized_ratios(value: Any) -> dict[str, float | None]:
    if isinstance(value, Mapping) and "metric_key" in value:
        value = MetricObservation.from_mapping(value)
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, Mapping) for item in value):
        value = tuple(MetricObservation.from_mapping(item) for item in value)
    if isinstance(value, MetricObservation):
        metadata = value.metadata or {}
        horizon = next(
            (item for item in _HORIZON_WEIGHTS if item in value.metric_key.lower()),
            "30d",
        )
        if any(key in metadata for key in ("normalized_flow_ratio", "normalized_flow", "flow_ratio")):
            return {horizon: _number(next(metadata[key] for key in ("normalized_flow_ratio", "normalized_flow", "flow_ratio") if key in metadata))}
        return {horizon: _ratio(value.value, metadata.get("denominator", metadata.get("aum", metadata.get("market_cap"))))}
    if isinstance(value, (list, tuple)) and all(isinstance(item, MetricObservation) for item in value):
        ratios: dict[str, float | None] = {}
        for item in value:
            ratio = _normalized_ratios(item)
            if set(ratios) & set(ratio):
                raise ValueError("multiple flow observations for one horizon require explicit source selection")
            ratios.update(ratio)
        return ratios or {"30d": None}
    if isinstance(value, Mapping) and isinstance(value.get("observations"), (list, tuple)):
        observations = value["observations"]
        if all(isinstance(item, MetricObservation) for item in observations):
            return _normalized_ratios(observations)
        if all(isinstance(item, Mapping) for item in observations):
            return _normalized_ratios(tuple(MetricObservation.from_mapping(item) for item in observations))
    if isinstance(value, FlowFacts):
        current = value.current
    elif isinstance(value, Mapping):
        current = value.get("current", value)
        if not isinstance(current, Mapping):
            current = value
    else:
        return {"30d": None}
    for key in ("normalized_flow_ratio", "normalized_flow", "normalized_value", "flow_ratio"):
        if key in current:
            return {"30d": _number(current[key])}
    if "value" in current and any(key in current for key in ("denominator", "aum", "market_cap")):
        return {"30d": _ratio(current["value"], current.get("denominator", current.get("aum", current.get("market_cap"))))}
    common_denominator = next(
        (current[key] for key in ("denominator", "aum", "market_cap") if key in current),
        None,
    )
    if ("flow" in current or "net_flow" in current) and common_denominator is not None:
        return {"30d": _ratio(current.get("flow", current.get("net_flow")), common_denominator)}
    ratios: dict[str, float | None] = {}
    for horizon in ("7d", "30d"):
        normalized = [
            current[key]
            for key in current
            if str(key).lower().endswith(f"etf_net_to_aum_{horizon}")
        ]
        if len(normalized) > 1:
            raise ValueError(f"multiple normalized ETF flow observations for {horizon}")
        if normalized:
            ratios[horizon] = _number(normalized[0])
    for horizon in _HORIZON_WEIGHTS:
        if horizon in ratios:
            continue
        flow = next(
            (current[key] for key in current if
             (str(key).lower().endswith(f"_{horizon}") or str(key).lower() == horizon)
             and not any(prefix in str(key).lower() for prefix in ("denominator", "aum", "market_cap"))),
            None,
        )
        denominator = next(
            (
                current[key]
                for key in current
                if (
                    str(key).lower() in {f"denominator_{horizon}", f"aum_{horizon}", f"market_cap_{horizon}"}
                    or (
                        str(key).lower().endswith(f"_{horizon}")
                        and any(prefix in str(key).lower() for prefix in ("denominator", "aum", "market_cap"))
                    )
                )
            ),
            common_denominator,
        )
        if flow is not None or denominator is not None:
            ratios[horizon] = _ratio(flow, denominator)
    if ratios:
        return ratios
    if "flow" in current or "net_flow" in current:
        return {"30d": _ratio(current.get("flow", current.get("net_flow")), current.get("denominator"))}
    return {"30d": None}


def _v2_score(value: float, neutral: float, strong: float) -> float:
    if abs(value) <= neutral:
        return 50.0
    span = strong - neutral
    if value > 0:
        return 50.0 + 50.0 * (min(value, strong) - neutral) / span
    return 50.0 + 50.0 * (max(value, -strong) + neutral) / span


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
    if isinstance(value, Mapping) and "metric_key" in value:
        value = MetricObservation.from_mapping(value)
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, Mapping) for item in value):
        value = tuple(MetricObservation.from_mapping(item) for item in value)
    source_observations = (value,) if isinstance(value, MetricObservation) else (
        tuple(value) if isinstance(value, (list, tuple)) and all(isinstance(item, MetricObservation) for item in value) else ()
    )
    source_confidence = min((item.confidence for item in source_observations),
                            key=("LOW", "MEDIUM", "HIGH").index, default="HIGH")
    if isinstance(value, FlowFacts):
        facts = value
    elif isinstance(value, MetricObservation) or (isinstance(value, Mapping) and "metric_key" in value):
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
    elif isinstance(value, Mapping):
        current = value.get("current", value)
        if not isinstance(current, Mapping):
            raise ValueError("flow mapping current must be an object")
        facts = FlowFacts(
            symbol=symbol,
            current=dict(current),
            previous={},
            changes={},
            trends={},
            coverage=1.0,
            freshness="CURRENT",
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

    if resolved.policy_version == 1:
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

    ratios = _normalized_ratios(value if value is not None else facts)
    available = {key: ratio for key, ratio in ratios.items() if ratio is not None}
    rules = resolved.factor_rules["flows"]
    neutral = float(rules["neutral_abs_max"])
    strong = float(rules["strong_abs"])
    if available:
        total = sum(_HORIZON_WEIGHTS[key] for key in available)
        normalized = sum(float(ratio) * _HORIZON_WEIGHTS[key] for key, ratio in available.items()) / total
        score = _v2_score(normalized, neutral, strong)
        state = "POSITIVE" if normalized > neutral else "NEGATIVE" if normalized < -neutral else "NEUTRAL"
        coverage = total / sum(_HORIZON_WEIGHTS.values())
        confidence = "HIGH" if coverage == 1 else "MEDIUM" if coverage >= 0.6 else "LOW"
        reasons = (f"normalized flow ratio is {normalized:+.3%}",)
    else:
        normalized = None
        score = None
        state = "UNKNOWN"
        coverage = 0.0
        confidence = "LOW"
        reasons = ("normalized flow is unavailable because its denominator is missing",)
    return FlowFactorResult(
        score=score,
        state=state,
        facts=facts,
        confidence=confidence,
        coverage=coverage,
        reasons=reasons,
        evidence_ids=facts.source_ids,
        normalized_flow=normalized,
        horizon_ratios=ratios,
        source_confidence=source_confidence,
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
