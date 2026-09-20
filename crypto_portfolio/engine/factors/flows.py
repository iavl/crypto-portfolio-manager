"""Deterministic interpretation of normalized capital-flow observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...facts.models import FlowFacts
from ...metrics_registry import (
    BNB_SUPPLY_CHANGE_HORIZON_DAYS,
    BNB_SUPPLY_CHANGE_PREFIX,
    validate_metric_observation_metadata,
)
from ...models.metrics_history import MetricObservation
from ...models.policy import Policy, resolve_policy
from ..metric_history import build_factor_facts


_CONTEXT_THRESHOLDS = {"positive_threshold": 0.0, "negative_threshold": 0.0}
_HORIZON_WEIGHTS = {"1d": 0.1, "7d": 0.3, "30d": 0.6}

# Two explicitly separate scoring methods share this result contract.
METHOD_NORMALIZED_FLOW = "normalized_flow_threshold"
METHOD_SUPPLY_CHANGE_PERCENTILE = "supply_change_percentile"
_FLOW_METHODS = (METHOD_NORMALIZED_FLOW, METHOD_SUPPLY_CHANGE_PERCENTILE)

# Design default, recorded in the canonical policy as the BNB supply-proxy
# rule.  It is not claimed to be return-optimized.
_DEFAULT_SUPPLY_HORIZON_WEIGHTS = {"7d": 0.2, "30d": 0.4, "90d": 0.4}
_SUPPLY_HORIZON_DETAIL_FIELDS = {
    "change_ratio",
    "abs_change_percentile",
    "raw_score",
    "weight",
    "contribution",
    "sample_count",
    "calibration_state",
}


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
    method: str = METHOD_NORMALIZED_FLOW
    horizons: Mapping[str, Mapping[str, Any]] | None = None
    effective_weight: float | None = None

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
        method = str(self.method).strip().lower()
        if method not in _FLOW_METHODS:
            raise ValueError("flow method is unsupported")
        object.__setattr__(self, "method", method)
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
        detail = _validated_horizon_detail(self.horizons)
        object.__setattr__(self, "horizons", detail)
        if self.effective_weight is not None:
            weight = float(self.effective_weight)
            if not math.isfinite(weight) or not 0 <= weight <= 1:
                raise ValueError("flow effective_weight must be in [0, 1]")
            object.__setattr__(self, "effective_weight", weight)
        if method == METHOD_SUPPLY_CHANGE_PERCENTILE and self.normalized_flow is not None:
            # A historical rank is not a normalized flow ratio and must never be
            # reported as one.
            raise ValueError("the supply-change percentile method cannot publish a normalized_flow ratio")
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
            "method": self.method,
            "horizons": {key: dict(value) for key, value in (self.horizons or {}).items()},
            "effective_weight": self.effective_weight,
        }


def _validated_horizon_detail(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("flow horizons must be an object")
    unknown = set(value) - set(BNB_SUPPLY_CHANGE_HORIZON_DAYS)
    if unknown:
        raise ValueError("flow horizons contains an unknown horizon")
    result: dict[str, dict[str, Any]] = {}
    for horizon, detail in value.items():
        if not isinstance(detail, Mapping):
            raise ValueError(f"flow horizons.{horizon} must be an object")
        unexpected = set(detail) - _SUPPLY_HORIZON_DETAIL_FIELDS
        if unexpected:
            raise ValueError(f"flow horizons.{horizon} contains unknown fields")
        row = dict(detail)
        for field in ("change_ratio", "raw_score", "weight", "contribution"):
            if row.get(field) is not None and (
                isinstance(row[field], bool) or not isinstance(row[field], (int, float)) or not math.isfinite(float(row[field]))
            ):
                raise ValueError(f"flow horizons.{horizon}.{field} must be finite numeric")
        percentile = row.get("abs_change_percentile")
        if percentile is not None:
            if isinstance(percentile, bool) or not isinstance(percentile, (int, float)) or not 0.0 <= float(percentile) <= 1.0:
                raise ValueError(f"flow horizons.{horizon}.abs_change_percentile must be in [0, 1]")
        result[str(horizon)] = row
    return result


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("flow value must be numeric or null")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("flow value must be finite")
    return value


def _supply_change_observations(
    observations: Iterable[MetricObservation],
) -> dict[str, MetricObservation]:
    """Collect the BNB supply-proxy observations, one per horizon."""
    by_horizon: dict[str, MetricObservation] = {}
    for item in observations:
        key = str(item.metric_key).strip().lower()
        if not key.startswith(BNB_SUPPLY_CHANGE_PREFIX):
            continue
        horizon = key[len(BNB_SUPPLY_CHANGE_PREFIX):]
        if horizon not in BNB_SUPPLY_CHANGE_HORIZON_DAYS:
            raise ValueError(f"unsupported BNB supply-change horizon: {horizon}")
        if horizon in by_horizon:
            raise ValueError(f"multiple BNB supply-change observations for {horizon}")
        by_horizon[horizon] = item
    return by_horizon


def _supply_horizon_weights(policy: Policy) -> dict[str, float]:
    """Read the canonical BNB supply-proxy horizon weights."""
    rules = policy.factor_rules.get("flows")
    if not isinstance(rules, Mapping):
        raise ValueError("factor_rules.flows is required")
    raw = rules.get("supply_change_horizon_weights")
    if not isinstance(raw, Mapping):
        raise ValueError("factor_rules.flows.supply_change_horizon_weights is required")
    if set(raw) != set(BNB_SUPPLY_CHANGE_HORIZON_DAYS):
        raise ValueError("factor_rules.flows.supply_change_horizon_weights has unknown or missing horizons")
    weights: dict[str, float] = {}
    for horizon in BNB_SUPPLY_CHANGE_HORIZON_DAYS:
        number = _number(raw[horizon])
        if number is None or number <= 0:
            raise ValueError(f"BNB supply-change {horizon} weight must be finite and > 0")
        weights[horizon] = number
    total = sum(weights.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("BNB supply-change horizon weights must sum to 1")
    return weights


def _supply_horizon_detail(observation: MetricObservation) -> dict[str, Any]:
    """Derive one horizon's deterministic supply-change score.

    The raw score is ``50 + 50 * sign(change) * percentile`` of the absolute
    change against its own trailing history, so a zero change is exactly 50 and
    a negative change can never exceed 50.  A horizon whose history cannot
    calibrate a percentile stays unavailable rather than awarding a full score.
    """
    metadata = dict(observation.metadata or {})
    validate_metric_observation_metadata(
        observation.metric_key, observation.asset, observation.value, metadata
    )
    change = _number(observation.value)
    if change is None:
        raise ValueError("BNB supply change must be numeric")
    if change < -1.0:
        raise ValueError("BNB supply change must be >= -1")
    sample_count = metadata.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        raise ValueError("BNB supply-change sample_count must be a non-negative integer")
    calibration = str(metadata.get("calibration_state", "")).strip().upper()
    if calibration == "CALIBRATED":
        percentile = _number(metadata.get("abs_change_percentile"))
        if percentile is None or not 0.0 <= percentile <= 1.0:
            raise ValueError("a calibrated BNB supply horizon requires a percentile in [0, 1]")
        raw = 50.0 if change == 0.0 else 50.0 + 50.0 * math.copysign(1.0, change) * percentile
    elif calibration == "UNCALIBRATED":
        percentile = None
        raw = None
    else:
        raise ValueError("BNB supply-change calibration_state is unsupported")
    return {
        "change_ratio": change,
        "abs_change_percentile": percentile,
        "raw_score": raw,
        "sample_count": sample_count,
        "calibration_state": calibration,
    }


def _score_supply_change(
    observations: Mapping[str, MetricObservation],
    *,
    facts: FlowFacts,
    policy: Policy,
    source_confidence: str,
) -> FlowFactorResult:
    weights = _supply_horizon_weights(policy)
    total_weight = sum(weights.values())
    horizons: dict[str, dict[str, Any]] = {}
    available: dict[str, dict[str, Any]] = {}
    for horizon, observation in observations.items():
        row = _supply_horizon_detail(observation)
        row["weight"] = weights[horizon] / total_weight
        row["contribution"] = None
        horizons[horizon] = row
        if row["raw_score"] is not None:
            available[horizon] = row
    if not available:
        return FlowFactorResult(
            score=None,
            state="UNKNOWN",
            facts=facts,
            confidence="LOW",
            coverage=0.0,
            reasons=("BNB stablecoin supply change is unavailable for every horizon",),
            evidence_ids=facts.source_ids,
            method=METHOD_SUPPLY_CHANGE_PERCENTILE,
            horizons=horizons,
            effective_weight=0.0,
            source_confidence=source_confidence,
        )
    # Available raw scores are renormalised over their own weights, so the
    # factor completeness (and therefore reliability) carries the missing part
    # exactly once instead of re-penalising it here.
    effective = sum(weights[horizon] for horizon in available)
    coverage = effective / total_weight
    score = sum(
        float(row["raw_score"]) * weights[horizon] for horizon, row in available.items()
    ) / effective
    for horizon, row in available.items():
        row["contribution"] = float(row["raw_score"]) * weights[horizon] / effective
    tolerance = 1e-9
    if abs(score - 50.0) <= tolerance:
        state = "NEUTRAL"
    elif score > 50.0:
        state = "POSITIVE"
    else:
        state = "NEGATIVE"
    coverage_confidence = "HIGH" if coverage == 1.0 else "MEDIUM" if coverage >= 0.6 else "LOW"
    # A MEDIUM-confidence source is never reported as HIGH quality.
    confidence = min(coverage_confidence, source_confidence, key=("LOW", "MEDIUM", "HIGH").index)
    missing = sorted(set(observations) - set(available))
    reasons = (
        "BNB stablecoin supply change is scored against its own trailing 365-day history",
        *(f"{horizon} change {row['change_ratio']:+.2%} ranked at p={row['abs_change_percentile']:.0%}"
          for horizon, row in sorted(available.items())),
        *((f"{horizon} is uncalibrated",) for horizon in missing),
    )
    return FlowFactorResult(
        score=score,
        state=state,
        facts=facts,
        confidence=confidence,
        coverage=coverage,
        reasons=reasons,
        evidence_ids=facts.source_ids,
        method=METHOD_SUPPLY_CHANGE_PERCENTILE,
        horizons=horizons,
        effective_weight=coverage,
        source_confidence=source_confidence,
    )


def _context_rules(policy: Policy) -> Mapping[str, float]:
    # Sign classification is regime context; scoring uses normalized ratios.
    return {
        **_CONTEXT_THRESHOLDS,
        **(
            {key: value for key, value in policy.factor_rules.get("flows", {}).items() if key in _CONTEXT_THRESHOLDS}
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


def _score(value: float, neutral: float, strong: float) -> float:
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
    # An object form may carry a batch of observations, e.g. the deterministic
    # receipt value that replays a BNB supply-proxy calculation.
    if isinstance(value, Mapping) and isinstance(value.get("observations"), (list, tuple)):
        items = tuple(value["observations"])
        if not all(isinstance(item, (MetricObservation, Mapping)) for item in items):
            raise ValueError("flow observations must be observations or observation objects")
        if symbol == "MARKET" and value.get("asset") is not None:
            symbol = str(value["asset"]).strip().upper()
        value = tuple(
            item if isinstance(item, MetricObservation) else MetricObservation.from_mapping(item)
            for item in items
        )
    if isinstance(value, Mapping) and "metric_key" in value:
        value = MetricObservation.from_mapping(value)
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, Mapping) for item in value):
        value = tuple(MetricObservation.from_mapping(item) for item in value)
    source_observations = (value,) if isinstance(value, MetricObservation) else (
        tuple(value) if isinstance(value, (list, tuple)) and all(isinstance(item, MetricObservation) for item in value) else ()
    )
    source_confidence = min((item.confidence for item in source_observations),
                            key=("LOW", "MEDIUM", "HIGH").index, default="HIGH")
    supply_observations = _supply_change_observations(source_observations)
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


    if supply_observations:
        # A BSC stablecoin supply proxy is not a normalised ETF-style flow, so
        # it never touches the threshold method below.
        return _score_supply_change(
            supply_observations,
            facts=facts,
            policy=resolved,
            source_confidence=source_confidence,
        )
    ratios = _normalized_ratios(value if value is not None else facts)
    available = {key: ratio for key, ratio in ratios.items() if ratio is not None}
    rules = resolved.factor_rules["flows"]
    neutral = float(rules["neutral_abs_max"])
    strong = float(rules["strong_abs"])
    if available:
        total = sum(_HORIZON_WEIGHTS[key] for key in available)
        normalized = sum(float(ratio) * _HORIZON_WEIGHTS[key] for key, ratio in available.items()) / total
        score = _score(normalized, neutral, strong)
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


__all__ = [
    "FlowFactorResult",
    "calculate_flow_factor",
    "classify_flow_state",
]
