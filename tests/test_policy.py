import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping, policy_hash, resolve_policy


class PolicyTests(unittest.TestCase):
    def test_canonical_policy_loads_and_normalizes(self):
        policy = load_policy()
        self.assertEqual(policy.core_symbols, ("BTC", "ETH"))
        self.assertEqual(policy.excluded_symbols, ("LUNC",))
        self.assertTrue(policy.is_excluded(" lunc "))
        self.assertNotIn("LUNC", policy.core_symbols + policy.satellite_symbols + policy.stable_symbols)
        self.assertIn("U", policy.stable_symbols)
        self.assertIn("USD1", policy.stable_symbols)
        self.assertEqual(policy.classify(" usdc "), "stablecoin")
        self.assertEqual(policy.classify("USD"), "cash")
        self.assertEqual(policy.events["lookback_days"]["FULL_REVIEW"]["security"], 90)
        self.assertEqual(policy.events["coverage"]["high_minimum"], 1.0)
        self.assertEqual(policy.scoring_profile_name("BTC"), "btc")
        self.assertEqual(policy.scoring_profile("BTC")["relative_strength_btc"], 0.0)
        self.assertEqual(policy.allocation["satellite_entry_score"], 67.0)
        self.assertEqual(policy.allocation["satellite_exit_score"], 60.0)

    def test_removed_policy_version_field_is_rejected(self):
        value = load_policy().as_dict()
        value["policy_version"] = 4
        with self.assertRaises(PolicyError):
            policy_from_mapping(value)

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

    def test_chain_liveness_policy_is_canonical_and_strict(self):
        policy = load_policy()
        self.assertEqual(policy.chain_liveness["BTC"]["halted_minimum_independent_sources"], 2)
        self.assertEqual(policy.chain_liveness["ETH"]["healthy_head_age_seconds"], 120.0)
        self.assertEqual(policy.chain_liveness["degraded_deployment_factor"], 0.25)
        invalid = json.loads(json.dumps(policy.as_dict()))
        invalid["chain_liveness"]["BTC"]["halted_minimum_independent_sources"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

    def test_incomplete_resolved_execution_policy_is_rejected(self):
        policy = load_policy()
        incomplete = json.loads(json.dumps(policy.as_dict()))
        for field in (
            "maximum_daily_candle_lag_days", "minimum_daily_coverage_ratio",
            "maximum_daily_gap_days", "maximum_zone_span_atr",
            "maximum_spot_close_gap_atr", "zone_quality",
        ):
            incomplete["execution"].pop(field)
        with self.assertRaises(PolicyError):
            policy_from_mapping(incomplete)

    def test_partial_override_is_explicit_and_uppercase(self):
        policy = resolve_policy({"core_symbols": [" alpha "]})
        self.assertEqual(policy.core_symbols, ("ALPHA",))
        self.assertEqual(policy.classify("alpha"), "core")

        excluded = resolve_policy({"excluded_symbols": ["lunc", " foo "]})
        self.assertEqual(excluded.excluded_symbols, ("LUNC", "FOO"))
        self.assertTrue(excluded.is_excluded("foo"))

    def test_invalid_override_values_fail(self):
        for override in (
            {"min_stablecoin_weight": -0.1},
            {"max_portfolio_drawdown": 0},
            {"min_stablecoin_weight": float("nan")},
            {"unknown": True},
            {"core_symbols": ["BTC"], "satellite_symbols": [" btc "]},
            {"excluded_symbols": ["LUNC", "lunc"]},
            {"excluded_symbols": ["BTC"]},
            {"excluded_symbols": ["U"]},
            {"excluded_symbols": ["AAVE"]},
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
        invalid_scoring["scoring_profiles"]["default"]["trend"] = 0.9
        cases.append(invalid_scoring)
        invalid_unknown = json.loads(json.dumps(original))
        invalid_unknown["unexpected"] = True
        cases.append(invalid_unknown)
        invalid_nested = json.loads(json.dumps(original))
        invalid_nested["risk"]["extra"] = 1
        cases.append(invalid_nested)
        for group in ("core", "satellites", "stable"):
            invalid_overlap = json.loads(json.dumps(original))
            invalid_overlap["universe"]["excluded"] = [invalid_overlap["universe"][group][0]]
            cases.append(invalid_overlap)
        for data in cases:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "policy.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(PolicyError):
                    load_policy(path)

        invalid_events = json.loads(json.dumps(original))
        invalid_events["events"]["lookback_days"]["SNAPSHOT_REVIEW"]["security"] = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(invalid_events), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(path)

        incomplete = json.loads(json.dumps(original))
        incomplete.pop("events")
        with self.assertRaises(PolicyError):
            policy_from_mapping(incomplete)

    def test_scoring_profiles_and_event_multipliers_are_strict(self):
        original = load_policy().as_dict()
        for mutate in (
            lambda data: data["scoring_profiles"]["default"].pop("trend"),
            lambda data: data["asset_scoring_profiles"].update({"ETH": "missing"}),
            lambda data: data["event_risk_multipliers"].update({"HIGH": 0.8}),
            lambda data: data["allocation"].update({"satellite_exit_score": 70}),
        ):
            invalid = json.loads(json.dumps(original))
            mutate(invalid)
            with self.subTest(invalid=invalid):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "policy.json"
                    path.write_text(json.dumps(invalid), encoding="utf-8")
                    with self.assertRaises(PolicyError):
                        load_policy(path)


if __name__ == "__main__":
    unittest.main()
