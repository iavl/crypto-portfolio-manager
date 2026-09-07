"""Builders for compact semantic factor packets."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..facts.models import FactBase
from ..models.factor_packet import AssetFactorPacket
from ..models.metrics_history import MetricObservation
from .metric_history import build_facts_for_asset


def _eth_fundamental_groups(fact: FactBase) -> Mapping[str, Any]:
    """Add only metric-key grouping metadata; numeric facts remain flat and Python-owned."""
    payload = fact.as_dict()
    current = dict(payload.get("current", {}))
    keys = tuple(current)
    groups = {
        "monetary_economics": [key for key in keys if key.startswith("eth.monetary.")],
        "staking_security": [key for key in keys if key.startswith("eth.staking.")],
        "settlement_da": [key for key in keys if key.startswith(("eth.l2.", "eth.da."))],
        "defi_stablecoin": [key for key in keys if key.startswith("fundamentals.") and key not in {"fundamentals.developer_activity"}],
        "developer_ecosystem": [key for key in keys if key == "fundamentals.developer_activity"],
    }
    current["semantic_groups"] = {name: values for name, values in groups.items() if values}
    payload["current"] = current
    return payload


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
        "trend", "valuation", "fundamentals", "onchain", "capital_flows",
        "relative_strength_btc", "btc_valuation", "macro_liquidity", "event_risk",
    }
    for key in factor_facts:
        if key.removesuffix("_facts") not in allowed:
            raise ValueError(f"unsupported factor packet field: {key}")
    if any(str(key).removesuffix("_facts") not in allowed for key in values):
        raise ValueError("facts contains an unsupported or raw field")
    if observations is not None:
        values.update(build_facts_for_asset(observations, symbol, previous_observations=previous_observations))
    values.update({key.removesuffix("_facts"): value for key, value in factor_facts.items() if value is not None})
    if symbol.strip().upper() == "ETH" and isinstance(values.get("fundamentals"), FactBase):
        values["fundamentals"] = _eth_fundamental_groups(values["fundamentals"])
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
        btc_valuation_facts=values.get("btc_valuation"),
        macro_liquidity_facts=values.get("macro_liquidity"),
        fundamental_facts=values.get("fundamentals"),
        onchain_facts=values.get("onchain"),
        flow_facts=values.get("capital_flows"),
        relative_strength_facts=values.get("relative_strength_btc"),
        event_facts=values.get("event_risk"),
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


def validate_asset_factor_packet(value: AssetFactorPacket | Mapping[str, Any]) -> bool:
    AssetFactorPacket.from_mapping(value) if not isinstance(value, AssetFactorPacket) else value
    return True


__all__ = ["build_asset_factor_packet", "build_factor_packets", "validate_asset_factor_packet"]
