"""Strategy V2.1 Phase E validation machinery.

Preregistered ablation ladder, stall attribution, family-score ranking, and
the frozen validation manifest — all research diagnostics that never feed
back into the engines.
"""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.score_evaluation import evaluate_scores
from crypto_portfolio.research.v21_validation import (
    classify_stall,
    evaluate_v3_family_scores,
    run_v21_ablation,
    stall_attribution,
    validation_manifest,
    v21_ablation_ladder,
)


def _policy():
    return load_policy()


def _factors(score=85):
    return {
        factor: {"score": score, "availability": "AVAILABLE", "reliability": 1.0}
        for factor in (
            "trend", "relative_strength_btc", "capital_flows",
            "valuation", "fundamentals", "onchain",
        )
    }


class ValidationManifestTests(unittest.TestCase):
    def test_manifest_freezes_every_identity_axis(self):
        policy = _policy()
        manifest = validation_manifest(policy, git_sha="abc123", data_manifest={"blockers": []})
        self.assertEqual(manifest["git_sha"], "abc123")
        self.assertEqual(manifest["risk_engine_mode"], "legacy_drawdown")
        self.assertEqual(manifest["scoring_v3_version"], "scoring-v3-phase-c")
        self.assertEqual(len(manifest["scoring_v3_families_sha256"]), 64)
        self.assertEqual(manifest["data_manifest"], {"blockers": []})
        # The hash is a pure function of the policy mapping.
        again = validation_manifest(policy, git_sha="abc123", data_manifest={"blockers": []})
        self.assertEqual(manifest, again)


class AblationLadderTests(unittest.TestCase):
    def test_ladder_has_the_preregistered_rungs_and_parses(self):
        ladder = v21_ablation_ladder(_policy())
        self.assertEqual([entry["rung"] for entry in ladder], list("ABCDEFGH"))
        for entry in ladder:
            parsed = policy_from_mapping(entry["policy"])
            self.assertEqual(parsed.risk_engine["mode"], "volatility_budget")

    def test_rung_a_strips_everything_but_btc_vol_targeting(self):
        ladder = {entry["rung"]: entry for entry in v21_ablation_ladder(_policy())}
        a = policy_from_mapping(ladder["A"]["policy"])
        self.assertEqual(a.satellite_symbols, ())
        # The frozen V2.1 ladder pins the legacy anchor core mode.
        self.assertEqual(a.core_allocation["mode"], "legacy_anchor")
        self.assertEqual(a.core_allocation["legacy_anchor"]["ETH"], 0.0)
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"):
            self.assertEqual(
                a.risk_engine["regime_risk_scaling"][regime]["target_volatility_multiplier"],
                1.0,
            )
        self.assertEqual(a.risk_engine["recovery"]["stage_1_reviews"], 10**6)

    def test_rung_d_mirrors_the_market_family_onto_structure(self):
        ladder = {entry["rung"]: entry for entry in v21_ablation_ladder(_policy())}
        d = policy_from_mapping(ladder["D"]["policy"])
        self.assertIn("trend", d.scoring_v3["families"]["default"]["structural"])
        e = policy_from_mapping(ladder["E"]["policy"])
        self.assertNotIn("trend", e.scoring_v3["families"]["default"]["structural"])

    def test_rung_h_is_the_unchanged_v21_policy(self):
        policy = _policy()
        ladder = {entry["rung"]: entry for entry in v21_ablation_ladder(policy)}
        h = policy_from_mapping(ladder["H"]["policy"])
        self.assertEqual(h.satellite_symbols, policy.satellite_symbols)
        self.assertEqual(h.scoring_v3, policy.scoring_v3)
        self.assertEqual(h.risk_engine["recovery"], policy.risk_engine["recovery"])


class RunAblationTests(unittest.TestCase):
    def test_runner_executes_every_rung_with_the_frozen_manifest(self):
        policy = _policy()
        calls: list[dict] = []

        def fake_runner(reviews, *, policy, fee_bps, slippage_bps, risk_inputs_by_review):
            calls.append({
                "mode": policy.risk_engine["mode"],
                "satellites": bool(policy.satellite_symbols),
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
            })
            return {
                "metrics": {
                    "cagr": 0.1 * len(calls), "maximum_drawdown": -0.1,
                    "annualized_volatility": 0.2, "sharpe_rf_zero": 0.5,
                    "sortino_target_zero": 0.6, "total_return": 0.3,
                },
                "total_turnover": 1.0, "average_cash_weight": 0.4,
                "benchmark_comparison": {"vol_matched_btc_cash_investable": {
                    "excess_return_annualized": 0.01,
                }},
                "stall_attribution": {"stalled_reviews": 0},
                "emergency_recovery_diagnostics": None,
                "risk_authority_separation": None,
            }

        result = run_v21_ablation(
            [], policy=policy, daily_by_symbol={}, risk_inputs_by_review=[],
            git_sha="deadbeef", backtest_runner=fake_runner,
        )
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(call["mode"] == "volatility_budget" for call in calls))
        # Rung G isolates costs: it runs with zero fees and slippage.
        self.assertEqual(calls[6]["fee_bps"], 0.0)
        self.assertGreater(calls[7]["fee_bps"], 0.0)
        rungs = result["rungs"]
        self.assertEqual(rungs[0]["manifest"]["git_sha"], "deadbeef")
        self.assertIsNone(rungs[0]["delta_vs_previous"])
        self.assertAlmostEqual(rungs[1]["delta_vs_previous"]["cagr"], 0.1)
        self.assertEqual(rungs[-1]["rung"], "H")


def _row(**overrides):
    row = {
        "trades": [],
        "risk_gate": {"violations": []},
        "allocation": {
            "risk_engine": {
                "emergency_overlay_state": {"state": "NORMAL"},
                "binding_risk_constraint": "strategic_target",
            },
            "deployment_allowances": {},
        },
        "rebalance": {"decision": "HOLD"},
        "entry_outcomes": {},
        "regime": {"regime": "NORMAL"},
    }
    row.update(overrides)
    return row


class StallAttributionTests(unittest.TestCase):
    def test_reviews_with_trades_are_never_stalled(self):
        self.assertIsNone(classify_stall(_row(trades=[{"symbol": "BTC"}])))

    def test_priority_order_is_deterministic(self):
        cases = [
            (_row(risk_gate={"violations": [{"code": "CHAIN_LIVENESS_HALTED"}]}), "LIVENESS"),
            (_row(risk_gate={"violations": [{"code": "SEVERE_EVENT_EXPOSURE"}]}), "EVENT_RISK"),
            (
                _row(allocation={"risk_engine": {
                    "emergency_overlay_state": {"state": "EMERGENCY"},
                    "binding_risk_constraint": "strategic_target",
                }, "deployment_allowances": {}}),
                "EMERGENCY_OVERLAY",
            ),
            (
                _row(allocation={"risk_engine": {
                    "emergency_overlay_state": {"state": "RECOVERY_1"},
                    "binding_risk_constraint": "strategic_target",
                }, "deployment_allowances": {}}),
                "RECOVERY_STATE",
            ),
            (
                _row(allocation={"risk_engine": {
                    "emergency_overlay_state": {"state": "NORMAL"},
                    "binding_risk_constraint": "volatility_budget",
                }, "deployment_allowances": {}}),
                "VOLATILITY_BUDGET",
            ),
            (
                _row(entry_outcomes={"SOL": {"status": "WAIT"}}),
                "EXECUTION_WAIT",
            ),
            (_row(regime={"regime": "DEFENSIVE"}), "MARKET_REGIME"),
            (
                _row(allocation={
                    "risk_engine": row_engine(),
                    "deployment_allowances": {"SOL": {"preserve_existing": True}},
                }),
                "EVIDENCE_BLOCK",
            ),
            (
                _row(allocation={
                    "risk_engine": row_engine(),
                    "deployment_allowances": {
                        "SOL": {"eligibility_state": "INELIGIBLE",
                                "conviction_state": "WATCH_ONLY"},
                    },
                }),
                "SCORE_BLOCK",
            ),
            (_row(rebalance={"decision": "NO_TRADE"}), "REBALANCE_BAND"),
        ]
        for row, expected in cases:
            self.assertEqual(classify_stall(row), expected, msg=str(row)[:120])
        self.assertEqual(classify_stall(_row(rebalance={"decision": "REBALANCE"})), "NO_CAPITAL_AVAILABLE")

    def test_aggregation_counts_share_and_streaks(self):
        rows = [
            _row(rebalance={"decision": "NO_TRADE"}),          # REBALANCE_BAND
            _row(trades=[{"symbol": "BTC"}]),                   # not stalled
            _row(regime={"regime": "DEFENSIVE"}),               # MARKET_REGIME
            _row(regime={"regime": "DEFENSIVE"}),               # MARKET_REGIME
        ]
        result = stall_attribution(rows)
        self.assertEqual(result["reviews"], 4)
        self.assertEqual(result["stalled_reviews"], 3)
        self.assertAlmostEqual(result["stalled_share"], 0.75)
        self.assertEqual(result["reason_counts"], {
            "REBALANCE_BAND": 1, "MARKET_REGIME": 2,
        })
        self.assertEqual(result["longest_consecutive_stalled_reviews"], 2)
        self.assertEqual(result["trading_stalled_root_causes"][0], "MARKET_REGIME")


def row_engine():
    return {"emergency_overlay_state": {"state": "NORMAL"},
            "binding_risk_constraint": "strategic_target"}


class FamilyScoreEvaluationTests(unittest.TestCase):
    def _prices(self, symbols):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        prices = {}
        for symbol, drift in symbols.items():
            prices[symbol] = [
                {
                    "timestamp": (start + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                    "price": 100.0 * ((1.0 + drift) ** index),
                }
                for index in range(0, 200, 5)
            ]
        return prices

    def test_families_are_ranked_and_convictions_counted(self):
        policy = _policy()
        observations = []
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        for index in range(12):
            observations.append({
                "timestamp": (start + timedelta(days=7 * index)).isoformat().replace("+00:00", "Z"),
                "symbol": "SOL",
                "score": 60.0,
                "normalized_score": 60.0,
                "coverage": 1.0,
                "factor_scores": _factors(70 + index),
                "synthetic": False,
            })
        result = evaluate_v3_family_scores(
            observations, self._prices({"SOL": 0.002, "BTC": 0.001}), policy=policy,
            horizons=(30,),
        )
        self.assertEqual(result["conviction_state_counts"], {"FULL_CONVICTION": 12})
        market = result["market"]["horizons"]["30"]
        self.assertEqual(market["available_samples"], 12)
        self.assertIn("buckets", market)
        self.assertTrue(set(market["buckets"]) & {"60-70", "70-80", "80-100"})

    def test_score_buckets_report_monotonicity(self):
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        observations = []
        for index, score in enumerate((20, 45, 55, 65, 75, 90, 35, 85)):
            observations.append({
                "timestamp": (start + timedelta(days=40 * index)).isoformat().replace("+00:00", "Z"),
                "symbol": "SOL",
                "score": float(score),
                "normalized_score": float(score),
                "coverage": 1.0,
                "factor_scores": {},
                "synthetic": False,
            })
        prices = self._prices({"SOL": 0.0, "BTC": 0.0})
        result = evaluate_scores(observations, prices, horizons=(30,))
        summary = result["horizons"]["30"]
        self.assertIn("buckets", summary)
        self.assertIn("bucket_relative_return_monotonic", summary)


if __name__ == "__main__":
    unittest.main()
