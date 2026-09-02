from ..engine.metric_history import build_factor_facts
from .models import FlowFacts


def build_flow_facts(observations, symbol, *, previous_observations=None) -> FlowFacts:
    return build_factor_facts(observations, symbol=symbol, factor="capital_flows", previous_observations=previous_observations, fact_type=FlowFacts)


__all__ = ["FlowFacts", "build_flow_facts"]
