from ..engine.metric_history import build_factor_facts
from .models import FundamentalFacts


def build_fundamental_facts(observations, symbol, *, previous_observations=None) -> FundamentalFacts:
    return build_factor_facts(observations, symbol=symbol, factor="fundamentals", previous_observations=previous_observations, fact_type=FundamentalFacts)


build_fundamentals_facts = build_fundamental_facts


__all__ = ["FundamentalFacts", "build_fundamental_facts", "build_fundamentals_facts"]
