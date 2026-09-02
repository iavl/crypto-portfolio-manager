import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping, policy_hash, resolve_policy


class PolicyTests(unittest.TestCase):
    def test_canonical_policy_loads_and_normalizes(self):
        policy = load_policy()
        self.assertEqual(policy.policy_version, 1)
        self.assertEqual(policy.core_symbols, ("BTC", "ETH"))
        self.assertEqual(policy.classify(" usdc "), "stablecoin")
        self.assertEqual(policy.classify("USD"), "cash")

    def test_policy_hash_is_canonical_and_changes_with_policy(self):
        policy = load_policy()
        self.assertEqual(policy.canonical_hash, policy_hash(policy))
        changed = resolve_policy({"min_stablecoin_weight": 0.2})
        self.assertNotEqual(policy.canonical_hash, changed.canonical_hash)

    def test_execution_policy_is_canonical_and_strict(self):
        policy = load_policy()
        self.assertEqual(policy.execution["moving_average_windows"], [20, 50, 100, 200])
        self.assertEqual(policy.execution["minimum_history_days"], 120)
        changed = json.loads(json.dumps(policy.as_dict()))
        changed["execution"]["zone_half_width_atr"] = 0.5
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            self.assertNotEqual(policy.canonical_hash, load_policy(path).canonical_hash)
        invalid = json.loads(json.dumps(policy.as_dict()))
        invalid["execution"]["moving_average_windows"] = [20, 50]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_execution_safety_constraints_are_monotonic(self):
        policy = load_policy()
        self.assertGreaterEqual(
            policy.execution["confidence_deployment_factor"]["HIGH"],
            policy.execution["confidence_deployment_factor"]["MEDIUM"],
        )
        self.assertGreaterEqual(
            policy.execution["confidence_deployment_factor"]["MEDIUM"],
            policy.execution["confidence_deployment_factor"]["LOW"],
        )
        self.assertLessEqual(
            policy.execution["breakout"]["max_initial_tranche"],
            policy.execution["max_initial_tranche"]["NORMAL"],
        )
        invalid = json.loads(json.dumps(policy.as_dict()))
        invalid["execution"]["confidence_deployment_factor"] = {"HIGH": 0.2, "MEDIUM": 0.5, "LOW": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

        invalid = json.loads(json.dumps(policy.as_dict()))
        invalid["execution"]["breakout"]["max_initial_tranche"] = 0.6
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_volume_profile_policy_is_canonical_and_bounded(self):
        policy = load_policy()
        self.assertEqual(policy.volume_profile["preferred_timeframe"], "4H")
        self.assertEqual(policy.volume_profile["lookback_days"], [90, 180])
        invalid = json.loads(json.dumps(policy.as_dict()))
        invalid["volume_profile"]["daily_approximation_confidence_cap"] = "HIGH"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_old_resolved_execution_policy_remains_hash_stable(self):
        policy = load_policy()
        legacy = json.loads(json.dumps(policy.as_dict()))
        for field in (
            "maximum_daily_candle_lag_days", "minimum_daily_coverage_ratio",
            "maximum_daily_gap_days", "maximum_zone_span_atr",
            "maximum_spot_close_gap_atr", "zone_quality",
        ):
            legacy["execution"].pop(field)
        parsed = policy_from_mapping(legacy)
        self.assertEqual(parsed.as_dict(), legacy)
        self.assertEqual(policy_hash(parsed), policy_hash(legacy))

    def test_partial_override_is_explicit_and_uppercase(self):
        policy = resolve_policy({"core_symbols": [" alpha "]})
        self.assertEqual(policy.core_symbols, ("ALPHA",))
        self.assertEqual(policy.classify("alpha"), "core")

    def test_invalid_override_values_fail(self):
        for override in (
            {"min_stablecoin_weight": -0.1},
            {"max_portfolio_drawdown": 0},
            {"min_stablecoin_weight": float("nan")},
            {"unknown": True},
            {"core_symbols": ["BTC"], "satellite_symbols": [" btc "]},
        ):
            with self.subTest(override=override):
                with self.assertRaises(PolicyError):
                    resolve_policy(override)

    def test_invalid_canonical_policy_values_fail(self):
        original = load_policy().as_dict()
        cases = []
        invalid_benchmark = json.loads(json.dumps(original))
        invalid_benchmark["benchmarks"]["primary"]["BTC"] = 0.9
        cases.append(invalid_benchmark)
        invalid_scoring = json.loads(json.dumps(original))
        invalid_scoring["scoring_weights"]["trend"] = 0.9
        cases.append(invalid_scoring)
        invalid_unknown = json.loads(json.dumps(original))
        invalid_unknown["unexpected"] = True
        cases.append(invalid_unknown)
        invalid_nested = json.loads(json.dumps(original))
        invalid_nested["risk"]["extra"] = 1
        cases.append(invalid_nested)
        for data in cases:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    load_policy(path)


if __name__ == "__main__":
    unittest.main()
