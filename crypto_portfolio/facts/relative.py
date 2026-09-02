from ..engine.metric_history import build_factor_facts
from .models import RelativeStrengthFacts


def build_relative_strength_facts(observations, symbol, *, previous_observations=None) -> RelativeStrengthFacts:
    return build_factor_facts(observations, symbol=symbol, factor="relative_strength_btc", previous_observations=previous_observations, fact_type=RelativeStrengthFacts)


__all__ = ["RelativeStrengthFacts", "build_relative_strength_facts"]
