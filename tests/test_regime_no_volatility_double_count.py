"""Volatility is not risk-penalized twice (Strategy V2.3 Phase 2).

The regime variants declare domain ownership without re-tuning thresholds:
within R1+ a volatility-state change alone never moves the regime label,
realized volatility is owned solely by the volatility budget, and systemic
hard risk is never lost.
"""

import unittest

from crypto_portfolio.engine.portfolio_risk import volatility_budget_scale
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime, market_only_regime
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.regime_variants import (
    REGIME_VARIANTS,
    cagr_sacrificed_per_maxdd_pp_saved,
    regime_variant_ownership,
    regime_variant_policy,
)


def _variant_policy(variant: str):
    return policy_from_mapping(regime_variant_policy(load_policy(), variant))


# Neutral ordinary domains isolate the volatility state's own authority:
# in weighted mode EXTREME alone (1.0 x 0.30) crosses normal_max (0.35) only
# once the neutral severities of the other domains are included.
LOW_VOL = dict(
    btc_trend="NEUTRAL", volatility_state="LOW", flow_state="NEUTRAL",
    breadth_state="NEUTRAL", portfolio_drawdown_band=0.0,
)
EXTREME_VOL = dict(
    btc_trend="NEUTRAL", volatility_state="EXTREME", flow_state="NEUTRAL",
    breadth_state="NEUTRAL", portfolio_drawdown_band=0.0,
)


class NoDoubleCountTests(unittest.TestCase):
    def test_r0_keeps_volatility_authority(self):
        policy = _variant_policy("R0")
        # Neutral domains contribute 0.175; EXTREME volatility adds 0.30 and
        # crosses the defensive threshold (0.65) is not needed - 0.475 sits
        # between normal_max and defensive_max.
        self.assertEqual(
            determine_regime(RegimeInputs(**LOW_VOL), policy=policy).regime, "NORMAL",
        )
        self.assertEqual(
            determine_regime(RegimeInputs(**EXTREME_VOL), policy=policy).regime,
            "DEFENSIVE",
        )

    def test_volatility_state_alone_never_moves_the_label_in_r1_r2_r3(self):
        for variant in ("R1", "R2", "R3"):
            policy = _variant_policy(variant)
            with self.subTest(variant=variant):
                low = determine_regime(RegimeInputs(**LOW_VOL), policy=policy).regime
                extreme = determine_regime(RegimeInputs(**EXTREME_VOL), policy=policy).regime
                self.assertEqual(low, extreme)
                self.assertEqual(low, "NORMAL")
                self.assertTrue(any(
                    "excluded" in reason or "ownership" in reason
                    for reason in determine_regime(
                        RegimeInputs(**EXTREME_VOL), policy=policy
                    ).reasons
                ))

    def test_market_only_regime_honors_the_same_ownership(self):
        for variant in ("R1", "R2", "R3"):
            policy = _variant_policy(variant)
            with self.subTest(variant=variant):
                self.assertEqual(
                    market_only_regime(RegimeInputs(**EXTREME_VOL), policy=policy),
                    "NORMAL",
                )

    def test_volatility_budget_still_owns_realized_volatility(self):
        # The single remaining penalty: the budget scale reacts to realized
        # vol exactly once, and an all-cash book consumes none of it.
        self.assertEqual(volatility_budget_scale(0.0, target_volatility=0.25, max_volatility=0.35)["risk_scaling_factor"], 1.0)
        hot = volatility_budget_scale(0.50, target_volatility=0.25, max_volatility=0.35)
        self.assertAlmostEqual(hot["risk_scaling_factor"], 0.5)
        self.assertTrue(hot["max_volatility_exceeded"])

    def test_systemic_hard_risk_is_never_lost(self):
        for variant in ("R0", "R1", "R2", "R3"):
            policy = _variant_policy(variant)
            with self.subTest(variant=variant):
                severe = dict(EXTREME_VOL, systemic_event_risk="SEVERE")
                self.assertEqual(
                    determine_regime(RegimeInputs(**severe), policy=policy).regime,
                    "CAPITAL_PRESERVATION",
                )

    def test_remaining_domains_still_move_the_label(self):
        policy = _variant_policy("R1")
        # Weighted mode: flows(1.0)x0.30 + breadth(1.0)x0.25 = 0.55, which
        # sits between normal_max (0.35) and defensive_max (0.65).
        risk_off_flows = dict(EXTREME_VOL, flow_state="NEGATIVE", breadth_state="WEAK")
        self.assertEqual(
            determine_regime(RegimeInputs(**risk_off_flows), policy=policy).regime,
            "DEFENSIVE",
        )
        all_risk_off = dict(
            EXTREME_VOL, flow_state="NEGATIVE", breadth_state="WEAK",
            btc_trend="BEARISH",
        )
        # BEARISH trend (1.0x0.15) pushes the score to 0.70 > defensive_max.
        self.assertEqual(
            determine_regime(RegimeInputs(**all_risk_off), policy=policy).regime,
            "CAPITAL_PRESERVATION",
        )

    def test_r3_ordinary_conditions_never_leave_normal(self):
        policy = _variant_policy("R3")
        bearish_all = dict(
            btc_trend="BEARISH", volatility_state="EXTREME",
            flow_state="NEGATIVE", breadth_state="WEAK",
            portfolio_drawdown_band=0.0,
        )
        # (weighted mode: every ordinary domain excluded -> score 0.)
        # with every ordinary domain excluded the label is NORMAL unless a
        # severe systemic event fires.
        result = determine_regime(
            RegimeInputs(**bearish_all), policy=policy,
            include_portfolio_drawdown=False,
        )
        self.assertEqual(result.regime, "NORMAL")

    def test_weights_and_thresholds_are_identical_across_variants(self):
        base = _variant_policy("R0").regime_model
        for variant in ("R1", "R2", "R3"):
            model = _variant_policy(variant).regime_model
            with self.subTest(variant=variant):
                self.assertEqual(model["domain_weights"], base["domain_weights"])
                self.assertEqual(model["normal_max"], base["normal_max"])
                self.assertEqual(model["defensive_max"], base["defensive_max"])
                self.assertEqual(model["severity"], base["severity"])


class OwnershipReportTests(unittest.TestCase):
    def test_ownership_table_covers_the_preregistered_variants(self):
        report = regime_variant_ownership(load_policy())
        self.assertEqual(sorted(report["variants"]), ["R0", "R1", "R2", "R3"])
        self.assertTrue(report["variants"]["R0"]["regime_volatility_authority"])
        for variant in ("R1", "R2", "R3"):
            self.assertFalse(
                report["variants"][variant]["regime_volatility_authority"]
            )
            self.assertEqual(
                report["variants"][variant]["volatility_owners"],
                ["volatility_budget"],
            )

    def test_variant_table_is_preregistered(self):
        self.assertEqual(REGIME_VARIANTS, {
            "R0": (),
            "R1": ("volatility",),
            "R2": ("trend", "volatility"),
            "R3": ("trend", "volatility", "flows", "breadth"),
        })


class EfficiencyMetricTests(unittest.TestCase):
    def test_prices_cagr_sacrificed_per_maxdd_pp_saved(self):
        baseline = {"cagr": 0.20, "maximum_drawdown": -0.28}
        variant = {"cagr": 0.15, "maximum_drawdown": -0.23}
        # 5pp CAGR sacrificed for 5pp MaxDD saved -> 1.0
        self.assertAlmostEqual(
            cagr_sacrificed_per_maxdd_pp_saved(baseline, variant), 1.0,
        )

    def test_no_drawdown_saved_returns_none(self):
        baseline = {"cagr": 0.20, "maximum_drawdown": -0.20}
        deeper = {"cagr": 0.18, "maximum_drawdown": -0.25}
        self.assertIsNone(
            cagr_sacrificed_per_maxdd_pp_saved(baseline, deeper),
        )

    def test_missing_metrics_return_none(self):
        self.assertIsNone(
            cagr_sacrificed_per_maxdd_pp_saved(
                {"cagr": 0.2}, {"cagr": 0.1, "maximum_drawdown": -0.1},
            ),
        )


class PolicyContractTests(unittest.TestCase):
    def test_canonical_policy_is_r0(self):
        self.assertEqual(load_policy().regime_model["excluded_domains"], ())

    def test_unknown_domains_are_rejected(self):
        from crypto_portfolio.models.policy import PolicyError
        raw = load_policy().as_dict()
        raw["regime_model"]["excluded_domains"] = ["portfolio_drawdown"]
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)

    def test_duplicate_exclusions_are_rejected(self):
        from crypto_portfolio.models.policy import PolicyError
        raw = load_policy().as_dict()
        raw["regime_model"]["excluded_domains"] = ["volatility", "volatility"]
        with self.assertRaises(PolicyError):
            policy_from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
