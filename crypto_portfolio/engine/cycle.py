"""Deterministic Bitcoin halving and cycle-context engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable, Mapping

from ..models.cycle import (
    BTCCycleContext,
    CycleRisk,
    CycleValuationState,
    HalvingContext,
    HolderBehaviorState,
    MarketCycleState,
)
from ..models.metrics_history import MetricObservation
from ..models.positioning import PositioningFacts
from ..models.policy import Policy, resolve_policy
from ..models.time import normalize_timestamp, parse_timestamp


HALVING_EVENTS = (
    "2012-11-28T15:24:37Z",
    "2016-07-09T16:46:13Z",
    "2020-05-11T19:23:43Z",
    "2024-04-20T00:09:27Z",
)
HALVING_INTERVAL_DAYS = 1461
_METRIC_FIELDS = {
    "onchain.btc.mvrv": "mvrv",
    "onchain.btc.mvrv_zscore": "mvrv_zscore",
    "onchain.btc.realized_price": "realized_price",
    "onchain.btc.market_to_realized_price": "market_to_realized_price",
    "onchain.btc.sopr": "sopr",
    "onchain.btc.lth_supply_pct": "lth_supply_pct",
    "onchain.btc.lth_net_position_change": "lth_net_position_change",
    "onchain.btc.sth_realized_price": "sth_realized_price",
    "onchain.btc.lth_realized_price": "lth_realized_price",
    "onchain.btc.nupl": "nupl",
    "market.spot_price": "current_price",
    "market.drawdown": "drawdown",
    "market.btc_trend": "trend_state",
    "market.flow_state": "flow_state",
    "market.stablecoin_supply": "stablecoin_supply",
    "flows.etf_net_7d": "etf_flow_7d",
    "market.breadth": "breadth",
}
_ALIASES = {key.rsplit(".", 1)[-1]: key for key in _METRIC_FIELDS}
_DEFAULT_CYCLE_POLICY = {
    "enabled": True,
    "halving_context_days": {
        "early_post_halving_max": 180,
        "mid_epoch_max": 730,
        "late_epoch_min": 900,
    },
    "minimum_non_clock_confirmations_for_elevated_risk": 2,
    "minimum_non_clock_confirmations_for_high_risk": 3,
    "valuation": {
        "mvrv_zscore_elevated": 3.5,
        "mvrv_zscore_extreme": 7.0,
        "market_to_realized_price_elevated": 1.5,
        "market_to_realized_price_extreme": 2.0,
    },
    "price": {"extension_atr": 2.0, "drawdown_reset": 0.5},
    "holder": {
        "lth_distribution_threshold": -0.05,
        "lth_accumulation_threshold": 0.05,
        "sopr_distribution_threshold": 1.05,
    },
}


@dataclass(frozen=True)
class _Point:
    key: str
    value: Any
    observed_at: str | None
    source: str
    observation_id: str
    metadata: Mapping[str, Any]


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _canonical_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("cycle metric key must be a non-empty string")
    key = _ALIASES.get(value.strip().lower(), value.strip().lower())
    if key not in _METRIC_FIELDS:
        raise ValueError(f"unknown BTC cycle metric key: {key}")
    return key


def _point(key: str, raw: Any, as_of: str | None) -> _Point:
    key = _canonical_key(key)
    if isinstance(raw, Mapping):
        value = raw.get("value")
        observed_at = raw.get("observed_at", as_of)
        source = raw.get("source", "direct")
        observation_id = raw.get("observation_id", f"direct:{key}")
        metadata = {
            name: raw[name]
            for name in ("methodology", "method", "venue", "scope", "aggregation_scope", "aggregation")
            if name in raw
        }
    else:
        value = raw
        observed_at = as_of
        source = "direct"
        observation_id = f"direct:{key}"
        metadata = {}
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"cycle {key} value must be numeric or string")
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        raise ValueError(f"cycle {key} value must be finite")
    return _Point(
        key,
        float(value) if isinstance(value, (int, float)) else value,
        normalize_timestamp(observed_at, "cycle observed_at") if observed_at else None,
        _text(source, "cycle source"),
        _text(observation_id, "cycle observation_id"),
        metadata,
    )


def _points(value: Any, as_of: str | None) -> list[_Point]:
    if value is None:
        return []
    if isinstance(value, MetricObservation):
        return [_point(value.metric_key, {"value": value.value, "observed_at": value.observed_at, "source": value.source, "observation_id": value.observation_id, **dict(value.metadata or {})}, as_of)]
    if isinstance(value, Mapping):
        if "observations" in value:
            return _points(value["observations"], as_of)
        if "metric_key" in value:
            return [_point(value["metric_key"], value, as_of)]
        return [_point(key, item, as_of) for key, item in value.items()]
    if isinstance(value, (str, bytes)):
        raise ValueError("cycle observations must be a sequence or mapping")
    try:
        return [point for item in value for point in _points(item, as_of)]
    except TypeError as exc:
        raise ValueError("cycle observations must be a sequence or mapping") from exc


def _cycle_policy(policy: Policy | None) -> dict[str, Any]:
    result = {
        key: {nested_key: nested_value for nested_key, nested_value in nested.items()}
        if isinstance(nested, Mapping)
        else nested
        for key, nested in _DEFAULT_CYCLE_POLICY.items()
    }
    configured = getattr(policy or resolve_policy(), "btc_cycle", {})
    if isinstance(configured, Mapping):
        for key, value in configured.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key].update(value)
            else:
                result[key] = value
    return result


def _latest(points: Iterable[_Point], as_of: str) -> dict[str, _Point]:
    cutoff = parse_timestamp(as_of)
    grouped: dict[str, list[_Point]] = {}
    for point in points:
        if point.observed_at and parse_timestamp(point.observed_at) <= cutoff:
            grouped.setdefault(point.key, []).append(point)
        elif point.observed_at is None:
            grouped.setdefault(point.key, []).append(point)
    return {
        key: max(
            values,
            key=lambda item: (
                parse_timestamp(item.observed_at) if item.observed_at else datetime.min.replace(tzinfo=timezone.utc),
                item.observation_id,
            ),
        )
        for key, values in grouped.items()
    }


def halving_context_for_days(days_since_halving: int | None, policy: Policy | None = None) -> str:
    if days_since_halving is None:
        return HalvingContext.PRE_HALVING.value
    if isinstance(days_since_halving, bool) or not isinstance(days_since_halving, int) or days_since_halving < 0:
        raise ValueError("days_since_halving must be a non-negative integer or null")
    ranges = (policy or resolve_policy()).btc_cycle.get("halving_context_days", {})
    early = int(ranges.get("early_post_halving_max", 180))
    mid = int(ranges.get("mid_epoch_max", 730))
    late = int(ranges.get("late_epoch_min", 900))
    if days_since_halving <= early:
        return HalvingContext.EARLY_POST_HALVING.value
    if days_since_halving <= mid:
        return HalvingContext.MID_EPOCH.value
    if days_since_halving >= late:
        return HalvingContext.LATE_EPOCH.value
    return HalvingContext.UNKNOWN.value


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _strict_number(value: Any, field_name: str, *, minimum: float | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric or null")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{field_name} must be finite and >= {minimum}" if minimum is not None else f"{field_name} must be finite")
    return result


def _valuation(values: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    zscore = _number(values.get("mvrv_zscore"))
    ratio = _number(values.get("market_to_realized_price"))
    if zscore is not None:
        if zscore >= settings["mvrv_zscore_extreme"]:
            return CycleValuationState.EXTREME.value
        if zscore >= settings["mvrv_zscore_elevated"]:
            return CycleValuationState.ELEVATED.value
        return CycleValuationState.NORMAL.value
    if ratio is not None:
        if ratio >= settings["market_to_realized_price_extreme"]:
            return CycleValuationState.EXTREME.value
        if ratio >= settings["market_to_realized_price_elevated"]:
            return CycleValuationState.ELEVATED.value
        return CycleValuationState.NORMAL.value
    return CycleValuationState.UNKNOWN.value


def _holder(values: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    lth_change = _number(values.get("lth_net_position_change"))
    sopr = _number(values.get("sopr"))
    if lth_change is not None:
        if lth_change <= settings["lth_distribution_threshold"]:
            return HolderBehaviorState.DISTRIBUTION.value
        if lth_change >= settings["lth_accumulation_threshold"]:
            return HolderBehaviorState.ACCUMULATION.value
    if sopr is not None and sopr >= settings["sopr_distribution_threshold"]:
        return HolderBehaviorState.DISTRIBUTION.value
    if lth_change is not None or sopr is not None:
        return HolderBehaviorState.NEUTRAL.value
    return HolderBehaviorState.UNKNOWN.value


def _state(value: Any) -> str:
    return str(value).strip().upper() if value is not None else ""


def build_btc_cycle_context(
    observations: Any = None,
    *,
    as_of: str | datetime | None = None,
    policy: Policy | None = None,
    positioning: PositioningFacts | Mapping[str, Any] | None = None,
    last_halving_timestamp: str | None = None,
    days_since_halving: int | None = None,
    current_price: float | None = None,
    price_at_halving: float | None = None,
    ath_price: float | None = None,
    return_since_halving: float | None = None,
    distance_from_ath: float | None = None,
    drawdown: float | None = None,
    trend_state: str | None = None,
    price_extension_atr: float | None = None,
    flow_state: str | None = None,
    liquidity_state: str | None = None,
    breadth_state: str | None = None,
    **metric_values: Any,
) -> BTCCycleContext:
    """Build cycle context using only observations at or before ``as_of``."""
    if isinstance(observations, str) and as_of is None:
        as_of, observations = observations, None
    raw_as_of = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    point_values = _points(observations, raw_as_of)
    if raw_as_of is None:
        observed = [item.observed_at for item in point_values if item.observed_at]
        raw_as_of = max(observed) if observed else datetime.now(timezone.utc).isoformat()
    normalized_as_of = normalize_timestamp(raw_as_of, "cycle as_of")
    selected = _latest(point_values, normalized_as_of)
    values = {field: selected[key].value for key, field in _METRIC_FIELDS.items() if key in selected}
    for key, value in metric_values.items():
        if not isinstance(key, str):
            raise ValueError("cycle metric keyword names must be strings")
        canonical = _ALIASES.get(key.strip().lower(), key.strip().lower())
        if canonical not in _METRIC_FIELDS:
            raise ValueError(f"unknown BTC cycle metric key: {key}")
        values[_METRIC_FIELDS[canonical]] = value
    current_price = _strict_number(current_price, "current_price", minimum=0.0)
    if current_price is None:
        current_price = _strict_number(values.get("current_price"), "current_price", minimum=0.0)
    if current_price is not None and current_price <= 0:
        raise ValueError("current_price must be > 0")
    price_at_halving = _strict_number(price_at_halving, "price_at_halving", minimum=0.0)
    ath_price = _strict_number(ath_price, "ath_price", minimum=0.0)
    if price_at_halving is not None and price_at_halving <= 0:
        raise ValueError("price_at_halving must be > 0")
    if ath_price is not None and ath_price <= 0:
        raise ValueError("ath_price must be > 0")
    distance_from_ath = _strict_number(distance_from_ath, "distance_from_ath")
    if current_price is not None and ath_price is not None and distance_from_ath is None:
        distance_from_ath = max(0.0, 1.0 - current_price / ath_price) if ath_price > 0 else None
    drawdown = _strict_number(drawdown, "drawdown")
    if drawdown is None:
        drawdown = _strict_number(values.get("drawdown"), "drawdown")
    if distance_from_ath is None and drawdown is not None:
        distance_from_ath = max(0.0, -drawdown)
    trend_state = trend_state or values.get("trend_state")
    flow_state = flow_state or values.get("flow_state")
    if drawdown is not None and drawdown > 0:
        raise ValueError("drawdown must be <= 0")
    price_extension_atr = _strict_number(price_extension_atr, "price_extension_atr", minimum=0.0)
    return_since_halving = _strict_number(return_since_halving, "return_since_halving")
    if return_since_halving is not None and return_since_halving < -1:
        raise ValueError("return_since_halving must be >= -1")
    if return_since_halving is None and current_price is not None and price_at_halving is not None and price_at_halving > 0:
        return_since_halving = current_price / price_at_halving - 1.0
    if days_since_halving is not None:
        if isinstance(days_since_halving, bool) or not isinstance(days_since_halving, int) or days_since_halving < 0:
            raise ValueError("days_since_halving must be a non-negative integer or null")
        as_of_time = parse_timestamp(normalized_as_of)
        if last_halving_timestamp is None:
            last_time = as_of_time - timedelta(days=days_since_halving)
            last_halving_timestamp = last_time.isoformat().replace("+00:00", "Z")
        else:
            last_halving_timestamp = normalize_timestamp(last_halving_timestamp, "last_halving_timestamp")
            last_time = parse_timestamp(last_halving_timestamp)
            if (as_of_time - last_time).days != days_since_halving:
                raise ValueError("days_since_halving does not match last_halving_timestamp and as_of")
        next_time = last_time + timedelta(days=HALVING_INTERVAL_DAYS)
    elif last_halving_timestamp is not None:
        last_halving_timestamp = normalize_timestamp(last_halving_timestamp, "last_halving_timestamp")
        last_time = parse_timestamp(last_halving_timestamp)
        as_of_time = parse_timestamp(normalized_as_of)
        if last_time > as_of_time:
            raise ValueError("last_halving_timestamp must not be after as_of")
        next_time = last_time + timedelta(days=HALVING_INTERVAL_DAYS)
    else:
        as_of_time = parse_timestamp(normalized_as_of)
        known = [parse_timestamp(value) for value in HALVING_EVENTS]
        earlier = [value for value in known if value <= as_of_time]
        last_time = max(earlier) if earlier else None
        last_halving_timestamp = last_time.isoformat().replace("+00:00", "Z") if last_time else None
        next_time = (
            (last_time + timedelta(days=HALVING_INTERVAL_DAYS))
            if last_time
            else min(known, default=as_of_time)
        )
    days_since = (as_of_time - last_time).days if last_time is not None else None
    estimated_next = next_time.isoformat().replace("+00:00", "Z") if next_time else None
    days_to_next = max(0, (next_time - as_of_time).days) if next_time else None
    progress = (
        max(0.0, min(1.0, (as_of_time - last_time).total_seconds() / (HALVING_INTERVAL_DAYS * 86400)))
        if last_time is not None
        else None
    )

    resolved = policy or resolve_policy()
    cycle_policy = _cycle_policy(resolved)
    valuation_settings = cycle_policy.get("valuation", {})
    holder_settings = cycle_policy.get("holder", {})
    valuation_state = _valuation(values, valuation_settings)
    holder_state = _holder(values, holder_settings)
    extension = _number(price_extension_atr)
    near_ath = distance_from_ath is not None and abs(distance_from_ath) <= 0.10
    price_signal = (
        extension is not None and extension >= cycle_policy.get("price", {}).get("extension_atr", 2.0)
    ) or near_ath
    trend = _state(trend_state)
    price_weak = trend in {"DOWNTREND", "STRONG_DOWNTREND", "BEARISH", "WEAK"} or (
        drawdown is not None and drawdown <= -cycle_policy.get("price", {}).get("drawdown_reset", 0.5)
    )
    price_strong = trend in {"UPTREND", "STRONG_UPTREND", "BULLISH", "STRONG"}
    holder_distribution = holder_state == HolderBehaviorState.DISTRIBUTION.value
    holder_accumulation = holder_state == HolderBehaviorState.ACCUMULATION.value
    flow_text = _state(flow_state)
    liquidity_text = _state(liquidity_state)
    breadth_text = _state(breadth_state)
    flow_weak = (
        flow_text in {"NEGATIVE", "WEAK", "DETERIORATING", "OUTFLOW"}
        or liquidity_text in {"NEGATIVE", "WEAK", "DETERIORATING"}
        or breadth_text in {"NEGATIVE", "WEAK", "DETERIORATING"}
    )
    flow_positive = (
        flow_text in {"POSITIVE", "HEALTHY", "IMPROVING"}
        or liquidity_text in {"POSITIVE", "HEALTHY", "IMPROVING"}
        or breadth_text in {"POSITIVE", "HEALTHY", "IMPROVING"}
    )
    positioning_state = None
    positioning_risk = None
    if isinstance(positioning, PositioningFacts):
        positioning_state, positioning_risk = positioning.leverage_state, positioning.risk
    elif isinstance(positioning, Mapping):
        positioning_state = positioning.get("leverage_state", positioning.get("state"))
        positioning_risk = positioning.get("risk")
    positioning_state = _state(positioning_state) or None
    positioning_risk = _state(positioning_risk) or None
    positioning_crowded = positioning_state in {"CROWDED", "EXTREME"} or positioning_risk in {"HIGH", "EXTREME"}

    confirmations: list[str] = []
    if valuation_state in {CycleValuationState.ELEVATED.value, CycleValuationState.EXTREME.value}:
        confirmations.append("valuation")
    if price_signal:
        confirmations.append("price_extension")
    if holder_distribution:
        confirmations.append("holder_distribution")
    if flow_weak:
        confirmations.append("flows_or_liquidity")
    if positioning_crowded:
        confirmations.append("positioning")
    elevated_count = int(cycle_policy.get("minimum_non_clock_confirmations_for_elevated_risk", 2))
    high_count = int(cycle_policy.get("minimum_non_clock_confirmations_for_high_risk", 3))
    timing_available = last_time is not None
    price_available = price_signal or price_weak or price_strong or distance_from_ath is not None or drawdown is not None
    valuation_available = valuation_state != CycleValuationState.UNKNOWN.value
    supporting_available = holder_state != HolderBehaviorState.UNKNOWN.value or flow_weak or flow_positive or positioning_state is not None
    if len(confirmations) >= high_count:
        market_state = MarketCycleState.OVERHEATED.value
        cycle_risk = CycleRisk.HIGH.value
    elif len(confirmations) >= elevated_count:
        market_state = MarketCycleState.OVERHEATED.value
        cycle_risk = CycleRisk.ELEVATED.value
    elif price_weak and (holder_distribution or flow_weak):
        market_state = MarketCycleState.CONTRACTION.value
        cycle_risk = CycleRisk.ELEVATED.value if holder_distribution and flow_weak else CycleRisk.NORMAL.value
    elif price_weak and holder_accumulation:
        market_state = MarketCycleState.RESET.value
        cycle_risk = CycleRisk.LOW.value
    elif price_strong and valuation_state in {CycleValuationState.NORMAL.value, CycleValuationState.UNKNOWN.value} and not positioning_crowded:
        market_state = MarketCycleState.EXPANSION.value
        cycle_risk = CycleRisk.NORMAL.value
    elif (
        days_since is not None
        and days_since >= cycle_policy.get("halving_context_days", {}).get("late_epoch_min", 900)
        and (confirmations or price_strong or price_weak or valuation_available or supporting_available)
    ):
        market_state = MarketCycleState.MATURE.value
        cycle_risk = CycleRisk.NORMAL.value
    else:
        market_state = MarketCycleState.UNKNOWN.value
        cycle_risk = CycleRisk.UNKNOWN.value

    if not timing_available or not price_available:
        confidence = "LOW"
    elif valuation_available and supporting_available:
        confidence = "HIGH"
    elif valuation_available or supporting_available or price_signal or price_strong or price_weak:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    reasons = ["halving timing is descriptive context and has no trade authority"]
    if confirmations:
        reasons.append(f"non-clock confirmations: {', '.join(confirmations)}")
    else:
        reasons.append("no independent non-clock cycle confirmation")
    if positioning_crowded:
        reasons.append("crowded positioning is treated as a risk confirmation, not a forecast")
    if flow_weak:
        reasons.append("flows or liquidity are weakening")
    source_metadata = {
        key: {
            "source": point.source,
            "observed_at": point.observed_at,
            **dict(point.metadata),
        }
        for key, point in selected.items()
    }
    source_metadata["halving_schedule"] = {
        "source": "Bitcoin protocol halving schedule",
        "estimated": True,
        "interval_days": HALVING_INTERVAL_DAYS,
    }
    evidence_ids = [point.observation_id for point in selected.values()]
    if isinstance(positioning, PositioningFacts):
        evidence_ids.extend(positioning.evidence_ids)
    return BTCCycleContext(
        as_of=normalized_as_of,
        last_halving_timestamp=last_halving_timestamp,
        days_since_halving=days_since,
        estimated_next_halving_timestamp=estimated_next,
        estimated_days_to_next_halving=days_to_next,
        halving_epoch_progress=progress,
        return_since_halving=return_since_halving,
        distance_from_ath=_number(distance_from_ath),
        drawdown=drawdown,
        mvrv=_number(values.get("mvrv")),
        mvrv_zscore=_number(values.get("mvrv_zscore")),
        realized_price=_number(values.get("realized_price")),
        market_to_realized_price=_number(values.get("market_to_realized_price")),
        sopr=_number(values.get("sopr")),
        lth_supply_pct=_number(values.get("lth_supply_pct")),
        lth_net_position_change=_number(values.get("lth_net_position_change")),
        sth_realized_price=_number(values.get("sth_realized_price")),
        lth_realized_price=_number(values.get("lth_realized_price")),
        nupl=_number(values.get("nupl")),
        halving_context=halving_context_for_days(days_since, resolved) if days_since is not None else HalvingContext.PRE_HALVING.value,
        valuation_state=valuation_state,
        holder_state=holder_state,
        market_cycle_state=market_state,
        cycle_risk=cycle_risk,
        confidence=confidence,
        positioning_state=positioning_state,
        positioning_risk=positioning_risk,
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        reasons=tuple(dict.fromkeys(reasons)),
        source_metadata=source_metadata,
        data_quality_flags=(),
    )


build_cycle_context = build_btc_cycle_context
classify_btc_cycle = build_btc_cycle_context


__all__ = [
    "HALVING_EVENTS",
    "HALVING_INTERVAL_DAYS",
    "build_btc_cycle_context",
    "build_cycle_context",
    "classify_btc_cycle",
    "halving_context_for_days",
]
