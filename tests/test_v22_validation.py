"""Strategy V2.2 validation harness (Phase E).

The preregistered ladder, the structural gate on rung F, the module
attribution, and the policy decision matrix are all deterministic from the
inputs; tests pin them with an injectable backtest runner.
"""

import unittest

from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.v22_validation import (
    alpha_attribution_report,
    policy_decision_matrix,
    run_v22_ablation,
    structural_ranking_admission,
    v22_ablation_ladder,
)


def _policy():
    return load_policy()


def _fake_result(cagr: float, excess: float = 0.01, eth_weight: float = 0.0):
    return {
        "metrics": {
            "cagr": cagr, "maximum_drawdown": -0.1, "annualized_volatility": 0.2,
            "sharpe_rf_zero": 0.5, "sortino_target_zero": 0.6, "total_return": 0.3,
        },
        "total_turnover": 1.0, "average_cash_weight": 0.4,
        "total_cost_usd": 100.0,
        "strategy_attribution": {"average_weights": {
            "BTC": 0.5 - eth_weight, "ETH": eth_weight,
        }},
        "trades": [{}, {}],
        "benchmark_comparison": {"vol_matched_btc_cash_investable": {
            "excess_return_annualized": excess,
        }},
        "stall_attribution": {"stalled_reviews": 0},
        "emergency_recovery_diagnostics": None,
        "risk_authority_separation": None,
        "reviews": [],
    }


class LadderShapeTests(unittest.TestCase):
    def test_rungs_follow_the_preregistered_letters(self):
        ladder = v22_ablation_ladder(_policy())
        self.assertEqual([entry["rung"] for entry in ladder], list("ABCDEFGH"))
        self.assertTrue(ladder[4]["diagnostic"])  # E observes, never replays
        self.assertEqual(ladder[5]["reviews"], "structural")

    def test_rung_a_is_btc_only_vol_targeting(self):
        ladder = {entry["rung"]: entry for entry in v22_ablation_ladder(_policy())}
        a = policy_from_mapping(ladder["A"]["policy"])
        self.assertEqual(a.satellite_symbols, ())
        self.assertEqual(a.core_allocation["mode"], "btc_baseline_with_active_tilts")
        self.assertFalse(a.core_allocation["eth"]["tilt_enabled"])
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"):
            self.assertEqual(
                a.risk_engine["regime_risk_scaling"][regime]["target_volatility_multiplier"],
                1.0,
            )
        self.assertEqual(a.risk_engine["recovery"]["stage_1_reviews"], 10**6)

    def test_rung_c_enables_the_eth_tilt_and_d_the_satellites(self):
        ladder = {entry["rung"]: entry for entry in v22_ablation_ladder(_policy())}
        c = policy_from_mapping(ladder["C"]["policy"])
        self.assertTrue(c.core_allocation["eth"]["tilt_enabled"])
        d = policy_from_mapping(ladder["D"]["policy"])
        self.assertEqual(d.satellite_symbols, _policy().satellite_symbols)
        self.assertTrue(d.core_allocation["eth"]["tilt_enabled"])

    def test_every_rung_parses_as_a_valid_policy(self):
        for entry in v22_ablation_ladder(_policy()):
            parsed = policy_from_mapping(entry["policy"])
            self.assertEqual(parsed.risk_engine["mode"], "volatility_budget")
            self.assertEqual(
                parsed.core_allocation["mode"], "btc_baseline_with_active_tilts",
            )


class RunV22AblationTests(unittest.TestCase):
    def test_structural_gate_skips_rung_f_unless_admitted(self):
        policy = _policy()
        calls: list[str] = []

        def fake_runner(reviews, *, policy, fee_bps, slippage_bps, risk_inputs_by_review):
            calls.append(str(policy.core_allocation["eth"]["tilt_enabled"]))
            return _fake_result(0.1)

        result = run_v22_ablation(
            [], policy=policy, daily_by_symbol={}, risk_inputs_by_review=[],
            git_sha="deadbeef", structural_admitted=False, backtest_runner=fake_runner,
        )
        letters = [rung["rung"] for rung in result["rungs"]]
        self.assertEqual(letters, list("ABCDEFGH"))
        f_rung = next(rung for rung in result["rungs"] if rung["rung"] == "F")
        self.assertEqual(f_rung.get("skipped"), "STRUCTURAL_RANKING_POWER_NOT_ADMITTED")
        self.assertIsNone(f_rung["metrics"])
        # A(1) B(1) C(1) D(1) G(default reviews) H(default reviews) = 6 replays.
        self.assertEqual(len(calls), 6)

    def test_admitted_structural_runs_f_and_h_on_structural_reviews(self):
        policy = _policy()

        def fake_runner(reviews, *, policy, fee_bps, slippage_bps, risk_inputs_by_review):
            return _fake_result(0.1)

        sentinel_reviews = ["default"]
        sentinel_structural = ["structural"]
        result = run_v22_ablation(
            sentinel_reviews, policy=policy, daily_by_symbol={},
            risk_inputs_by_review=[sentinel_reviews],
            structural_reviews=sentinel_structural,
            git_sha="deadbeef", structural_admitted=True,
            eth_tilt_admitted=True, backtest_runner=fake_runner,
        )
        # The harness keeps the deployment result used for attribution.
        self.assertEqual(result["_deployment_reviews"], sentinel_structural)
        self.assertEqual(result["_full_reviews"], sentinel_structural)

    def test_h_tilt_follows_the_admission_verdict(self):
        policy = _policy()
        tilt_flags: list[bool] = []

        def fake_runner(reviews, *, policy, fee_bps, slippage_bps, risk_inputs_by_review):
            tilt_flags.append(bool(policy.core_allocation["eth"]["tilt_enabled"]))
            return _fake_result(0.1)

        run_v22_ablation(
            [], policy=policy, daily_by_symbol={}, risk_inputs_by_review=[],
            git_sha="sha", eth_tilt_admitted=False, backtest_runner=fake_runner,
        )
        self.assertFalse(tilt_flags[-1])  # rung H keeps the tilt locked

    def test_missing_structural_reviews_for_admitted_f_fail_loudly(self):
        with self.assertRaisesRegex(ValueError, "structural reviews"):
            run_v22_ablation(
                [], policy=_policy(), daily_by_symbol={}, risk_inputs_by_review=[],
                git_sha="sha", structural_admitted=True,
                backtest_runner=lambda *args, **kwargs: _fake_result(0.1),
            )


class AttributionTests(unittest.TestCase):
    def test_module_deltas_and_residual_reconcile(self):
        rungs = [
            {"rung": letter, "metrics": _fake_result(0.10 * index)["metrics"] | {
                "total_cost_usd": 50.0,
            }}
            for index, letter in enumerate("ABCDFH")
        ]
        report = alpha_attribution_report(rungs)
        self.assertAlmostEqual(report["btc_baseline_cagr"], 0.0)
        self.assertAlmostEqual(report["market_regime_machinery_cagr_delta"], 0.10)
        self.assertAlmostEqual(report["eth_tilt_cagr_delta"], 0.10)
        self.assertAlmostEqual(report["satellite_tactical_cagr_delta"], 0.10)
        self.assertAlmostEqual(report["structural_deployment_cagr_delta"], 0.10)
        self.assertAlmostEqual(report["full_v22_cagr"], 0.50)
        # H (0.5) minus the baseline and the four module deltas (0.4).
        self.assertAlmostEqual(report["interaction_residual"], 0.10)
        self.assertAlmostEqual(report["cash_carry"], 0.0)


class DecisionMatrixTests(unittest.TestCase):
    def _window(self, letters_to_excess: dict[str, float]):
        return {"rungs": [
            {"rung": letter, "metrics": {
                "vol_matched_excess_annualized": value,
            }}
            for letter, value in letters_to_excess.items()
        ]}

    def test_consistent_positive_module_is_kept(self):
        primary = self._window({"A": 0.0, "B": 0.02, "C": 0.05, "D": 0.06, "F": 0.07})
        sister = self._window({"A": 0.0, "B": 0.01, "C": 0.03, "D": 0.05, "F": 0.08})
        matrix = policy_decision_matrix(
            primary, sister, eth_tilt_admitted=False, structural_admitted=False,
        )
        modules = matrix["modules"]
        self.assertEqual(modules["market_regime_machinery"]["decision"], "KEEP")
        # Unadmitted signals cap at RESEARCH_ONLY even when both windows
        # improve.
        self.assertEqual(modules["eth_relative_alpha_tilt"]["decision"], "RESEARCH_ONLY")
        self.assertEqual(modules["structural_full_conviction"]["decision"], "RESEARCH_ONLY")

    def test_negative_module_is_removed_or_demoted(self):
        primary = self._window({"A": 0.05, "B": 0.05, "C": 0.01, "D": 0.02, "F": 0.02})
        sister = self._window({"A": 0.05, "B": 0.05, "C": 0.00, "D": 0.02, "F": 0.02})
        matrix = policy_decision_matrix(
            primary, sister, eth_tilt_admitted=False, structural_admitted=False,
        )
        self.assertEqual(matrix["modules"]["eth_relative_alpha_tilt"]["decision"], "REMOVE")

    def test_single_window_cannot_decide(self):
        primary = self._window({"A": 0.0, "B": 0.02, "C": 0.05, "D": 0.06})
        matrix = policy_decision_matrix(
            primary, None, eth_tilt_admitted=False, structural_admitted=False,
        )
        self.assertEqual(
            matrix["modules"]["market_regime_machinery"]["decision"], "RESEARCH_ONLY",
        )


class StructuralAdmissionTests(unittest.TestCase):
    def test_both_windows_must_agree(self):
        def window(ic: float, blocks: int = 20, monotone: bool = True):
            return {"symbols": {"AAVE": {"factors": {"protocol_tvl_growth_90d": {
                "90": {"spearman_ic": ic, "independent_observations": blocks,
                       "bucket_monotonic": monotone},
                "180": {"spearman_ic": ic / 2, "independent_observations": blocks,
                        "bucket_monotonic": monotone},
            }}}}}

        admitted = structural_ranking_admission({
            "primary": window(0.2), "sister": window(0.15),
        })
        self.assertTrue(admitted["factors"]["protocol_tvl_growth_90d"]["admitted"])
        rejected = structural_ranking_admission({
            "primary": window(0.2), "sister": window(-0.15),
        })
        self.assertFalse(rejected["factors"]["protocol_tvl_growth_90d"]["admitted"])
        thin = structural_ranking_admission({
            "primary": window(0.2, blocks=4), "sister": window(0.2, blocks=4),
        })
        self.assertFalse(thin["factors"]["protocol_tvl_growth_90d"]["admitted"])


if __name__ == "__main__":
    unittest.main()
