"""Optional two-stage factor-family scoring regressions (phase 9).

The canonical policy keeps the flat compatibility path
(``scoring_families: {}``); a configured tree derives flat factor weights as
``sum(family_weight * in_family_weight)`` so the linear aggregation is
exactly equivalent to flat scoring with those weights, while attribution
exposes both family and factor contributions.  The AAVE profile is not
touched.
"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.models.policy import PolicyError, load_policy

FAMILY_TREE = {
    "default": {
        "price_momentum": {"weight": 0.35, "factors": {"trend": 0.7, "relative_strength_btc": 0.3}},
        "fundamental": {"weight": 0.25, "factors": {"fundamentals": 1.0}},
        "valuation": {"weight": 0.15, "factors": {"valuation": 1.0}},
        "capital": {"weight": 0.15, "factors": {"capital_flows": 1.0}},
        "network": {"weight": 0.10, "factors": {"onchain": 1.0}},
    }
}

ALL_FACTORS = {
    "trend": 80, "valuation": 60, "fundamentals": 70,
    "onchain": 55, "capital_flows": 65, "relative_strength_btc": 75,
}


def _policy_with_families(tree):
    base = load_policy()
    return replace(base, scoring_families=tree)


class FactorFamilyScoringTests(unittest.TestCase):
    def test_canonical_policy_stays_on_the_flat_path(self):
        result = score_factors(ALL_FACTORS, symbol="SOL")
        self.assertEqual(result.profile_name, "default")
        self.assertEqual(dict(result.family_weights or {}), {})
        self.assertEqual(dict(result.family_contributions or {}), {})

    def test_family_scoring_equals_flat_scoring_with_derived_weights(self):
        policy = _policy_with_families(FAMILY_TREE)
        derived = {}
        for family, spec in FAMILY_TREE["default"].items():
            for factor, factor_weight in spec["factors"].items():
                derived[factor] = derived.get(factor, 0.0) + spec["weight"] * factor_weight
        family_result = score_factors(ALL_FACTORS, policy=policy, symbol="SOL")
        flat_result = score_factors(ALL_FACTORS, derived, policy=policy, symbol="SOL")
        self.assertAlmostEqual(family_result.score, flat_result.score, places=9)
        self.assertAlmostEqual(family_result.coverage, flat_result.coverage, places=9)
        self.assertEqual(dict(family_result.effective_weights), dict(flat_result.effective_weights))

    def test_family_attribution_sums_to_the_total_score(self):
        policy = _policy_with_families(FAMILY_TREE)
        result = score_factors(ALL_FACTORS, policy=policy, symbol="SOL")
        self.assertAlmostEqual(sum(result.family_contributions.values()), result.score, places=9)
        self.assertAlmostEqual(sum(result.family_weights.values()), 1.0)
        momentum = result.family_contributions["price_momentum"]
        expected = 0.35 * (0.7 * 80 + 0.3 * 75)
        self.assertAlmostEqual(momentum, expected, places=9)

    def test_missing_factor_shrinks_inside_its_family_deterministically(self):
        policy = _policy_with_families(FAMILY_TREE)
        factors = dict(ALL_FACTORS)
        factors["trend"] = None
        result = score_factors(factors, policy=policy, symbol="SOL")
        self.assertIn("trend", result.missing_factors)
        # trend is neutral-50 at full family weight; the momentum family
        # absorbs the shrink instead of the whole profile renormalizing.
        expected_momentum = 0.35 * (0.7 * 50.0 + 0.3 * 75)
        self.assertAlmostEqual(result.family_contributions["price_momentum"], expected_momentum, places=9)
        self.assertLess(result.score, score_factors(ALL_FACTORS, policy=policy, symbol="SOL").score)

    def test_correlated_family_weight_caps_momentum_exposure(self):
        # The design intent: trend and relative strength together are capped
        # at the price_momentum family budget even when both score 100.
        policy = _policy_with_families(FAMILY_TREE)
        hot = {factor: 100 for factor in ALL_FACTORS}
        cold = dict(hot)
        cold["trend"] = 0
        cold["relative_strength_btc"] = 0
        result_hot = score_factors(hot, policy=policy, symbol="SOL")
        result_cold = score_factors(cold, policy=policy, symbol="SOL")
        # Zeroing the whole momentum family costs exactly its 35-point
        # family budget; no other family's exposure can compensate it.
        self.assertAlmostEqual(result_hot.score - result_cold.score, 35.0, places=9)


class FactorFamilyPolicyTests(unittest.TestCase):
    def _load_with(self, mutate):
        original = load_policy().as_dict()
        mutate(original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            return load_policy(path)

    def test_invalid_family_trees_are_rejected(self):
        for mutate in (
            lambda data: data["scoring_families"].update({"missing_profile": FAMILY_TREE["default"]}),
            lambda data: data["scoring_families"].update({"default": {
                "only": {"weight": 0.9, "factors": {"trend": 1.0}},
            }}),
            lambda data: data["scoring_families"].update({"default": {
                "a": {"weight": 0.5, "factors": {"trend": 1.0}},
                "b": {"weight": 0.5, "factors": {"trend": 1.0}},
            }}),
            lambda data: data["scoring_families"].update({"default": {
                "a": {"weight": 0.5, "factors": {"trend": 0.6, "valuation": 0.6}},
            }}),
            lambda data: data["scoring_families"].update({"default": {
                "a": {"weight": 0.5, "factors": {"trend": 1.0}},
                "b": {"weight": 0.4, "factors": {"valuation": 1.0}},
            }}),
            lambda data: data["scoring_families"].update({"default": {
                "a": {"weight": 0.5, "factors": {"not_a_factor": 1.0}},
                "b": {"weight": 0.5, "factors": {"trend": 1.0}},
            }}),
        ):
            with self.subTest(mutate=mutate):
                with self.assertRaises(PolicyError):
                    self._load_with(mutate)

    def test_tree_must_cover_the_profile_factors_exactly(self):
        # The default profile has six positive-weight factors; a tree that
        # silently drops one is rejected instead of renormalizing.
        incomplete = {
            "default": {
                "price_momentum": {"weight": 0.5, "factors": {"trend": 1.0}},
                "rest": {"weight": 0.5, "factors": {
                    "valuation": 0.25, "fundamentals": 0.25, "onchain": 0.25, "capital_flows": 0.25,
                }},
            }
        }
        with self.assertRaisesRegex(PolicyError, "exactly the positive-weight factors"):
            self._load_with(lambda data: data["scoring_families"].update(incomplete))


if __name__ == "__main__":
    unittest.main()
