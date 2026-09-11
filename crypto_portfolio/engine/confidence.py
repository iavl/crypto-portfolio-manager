"""Pure confidence calculations for data, regime, and decisions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from dataclasses import dataclass, field as dataclass_field
import math
from typing import Any

from ..models.confidence import (
    ConfidenceCap,
    ConfidenceDimension,
    ConfidenceResult,
    DEFAULT_HIGH_MIN,
    DEFAULT_MEDIUM_MIN,
    DecisionConfidence,
    confidence_band,
)
from ..models.time import parse_timestamp


DATA_DIMENSION_WEIGHTS = {
    "coverage": 0.30,
    "freshness": 0.20,
    "source_quality": 0.20,
    "redundancy": 0.30,
}
REGIME_DOMAIN_WEIGHTS = {
    "trend": 0.20,
    "volatility": 0.15,
    "breadth": 0.15,
    "flows": 0.15,
    "portfolio_drawdown": 0.15,
    "systemic_risk": 0.20,
}
DECISION_COMPONENT_WEIGHTS = {
    "portfolio_data": 0.30,
    "regime_confidence": 0.20,
    "asset_evidence": 0.20,
    "portfolio_accounting": 0.15,
    "signal_agreement": 0.15,
}
_SOURCE_QUALITY = {
    "AUTHORITATIVE": 1.0,
    "PRIMARY": 1.0,
    "TIER_1": 1.0,
    "ESTABLISHED": 0.75,
    "TIER_2": 0.75,
    "REPUTABLE": 0.50,
    "TIER_3": 0.50,
    "UNKNOWN": 0.25,
    "UNVERIFIED": 0.25,
}
_DIRECTIONAL_SIGNS = {
    "POSITIVE": 1,
    "BULLISH": 1,
    "UP": 1,
    "RISING": 1,
    "HEALTHY": 1,
    "CLEAR": 1,
    "NORMAL": 1,
    "NEGATIVE": -1,
    "BEARISH": -1,
    "DOWN": -1,
    "FALLING": -1,
    "WEAK": -1,
    "CRITICAL": -1,
    "SEVERE": -1,
    "NEUTRAL": 0,
    "UNKNOWN": None,
    "UNAVAILABLE": None,
}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}


@dataclass(frozen=True)
class DecisionScope:
    """Assets whose evidence can change the contemplated portfolio action."""

    action: str
    relevant_assets: tuple[str, ...] = ()
    exposure_weights: Mapping[str, float] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        action = str(self.action).strip().upper()
        if action not in _ACTIONS:
            raise ValueError(f"decision scope action must be one of {sorted(_ACTIONS)}")
        assets = tuple(dict.fromkeys(str(item).strip().upper() for item in self.relevant_assets if str(item).strip()))
        weights: dict[str, float] = {}
        for raw_asset, raw_weight in self.exposure_weights.items():
            asset = str(raw_asset).strip().upper()
            if not asset:
                raise ValueError("decision scope exposure_weights contains an empty asset")
            weight = _bounded(raw_weight, f"decision scope exposure_weights.{asset}")
            weights[asset] = weight
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "relevant_assets", assets)
        object.__setattr__(self, "exposure_weights", weights)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "relevant_assets": list(self.relevant_assets),
            "exposure_weights": dict(self.exposure_weights),
        }


def _bounded(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f"{field} must be finite and in [0, 1]")
    return result


def _weights(value: Mapping[str, Any], field: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field} must be a non-empty object")
    result = {str(key).strip().lower(): _bounded(raw, f"{field}.{key}") for key, raw in value.items()}
    if not all(result):
        raise ValueError(f"{field} keys must be non-empty")
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field} weights must sum to 1")
    return result


def _policy_mapping(policy: Any, name: str, default: Mapping[str, Any]) -> Mapping[str, Any]:
    if policy is None:
        return default
    value = getattr(policy, name, None)
    if value is None and isinstance(policy, Mapping):
        value = policy.get(name)
    return value if isinstance(value, Mapping) else default


def _band_for_policy(score: float, policy: Any | None) -> str:
    medium, high = _thresholds_for_policy(policy)
    return confidence_band(score, medium_min=medium, high_min=high)


def _thresholds_for_policy(policy: Any | None) -> tuple[float, float]:
    confidence = _policy_mapping(policy, "confidence", {})
    thresholds = confidence.get("band_thresholds", {}) if isinstance(confidence, Mapping) else {}
    return float(thresholds.get("medium_min", DEFAULT_MEDIUM_MIN)), float(thresholds.get("high_min", DEFAULT_HIGH_MIN))


def _policy_cap(policy: Any | None, name: str, default: float) -> float:
    confidence = _policy_mapping(policy, "confidence", {})
    caps = confidence.get("caps", {}) if isinstance(confidence, Mapping) else {}
    value = caps.get(name, default) if isinstance(caps, Mapping) else default
    return _bounded(value, f"confidence cap {name}")


def _asset_evidence_policy(policy: Any | None) -> Mapping[str, Any]:
    confidence = _policy_mapping(policy, "confidence", {})
    value = confidence.get("asset_evidence", {}) if isinstance(confidence, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def confidence_deployment_factor(score: Any, policy: Any | None = None) -> float:
    """Resolve deployment sizing from the canonical confidence band policy."""
    value = _bounded(score, "confidence score")
    band = _band_for_policy(value, policy)
    execution = getattr(policy, "execution", None) if policy is not None else None
    if execution is None and isinstance(policy, Mapping):
        execution = policy.get("execution")
    configured = execution.get("confidence_deployment_factor", {}) if isinstance(execution, Mapping) else {}
    if not configured:
        from ..models.policy import resolve_policy

        configured = resolve_policy().execution["confidence_deployment_factor"]
    return _bounded(configured[band], f"confidence deployment factor {band}")


def _as_record(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "as_dict"):
        result = value.as_dict()
        if isinstance(result, Mapping):
            return result
    raise ValueError("confidence evidence must be a mapping or model with as_dict")


def source_quality_score(
    source: Any = None,
    *,
    tier: Any = None,
    quality: Any = None,
    mapping: Mapping[str, Any] | None = None,
) -> float:
    """Map source metadata to a bounded quality score without trusting HTTP status."""
    if quality is not None:
        return _bounded(quality, "source quality")
    if isinstance(tier, bool) or isinstance(tier, (int, float)):
        if not isinstance(tier, (int, float)) or not math.isfinite(float(tier)):
            raise ValueError("source tier must be 1, 2, or 3")
        tier_value = int(tier)
        if float(tier) != tier_value or tier_value not in {1, 2, 3}:
            raise ValueError("source tier must be 1, 2, or 3")
        return {1: 1.0, 2: 0.75, 3: 0.50}[tier_value]
    if isinstance(source, str):
        key = source.strip().upper().replace(" ", "_").replace("-", "_")
        configured = mapping or {}
        if key in configured:
            return _bounded(configured[key], f"source_quality.{key}")
        if key in _SOURCE_QUALITY:
            return _SOURCE_QUALITY[key]
    return _SOURCE_QUALITY["UNKNOWN"]


def _as_of(value: Any | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(timezone.utc)
    return parse_timestamp(value).astimezone(timezone.utc)


def freshness_score(
    observed_at: Any,
    *,
    as_of: Any | None = None,
    max_age_seconds: Any,
    half_life_seconds: Any,
    freshness: str | None = None,
) -> float:
    """Calculate exponential freshness with a hard max-age boundary."""
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
        raise ValueError("max_age_seconds must be a positive number")
    if isinstance(half_life_seconds, bool) or not isinstance(half_life_seconds, (int, float)):
        raise ValueError("half_life_seconds must be a positive number")
    max_age = float(max_age_seconds)
    half_life = float(half_life_seconds)
    if not math.isfinite(max_age) or max_age <= 0 or not math.isfinite(half_life) or half_life <= 0:
        raise ValueError("freshness ages must be positive finite numbers")
    if freshness is not None and str(freshness).strip().upper() == "UNKNOWN":
        return 0.0
    try:
        observed = parse_timestamp(observed_at).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return 0.0
    age = (_as_of(as_of) - observed).total_seconds()
    if age < 0 or age > max_age:
        return 0.0
    return math.exp(-math.log(2.0) * age / half_life)


def calculate_freshness(
    observed_at: Any,
    *,
    as_of: Any | None = None,
    max_age_seconds: Any,
    half_life_seconds: Any,
    freshness: str | None = None,
) -> tuple[float, str]:
    score = freshness_score(
        observed_at,
        as_of=as_of,
        max_age_seconds=max_age_seconds,
        half_life_seconds=half_life_seconds,
        freshness=freshness,
    )
    if score == 0.0:
        status = "UNKNOWN" if freshness and str(freshness).upper() == "UNKNOWN" else "STALE"
    else:
        status = "CURRENT"
    return score, status


def redundancy_score(source_groups: Iterable[Any], *, expected: bool = True) -> float:
    groups = {
        str(item).strip().lower()
        for item in source_groups
        if item is not None and str(item).strip()
    }
    count = len(groups)
    if count == 0:
        return 0.0
    if count == 1:
        return 0.5 if expected else 1.0
    if count == 2:
        return 0.8
    return 1.0


def _signal(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number == 0:
            return 0 if math.isfinite(number) else None
        return 1 if number > 0 else -1
    return _DIRECTIONAL_SIGNS.get(str(value).strip().upper())


def signal_consistency_score(
    observations: Sequence[Any],
    *,
    time_explainable_seconds: float = 86400.0,
) -> float:
    """Score directional agreement across independent structured observations."""
    records = [_as_record(item) for item in observations]
    valid = []
    for record in records:
        status = str(record.get("status", "SUCCESS")).strip().upper()
        freshness = str(record.get("freshness", "CURRENT")).strip().upper()
        if status in {"FAILED", "STALE", "CONFLICT", "UNKNOWN", "SKIPPED"} or freshness in {"STALE", "UNKNOWN"}:
            continue
        sign = _signal(record.get("direction", record.get("state", record.get("value"))))
        if sign is not None:
            valid.append((record, sign))
    if not valid:
        return 0.0
    groups = {
        str(record.get("source_group", record.get("source", "unknown"))).strip().lower()
        for record, _ in valid
    }
    if len(groups) <= 1:
        return 0.5
    signs = {sign for _, sign in valid}
    if len(signs) == 1:
        return 1.0
    try:
        timestamps = [parse_timestamp(record["observed_at"]) for record, _ in valid if record.get("observed_at")]
    except (KeyError, TypeError, ValueError):
        timestamps = []
    if timestamps and (max(timestamps) - min(timestamps)).total_seconds() > time_explainable_seconds:
        return 0.75
    return 0.0


def calculate_signal_consistency(observations: Sequence[Any], **kwargs: Any) -> float:
    return signal_consistency_score(observations, **kwargs)


def _metric_status(record: Mapping[str, Any]) -> str:
    status = record.get("status")
    if status is not None:
        return str(status).strip().upper()
    freshness = str(record.get("freshness", "CURRENT")).strip().upper()
    if freshness == "STALE":
        return "STALE"
    if freshness == "UNKNOWN":
        return "UNKNOWN"
    if record.get("value") is None:
        return "MISSING"
    return "SUCCESS"


def _record_id(record: Mapping[str, Any]) -> str | None:
    for field in ("evidence_id", "observation_id", "id"):
        value = record.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _configured_freshness(
    metric_key: str,
    domain: str,
    freshness_policy: Mapping[str, Any] | None,
) -> tuple[float, float]:
    defaults = {
        "max_age_seconds": 7 * 86400,
        "half_life_seconds": 3 * 86400,
    }
    policy = freshness_policy or {}
    overrides = policy.get("metric_overrides", {}) if isinstance(policy, Mapping) else {}
    domains = policy.get("domain_defaults", {}) if isinstance(policy, Mapping) else {}
    selected = overrides.get(metric_key) or domains.get(domain) or defaults
    if not isinstance(selected, Mapping):
        raise ValueError("freshness policy entry must be an object")
    return float(selected.get("max_age_seconds", defaults["max_age_seconds"])), float(
        selected.get("half_life_seconds", defaults["half_life_seconds"])
    )


def calculate_data_confidence(
    observations: Iterable[Any],
    *,
    metric_weights: Mapping[str, float] | None = None,
    as_of: Any | None = None,
    freshness_policy: Mapping[str, Any] | None = None,
    dimension_weights: Mapping[str, float] | None = None,
    source_quality: Mapping[str, Any] | None = None,
    hard_critical_metrics: Iterable[str] = (),
    hard_critical_ceiling: float | None = None,
    applies_to: str = "DATA",
    policy: Any | None = None,
) -> ConfidenceResult:
    """Calculate data confidence with a fixed denominator and explicit caps."""
    records = [_as_record(item) for item in observations]
    if freshness_policy is None:
        freshness_policy = getattr(policy, "freshness_policy", None) if policy is not None else None
    if source_quality is None:
        configured_source_quality = getattr(policy, "source_quality", None) if policy is not None else None
        source_quality = configured_source_quality.get("tier_scores") if isinstance(configured_source_quality, Mapping) else None
    if metric_weights is None:
        keys = sorted({str(item.get("metric_key", item.get("factor", "metric"))).strip() for item in records})
        metric_weights = {key: 1.0 for key in keys if key}
    weights = {str(key).strip(): _bounded(value, f"metric weight {key}") for key, value in metric_weights.items()}
    if not weights:
        dimensions = {
            name: ConfidenceDimension(name, 0.0, weight, ("NO_APPLICABLE_EVIDENCE",))
            for name, weight in (dimension_weights or DATA_DIMENSION_WEIGHTS).items()
        }
        medium, high = _thresholds_for_policy(policy)
        return ConfidenceResult(0.0, 0.0, _band_for_policy(0.0, policy), dimensions, status="BLOCKED", reasons=("NO_APPLICABLE_EVIDENCE",), medium_min=medium, high_min=high)
    configured_dimensions = dimension_weights or _policy_mapping(policy, "confidence", {}).get("data_dimension_weights", DATA_DIMENSION_WEIGHTS)
    configured_dimensions = configured_dimensions or DATA_DIMENSION_WEIGHTS
    dim_weights = _weights(configured_dimensions, "data dimension weights")
    by_metric: dict[str, list[Mapping[str, Any]]] = {key: [] for key in weights}
    for record in records:
        key = str(record.get("metric_key", record.get("factor", ""))).strip()
        if key in by_metric:
            by_metric[key].append(record)
    applicable_total = sum(weights.values())
    coverage_numerator = 0.0
    freshness_values: list[tuple[float, float]] = []
    quality_values: list[tuple[float, float]] = []
    valid_records: list[Mapping[str, Any]] = []
    reasons: set[str] = set()
    evidence_ids: set[str] = set()
    critical = {str(item).strip() for item in hard_critical_metrics}
    hard_critical_ceiling = _policy_cap(
        policy,
        "hard_critical_missing",
        math.nextafter(DEFAULT_MEDIUM_MIN, 0.0) if hard_critical_ceiling is None else hard_critical_ceiling,
    )
    caps: list[ConfidenceCap] = []
    for key, weight in weights.items():
        rows = by_metric[key]
        for row in rows:
            item_id = _record_id(row)
            if item_id:
                evidence_ids.add(item_id)
        not_applicable = any(_metric_status(row) == "NOT_APPLICABLE" for row in rows)
        if not rows and key in critical:
            reasons.add(f"MISSING:{key}")
            caps.append(ConfidenceCap("HARD_CRITICAL_MISSING", hard_critical_ceiling, applies_to, f"hard-critical metric {key} is missing"))
            continue
        if not rows and key not in critical:
            reasons.add(f"MISSING:{key}")
            continue
        if not_applicable:
            applicable_total -= weight
            continue
        rows = sorted(rows, key=lambda row: str(row.get("observed_at", "")))
        latest = rows[-1]
        status = _metric_status(latest)
        if status in {"SUCCESS", "AVAILABLE"} and latest.get("value") is not None:
            coverage_numerator += weight
            valid_records.extend(row for row in rows if _metric_status(row) in {"SUCCESS", "AVAILABLE"})
        elif status == "STALE":
            reasons.add(f"STALE:{key}")
        elif status == "CONFLICT":
            reasons.add(f"CONFLICT:{key}")
        else:
            reasons.add(f"UNKNOWN:{key}")
        domain = str(latest.get("confidence_domain", latest.get("domain", ""))).strip().lower()
        max_age, half_life = _configured_freshness(key, domain, freshness_policy)
        freshness, freshness_status = calculate_freshness(
            latest.get("freshness_reference_at", latest.get("observed_at")),
            as_of=as_of,
            max_age_seconds=max_age,
            half_life_seconds=half_life,
            freshness=latest.get("freshness"),
        ) if latest.get("observed_at") is not None else (0.0, "UNKNOWN")
        freshness_values.append((freshness, weight))
        if freshness_status != "CURRENT":
            reasons.add(f"{freshness_status}:{key}")
            if key in critical:
                stale_ceiling = _policy_cap(policy, "hard_critical_stale", hard_critical_ceiling)
                caps.append(ConfidenceCap("HARD_CRITICAL_STALE", stale_ceiling, applies_to, f"hard-critical metric {key} is {freshness_status}"))
        quality = source_quality_score(
            latest.get("source"),
            tier=latest.get("authority_tier", latest.get("tier")),
            quality=latest.get("source_quality"),
            mapping=source_quality,
        )
        quality_values.append((quality, weight))
        if latest.get("fallback_used"):
            reasons.add(f"FALLBACK_SOURCE:{key}")
    denominator = applicable_total if applicable_total > 0 else 1.0
    coverage = coverage_numerator / denominator
    freshness = sum(value * weight for value, weight in freshness_values) / denominator
    quality = sum(value * weight for value, weight in quality_values) / denominator
    redundancy_values: list[tuple[float, float]] = []
    for key, weight in weights.items():
        rows = [row for row in valid_records if str(row.get("metric_key", row.get("factor", ""))).strip() == key]
        facts: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            fact_key = (
                str(row.get("asset", "")).strip().upper(),
                str(row.get("metric_key", row.get("factor", key))).strip().lower(),
                str(row.get("window", row.get("horizon", ""))).strip().lower(),
            )
            facts.setdefault(fact_key, []).append(row)
        if not facts:
            continue
        fact_scores = []
        for fact_rows in facts.values():
            groups = [
                row.get("source_group", row.get("source"))
                for row in fact_rows
                if row.get("source_group", row.get("source")) is not None
            ]
            expected = all(row.get("redundancy_expected", True) is not False for row in fact_rows)
            fact_scores.append(redundancy_score(groups, expected=expected))
        redundancy_values.append((sum(fact_scores) / len(fact_scores), weight))
    redundancy = (
        sum(value * weight for value, weight in redundancy_values) / sum(weight for _, weight in redundancy_values)
        if redundancy_values else 0.0
    )
    dimensions = {
        "coverage": ConfidenceDimension("coverage", coverage, dim_weights["coverage"], tuple(sorted(reasons))),
        "freshness": ConfidenceDimension("freshness", freshness, dim_weights["freshness"], tuple(sorted(reason for reason in reasons if reason.startswith(("STALE:", "UNKNOWN:"))))),
        "source_quality": ConfidenceDimension("source_quality", quality, dim_weights["source_quality"], tuple(sorted(reason for reason in reasons if reason.startswith("FALLBACK_SOURCE:")))),
        "redundancy": ConfidenceDimension("redundancy", redundancy, dim_weights["redundancy"]),
    }
    raw = sum(item.score * item.weight for item in dimensions.values())
    merged_caps = tuple(caps)
    score = min([raw, *(cap.ceiling for cap in merged_caps)])
    medium, high = _thresholds_for_policy(policy)
    status = "BLOCKED" if any(cap.ceiling < medium for cap in merged_caps) else "PROVISIONAL" if reasons else "AVAILABLE"
    return ConfidenceResult(
        raw_score=raw,
        score=score,
        band=_band_for_policy(score, policy),
        dimensions=dimensions,
        caps=merged_caps,
        reasons=tuple(sorted(reasons)),
        evidence_ids=tuple(sorted(evidence_ids)),
        status=status,
        medium_min=medium,
        high_min=high,
    )


def apply_confidence_caps(raw_score: Any, caps: Iterable[ConfidenceCap | Mapping[str, Any]]) -> float:
    raw = _bounded(raw_score, "raw_score")
    parsed = [cap if isinstance(cap, ConfidenceCap) else ConfidenceCap.from_mapping(cap) for cap in caps]
    return min([raw, *(cap.ceiling for cap in parsed)])


def _attribution_source(result: ConfidenceResult | Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    """Return (dimensions, components, caps) as plain mappings for attribution."""
    if isinstance(result, ConfidenceResult):
        dimensions = {name: dimension.as_dict() for name, dimension in result.dimensions.items()}
        caps = tuple(cap.as_dict() for cap in result.caps)
        components = (
            {name: dict(value) for name, value in result.components.items()}
            if isinstance(result, DecisionConfidence)
            else {}
        )
        return dimensions, components, caps
    if not isinstance(result, Mapping):
        raise ValueError("confidence result must be a ConfidenceResult or mapping")
    dimensions = {
        name: (item.as_dict() if hasattr(item, "as_dict") else dict(item))
        for name, item in (result.get("dimensions") or {}).items()
    }
    caps = tuple(
        (item.as_dict() if hasattr(item, "as_dict") else dict(item))
        for item in result.get("caps") or ()
    )
    components = {
        name: dict(value)
        for name, value in (result.get("components") or {}).items()
    }
    return dimensions, components, caps


def confidence_attribution(result: ConfidenceResult | Mapping[str, Any]) -> dict[str, Any]:
    """Expose per-dimension score/weight/contribution for audit and reports.

    Contribution arithmetic stays in deterministic Python so downstream
    reports never recompute score * weight themselves. When a cap applies,
    the raw score, cap ceilings, and final score are all preserved.
    """
    dimensions, components, caps = _attribution_source(result)
    entries = components or dimensions
    attribution: dict[str, Any] = {}
    for name in sorted(entries):
        item = entries[name]
        score = _bounded(item.get("score"), f"attribution {name}.score")
        weight = _bounded(item.get("weight"), f"attribution {name}.weight")
        entry: dict[str, Any] = {
            "score": score,
            "weight": weight,
            "contribution": score * weight,
        }
        for field in ("explanation", "reason", "reason_codes"):
            if item.get(field):
                entry[field] = item[field]
        attribution[name] = entry
    raw_value = result.raw_score if isinstance(result, ConfidenceResult) else result.get("raw_score")
    final_value = result.score if isinstance(result, ConfidenceResult) else result.get("score")
    band = result.band if isinstance(result, ConfidenceResult) else result.get("band")
    if raw_value is None or final_value is None:
        raw_value = sum(entry["contribution"] for entry in attribution.values())
        final_value = raw_value
    raw_score = _bounded(raw_value, "attribution raw_score")
    final_score = _bounded(final_value, "attribution final_score")
    output = {
        "raw_score": raw_score,
        "final_score": final_score,
        "band": band,
        "components": attribution,
        "caps": [
            {"code": cap.get("code"), "ceiling": cap.get("ceiling"), "reason": cap.get("reason", "")}
            for cap in caps
        ],
    }
    penalties = result.get("soft_penalties") if isinstance(result, Mapping) else (
        result.soft_penalties if isinstance(result, DecisionConfidence) else ()
    )
    if penalties:
        output["soft_penalties"] = [dict(item) for item in penalties]
    return output


def top_confidence_drags(
    result: ConfidenceResult | Mapping[str, Any],
    *,
    limit: int = 3,
) -> tuple[Mapping[str, Any], ...]:
    """Rank the dimensions losing the most weighted confidence.

    Impact is the weighted headroom ``weight * (1 - score)``; a perfect
    dimension contributes zero drag. Read-only diagnostics — this never
    changes scores or thresholds.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    dimensions, _, _ = _attribution_source(result)
    drags = []
    for name in sorted(dimensions):
        item = dimensions[name]
        score = _bounded(item.get("score"), f"drag {name}.score")
        weight = _bounded(item.get("weight"), f"drag {name}.weight")
        drags.append({
            "dimension": name,
            "impact": weight * (1.0 - score),
            "score": score,
            "weight": weight,
        })
    drags.sort(key=lambda item: (-item["impact"], item["dimension"]))
    return tuple(drags[:limit])


def calculate_regime_confidence(
    domain_confidence: Mapping[str, Any],
    *,
    domain_weights: Mapping[str, float] | None = None,
    caps: Iterable[ConfidenceCap | Mapping[str, Any]] = (),
    reasons: Iterable[str] = (),
    evidence_ids: Iterable[str] = (),
    policy: Any | None = None,
) -> ConfidenceResult:
    configured = domain_weights or _policy_mapping(policy, "confidence", {}).get("regime_domain_weights", REGIME_DOMAIN_WEIGHTS)
    weights = _weights(configured, "regime domain weights")
    dimensions: dict[str, ConfidenceDimension] = {}
    all_reasons = set(str(item) for item in reasons)
    all_evidence = set(str(item) for item in evidence_ids)
    for name, weight in weights.items():
        raw = domain_confidence.get(name)
        if isinstance(raw, ConfidenceResult):
            score = raw.score
            all_reasons.update(raw.reasons)
            all_evidence.update(raw.evidence_ids)
        elif isinstance(raw, Mapping):
            score = raw.get("score", raw.get("confidence_score", 0.0))
            all_reasons.update(str(item) for item in raw.get("reasons", ()))
            all_evidence.update(str(item) for item in raw.get("evidence_ids", ()))
        elif raw is None:
            score = 0.0
            all_reasons.add(f"UNKNOWN_DOMAIN:{name}")
        else:
            score = raw
        dimensions[name] = ConfidenceDimension(name, _bounded(score, f"regime domain {name}"), weight)
    raw_score = sum(item.score * item.weight for item in dimensions.values())
    parsed_caps = tuple(cap if isinstance(cap, ConfidenceCap) else ConfidenceCap.from_mapping(cap) for cap in caps)
    score = apply_confidence_caps(raw_score, parsed_caps)
    medium, high = _thresholds_for_policy(policy)
    return ConfidenceResult(
        raw_score=raw_score,
        score=score,
        band=_band_for_policy(score, policy),
        dimensions=dimensions,
        caps=parsed_caps,
        reasons=tuple(sorted(all_reasons)),
        evidence_ids=tuple(sorted(all_evidence)),
        status="BLOCKED" if any(cap.ceiling < medium for cap in parsed_caps) else "PROVISIONAL" if all_reasons else "AVAILABLE",
        medium_min=medium,
        high_min=high,
    )


def _scalar_component_score(value: Any, name: str) -> float:
    if isinstance(value, ConfidenceResult):
        return value.score
    if isinstance(value, Mapping):
        return _bounded(value.get("score", value.get("confidence_score", 0.0)), f"component {name}.score")
    return _bounded(value, f"component {name}")


def aggregate_asset_evidence_confidence(
    asset_scores: Mapping[str, Any],
    exposure_weights: Mapping[str, float] | None = None,
    *,
    policy: Any | None = None,
) -> float:
    """Aggregate only action-scoped asset evidence, with a concentration guard."""
    if not isinstance(asset_scores, Mapping):
        raise ValueError("asset_scores must be an object")
    scores = {
        str(asset).strip().upper(): _scalar_component_score(value, f"asset {asset}")
        for asset, value in asset_scores.items()
    }
    if not scores:
        return 0.0
    weights = {
        asset: _bounded((exposure_weights or {}).get(asset, 0.0), f"asset weight {asset}")
        for asset in scores
    }
    total = sum(weights.values())
    if total <= 0:
        weights = {asset: 1.0 for asset in scores}
        total = float(len(scores))
    score = sum(scores[asset] * weights[asset] for asset in scores) / total
    settings = _asset_evidence_policy(policy)
    threshold = float(settings.get("material_exposure_threshold", 0.10))
    floor = float(settings.get("low_confidence_position_floor", DEFAULT_MEDIUM_MIN))
    for asset, weight in weights.items():
        if weight / total >= threshold and scores[asset] < floor:
            score = min(score, scores[asset])
    return score


def _component_score(
    value: Any,
    name: str,
    *,
    policy: Any | None = None,
    scope: DecisionScope | None = None,
) -> tuple[float, str]:
    if isinstance(value, Mapping) and isinstance(value.get("assets"), Mapping):
        assets = value["assets"]
        weights = value.get("weights")
        if weights is None and scope is not None:
            weights = scope.exposure_weights
        return aggregate_asset_evidence_confidence(assets, weights, policy=policy), str(value.get("explanation", ""))
    if isinstance(value, ConfidenceResult):
        return value.score, f"{name} confidence"
    return _scalar_component_score(value, name), ""


def calculate_decision_confidence(
    components: Mapping[str, Any],
    *,
    component_weights: Mapping[str, float] | None = None,
    caps: Iterable[ConfidenceCap | Mapping[str, Any]] = (),
    blocked_actions: Iterable[str] = (),
    evidence_ids: Iterable[str] = (),
    scope: DecisionScope | Mapping[str, Any] | None = None,
    soft_penalties: Iterable[Mapping[str, Any]] = (),
    policy: Any | None = None,
) -> DecisionConfidence:
    configured = component_weights or _policy_mapping(policy, "confidence", {}).get("decision_component_weights", DECISION_COMPONENT_WEIGHTS)
    weights = _weights(configured, "decision component weights")
    decision_scope = (
        scope if isinstance(scope, DecisionScope)
        else DecisionScope(**scope) if isinstance(scope, Mapping)
        else None
    )
    details: dict[str, Mapping[str, Any]] = {}
    reasons: set[str] = set()
    raw = 0.0
    for name, weight in weights.items():
        score, explanation = _component_score(components.get(name, 0.0), name, policy=policy, scope=decision_scope)
        details[name] = {"score": score, "weight": weight, "explanation": explanation}
        raw += score * weight
    parsed_caps = tuple(cap if isinstance(cap, ConfidenceCap) else ConfidenceCap.from_mapping(cap) for cap in caps)
    parsed_penalties = []
    for item in soft_penalties:
        if not isinstance(item, Mapping):
            raise ValueError("soft penalties must be objects")
        amount = _bounded(item.get("penalty", item.get("amount", 0.0)), "soft penalty")
        parsed_penalties.append({"penalty": amount, **{str(key): value for key, value in item.items() if key not in {"penalty", "amount"}}})
    score = max(0.0, apply_confidence_caps(raw, parsed_caps) - sum(item["penalty"] for item in parsed_penalties))
    blockers = tuple(cap.code for cap in parsed_caps)
    scoped_blockers = {
        cap.applies_to for cap in parsed_caps if cap.applies_to.startswith("ACTION:")
    }
    blocked = tuple(sorted({str(item).strip().upper() for item in blocked_actions} | set(blockers) | scoped_blockers))
    medium, high = _thresholds_for_policy(policy)
    allowed = ("HOLD", "NO_TRADE") if score < high else ("HOLD", "NO_TRADE", "INCREASE", "REDUCE", "EXIT")
    if parsed_caps:
        reasons.update(cap.code for cap in parsed_caps)
    explanation = "confidence is capped by hard evidence constraints" if parsed_caps else "confidence is the action-scoped weighted evidence score"
    if parsed_penalties:
        explanation += "; soft evidence penalties are applied once"
    return DecisionConfidence(
        raw_score=raw,
        score=score,
        band=_band_for_policy(score, policy),
        dimensions={},
        caps=parsed_caps,
        reasons=tuple(sorted(reasons)),
        evidence_ids=tuple(sorted({str(item) for item in evidence_ids})),
        status="BLOCKED" if any(cap.ceiling < medium for cap in parsed_caps) else "PROVISIONAL" if parsed_caps or parsed_penalties else "AVAILABLE",
        components=details,
        critical_blockers=blockers,
        allowed_actions=allowed,
        blocked_actions=blocked,
        explanation=explanation,
        scope=decision_scope.as_dict() if decision_scope is not None else None,
        soft_penalties=tuple(parsed_penalties),
        medium_min=medium,
        high_min=high,
    )


__all__ = [
    "DATA_DIMENSION_WEIGHTS",
    "DECISION_COMPONENT_WEIGHTS",
    "DecisionScope",
    "REGIME_DOMAIN_WEIGHTS",
    "apply_confidence_caps",
    "calculate_data_confidence",
    "calculate_decision_confidence",
    "confidence_attribution",
    "confidence_deployment_factor",
    "calculate_freshness",
    "calculate_regime_confidence",
    "calculate_signal_consistency",
    "freshness_score",
    "redundancy_score",
    "aggregate_asset_evidence_confidence",
    "signal_consistency_score",
    "source_quality_score",
    "top_confidence_drags",
]
