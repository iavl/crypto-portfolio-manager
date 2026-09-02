from ..engine.metric_history import build_factor_facts
from .models import ValuationFacts


def build_valuation_facts(observations, symbol, *, previous_observations=None) -> ValuationFacts:
    return build_factor_facts(observations, symbol=symbol, factor="valuation", previous_observations=previous_observations, fact_type=ValuationFacts)


__all__ = ["ValuationFacts", "build_valuation_facts"]
