"""Deterministic fact-packet façade for normalized metric observations."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..facts.models import FactBase
from .metric_history import build_factor_facts, build_facts_for_asset
from .positioning import build_positioning_facts
from .cycle import build_btc_cycle_context


def build_deterministic_facts(
    observations,
    *,
    symbol: str | None = None,
    factor: str | None = None,
    previous_observations=None,
) -> FactBase:
    return build_factor_facts(
        observations,
        symbol=symbol,
        factor=factor,
        previous_observations=previous_observations,
    )


def build_asset_facts(
    observations: Iterable[Any],
    symbol: str,
    *,
    previous_observations: Iterable[Any] | None = None,
) -> Mapping[str, FactBase]:
    return build_facts_for_asset(
        observations,
        symbol,
        previous_observations=previous_observations,
    )


build_facts = build_deterministic_facts


__all__ = [
    "build_asset_facts",
    "build_btc_cycle_context",
    "build_deterministic_facts",
    "build_facts",
    "build_positioning_facts",
]
