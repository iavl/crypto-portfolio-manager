from ..engine.metric_history import build_factor_facts
from .models import TrendFacts


def build_trend_facts(observations, symbol, *, previous_observations=None) -> TrendFacts:
    return build_factor_facts(observations, symbol=symbol, factor="trend", previous_observations=previous_observations, fact_type=TrendFacts)


__all__ = ["TrendFacts", "build_trend_facts"]
