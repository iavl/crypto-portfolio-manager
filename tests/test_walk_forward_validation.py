"""Walk-forward validation tooling (Strategy V2 Phase 6)."""

import unittest

from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.validation import (
    PARAMETER_CLASSIFICATION,
    ablation_policy,
    block_bootstrap,
    dynamic_universe_eligibility,
    gap_risk_stress,
    stablecoin_stress,
    threshold_rank_monotonicity,
    walk_forward_windows,
)


class WalkForwardWindowTests(unittest.TestCase):
    def test_windows_roll_without_validation_overlap(self):
        windows = walk_forward_windows(
            start_at="2022-01-01T00:00:00Z", end_at="2026-09-22T00:00:00Z",
        )
        self.assertEqual(len(windows), 2)
        for window in windows:
            self.assertLess(window["train_end"], window["validate_start"])
        # The calendar rolls: the next training window starts where the
        # previous validation window began.
        self.assertEqual(windows[1]["train_start"], windows[0]["validate_start"])

    def test_short_window_is_an_error(self):
        with self.assertRaises(ValueError):
            walk_forward_windows(start_at="2024-01-01T00:00:00Z", end_at="2024-06-01T00:00:00Z")


class DynamicUniverseTests(unittest.TestCase):
    def test_short_history_assets_are_ineligible(self):
        result = dynamic_universe_eligibility(
            closes_by_symbol={"BTC": [100.0] * 400, "SOL": [10.0] * 100},
            minimum_history_days=365,
        )
        self.assertEqual(result["eligible"], ["BTC"])
        self.assertIn("INSUFFICIENT_HISTORY", result["ineligible"]["SOL"])

    def test_liquidity_floor_excludes_thin_assets(self):
        closes = {"BTC": [100.0] * 400, "AAVE": [50.0] * 400}
        volumes = {"BTC": [20000.0] * 400, "AAVE": [1.0] * 400}
        result = dynamic_universe_eligibility(
            closes_by_symbol=closes, volumes_by_symbol=volumes,
            minimum_history_days=365, minimum_median_volume_usd=1_000_000.0,
        )
        self.assertEqual(result["eligible"], ["BTC"])
        self.assertIn("INSUFFICIENT_LIQUIDITY", result["ineligible"]["AAVE"])

    def test_eligibility_is_deterministic(self):
        closes = {"BTC": [100.0] * 400}
        self.assertEqual(
            dynamic_universe_eligibility(closes_by_symbol=closes),
            dynamic_universe_eligibility(closes_by_symbol=closes),
        )


class ThresholdMonotonicityTests(unittest.TestCase):
    def test_monotone_scores_score_high(self):
        rows = [
            {"normalized_score": 10 + bucket * 20 + (i % 10), "forward_return": bucket * 0.01}
            for bucket in range(5) for i in range(20)
        ]
        result = threshold_rank_monotonicity(rows)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertAlmostEqual(result["monotone_adjacent_share"], 1.0)

    def test_inverse_scores_are_flagged(self):
        rows = [
            {"normalized_score": 10 + bucket * 20 + (i % 10), "forward_return": -bucket * 0.01}
            for bucket in range(5) for i in range(20)
        ]
        result = threshold_rank_monotonicity(rows)
        self.assertAlmostEqual(result["monotone_adjacent_share"], 0.0)

    def test_empty_buckets_do_not_count_as_violations(self):
        rows = [{"normalized_score": 30.0 + (i % 5), "forward_return": 0.01} for i in range(30)]
        result = threshold_rank_monotonicity(rows)
        self.assertGreater(result["empty_bucket_means"], 0)
        self.assertEqual(result["comparable_bucket_means"], 1)
        self.assertIsNone(result["monotone_adjacent_share"])

    def test_insufficient_samples_is_explicit(self):
        self.assertEqual(
            threshold_rank_monotonicity([{"normalized_score": 50, "forward_return": 0.0}])["status"],
            "INSUFFICIENT_SAMPLES",
        )


class BlockBootstrapTests(unittest.TestCase):
    def _drift_returns(self, count=400, drift=0.001):
        # Period 2 against 21-day blocks: successive blocks start on
        # alternating phases, so block resampling actually varies the path.
        return [drift if i % 2 else -0.002 for i in range(count)]

    def test_bootstrap_is_seed_reproducible(self):
        returns = self._drift_returns()
        first = block_bootstrap(returns, draws=50, seed=7)
        second = block_bootstrap(returns, draws=50, seed=7)
        self.assertEqual(first, second)
        other = block_bootstrap(returns, draws=50, seed=8)
        self.assertNotEqual(first["fingerprint"], other["fingerprint"])

    def test_distributions_and_breach_frequency(self):
        returns = self._drift_returns()
        result = block_bootstrap(returns, draws=60, seed=11, risk_budget=0.15)
        self.assertLessEqual(result["max_drawdown"]["p50"], 0.0)
        self.assertGreaterEqual(result["cagr"]["min"], result["cagr"]["p05"] - 1e-12)
        self.assertIsNotNone(result["breach_frequency"])
        self.assertLessEqual(result["breach_frequency"], 1.0)

    def test_volatile_paths_breach_more_often_than_calm_ones(self):
        calm = block_bootstrap(self._drift_returns(drift=0.003), draws=80, seed=3, risk_budget=0.15)
        wild_returns = [0.05 if i % 2 else -0.05 for i in range(400)]
        wild = block_bootstrap(wild_returns, draws=80, seed=3, risk_budget=0.15)
        self.assertGreater(wild["breach_frequency"], calm["breach_frequency"])


class GapStressTests(unittest.TestCase):
    def test_gap_at_maximum_exposure(self):
        valuations = [
            {"nav": 1.0, "risky_weight": 0.5},
            {"nav": 1.2, "risky_weight": 0.85},
            {"nav": 1.1, "risky_weight": 0.4},
        ]
        result = gap_risk_stress(valuations=valuations, risk_budget=0.15, gaps=(-0.30,))
        row = result["rows"][0]
        self.assertAlmostEqual(row["pre_shock_risky_exposure"], 0.85)
        self.assertAlmostEqual(row["portfolio_shock_return"], -0.255)
        self.assertTrue(row["budget_breach"])

    def test_gap_magnitude_monotonicity(self):
        valuations = [{"nav": 1.0, "risky_weight": 1.0}]
        result = gap_risk_stress(valuations=valuations, risk_budget=0.15)
        losses = [row["portfolio_shock_return"] for row in result["rows"]]
        self.assertEqual(losses, sorted(losses, reverse=True))


class StablecoinStressTests(unittest.TestCase):
    def test_issuer_concentration_and_scenarios(self):
        result = stablecoin_stress(
            {"USDT": 0.30, "USDC": 0.10, "BTC": 0.40, "ETH": 0.20},
            policy=load_policy(),
        )
        self.assertAlmostEqual(result["sleeve_weight"], 0.40)
        self.assertAlmostEqual(result["issuer_concentration"]["USDT"], 0.75)
        self.assertEqual(result["largest_issuer"]["symbol"], "USDT")
        scenarios = result["scenarios"]
        self.assertAlmostEqual(
            scenarios["configured_depeg"]["portfolio_return"],
            0.30 * -0.05 + 0.10 * -0.05,
        )
        self.assertAlmostEqual(
            scenarios["single_issuer_minus_20pct"]["portfolio_return"], 0.30 * -0.20,
        )
        self.assertAlmostEqual(
            scenarios["custody_loss_on_sleeve"]["portfolio_return"], -0.40,
        )

    def test_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            stablecoin_stress({"USDT": 0.5}, policy=load_policy())


class AblationPolicyTests(unittest.TestCase):
    def test_factor_ablation_renormalizes_and_stamps(self):
        variant, manifest = ablation_policy(load_policy(), disable_factors=("fundamentals",))
        policy = policy_from_mapping(variant)
        for profile in policy.scoring_profiles.values():
            # Zero weight is the contract's NOT_APPLICABLE ablation: the
            # factor key stays, its weight is exactly zero, and the profile
            # still sums to 1.
            self.assertAlmostEqual(profile["fundamentals"], 0.0)
            self.assertAlmostEqual(sum(profile.values()), 1.0)
        self.assertEqual(manifest["disable_factors"], ["fundamentals"])

    def test_emptying_a_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            ablation_policy(
                load_policy(), disable_factors=tuple(load_policy().scoring_profiles["btc"]),
            )

    def test_overlay_and_satellite_switches(self):
        variant, _manifest = ablation_policy(
            load_policy(), disable_satellites=True, disable_emergency_overlay=True,
            disable_execution_overlay=True,
        )
        policy = policy_from_mapping(variant)
        self.assertEqual(policy.satellite_symbols, ())
        self.assertFalse(policy.drawdown_budget_overlay["enabled"])
        self.assertFalse(policy.execution_overlay["wait"]["enabled"])
        self.assertFalse(policy.positioning["enabled"])

    def test_parameter_classification_registry_shape(self):
        self.assertIn("structural", PARAMETER_CLASSIFICATION)
        self.assertIn("calibrated", PARAMETER_CLASSIFICATION)
        self.assertIn("forbidden", PARAMETER_CLASSIFICATION)
        self.assertIn("rule", PARAMETER_CLASSIFICATION["forbidden"])


if __name__ == "__main__":
    unittest.main()
