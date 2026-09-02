from ..engine.metric_history import build_factor_facts
from .models import EventFacts


def build_event_facts(observations, symbol, *, previous_observations=None) -> EventFacts:
    return build_factor_facts(observations, symbol=symbol, factor="event_risk", previous_observations=previous_observations, fact_type=EventFacts)


__all__ = ["EventFacts", "build_event_facts"]
