"""Deterministic derivatives and social positioning overlay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from ..models.metrics_history import MetricObservation
from ..models.positioning import (
    PositioningBias,
    PositioningFacts,
    PositioningLeverageState,
    PositioningRisk,
    SocialSentimentState,
)
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp


_METRIC_FIELDS = {
    "derivatives.funding_rate": "funding_rate",
    "derivatives.funding_rate_24h_avg": "funding_rate_24h_avg",
    "derivatives.funding_rate_7d_avg": "funding_rate_7d_avg",
    "derivatives.funding_rate_percentile": "funding_rate_percentile",
    "derivatives.open_interest_usd": "open_interest_usd",
    "derivatives.open_interest_change_1d": "open_interest_change_1d",
    "derivatives.open_interest_change_7d": "open_interest_change_7d",
    "derivatives.open_interest_to_market_cap": "open_interest_to_market_cap",
    "derivatives.long_short_account_ratio": "long_short_account_ratio",
    "derivatives.top_trader_long_short_ratio": "top_trader_long_short_ratio",
    "derivatives.long_liquidations_24h_usd": "long_liquidations_24h_usd",
    "derivatives.short_liquidations_24h_usd": "short_liquidations_24h_usd",
    "derivatives.total_liquidations_24h_usd": "total_liquidations_24h_usd",
    "derivatives.long_liquidations_7d_usd": "long_liquidations_7d_usd",
    "derivatives.short_liquidations_7d_usd": "short_liquidations_7d_usd",
    "derivatives.futures_basis_annualized": "futures_basis_annualized",
    "sentiment.social_bullish_share": "social_bullish_share",
    "sentiment.social_mentions_24h": "social_mentions_24h",
    "sentiment.social_mentions_change_7d": "social_mentions_change_7d",
    "sentiment.social_sentiment_percentile": "social_sentiment_percentile",
    "sentiment.social_attention_percentile": "social_attention_percentile",
    "sentiment.market_fear_greed": "market_fear_greed",
}
_ALIASES = {key.rsplit(".", 1)[-1]: key for key in _METRIC_FIELDS}
_DEFAULTS = {
    "minimum_derivatives_confirmations_for_crowded": 2,
    "minimum_derivatives_confirmations_for_extreme": 3,
    "funding_rate": {
        "elevated_positive": 0.0003,
        "extreme_positive": 0.001,
        "elevated_negative": -0.0003,
        "extreme_negative": -0.001,
    },
    "open_interest_change_7d": {"building": 0.20, "rapid": 0.40},
    "long_short_ratio": {
        "long_crowded": 1.50,
        "short_crowded": 0.67,
        "long_extreme": 1.75,
        "short_extreme": 0.57,
    },
    "futures_basis": {
        "elevated_positive": 0.10,
        "extreme_positive": 0.20,
        "elevated_negative": -0.10,
        "extreme_negative": -0.20,
    },
    "social": {
        "fearful_bullish_share": 0.20,
        "optimistic_bullish_share": 0.60,
        "euphoric_bullish_share": 0.80,
        "attention_growth_extreme": 2.0,
    },
    "deleveraging": {
        "liquidation_to_open_interest": 0.10,
        "normalized_funding_abs": 0.0003,
    },
}


@dataclass(frozen=True)
class _Point:
    key: str
    value: float
    observed_at: str | None
    source: str
    observation_id: str
    metadata: Mapping[str, Any]


def _copy_defaults(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        result[key] = _copy_defaults(item) if isinstance(item, Mapping) else item
    return result


def _settings(policy: Policy | None) -> dict[str, Any]:
    result = _copy_defaults(_DEFAULTS)
    configured = getattr(policy or resolve_policy(), "positioning", {})
    if isinstance(configured, Mapping):
        for key, value in configured.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key].update(value)
            else:
                result[key] = value
    return result


def _canonical_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("positioning metric key must be a non-empty string")
    raw = value.strip().lower()
    key = _ALIASES.get(raw, raw)
    if key not in _METRIC_FIELDS:
        raise ValueError(f"unknown positioning metric key: {raw}")
    return key


def _text(value: Any, field_name: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _point_from_mapping(key: str, value: Any, as_of: str | None) -> _Point:
    key = _canonical_key(key)
    if isinstance(value, Mapping):
        raw_value = value.get("value")
        observed_at = value.get("observed_at", as_of)
        source = value.get("source", "direct")
        observation_id = value.get("observation_id", f"direct:{key}")
        metadata = {
            name: value[name]
            for name in ("venue", "aggregation_scope", "aggregation", "scope", "funding_interval", "interval", "period", "methodology", "method")
            if name in value
        }
    else:
        raw_value = value
        observed_at = as_of
        source = "direct"
        observation_id = f"direct:{key}"
        metadata = {}
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"positioning {key} value must be numeric")
    number = float(raw_value)
    if not math.isfinite(number):
        raise ValueError(f"positioning {key} value must be finite")
    normalized_observed = normalize_timestamp(observed_at, "positioning observed_at") if observed_at else None
    return _Point(
        key,
        number,
        normalized_observed,
        _text(source, "positioning source", "direct"),
        _text(observation_id, "positioning observation_id", f"direct:{key}"),
        metadata,
    )


def _points(value: Any, as_of: str | None) -> list[_Point]:
    if value is None:
        return []
    if isinstance(value, MetricObservation):
        key = _canonical_key(value.metric_key)
        metadata = {"period": value.period, **dict(value.metadata or {})} if value.period is not None else dict(value.metadata or {})
        return [_Point(key, float(value.value), value.observed_at, value.source, value.observation_id, metadata)]
    if isinstance(value, Mapping):
        if "observations" in value:
            return _points(value["observations"], as_of)
        if "metric_key" in value:
            key = _canonical_key(value["metric_key"])
            return [_point_from_mapping(key, value, as_of)]
        result: list[_Point] = []
        for key, item in value.items():
            if str(key).strip().lower() in {"current", "metrics", "values"} and isinstance(item, Mapping):
                result.extend(_points(item, as_of))
            else:
                result.append(_point_from_mapping(str(key), item, as_of))
        return result
    if isinstance(value, (str, bytes)):
        raise ValueError("positioning observations must be a sequence or mapping")
    try:
        return [point for item in value for point in _points(item, as_of)]
    except TypeError as exc:
        raise ValueError("positioning observations must be a sequence or mapping") from exc


def _source_signature(point: _Point) -> tuple[str, ...]:
    metadata = point.metadata
    return (
        point.source,
        str(metadata.get("venue", "")),
        str(metadata.get("aggregation_scope", metadata.get("aggregation", metadata.get("scope", "")))),
        str(metadata.get("methodology", metadata.get("method", ""))),
        str(metadata.get("funding_interval", metadata.get("interval", metadata.get("period", "")))),
    )


def source_compatible(left: MetricObservation | Mapping[str, Any], right: MetricObservation | Mapping[str, Any]) -> bool:
    """Return whether two observations can be compared as one series."""
    if isinstance(left, MetricObservation):
        left_point = _points(left, left.observed_at)[0]
    else:
        left_point = _points(left, left.get("observed_at") if isinstance(left, Mapping) else None)[0]
    if isinstance(right, MetricObservation):
        right_point = _points(right, right.observed_at)[0]
    else:
        right_point = _points(right, right.get("observed_at") if isinstance(right, Mapping) else None)[0]
    return left_point.key == right_point.key and _source_signature(left_point) == _source_signature(right_point)


def validate_source_compatibility(
    observations: Iterable[MetricObservation | Mapping[str, Any]],
) -> bool:
    points = _points(tuple(observations), None)
    by_key: dict[str, list[_Point]] = {}
    for point in points:
        by_key.setdefault(point.key, []).append(point)
    return all(len({_source_signature(item) for item in values}) <= 1 for values in by_key.values())


def _latest(points: Iterable[_Point], as_of: str | None) -> tuple[dict[str, _Point], set[str]]:
    cutoff = parse_timestamp(as_of) if as_of else None
    grouped: dict[str, list[_Point]] = {}
    for point in points:
        if point.observed_at is not None and cutoff is not None and parse_timestamp(point.observed_at) > cutoff:
            continue
        grouped.setdefault(point.key, []).append(point)
    conflicts = {
        key
        for key, values in grouped.items()
        if len({_source_signature(item) for item in values}) > 1
    }
    selected = {
        key: max(values, key=lambda item: (parse_timestamp(item.observed_at) if item.observed_at else datetime.min.replace(tzinfo=timezone.utc), item.observation_id))
        for key, values in grouped.items()
    }
    return selected, conflicts


def _value(selected: Mapping[str, _Point], key: str) -> float | None:
    point = selected.get(key)
    return None if point is None else point.value


def _social_state(
    bullish_share: float | None,
    mentions_change: float | None,
    attention_percentile: float | None,
    fear_greed: float | None,
    settings: Mapping[str, Any],
) -> str:
    if bullish_share is None:
        if fear_greed is None:
            return SocialSentimentState.UNKNOWN.value
        if fear_greed >= 75:
            return SocialSentimentState.EUPHORIC.value
        if fear_greed <= 25:
            return SocialSentimentState.FEARFUL.value
        return SocialSentimentState.NEUTRAL.value
    social = settings["social"]
    if bullish_share >= social["euphoric_bullish_share"] and (
        mentions_change is None
        or mentions_change >= social["attention_growth_extreme"]
        or (attention_percentile is not None and attention_percentile >= 0.9)
    ):
        return SocialSentimentState.EUPHORIC.value
    if bullish_share <= social["fearful_bullish_share"]:
        return SocialSentimentState.FEARFUL.value
    if bullish_share >= social["optimistic_bullish_share"]:
        return SocialSentimentState.OPTIMISTIC.value
    return SocialSentimentState.NEUTRAL.value


def build_positioning_facts(
    observations: Any = None,
    symbol: str = "BTC",
    *,
    metrics: Mapping[str, Any] | None = None,
    previous_observations: Any = None,
    policy: Policy | None = None,
    as_of: str | datetime | None = None,
) -> PositioningFacts:
    """Build a positioning overlay from current compatible observations.

    ``as_of`` is a hard cutoff; observations after it are ignored for replay.
    ``previous_observations`` is accepted for API symmetry and source checks,
    but no future or incompatible series is used in current classification.
    """
    if isinstance(observations, str) and isinstance(symbol, Mapping):
        observations, symbol = symbol, observations
    elif isinstance(observations, str) and symbol == "BTC":
        symbol, observations = observations, None
    if metrics is not None:
        if observations is not None:
            raise ValueError("provide only one of observations or metrics")
        observations = metrics
    normalized_symbol = _text(symbol, "positioning symbol").upper()
    raw_as_of = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    point_values = _points(observations, raw_as_of)
    if previous_observations is not None:
        point_values.extend(_points(previous_observations, raw_as_of))
    if raw_as_of is None:
        observed = [item.observed_at for item in point_values if item.observed_at]
        raw_as_of = max(observed) if observed else datetime.now(timezone.utc).isoformat()
    normalized_as_of = normalize_timestamp(raw_as_of, "positioning as_of")
    selected, conflicts = _latest(point_values, normalized_as_of)
    values = {field: _value(selected, key) for key, field in _METRIC_FIELDS.items()}
    if values["total_liquidations_24h_usd"] is None and (
        values["long_liquidations_24h_usd"] is not None
        and values["short_liquidations_24h_usd"] is not None
    ):
        values["total_liquidations_24h_usd"] = (
            values["long_liquidations_24h_usd"] + values["short_liquidations_24h_usd"]
        )
    settings = _settings(policy)
    configured_policy = getattr(policy or resolve_policy(), "positioning", {})
    if isinstance(configured_policy, Mapping) and configured_policy.get("enabled") is False:
        return PositioningFacts(
            symbol=normalized_symbol,
            as_of=normalized_as_of,
            **values,
            confidence="LOW",
            notes=("positioning overlay is disabled by policy",),
        )
    notes: list[str] = []
    flags: list[str] = []
    if conflicts:
        flags.extend(f"SOURCE_CONFLICT:{key}" for key in sorted(conflicts))
        notes.append("incompatible source or methodology prevented series comparison")

    funding_key = None
    for candidate in ("derivatives.funding_rate_7d_avg", "derivatives.funding_rate_24h_avg"):
        if candidate in selected and candidate not in conflicts:
            funding_key = candidate
            break
    funding = _value(selected, funding_key) if funding_key else None
    funding_persistent = funding is not None
    funding_cfg = settings["funding_rate"]
    funding_long = funding_persistent and funding >= funding_cfg["elevated_positive"]
    funding_short = funding_persistent and funding <= funding_cfg["elevated_negative"]
    funding_long_extreme = funding_persistent and funding >= funding_cfg["extreme_positive"]
    funding_short_extreme = funding_persistent and funding <= funding_cfg["extreme_negative"]

    oi_key = None
    for candidate in ("derivatives.open_interest_change_7d", "derivatives.open_interest_change_1d"):
        if candidate in selected and candidate not in conflicts:
            oi_key = candidate
            break
    oi_change = _value(selected, oi_key) if oi_key else None
    oi_cfg = settings["open_interest_change_7d"]
    oi_building = oi_change is not None and oi_change >= oi_cfg["building"]
    oi_rapid = oi_change is not None and oi_change >= oi_cfg["rapid"]
    oi_declining = oi_change is not None and oi_change <= -oi_cfg["building"]

    ratio_key = next(
        (
            candidate
            for candidate in (
                "derivatives.top_trader_long_short_ratio",
                "derivatives.long_short_account_ratio",
            )
            if candidate in selected and candidate not in conflicts
        ),
        None,
    )
    ratio = _value(selected, ratio_key) if ratio_key else None
    ratio_cfg = settings["long_short_ratio"]
    ratio_long = ratio is not None and ratio >= ratio_cfg["long_crowded"]
    ratio_short = ratio is not None and ratio <= ratio_cfg["short_crowded"]
    ratio_long_extreme = ratio is not None and ratio >= ratio_cfg.get("long_extreme", ratio_cfg["long_crowded"])
    ratio_short_extreme = ratio is not None and ratio <= ratio_cfg.get("short_extreme", ratio_cfg["short_crowded"])

    basis = _value(selected, "derivatives.futures_basis_annualized") if "derivatives.futures_basis_annualized" not in conflicts else None
    basis_cfg = settings["futures_basis"]
    basis_long = basis is not None and basis >= basis_cfg["elevated_positive"]
    basis_short = basis is not None and basis <= basis_cfg["elevated_negative"]
    basis_long_extreme = basis is not None and basis >= basis_cfg["extreme_positive"]
    basis_short_extreme = basis is not None and basis <= basis_cfg["extreme_negative"]

    long_signals = []
    short_signals = []
    if funding_long:
        long_signals.append("funding")
    if funding_short:
        short_signals.append("funding")
    if ratio_long:
        long_signals.append("long_short_ratio")
    if ratio_short:
        short_signals.append("long_short_ratio")
    if basis_long:
        long_signals.append("basis")
    if basis_short:
        short_signals.append("basis")
    if oi_building and long_signals:
        long_signals.append("open_interest")
    if oi_building and short_signals:
        short_signals.append("open_interest")

    derivatives_present = any(key.startswith("derivatives.") for key in selected)
    min_crowded = int(settings["minimum_derivatives_confirmations_for_crowded"])
    min_extreme = int(settings["minimum_derivatives_confirmations_for_extreme"])
    long_extreme_count = sum((funding_long_extreme, oi_rapid, ratio_long_extreme, basis_long_extreme))
    short_extreme_count = sum((funding_short_extreme, oi_rapid, ratio_short_extreme, basis_short_extreme))

    liquidation_ratio = None
    if values["open_interest_usd"] and values["open_interest_usd"] > 0:
        liquidation_ratio = max(
            values["long_liquidations_24h_usd"] or 0.0,
            values["short_liquidations_24h_usd"] or 0.0,
        ) / values["open_interest_usd"]
    deleveraging = (
        oi_declining
        and (values["long_liquidations_24h_usd"] or 0.0) > 0
        and (liquidation_ratio is None or liquidation_ratio >= settings["deleveraging"]["liquidation_to_open_interest"])
        and funding is not None
        and abs(funding) <= settings["deleveraging"]["normalized_funding_abs"]
    )

    if deleveraging:
        leverage_state = PositioningLeverageState.DELEVERAGED.value
    elif len(long_signals) >= min_extreme and long_extreme_count >= min_extreme:
        leverage_state = PositioningLeverageState.EXTREME.value
    elif len(short_signals) >= min_extreme and short_extreme_count >= min_extreme:
        leverage_state = PositioningLeverageState.EXTREME.value
    elif len(long_signals) >= min_crowded or len(short_signals) >= min_crowded:
        leverage_state = PositioningLeverageState.CROWDED.value
    elif oi_building or funding_long or funding_short:
        leverage_state = PositioningLeverageState.BUILDING.value
    elif derivatives_present:
        leverage_state = PositioningLeverageState.NORMAL.value
    else:
        leverage_state = PositioningLeverageState.UNKNOWN.value

    if len(long_signals) >= min_crowded:
        bias = PositioningBias.LONG_CROWDED.value
    elif len(short_signals) >= min_crowded:
        bias = PositioningBias.SHORT_CROWDED.value
    elif long_signals and len(long_signals) > len(short_signals):
        bias = PositioningBias.LONG_BIASED.value
    elif short_signals and len(short_signals) > len(long_signals):
        bias = PositioningBias.SHORT_BIASED.value
    elif derivatives_present:
        bias = PositioningBias.BALANCED.value
    else:
        bias = PositioningBias.UNKNOWN.value

    social_state = _social_state(
        values["social_bullish_share"],
        values["social_mentions_change_7d"],
        values["social_attention_percentile"],
        values["market_fear_greed"],
        settings,
    )
    if leverage_state == PositioningLeverageState.EXTREME.value:
        risk = PositioningRisk.EXTREME.value
    elif leverage_state == PositioningLeverageState.CROWDED.value:
        risk = PositioningRisk.HIGH.value
    elif leverage_state == PositioningLeverageState.BUILDING.value:
        risk = PositioningRisk.ELEVATED.value
    elif leverage_state == PositioningLeverageState.DELEVERAGED.value:
        risk = PositioningRisk.LOW.value
    elif social_state == SocialSentimentState.EUPHORIC.value:
        risk = PositioningRisk.ELEVATED.value
    elif leverage_state == PositioningLeverageState.NORMAL.value:
        risk = PositioningRisk.NORMAL.value
    else:
        risk = PositioningRisk.UNKNOWN.value

    derivative_metric_keys = {key for key in selected if key.startswith("derivatives.") and key not in conflicts}
    funding_available = bool({"derivatives.funding_rate_7d_avg", "derivatives.funding_rate_24h_avg"} & derivative_metric_keys)
    oi_available = bool({"derivatives.open_interest_usd", "derivatives.open_interest_change_7d", "derivatives.open_interest_change_1d"} & derivative_metric_keys)
    additional_available = bool({
        "derivatives.long_short_account_ratio",
        "derivatives.top_trader_long_short_ratio",
        "derivatives.futures_basis_annualized",
        "derivatives.long_liquidations_24h_usd",
        "derivatives.short_liquidations_24h_usd",
    } & derivative_metric_keys)
    if conflicts or not derivatives_present:
        confidence = "LOW"
    elif funding_available and oi_available and additional_available:
        confidence = "HIGH"
    elif funding_available and oi_available:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    if social_state == SocialSentimentState.EUPHORIC.value:
        notes.append("social euphoria is confirmation only and cannot create an extreme positioning state")
    if long_signals or short_signals:
        notes.append(f"derivatives confirmations: {', '.join(long_signals or short_signals)}")
    if deleveraging:
        notes.append("open-interest decline, liquidation activity, and normalized funding indicate leverage removal")
    if not derivatives_present:
        notes.append("no compatible derivatives observations; positioning risk remains UNKNOWN")

    source_metadata = {
        key: {
            "source": point.source,
            "observed_at": point.observed_at,
            **dict(point.metadata),
        }
        for key, point in selected.items()
    }
    evidence_ids = tuple(dict.fromkeys(point.observation_id for point in selected.values()))
    return PositioningFacts(
        symbol=normalized_symbol,
        as_of=normalized_as_of,
        **values,
        leverage_state=leverage_state,
        bias=bias,
        risk=risk,
        social_state=social_state,
        confidence=confidence,
        evidence_ids=evidence_ids,
        notes=tuple(dict.fromkeys(notes)),
        source_metadata=source_metadata,
        data_quality_flags=tuple(dict.fromkeys(flags)),
    )


build_positioning_overlay = build_positioning_facts
classify_positioning = build_positioning_facts


__all__ = [
    "build_positioning_facts",
    "build_positioning_overlay",
    "classify_positioning",
    "source_compatible",
    "validate_source_compatibility",
]
