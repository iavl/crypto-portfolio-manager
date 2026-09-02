"""Deterministic structure-based staged entry planning."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ..models.execution import ExecutionPlan, ExecutionTranche, Invalidation, PriceZone
from ..models.evidence import Evidence
from ..models.market import TechnicalSnapshot
from ..models.policy import Policy, resolve_policy
from .technical import structural_confluence


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_MODES = {"PULLBACK", "BREAKOUT", "WAIT"}


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{field} must be finite and >= {minimum}")
    return result


def _confidence(value: Any, field: str) -> str:
    if not isinstance(value, str) or value.strip().upper() not in _CONFIDENCE:
        raise ValueError(f"{field} must be one of {sorted(_CONFIDENCE)}")
    return value.strip().upper()


def _metadata(snapshot: TechnicalSnapshot) -> dict[str, Any]:
    metadata = dict(snapshot.ohlcv_metadata or {})
    return {
        **metadata,
        "symbol": snapshot.symbol,
        "source": snapshot.source,
        "timeframe": snapshot.timeframe,
        "candle_count": snapshot.candle_count,
        "end_timestamp": metadata.get("end_timestamp", snapshot.as_of),
        "ohlcv_hash": snapshot.ohlcv_hash or None,
        "calendar_span_days": snapshot.calendar_span_days,
        "missing_day_count": snapshot.missing_day_count,
        "coverage_ratio": snapshot.coverage_ratio,
        "max_gap_days": snapshot.max_gap_days,
        "observation_lag_days": snapshot.observation_lag_days,
        "venue": (snapshot.ohlcv_metadata or {}).get("venue"),
        "market": (snapshot.ohlcv_metadata or {}).get("market"),
        "quote_currency": (snapshot.ohlcv_metadata or {}).get("quote_currency"),
    }


def _wait_plan(
    symbol: str,
    approved: float,
    snapshot: TechnicalSnapshot,
    reason: str,
    *,
    action: str = "WAIT",
) -> ExecutionPlan:
    return ExecutionPlan(
        execution_plan_version=2,
        symbol=symbol,
        action=action,
        approved_amount_usd=approved,
        planned_amount_usd=0.0,
        unallocated_amount_usd=approved,
        current_price=snapshot.current_spot_price,
        entry_mode="WAIT",
        technical_confidence=snapshot.data_confidence,
        rationale=reason,
        ohlcv_hash=snapshot.ohlcv_hash or None,
        volume_profile_hash=snapshot.volume_profile_hash,
        volume_profile_metadata=snapshot.volume_profile_metadata,
        ohlcv_metadata=_metadata(snapshot),
        technical_summary=snapshot.technical_summary(),
    )


def _zone_quality(
    zone: PriceZone,
    current_price: float,
    atr_value: float,
    *,
    volume_state: str,
) -> tuple[float, str]:
    confluence = structural_confluence(zone.sources)
    source_bonus = min(25.0, max(
        (
            {
                "SWING_LOW": 20.0,
                "MA50": 18.0,
                "MA100": 20.0,
                "MA200": 22.0,
                "MA20": 10.0,
            }.get(source, 0.0)
            for source in zone.sources
        ),
        default=0.0,
    ))
    distance_atr = max(0.0, (current_price - zone.midpoint) / atr_value)
    distance_bonus = max(0.0, 22.0 - min(22.0, distance_atr * 4.0))
    profile_sources = [source for source in zone.sources if source.startswith("VOLUME_")]
    profile_bonus = min(
        12.0,
        4.0 * len(profile_sources) + (4.0 if "VOLUME_PROFILE_CONFLUENCE" in zone.sources else 0.0),
    )
    relative_volume_bonus = 5.0 if volume_state == "SUPPORTIVE" else 0.0
    volume_bonus = min(15.0, profile_bonus + relative_volume_bonus)
    quality = confluence + source_bonus + distance_bonus + volume_bonus + zone.strength * 0.20
    if zone.sources and all(source.startswith("VOLUME_") for source in zone.sources):
        quality = min(50.0, quality)
    quality = min(100.0, quality)
    reasons = [f"{source} confluence" for source in zone.sources]
    reasons.append(f"{distance_atr:.2f} ATR below spot")
    if volume_state == "SUPPORTIVE":
        reasons.append("supportive completed-candle volume")
    elif volume_state == "UNKNOWN":
        reasons.append("volume unavailable; confidence reduced")
    return quality, "; ".join(reasons)


def rank_support_zones(
    snapshot: TechnicalSnapshot,
) -> tuple[tuple[PriceZone, float, str], ...]:
    """Return support candidates with deterministic quality and reasons."""
    if snapshot.atr14 is None or snapshot.atr14 <= 0:
        return ()
    ranked = [
        (zone, *_zone_quality(zone, snapshot.current_spot_price, snapshot.atr14, volume_state=snapshot.volume_state))
        for zone in snapshot.support_zones
        if zone.high <= snapshot.current_spot_price
    ]
    return tuple(sorted(ranked, key=lambda item: (-item[1], -item[0].midpoint, item[0].sources)))


def _select_zones(
    ranked: Iterable[tuple[PriceZone, float, str]],
    *,
    max_tranches: int,
    atr_value: float,
    separation_factor: float,
    minimum_quality: float,
) -> list[tuple[PriceZone, float, str]]:
    candidates = [candidate for candidate in ranked if candidate[1] >= minimum_quality]
    if not candidates:
        return []
    nearest = max(candidates, key=lambda item: (item[0].midpoint, -item[1]))
    selected: list[tuple[PriceZone, float, str]] = [nearest]
    for candidate in sorted(candidates, key=lambda item: (-item[1], -item[0].midpoint, item[0].sources)):
        if candidate == nearest:
            continue
        if all(abs(candidate[0].midpoint - item[0].midpoint) >= atr_value * separation_factor for item in selected):
            selected.append(candidate)
        if len(selected) == max_tranches:
            break
    return sorted(selected, key=lambda item: item[0].midpoint, reverse=True)


def _template(config: Mapping[str, Any], regime: str, volatility: str) -> list[float]:
    templates = config["tranche_templates"]
    if regime == "DEFENSIVE":
        return list(templates["DEFENSIVE"])
    if regime == "CAPITAL_PRESERVATION":
        return list(templates["CAPITAL_PRESERVATION"])
    return list(templates["NORMAL_HIGH_VOL" if volatility == "HIGH" else "NORMAL_LOW_VOL"])


def _fractions(
    config: Mapping[str, Any],
    regime: str,
    volatility: str,
    mode: str,
    count: int,
    qualities: Iterable[float] = (),
) -> tuple[list[float], float]:
    values = _template(config, regime, volatility)[:count]
    quality_values = list(qualities)
    if len(quality_values) == count:
        values = [value * (0.75 + max(0.0, min(100.0, quality)) / 200.0) for value, quality in zip(values, quality_values)]
    cap = config["max_initial_tranche"][regime]
    if values[0] > cap:
        remainder = values[0] - cap
        values[0] = cap
        if len(values) > 1:
            tail = sum(values[1:])
            for index in range(1, len(values)):
                values[index] += remainder * values[index] / tail
    deployed_fraction = sum(values)
    normalized = [value / deployed_fraction for value in values]
    return normalized, deployed_fraction


def build_entry_plan(
    symbol: str,
    approved_amount_usd: float,
    technical_snapshot: TechnicalSnapshot,
    regime: str,
    portfolio_confidence: str,
    action: str = "INCREASE",
    *,
    policy: Policy | None = None,
    entry_mode: str | None = None,
    breakout_confirmed: bool = False,
    severe_event: bool = False,
    thesis_broken: bool = False,
    relative_strength_confirmed: bool = False,
) -> ExecutionPlan:
    """Stage only the amount approved by the portfolio/rebalance engine."""
    if not isinstance(technical_snapshot, TechnicalSnapshot):
        raise ValueError("technical_snapshot must be a validated TechnicalSnapshot")
    normalized_symbol = str(symbol).strip().upper()
    if not normalized_symbol or normalized_symbol != technical_snapshot.symbol:
        raise ValueError("symbol must match technical_snapshot.symbol")
    approved = _number(approved_amount_usd, "approved_amount_usd")
    normalized_regime = str(regime).strip().upper()
    if normalized_regime not in _REGIMES:
        raise ValueError(f"regime must be one of {sorted(_REGIMES)}")
    portfolio_level = _confidence(portfolio_confidence, "portfolio_confidence")
    normalized_action = str(action).strip().upper()
    if normalized_action not in _ACTIONS:
        raise ValueError(f"action must be one of {sorted(_ACTIONS)}")
    config = dict((policy or resolve_policy()).execution)
    if not config:
        raise ValueError("execution configuration is required")
    if normalized_action != "INCREASE":
        raise ValueError("technical entry planner only supports INCREASE actions")
    if approved <= 0:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "approved amount is zero")
    if normalized_regime == "CAPITAL_PRESERVATION":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "capital-preservation regime reserves all approved risk")
    if not technical_snapshot.history_sufficient or technical_snapshot.history_days < config["minimum_history_days"]:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "insufficient completed daily history")
    if not technical_snapshot.market_data_fresh:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "market observations are stale")
    if "STALE_RETRIEVAL" in technical_snapshot.data_quality_flags:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "market-data retrieval is stale")
    if not technical_snapshot.cadence_valid:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "daily OHLCV coverage is insufficient")
    if "DATA_CONFLICT" in technical_snapshot.data_quality_flags:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "spot and OHLCV observations conflict")
    if technical_snapshot.data_confidence == "LOW":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "technical data confidence is LOW")
    if technical_snapshot.volatility_state == "EXTREME":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "volatility is EXTREME")

    requested_mode = "PULLBACK" if entry_mode is None else str(entry_mode).strip().upper()
    if requested_mode not in _MODES:
        raise ValueError(f"entry_mode must be one of {sorted(_MODES)}")
    if requested_mode == "WAIT":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "entry mode is explicitly WAIT")
    if requested_mode == "BREAKOUT":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "BREAKOUT generation is disabled until breakout/retest structure is implemented")
    mode = "PULLBACK"
    if technical_snapshot.setup_quality < config["zone_quality"]["minimum_for_entry"]:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "setup quality is below the entry threshold")
    ranked = rank_support_zones(technical_snapshot)
    if not ranked:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "no confirmed support structure is available")
    selected = _select_zones(
        ranked,
        max_tranches=config["max_tranches"],
        atr_value=technical_snapshot.atr14 or 0.0,
        separation_factor=config["minimum_zone_separation_atr"],
        minimum_quality=config["zone_quality"]["minimum_for_entry"],
    )
    if not selected:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "support candidates are not materially distinct")
    nearest = selected[0][0]
    extension = (technical_snapshot.current_spot_price - nearest.midpoint) / (technical_snapshot.atr14 or 1.0)
    if extension > config["breakout"]["max_atr_extension"]:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, f"spot is {extension:.2f} ATR above nearest support")

    fractions, deployed_fraction = _fractions(
        config,
        normalized_regime,
        technical_snapshot.volatility_state,
        mode,
        len(selected),
        (quality for _, quality, _ in selected),
    )
    confidence_factor = min(
        config["confidence_deployment_factor"][portfolio_level],
        config["confidence_deployment_factor"][technical_snapshot.data_confidence],
    )
    planned = approved * deployed_fraction * confidence_factor
    planned = min(approved, max(0.0, planned))
    tranche_values = []
    for sequence, ((zone, quality, reason), fraction) in enumerate(zip(selected, fractions), 1):
        amount = planned * fraction
        reference = zone.midpoint
        tranche_values.append(
            ExecutionTranche(
                sequence=sequence,
                allocation_fraction=fraction,
                amount_usd=amount,
                price_low=zone.low,
                price_high=zone.high,
                reference_price=reference,
                estimated_quantity=amount / reference,
                rationale=reason,
                structural_sources=zone.sources,
                zone_quality=quality,
            )
        )
    major_zone = selected[-1][0]
    invalidation = Invalidation(
        kind="STRUCTURAL_SUPPORT_LOSS",
        trigger="completed daily close below major confirmed support",
        reference_price=major_zone.low,
    )
    rationale = (
        f"{mode} entry from {len(selected)} confirmed support zone(s); "
        f"planned {planned:.2f} USD of {approved:.2f} USD approved capacity"
    )
    return ExecutionPlan(
        execution_plan_version=2,
        symbol=normalized_symbol,
        action="INCREASE",
        approved_amount_usd=approved,
        planned_amount_usd=planned,
        unallocated_amount_usd=approved - planned,
        current_price=technical_snapshot.current_spot_price,
        entry_mode=mode,
        technical_confidence=technical_snapshot.data_confidence,
        tranches=tuple(tranche_values),
        invalidation=invalidation,
        rationale=rationale,
        ohlcv_hash=technical_snapshot.ohlcv_hash or None,
        volume_profile_hash=technical_snapshot.volume_profile_hash,
        volume_profile_metadata=technical_snapshot.volume_profile_metadata,
        ohlcv_metadata=_metadata(technical_snapshot),
        technical_summary=technical_snapshot.technical_summary(zone for zone, _, _ in selected),
    )


def build_execution_evidence(
    snapshot: TechnicalSnapshot, plan: ExecutionPlan | None = None
) -> Evidence:
    """Create the structured evidence record that explains a technical plan."""
    if not isinstance(snapshot, TechnicalSnapshot):
        raise ValueError("snapshot must be a TechnicalSnapshot")
    if plan is not None and plan.symbol != snapshot.symbol:
        raise ValueError("plan symbol must match snapshot symbol")
    fetched_at = snapshot.spot_fetched_at or (snapshot.ohlcv_metadata or {}).get("fetched_at")
    if fetched_at is None:
        raise ValueError("technical evidence requires a fetched_at timestamp")
    selected_zones = ()
    if plan is not None and plan.technical_summary is not None:
        selected_zones = tuple(
            PriceZone.from_mapping(value)
            for value in plan.technical_summary.get("selected_zones", ())
        )
    summary = snapshot.technical_summary(selected_zones)
    digest = snapshot.ohlcv_hash or "no-ohlcv"
    return Evidence(
        id=f"execution-technical:{snapshot.symbol}:{digest}",
        asset=snapshot.symbol,
        factor="execution_technical",
        source=snapshot.spot_source or snapshot.source or "unknown",
        observed_at=snapshot.spot_observed_at or snapshot.as_of,
        fetched_at=fetched_at,
        freshness="CURRENT" if snapshot.market_data_fresh else "STALE",
        confidence=snapshot.data_confidence,
        value={
            "ohlcv_hash": snapshot.ohlcv_hash or None,
            "volume_profile_hash": snapshot.volume_profile_hash,
            "technical_summary": summary,
        },
        summary=plan.rationale if plan is not None else "technical execution evidence",
    )


__all__ = ["build_entry_plan", "build_execution_evidence", "rank_support_zones"]
