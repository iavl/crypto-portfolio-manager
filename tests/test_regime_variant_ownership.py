"""Regime variant ownership construction (Strategy V2.3 Phase 2)."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.regime_variants import (
    regime_variant_policy,
    run_regime_variant_comparison,
)


class VariantConstructionTests(unittest.TestCase):
    def test_each_variant_policy_parses_and_declares_its_domains(self):
        for variant, excluded in (
            ("R0", []), ("R1", ["volatility"]),
            ("R2", ["trend", "volatility"]),
            ("R3", ["trend", "volatility", "flows", "breadth"]),
        ):
            mapping = regime_variant_policy(load_policy(), variant)
            with self.subTest(variant=variant):
                policy = policy_from_mapping(mapping)
                self.assertEqual(
                    sorted(policy.regime_model["excluded_domains"]),
                    sorted(excluded),
                )
                # Only the ownership declaration changes.
                canonical = load_policy()
                self.assertEqual(
                    policy.regime_model["domain_weights"],
                    canonical.regime_model["domain_weights"],
                )
                self.assertEqual(policy.risk_engine, canonical.risk_engine)
                self.assertEqual(policy.allocation, canonical.allocation)

    def test_unknown_variant_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "R0"):
            regime_variant_policy(load_policy(), "R4")

    def test_round_trip_through_the_policy_mapping(self):
        mapping = regime_variant_policy(load_policy(), "R2")
        policy = policy_from_mapping(mapping)
        self.assertEqual(
            policy_from_mapping(json.loads(json.dumps(policy.as_dict())))
            .regime_model["excluded_domains"],
            ("trend", "volatility"),
        )


class ComparisonRunnerTests(unittest.TestCase):
    def test_runner_executes_every_variant_with_the_efficiency_metric(self):
        from crypto_portfolio.engine.strategy_replay import ReplayReview

        policy = load_policy()
        policy = policy_from_mapping({
            **policy.as_dict(), "risk_engine": {
                **policy.as_dict()["risk_engine"], "mode": "volatility_budget",
            },
        })
        reviews = []
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for index in range(40):
            moment = start + timedelta(days=index)
            reviews.append(ReplayReview(
                as_of=moment.isoformat().replace("+00:00", "Z"),
                period_end=(moment + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                current_weights={"BTC": 0.4, "USDT": 0.6},
                portfolio_value=1000.0 + index,
                assessments={
                    "BTC": {
                        "weighted_score": 70, "normalized_score": 70,
                        "confidence": "HIGH",
                    },
                },
                regime_inputs={
                    "btc_trend": "BULLISH", "volatility_state": "LOW",
                    "flow_state": "POSITIVE", "breadth_state": "HEALTHY",
                },
                next_returns={"BTC": 0.01, "USDT": 0.0},
                current_prices={"BTC": 100.0},
            ))

        class _Runner:
            def __call__(self, reviews, *, policy, fee_bps, slippage_bps, risk_inputs_by_review):
                return {
                    "metrics": {"cagr": 0.2, "maximum_drawdown": -0.2},
                    "regime_counts": {"NORMAL": len(reviews)},
                    "benchmark_comparison": {},
                }

        result = run_regime_variant_comparison(
            reviews, policy=policy, risk_inputs_by_review=[None] * len(reviews),
            fee_bps=0.0, slippage_bps=0.0, backtest_runner=_Runner(),
        )
        self.assertEqual(
            [row["variant"] for row in result["variants"]],
            ["R0", "R1", "R2", "R3"],
        )
        self.assertIsNone(result["variants"][0]["cagr_sacrificed_per_maxdd_pp_saved_vs_r0"])
        # Identical stub metrics -> zero sacrifice for zero saving -> None.
        self.assertIsNone(result["variants"][1]["cagr_sacrificed_per_maxdd_pp_saved_vs_r0"])
        self.assertIn("ownership", result)
        self.assertTrue(result["ownership"]["variants"]["R3"]["severe_systemic_override_preserved"])


if __name__ == "__main__":
    unittest.main()
