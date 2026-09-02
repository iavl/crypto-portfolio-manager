from ..engine.metric_history import build_factor_facts
from .models import OnchainFacts


def build_onchain_facts(observations, symbol, *, previous_observations=None) -> OnchainFacts:
    return build_factor_facts(observations, symbol=symbol, factor="onchain", previous_observations=previous_observations, fact_type=OnchainFacts)


build_on_chain_facts = build_onchain_facts


__all__ = ["OnchainFacts", "build_onchain_facts", "build_on_chain_facts"]
