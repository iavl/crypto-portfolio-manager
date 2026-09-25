"""Multi-scenario stress framework (Strategy V2 Phase 1).

The framework keeps the Strategy V1 single scenario byte-for-byte as
``moderate`` and adds the named severe scenarios as mechanism placeholders
pending Phase 6 walk-forward calibration.
"""

import json
import unittest

from crypto_portfolio.engine.feasibility import check_policy_feasibility
from crypto_portfolio.engine.review_diagnostics import portfolio_stress
from crypto_portfolio.engine.risk import stress_diagnostic
from crypto_portfolio.models.policy import PolicyError, load_policy, policy_from_mapping


_V1_MODERATE = {"BTC": -0.2, "ETH": -0.3, "SOL": -0.4, "AAVE": -0.4, "BNB": -0.35}


class StressScenarioContractTests(unittest.TestCase):
    def test_policy_defines_the_canonical_scenario_set(self):
        policy = load_policy()
        self.assertEqual(
            set(policy.stress_scenarios),
            {
                "moderate",
                "severe_crypto_crash",
                "liquidity_shock",
                "correlation_one",
                "btc_gap_down",
                "eth_alt_crash",
                "stablecoin_depeg",
            },
        )

    def test_moderate_preserves_the_v1_single_scenario_exactly(self):
        self.assertEqual(dict(load_policy().stress_scenarios["moderate"]), _V1_MODERATE)

    def test_every_scenario_covers_core_and_satellites(self):
        policy = load_policy()
        required = set(policy.core_symbols) | set(policy.satellite_symbols)
        for name, scenario in policy.stress_scenarios.items():
            self.assertTrue(
                required <= set(scenario), f"scenario {name} misses required assets"
            )

    def test_correlation_one_is_uniform_across_risky_assets(self):
        scenario = load_policy().stress_scenarios["correlation_one"]
        risky = {s: v for s, v in scenario.items() if s not in load_policy().stable_symbols}
        self.assertEqual(len(set(risky.values())), 1)

    def test_only_the_depeg_scenario_may_stress_stables(self):
        policy = load_policy()
        for name, scenario in policy.stress_scenarios.items():
            stable_entries = {
                symbol: value for symbol, value in scenario.items()
                if symbol in policy.stable_symbols
            }
            if name == "stablecoin_depeg":
                self.assertTrue(
                    any(value < 0 for value in stable_entries.values()),
                    "the depeg scenario must actually stress a stable",
                )
            else:
                self.assertTrue(
                    all(value == 0 for value in stable_entries.values()),
                    f"scenario {name} must not stress stables",
                )

    def test_missing_scenario_asset_is_a_policy_error(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        del data["stress_scenarios"]["moderate"]["AAVE"]
        with self.assertRaisesRegex(PolicyError, "moderate"):
            policy_from_mapping(data)

    def test_unknown_scenario_name_is_a_policy_error(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["stress_scenarios"]["mild"] = dict(data["stress_scenarios"]["moderate"])
        with self.assertRaisesRegex(PolicyError, "canonical scenario set"):
            policy_from_mapping(data)

    def test_non_uniform_correlation_one_is_a_policy_error(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["stress_scenarios"]["correlation_one"]["BTC"] = -0.5
        with self.assertRaisesRegex(PolicyError, "correlation_one"):
            policy_from_mapping(data)

    def test_depeg_values_in_non_depeg_scenario_are_rejected(self):
        data = json.loads(json.dumps(load_policy().as_dict()))
        data["stress_scenarios"]["moderate"]["USDT"] = -0.05
        with self.assertRaisesRegex(PolicyError, "stablecoin_depeg"):
            policy_from_mapping(data)


class StressScenarioConsumerTests(unittest.TestCase):
    def test_feasibility_runs_on_the_moderate_scenario(self):
        report = check_policy_feasibility(load_policy())
        self.assertTrue(len(report.drawdown_rows) > 0)
        self.assertTrue(
            any(row.get("scenario_name") == "POLICY_STRESS" for row in report.drawdown_rows)
        )

    def test_portfolio_stress_uses_moderate_values(self):
        policy = load_policy()
        weights = {"BTC": 0.5, "ETH": 0.5}
        result = portfolio_stress(weights, policy=policy, drawdown=-0.02)
        self.assertEqual(result["availability"], "AVAILABLE")
        self.assertAlmostEqual(
            result["asset_contributions"]["BTC"], 0.5 * _V1_MODERATE["BTC"]
        )
        self.assertAlmostEqual(
            result["asset_contributions"]["ETH"], 0.5 * _V1_MODERATE["ETH"]
        )

    def test_stress_diagnostic_supports_every_configured_scenario(self):
        policy = load_policy()
        weights = {"BTC": 0.4, "ETH": 0.35, "SOL": 0.05, "USD": 0.2}
        for name, scenario in policy.stress_scenarios.items():
            merged = {**scenario, **{s: 0.0 for s in policy.stable_symbols}}
            if name == "stablecoin_depeg":
                merged.update({s: scenario.get(s, 0.0) for s in policy.stable_symbols})
            diagnostic = stress_diagnostic(
                weights, merged, current_drawdown=-0.02,
                risk_budget=policy.max_portfolio_drawdown,
            )
            self.assertIn("scenario_return", diagnostic)
            self.assertTrue(
                diagnostic["scenario_return"] <= 0,
                f"loss scenarios must not produce gains: {name}",
            )

    def test_depeg_scenario_prices_the_stable_sleeve(self):
        policy = load_policy()
        scenario = policy.stress_scenarios["stablecoin_depeg"]
        weights = {"USDT": 0.6, "BTC": 0.4}
        diagnostic = stress_diagnostic(
            weights, {**scenario}, current_drawdown=0.0,
            risk_budget=policy.max_portfolio_drawdown,
        )
        self.assertAlmostEqual(
            diagnostic["asset_contributions"]["USDT"], 0.6 * scenario["USDT"]
        )


if __name__ == "__main__":
    unittest.main()
