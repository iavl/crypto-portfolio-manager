"""Builders for compact semantic factor packets."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..facts.models import FactBase
from ..models.factor_packet import AssetFactorPacket
from ..models.metrics_history import MetricObservation
from .metric_history import build_facts_for_asset


def build_asset_factor_packet(
    symbol: str,
    facts: Mapping[str, FactBase | Mapping[str, Any]] | None = None,
    *,
    observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
    previous_observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
    previous_assessment: Any = None,
    coverage: float | None = None,
    evidence_ids: Iterable[str] = (),
    **factor_facts: Any,
) -> AssetFactorPacket:
    """Build a packet without forwarding raw source payloads."""
    values = dict(facts or {})
    allowed = {
        "trend", "valuation", "fundamentals", "fundamental", "onchain",
        "capital_flows", "flows", "relative_strength_btc", "relative_strength",
        "event_risk", "events",
    }
    for key in factor_facts:
        if key.removesuffix("_facts") not in allowed:
            raise ValueError(f"unsupported factor packet field: {key}")
    if any(str(key).removesuffix("_facts") not in allowed for key in values):
        raise ValueError("facts contains an unsupported or raw field")
    if observations is not None:
        values.update(build_facts_for_asset(observations, symbol, previous_observations=previous_observations))
    values.update({key.removesuffix("_facts"): value for key, value in factor_facts.items() if value is not None})
    ids = list(evidence_ids)
    for fact in values.values():
        if isinstance(fact, FactBase):
            ids.extend(fact.source_ids)
        elif isinstance(fact, Mapping):
            ids.extend(fact.get("source_ids", ()))
    return AssetFactorPacket(
        symbol=symbol,
        trend_facts=values.get("trend"),
        valuation_facts=values.get("valuation"),
        fundamental_facts=values.get("fundamentals", values.get("fundamental")),
        onchain_facts=values.get("onchain"),
        flow_facts=values.get("capital_flows", values.get("flows")),
        relative_strength_facts=values.get("relative_strength_btc", values.get("relative_strength")),
        event_facts=values.get("event_risk", values.get("events")),
        coverage=coverage,
        previous_assessment=previous_assessment,
        evidence_ids=tuple(dict.fromkeys(ids)),
    )


def build_factor_packets(
    symbols: Iterable[str],
    observations: Iterable[MetricObservation | Mapping[str, Any]],
    *,
    previous_observations: Iterable[MetricObservation | Mapping[str, Any]] | None = None,
) -> tuple[AssetFactorPacket, ...]:
    values = tuple(observations)
    previous = tuple(previous_observations or ())
    return tuple(
        build_asset_factor_packet(
            symbol,
            observations=values,
            previous_observations=previous,
        )
        for symbol in symbols
    )


build_factor_packet = build_asset_factor_packet


def validate_asset_factor_packet(value: AssetFactorPacket | Mapping[str, Any]) -> bool:
    AssetFactorPacket.from_mapping(value) if not isinstance(value, AssetFactorPacket) else value
    return True


__all__ = ["build_asset_factor_packet", "build_factor_packet", "build_factor_packets", "validate_asset_factor_packet"]
