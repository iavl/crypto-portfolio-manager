"""Deterministic structure-based staged entry planning."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ..models.execution import ExecutionPlan, ExecutionTranche, PriceZone
from ..models.market import TechnicalSnapshot
from ..models.policy import Policy, resolve_policy


_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_MODES = {"PULLBACK", "BREAKOUT", "MIXED", "WAIT"}


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
    return {
        **dict(snapshot.ohlcv_metadata or {}),
        "symbol": snapshot.symbol,
        "source": snapshot.source,
        "timeframe": snapshot.timeframe,
        "candle_count": snapshot.history_days,
        "end_timestamp": snapshot.as_of,
        "ohlcv_hash": snapshot.ohlcv_hash,
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
        execution_plan_version=1,
        symbol=symbol,
        action=action,
        approved_amount_usd=approved,
        planned_amount_usd=0.0,
        unallocated_amount_usd=approved,
        current_price=snapshot.current_spot_price,
        entry_mode="WAIT",
        technical_confidence=snapshot.technical_confidence,
        rationale=reason,
        ohlcv_hash=snapshot.ohlcv_hash or None,
        ohlcv_metadata=_metadata(snapshot),
    )


def _zone_quality(
    zone: PriceZone,
    current_price: float,
    atr_value: float,
    *,
    volume_state: str,
) -> tuple[float, str]:
    confluence = min(35.0, 18.0 + 9.0 * max(0, len(zone.sources) - 1))
    source_bonus = min(25.0, max(
        (
            {
                "SWING_LOW": 20.0,
                "MA50": 18.0,
                "MA100": 20.0,
                "MA200": 22.0,
                "MA20": 10.0,
                "BREAKOUT_RETEST": 22.0,
                "ATR_PULLBACK": 8.0,
            }.get(source, 0.0)
            for source in zone.sources
        ),
        default=0.0,
    ))
    distance_atr = max(0.0, (current_price - zone.midpoint) / atr_value)
    distance_bonus = max(0.0, 22.0 - min(22.0, distance_atr * 4.0))
    volume_bonus = 10.0 if volume_state == "SUPPORTIVE" else 0.0
    quality = min(100.0, confluence + source_bonus + distance_bonus + volume_bonus + zone.strength * 0.20)
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
) -> list[tuple[PriceZone, float, str]]:
    selected: list[tuple[PriceZone, float, str]] = []
    for candidate in ranked:
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
    if mode == "BREAKOUT":
        cap = config["breakout"]["max_initial_tranche"]
        first = min(values[0], cap)
        remainder = values[0] - first
        values[0] = first
        if remainder and len(values) > 1:
            tail = sum(values[1:])
            for index in range(1, len(values)):
                values[index] += remainder * values[index] / tail
    deployed_fraction = sum(values)
    normalized = [value / deployed_fraction for value in values]
    return normalized, deployed_fraction


def _breakout_allowed(
    snapshot: TechnicalSnapshot,
    regime: str,
    portfolio_confidence: str,
    config: Mapping[str, Any],
    *,
    breakout_confirmed: bool,
    severe_event: bool,
    thesis_broken: bool,
    relative_strength_confirmed: bool,
) -> bool:
    if not breakout_confirmed or severe_event or thesis_broken or not relative_strength_confirmed:
        return False
    if regime != "NORMAL" or portfolio_confidence != "HIGH" or snapshot.technical_confidence != "HIGH":
        return False
    if snapshot.trend_state != "STRONG_UPTREND" or snapshot.volume_state != "SUPPORTIVE":
        return False
    if snapshot.relative_volume is None or snapshot.relative_volume < config["breakout"]["minimum_relative_volume"]:
        return False
    if snapshot.atr14 is None or not snapshot.support_zones:
        return False
    candidates = [zone.midpoint for zone in snapshot.support_zones if zone.midpoint <= snapshot.current_spot_price]
    if not candidates:
        return False
    nearest = max(candidates)
    return (snapshot.current_spot_price - nearest) / snapshot.atr14 <= config["breakout"]["max_atr_extension"]


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
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "technical entry planner only stages approved increases", action=normalized_action)
    if approved <= 0:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "approved amount is zero")
    if normalized_regime == "CAPITAL_PRESERVATION":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "capital-preservation regime reserves all approved risk")
    if technical_snapshot.history_days < config["minimum_history_days"]:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "insufficient completed daily history")
    if technical_snapshot.data_quality.startswith(("INSUFFICIENT", "STALE", "CONFLICT")):
        return _wait_plan(normalized_symbol, approved, technical_snapshot, f"technical data quality is {technical_snapshot.data_quality}")
    if technical_snapshot.technical_confidence == "LOW":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "technical confidence is LOW")
    if technical_snapshot.volatility_state == "EXTREME":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "volatility is EXTREME")

    requested_mode = "PULLBACK" if entry_mode is None else str(entry_mode).strip().upper()
    if requested_mode not in _MODES:
        raise ValueError(f"entry_mode must be one of {sorted(_MODES)}")
    if requested_mode == "WAIT":
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "entry mode is explicitly WAIT")
    if requested_mode == "BREAKOUT" and not _breakout_allowed(
        technical_snapshot,
        normalized_regime,
        portfolio_level,
        config,
        breakout_confirmed=breakout_confirmed,
        severe_event=severe_event,
        thesis_broken=thesis_broken,
        relative_strength_confirmed=relative_strength_confirmed,
    ):
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "breakout gates are not all satisfied")
    mode = requested_mode if requested_mode in {"BREAKOUT", "MIXED"} else "PULLBACK"
    ranked = rank_support_zones(technical_snapshot)
    if not ranked:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "no confirmed support structure is available")
    selected = _select_zones(
        ranked,
        max_tranches=config["max_tranches"],
        atr_value=technical_snapshot.atr14 or 0.0,
        separation_factor=config["minimum_zone_separation_atr"],
    )
    if not selected:
        return _wait_plan(normalized_symbol, approved, technical_snapshot, "support candidates are not materially distinct")
    nearest = selected[0][0]
    extension = (technical_snapshot.current_spot_price - nearest.midpoint) / (technical_snapshot.atr14 or 1.0)
    if extension > config["breakout"]["max_atr_extension"] and mode in {"PULLBACK", "MIXED"}:
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
        config["confidence_deployment_factor"][technical_snapshot.technical_confidence],
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
    invalidation = {
        "kind": "STRUCTURAL_SUPPORT_LOSS",
        "trigger": "completed daily close below major confirmed support",
        "reference_price": major_zone.low,
        "review_only": True,
        "automatic_order": False,
    }
    rationale = (
        f"{mode} entry from {len(selected)} confirmed support zone(s); "
        f"planned {planned:.2f} USD of {approved:.2f} USD approved capacity"
    )
    return ExecutionPlan(
        execution_plan_version=1,
        symbol=normalized_symbol,
        action="INCREASE",
        approved_amount_usd=approved,
        planned_amount_usd=planned,
        unallocated_amount_usd=approved - planned,
        current_price=technical_snapshot.current_spot_price,
        entry_mode=mode,
        technical_confidence=technical_snapshot.technical_confidence,
        tranches=tuple(tranche_values),
        invalidation=invalidation,
        rationale=rationale,
        ohlcv_hash=technical_snapshot.ohlcv_hash or None,
        ohlcv_metadata=_metadata(technical_snapshot),
    )


__all__ = ["build_entry_plan", "rank_support_zones"]
