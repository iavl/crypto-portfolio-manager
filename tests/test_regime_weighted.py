"""Weighted/severity regime model regressions (phase 8).

The canonical policy classifies regimes with a policy-configured weighted
severity score over the four ordinary domains while the drawdown floors,
severe systemic events, and the one-notch transition cap remain hard
overrides.  The legacy vote-count model stays available behind
``regime_model.mode = "vote_count"`` for A/B regression.
"""

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.models.policy import Policy, PolicyError, load_policy


def _inputs(**kwargs):
    base = dict(
        btc_trend="BULLISH",
        volatility_state="NORMAL",
        portfolio_drawdown_band="NORMAL",
        flow_state="POSITIVE",
        breadth_state="HEALTHY",
        systemic_event_risk=False,
    )
    base.update(kwargs)
    return RegimeInputs(**base)


class WeightedRegimeTests(unittest.TestCase):
    def test_canonical_policy_uses_the_weighted_model(self):
        model = load_policy().regime_model
        self.assertEqual(model["mode"], "weighted")

    def test_score_bands_map_to_regimes(self):
        cases = (
            (_inputs(), "NORMAL"),                                              # 0.0
            (_inputs(btc_trend="BEARISH"), "NORMAL"),                           # 0.30 <= 0.35
            (_inputs(btc_trend="BEARISH", volatility_state="ELEVATED"), "DEFENSIVE"),  # 0.475
            (_inputs(volatility_state="EXTREME", flow_state="NEUTRAL",
                     breadth_state="NEUTRAL"), "DEFENSIVE"),                    # 0.3625
            (_inputs(btc_trend="BEARISH", volatility_state="ELEVATED",
                     flow_state="OUTFLOW", breadth_state="WEAK"), "CAPITAL_PRESERVATION"),  # 0.925
        )
        for evidence, expected in cases:
            with self.subTest(expected=expected, evidence=asdict(evidence)):
                result = determine_regime(evidence)
                self.assertEqual(result.regime, expected)
                self.assertTrue(any("weighted regime risk score" in r for r in result.reasons))

    def test_severity_differentiates_weak_and_strong_volatility(self):
        # Same combination, only the volatility severity differs: EXTREME
        # crosses the defensive threshold where ELEVATED does not.
        elevated = determine_regime(
            _inputs(volatility_state="ELEVATED", flow_state="NEUTRAL", breadth_state="NEUTRAL")
        )
        extreme = determine_regime(
            _inputs(volatility_state="EXTREME", flow_state="NEUTRAL", breadth_state="NEUTRAL")
        )
        self.assertEqual(elevated.regime, "NORMAL")
        self.assertEqual(extreme.regime, "DEFENSIVE")

    def test_drawdown_floor_overrides_the_weighted_score(self):
        low_score_but_deep_drawdown = _inputs(portfolio_drawdown_band=-0.10)
        self.assertEqual(determine_regime(low_score_but_deep_drawdown).regime, "DEFENSIVE")
        breach = _inputs(portfolio_drawdown_band=-0.13)
        self.assertEqual(determine_regime(breach).regime, "CAPITAL_PRESERVATION")

    def test_severe_systemic_event_still_forces_capital_preservation(self):
        result = determine_regime(_inputs(systemic_event_risk=True))
        self.assertEqual(result.regime, "CAPITAL_PRESERVATION")

    def test_one_notch_transition_cap_applies_to_the_weighted_model(self):
        evidence = _inputs(
            btc_trend="BEARISH", volatility_state="ELEVATED",
            flow_state="OUTFLOW", breadth_state="WEAK",
        )
        self.assertEqual(determine_regime(evidence).regime, "CAPITAL_PRESERVATION")
        capped = determine_regime(evidence, previous="NORMAL")
        self.assertEqual(capped.regime, "DEFENSIVE")
        self.assertTrue(any("awaits confirmation" in reason for reason in capped.reasons))

    def test_unknown_domains_add_no_severity(self):
        result = determine_regime(
            _inputs(btc_trend="UNKNOWN", volatility_state="UNKNOWN",
                    flow_state="UNKNOWN", breadth_state="UNKNOWN")
        )
        self.assertEqual(result.regime, "NORMAL")
        self.assertTrue(any("unknown domains add no severity" in r for r in result.reasons))

    def test_vote_count_mode_remains_available(self):
        policy = load_policy()
        legacy = Policy(**{**policy.__dict__, "regime_model": {
            **policy.regime_model, "mode": "vote_count",
        }})
        evidence = _inputs(
            btc_trend="BEARISH", volatility_state="ELEVATED",
            flow_state="OUTFLOW", breadth_state="HEALTHY",
        )
        # Three vote signals without a prior regime -> immediate CP.
        self.assertEqual(determine_regime(evidence, policy=legacy).regime, "CAPITAL_PRESERVATION")
        # Two strong signals classify DEFENSIVE under both models
        # (weighted score 0.30 + 0.85 x 0.25 = 0.5125).
        two = _inputs(btc_trend="BEARISH", volatility_state="HIGH")
        self.assertEqual(determine_regime(two, policy=legacy).regime, "DEFENSIVE")
        self.assertEqual(determine_regime(two).regime, "DEFENSIVE")
        # A single moderate signal stays NORMAL under both models.
        single = _inputs(btc_trend="BEARISH")
        self.assertEqual(determine_regime(single, policy=legacy).regime, "NORMAL")
        self.assertEqual(determine_regime(single).regime, "NORMAL")

    def test_market_flow_state_is_independent_from_asset_capital_flows(self):
        # The regime flow domain reads the portfolio-wide flow state only;
        # a negative BTC-specific capital-flows factor is asset evidence and
        # must not label the whole portfolio flow negative.
        base = _inputs(flow_state="POSITIVE")
        first = determine_regime(base)
        from crypto_portfolio.engine.allocation import build_target_allocation

        # Feeding asset-level assessments with a negative BTC flow factor
        # cannot change the regime classification of the same inputs.
        build_target_allocation(
            regime=first.regime,
            assessments={
                "BTC": {"weighted_score": 50, "confidence": "HIGH", "capital_flows": 10},
                "ETH": {"weighted_score": 60, "confidence": "HIGH", "relative_strength_vs_btc": 60},
            },
            current_weights={"BTC": 0.5, "ETH": 0.3, "USDT": 0.2},
        )
        again = determine_regime(base)
        self.assertEqual(first.regime, again.regime, "asset evidence must not leak into regime")


class RegimeModelPolicyTests(unittest.TestCase):
    def _mutate(self, mutate):
        original = load_policy().as_dict()
        mutate(original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            return path

    def test_invalid_regime_models_are_rejected(self):
        for mutate in (
            lambda data: data["regime_model"].update({"mode": "fuzzy"}),
            lambda data: data["regime_model"].update({"normal_max": 0.7}),
            lambda data: data["regime_model"]["domain_weights"].update({"trend": 0.9}),
            lambda data: data["regime_model"]["severity"]["volatility"].pop("HIGH"),
            lambda data: data["regime_model"]["severity"].update({"trend": {"BULLISH": 0.0}}),
            lambda data: data["regime_model"].pop("severity"),
        ):
            with self.subTest(mutate=mutate):
                with self.assertRaises(PolicyError):
                    load_policy(self._mutate(mutate))


if __name__ == "__main__":
    unittest.main()
