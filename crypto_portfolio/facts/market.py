"""Market facts are deterministic observations, not investment conclusions."""

from ..engine.metric_history import build_factor_facts
from .models import FactBase


def build_market_facts(observations, symbol="MARKET", *, previous_observations=None) -> FactBase:
    return build_factor_facts(observations, symbol=symbol, previous_observations=previous_observations)


__all__ = ["build_market_facts"]
