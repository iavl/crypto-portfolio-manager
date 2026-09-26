"""Market-only regime authority separation (Strategy V2.1 Phase A).

The volatility-budget risk engine must classify the regime from market and
systemic conditions only: the portfolio's own drawdown may not vote the regime
more defensive nor pin it through the mandatory floor. Legacy drawdown mode
keeps the full floor behavior byte for byte.
"""

import unittest

from crypto_portfolio.engine.regime import (
    RegimeInputs,
    determine_regime,
    market_only_regime,
)
from crypto_portfolio.models.policy import load_policy


_MARKET_RISK_OFF = {
    "btc_trend": "BEARISH",
    "volatility_state": "HIGH",
    "flow_state": "WEAK",
    "breadth_state": "BEARISH",
    "systemic_event_risk": False,
}

_MARKET_CALM = {
    "btc_trend": "BULLISH",
    "volatility_state": "NORMAL",
    "flow_state": "POSITIVE",
    "breadth_state": "HEALTHY",
    "systemic_event_risk": False,
}


class MarketOnlyRegimeTests(unittest.TestCase):
    def test_case_a_portfolio_drawdown_never_moves_the_market_regime(self):
        policy = load_policy()
        for base in (_MARKET_CALM, _MARKET_RISK_OFF):
            calm = determine_regime(
                RegimeInputs(portfolio_drawdown_band=0.0, **base),
                policy=policy,
                include_portfolio_drawdown=False,
            )
            stressed = determine_regime(
                RegimeInputs(portfolio_drawdown_band=-0.14, **base),
                policy=policy,
                include_portfolio_drawdown=False,
            )
            self.assertEqual(calm.regime, stressed.regime)
            self.assertEqual(calm.confidence, stressed.confidence)

    def test_case_a_legacy_mode_still_pins_the_regime_on_drawdown(self):
        policy = load_policy()
        calm = determine_regime(
            RegimeInputs(portfolio_drawdown_band=0.0, **_MARKET_CALM), policy=policy
        )
        stressed = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.10, **_MARKET_CALM), policy=policy
        )
        breached = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_CALM), policy=policy
        )
        self.assertEqual(calm.regime, "NORMAL")
        # -10% vs a 15% budget sits between the -0.6D and -0.8D floors.
        self.assertEqual(stressed.regime, "DEFENSIVE")
        self.assertEqual(breached.regime, "CAPITAL_PRESERVATION")

    def test_case_b_legacy_floor_thresholds_are_unchanged(self):
        policy = load_policy()
        budget = policy.max_portfolio_drawdown
        cases = {
            -0.5 * budget: "NORMAL",
            -0.61 * budget: "DEFENSIVE",
            -0.81 * budget: "CAPITAL_PRESERVATION",
        }
        for drawdown, expected in cases.items():
            result = determine_regime(
                RegimeInputs(portfolio_drawdown_band=drawdown, **_MARKET_CALM),
                policy=policy,
            )
            self.assertEqual(result.regime, expected, msg=f"drawdown {drawdown}")

    def test_case_b_legacy_default_includes_drawdown(self):
        policy = load_policy()
        default = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_CALM), policy=policy
        )
        explicit = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_CALM),
            policy=policy,
            include_portfolio_drawdown=True,
        )
        self.assertEqual(default.regime, explicit.regime)
        self.assertEqual(default.reasons, explicit.reasons)

    def test_market_only_regime_ignores_drawdown_in_both_modes(self):
        policy = load_policy()
        calm = market_only_regime(
            RegimeInputs(portfolio_drawdown_band=0.0, **_MARKET_CALM), policy=policy
        )
        stressed = market_only_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_RISK_OFF), policy=policy
        )
        self.assertEqual(calm, "NORMAL")
        self.assertEqual(stressed, "CAPITAL_PRESERVATION")

    def test_severe_systemic_event_still_overrides_without_drawdown(self):
        policy = load_policy()
        severe = dict(_MARKET_CALM, systemic_event_risk="SEVERE")
        result = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **severe),
            policy=policy,
            include_portfolio_drawdown=False,
        )
        self.assertEqual(result.regime, "CAPITAL_PRESERVATION")

    def test_drawdown_still_appears_in_reasons_and_confidence(self):
        policy = load_policy()
        result = determine_regime(
            RegimeInputs(portfolio_drawdown_band=-0.14, **_MARKET_CALM),
            policy=policy,
            include_portfolio_drawdown=False,
        )
        self.assertTrue(
            any("emergency overlay owns portfolio-drawdown risk" in r for r in result.reasons)
        )
        # The drawdown domain keeps its confidence authority in both modes.
        self.assertIn("portfolio_drawdown", result.domain_confidence)

    def test_transition_cap_still_applies_without_drawdown(self):
        policy = load_policy()
        inputs = RegimeInputs(portfolio_drawdown_band=0.0, **_MARKET_RISK_OFF)
        first = determine_regime(
            inputs, policy=policy, include_portfolio_drawdown=False
        )
        second = determine_regime(
            inputs, policy=policy, previous=first, include_portfolio_drawdown=False
        )
        self.assertEqual(first.regime, second.regime)


if __name__ == "__main__":
    unittest.main()
