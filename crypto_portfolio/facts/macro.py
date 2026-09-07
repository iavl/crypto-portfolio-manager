"""Deterministic macro/liquidity facts."""

from ..engine.metric_history import build_factor_facts
from .models import MacroLiquidityFacts


def build_macro_liquidity_facts(observations, symbol="BTC", *, previous_observations=None) -> MacroLiquidityFacts:
    return build_factor_facts(
        observations,
        symbol=symbol,
        factor="macro_liquidity",
        previous_observations=previous_observations,
        fact_type=MacroLiquidityFacts,
    )


__all__ = ["MacroLiquidityFacts", "build_macro_liquidity_facts"]
