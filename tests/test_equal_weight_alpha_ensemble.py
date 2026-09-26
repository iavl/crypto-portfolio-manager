"""Equal-weight admitted-signal ensembles (Strategy V2.3 Phase 5).

Every asset ensemble is the mean of its ADMITTED signals mapped to
-1 / 0 / +1; weights are equal by preregistration, MISSING votes neutral,
and the preregistered half-strength threshold decides the discrete state.
Only the POSITIVE state can ever request a production tilt.
"""

import unittest

from crypto_portfolio.engine.aave_relative_alpha import aave_alpha_state
from crypto_portfolio.engine.bnb_relative_alpha import bnb_alpha_state
from crypto_portfolio.engine.eth_relative_alpha import eth_alpha_state
from crypto_portfolio.engine.relative_alpha_core import (
    ENSEMBLE_THRESHOLD,
    asset_alpha_state_names,
    ensemble_alpha_state,
)


class EnsembleMathTests(unittest.TestCase):
    def test_votes_are_signs_and_missing_votes_neutral(self):
        state = ensemble_alpha_state(
            "BNB",
            {"a": 0.4, "b": None, "c": -0.2},
            ("a", "b", "c"),
        )
        # votes +1, 0, -1 -> mean 0 -> NEUTRAL (missing pulls to neutral).
        self.assertEqual(state, "BNB_ALPHA_NEUTRAL")

    def test_half_strength_threshold_is_preregistered(self):
        self.assertEqual(ENSEMBLE_THRESHOLD, 0.5)
        two_of_three = ensemble_alpha_state(
            "BNB", {"a": 1, "b": 1, "c": 0}, ("a", "b", "c"),
        )
        self.assertEqual(two_of_three, "BNB_ALPHA_POSITIVE")
        one_of_three = ensemble_alpha_state(
            "BNB", {"a": 1, "b": 0, "c": 0}, ("a", "b", "c"),
        )
        self.assertEqual(one_of_three, "BNB_ALPHA_NEUTRAL")
        negative = ensemble_alpha_state(
            "AAVE", {"a": -1, "b": -1, "c": -0.5}, ("a", "b", "c"),
        )
        self.assertEqual(negative, "AAVE_ALPHA_NEGATIVE")

    def test_empty_admitted_set_is_neutral_by_definition(self):
        self.assertEqual(
            ensemble_alpha_state("BNB", {"a": 1.0}, ()), "BNB_ALPHA_NEUTRAL",
        )

    def test_unadmitted_signals_never_count(self):
        with_all = ensemble_alpha_state(
            "BNB", {"a": 1.0, "b": 1.0, "c": -1.0}, ("a", "b", "c"),
        )
        without_c = ensemble_alpha_state("BNB", {"a": 1.0, "b": 1.0, "c": -1.0}, ("a", "b"))
        self.assertEqual(with_all, "BNB_ALPHA_NEUTRAL")
        self.assertEqual(without_c, "BNB_ALPHA_POSITIVE")

    def test_non_finite_values_are_rejected(self):
        with self.assertRaises(ValueError):
            ensemble_alpha_state("BNB", {"a": float("nan")}, ("a",))

    def test_state_names_are_canonical_per_asset(self):
        self.assertEqual(
            asset_alpha_state_names("BNB"),
            ("BNB_ALPHA_POSITIVE", "BNB_ALPHA_NEUTRAL", "BNB_ALPHA_NEGATIVE"),
        )


class PerAssetEnsemblesTests(unittest.TestCase):
    def test_bnb_ensemble_uses_only_admitted_bnb_signals(self):
        signals = {
            "rel_return_30d": 0.3, "rel_return_90d": 0.2, "rel_return_180d": 0.1,
            "chain_tvl_growth_90d": 0.4,
        }
        self.assertEqual(
            bnb_alpha_state(signals, ("rel_return_30d", "rel_return_90d", "rel_return_180d")),
            "BNB_ALPHA_POSITIVE",
        )
        flat = {**signals, "rel_return_180d": 0.0}
        self.assertEqual(
            bnb_alpha_state(flat, ("rel_return_180d",)), "BNB_ALPHA_NEUTRAL",
        )

    def test_aave_ensemble_minority_stays_neutral(self):
        signals = {
            "protocol_tvl_growth_90d": 0.5,
            "borrow_growth_90d": -0.2,
            "fees_growth_30d": -0.1,
        }
        admitted = ("protocol_tvl_growth_90d", "borrow_growth_90d", "fees_growth_30d")
        self.assertEqual(aave_alpha_state(signals, admitted), "AAVE_ALPHA_NEUTRAL")

    def test_eth_keeps_its_preregistered_dual_condition_rule(self):
        # ETH (V2.2) is already admission-gated: its discrete rule requires
        # the trend composite AND MA structure to agree; missing inputs are
        # NEUTRAL — the same fail-closed family as the ensembles.
        self.assertEqual(
            eth_alpha_state({"rel_return_30d": 0.05, "rel_return_90d": 0.04,
                             "rel_return_180d": 0.03, "ma_structure": 1.0}),
            "ETH_ALPHA_POSITIVE",
        )
        self.assertEqual(
            eth_alpha_state({"rel_return_30d": 0.05, "rel_return_90d": 0.04,
                             "rel_return_180d": 0.03, "ma_structure": None}),
            "ETH_ALPHA_NEUTRAL",
        )


if __name__ == "__main__":
    unittest.main()
