from ..engine.metric_history import build_factor_facts
from .models import BTCValuationFacts, ValuationFacts


def build_valuation_facts(observations, symbol, *, previous_observations=None) -> ValuationFacts:
    return build_factor_facts(observations, symbol=symbol, factor="valuation", previous_observations=previous_observations, fact_type=ValuationFacts)


def build_btc_valuation_facts(observations, symbol="BTC", *, previous_observations=None) -> BTCValuationFacts:
    return build_factor_facts(
        observations,
        symbol=symbol,
        factor="btc_valuation",
        previous_observations=previous_observations,
        fact_type=BTCValuationFacts,
    )


__all__ = ["BTCValuationFacts", "ValuationFacts", "build_btc_valuation_facts", "build_valuation_facts"]
