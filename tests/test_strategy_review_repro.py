"""Synthetic regressions from the 2026-09-13 strategy review (plan.md).

Every case uses fabricated inputs only.  Assertions marked
``PENDING_POLICY_DECISION`` document current behavior that may only change
after the matching phase-8 decision; the other assertions capture the
pre-fix defect and are flipped when the corresponding phase lands:

- F2 missing-data exits            -> phase 4
- F5 relative-strength unit guess  -> phase 4
- A2 confidence label vs numeric   -> phase 2
- A3 REDUCE with NO_TRADE scope    -> phase 3
- F4 double deployment cap         -> phase 5
- F6 post-action stable shortfall  -> phase 6
- F1 satellite entry cliff         -> fixed by the continuous target curve (phase 1)
- F3 core temporary-cap targets    -> 8B decided: multipliers stay in raw proportions
- F7 regime notch per review       -> PENDING_POLICY_DECISION (8D)
"""

import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.engine.allocation import build_target_allocation, satellite_eligibility
from crypto_portfolio.engine.confidence import DecisionScope, calculate_decision_confidence
from crypto_portfolio.engine.core_eligibility import relative_strength_score
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.rebalance import recommend_rebalance
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.scoring import score_factors

CORE = {
    "BTC": {"weighted_score": 80, "confidence": "HIGH"},
    "ETH": {"weighted_score": 80, "confidence": "HIGH", "relative_strength_vs_btc": 70},
}
CURRENT = {"BTC": 0.5, "ETH": 0.2, "USDT": 0.2, "SOL": 0.1}
ALL_FACTORS = {
    "trend": 80, "valuation": 80, "fundamentals": 80,
    "onchain": 80, "capital_flows": 80, "relative_strength_btc": 80,
}


def _sol_target(score: float) -> float:
    result = build_target_allocation(
        regime="NORMAL",
        assessments={
            **CORE,
            "SOL": {"weighted_score": score, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"},
        },
        current_weights=CURRENT,
    )
    return result.target_weights.get("SOL", 0.0)


class F1SatelliteEntryCliffTests(unittest.TestCase):
    """Score improving across the entry boundary must not cut a held target."""

    def test_continuous_curve_removed_the_entry_cliff(self):
        # Fixed by the phase-1 continuous target curve: the held target is a
        # monotone piecewise-linear function of score across the whole
        # domain, so 66.9 -> 67.0 is an adjacent curve step, not a collapse.
        self.assertAlmostEqual(_sol_target(61), 0.04)
        self.assertAlmostEqual(_sol_target(62), 0.05)
        self.assertAlmostEqual(_sol_target(66), 0.09)
        self.assertAlmostEqual(_sol_target(67), 0.10)
        self.assertAlmostEqual(_sol_target(85), 0.25)


class F2MissingDataExitTests(unittest.TestCase):
    def test_missing_factors_preserve_held_position(self):
        assessment = {
            "weighted_score": 50,
            "confidence": "LOW",
            "critical_data_complete": False,
            "relative_strength_vs_btc": None,
        }
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.1), "HOLD_OR_REDUCE")
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.0), "INELIGIBLE")

    def test_confirmed_negative_evidence_still_ineligible(self):
        assessment = {
            "weighted_score": 50,
            "confidence": "HIGH",
            "critical_data_complete": True,
            "relative_strength_vs_btc": "UNDERPERFORM",
        }
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.1), "INELIGIBLE")

    def test_missing_evidence_does_not_shield_confirmed_weakness(self):
        # Weak BTC-relative evidence decides even when the other factors are
        # missing: bad news is never masked by data unavailability.
        assessment = {
            "weighted_score": 50,
            "confidence": "LOW",
            "critical_data_complete": False,
            "relative_strength_vs_btc": "MATERIALLY_WEAK",
        }
        self.assertEqual(satellite_eligibility(assessment, current_weight=0.1), "INELIGIBLE")

    def test_new_cash_cannot_implicitly_increase_hold_only_satellite(self):
        allocation = build_target_allocation(
            regime="NORMAL",
            assessments={
                **CORE,
                "SOL": {
                    "weighted_score": 50, "confidence": "LOW",
                    "critical_data_complete": False, "relative_strength_vs_btc": None,
                },
            },
            current_weights=CURRENT,
        )
        result = recommend_rebalance(
            CURRENT, dict(allocation.target_weights), 10000.0, new_cash_available=1000.0
        )
        sol = next(action for action in result.actions if action.symbol == "SOL")
        self.assertIn(sol.action, {"HOLD", "WAIT"})
        self.assertEqual(sol.amount_usd, 0.0)


class F3CoreTemporaryCapTests(unittest.TestCase):
    def test_core_confidence_and_event_multipliers_stay_in_raw_proportions(self):
        # 8B decided by the structural plan: temporary confidence/event
        # limits stay inside the core raw proportions (anchor x quality x
        # confidence x event), while capped-budget water-filling (phase 3)
        # lets an eligible ETH absorb the BTC-capped residual up to its own
        # sleeve cap before anything becomes stablecoin.
        def eth_target(eth: dict) -> float:
            result = build_target_allocation(
                assessments={"BTC": CORE["BTC"], "ETH": eth},
                current_weights={"BTC": 0.5, "ETH": 0.255, "USDT": 0.245},
            )
            return result.target_weights.get("ETH", 0.0)

        baseline = eth_target(CORE["ETH"])
        low_confidence = eth_target({**CORE["ETH"], "confidence": "LOW"})
        high_event = eth_target({**CORE["ETH"], "event_risk": {"state": "HIGH"}})
        # Baseline: BTC caps at 50%, ETH water-fills the residual to its
        # 40%-of-core-sleeve cap (0.34 of the 85% risky budget).
        self.assertAlmostEqual(baseline, 0.34)
        # LOW confidence shrinks the raw proportion and gates ETH to its
        # desired share; it must not absorb redistributed budget.
        self.assertAlmostEqual(low_confidence, 0.082258, places=5)
        # A HIGH event halves the raw proportion (multiplier 0.5) in the
        # step-3 split; once BTC is capped ETH is the only redistributable
        # core asset, so it still water-fills the residual to its cap.
        # The event limit remains visible in deployment, not in the target.
        self.assertAlmostEqual(high_event, 0.34)


class F4DoubleDeploymentCapTests(unittest.TestCase):
    def _allocation(self):
        return build_target_allocation(
            regime="NORMAL",
            assessments={
                **CORE,
                "SOL": {"weighted_score": 85, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"},
            },
            current_weights=CURRENT,
            decision_confidence={"score": 0.7},
        )

    def test_cap_is_consumed_exactly_once(self):
        allocation = self._allocation()
        factor = allocation.deployment_factors["SOL"]
        self.assertAlmostEqual(factor, 0.7)
        result = recommend_rebalance(
            CURRENT, dict(allocation.target_weights), 10000.0,
            deployment_caps={"SOL": factor},
        )
        amount = next(a.amount_usd for a in result.actions if a.symbol == "SOL")
        self.assertAlmostEqual(amount, 910.0)
        # Re-expressing the same decision confidence alongside the folded
        # per-symbol cap is rejected instead of silently multiplying again.
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            recommend_rebalance(
                CURRENT, dict(allocation.target_weights), 10000.0,
                decision_confidence={"score": 0.7}, deployment_caps={"SOL": 0.7},
            )

    def test_entry_does_not_reapply_confidence_to_approved_amount(self):
        from crypto_portfolio.engine.confidence import confidence_deployment_factor
        from crypto_portfolio.engine.entry import build_entry_plan
        from crypto_portfolio.engine.technical import build_technical_snapshot
        from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
        from datetime import date, timedelta

        candles = []
        start = date(2025, 1, 1)
        for index in range(365):
            close = 100 + index * 0.5
            if index == 330:
                close -= 20
            candles.append(Candle(
                (start + timedelta(days=index)).isoformat() + "T00:00:00Z",
                close - 0.5, close + 2, close - 2, close,
                200 if index == 364 else 100,
            ))
        series = OHLCVSeries("ETH", "1D", tuple(candles), source="synthetic", fetched_at="2026-01-01T00:00:00Z")
        snapshot = build_technical_snapshot(
            series, SpotPrice("ETH", 282, "2026-01-01T08:00:00Z", "synthetic", "2026-01-01T08:00:00Z")
        )
        from dataclasses import replace

        high = build_entry_plan("ETH", 2000, snapshot, "NORMAL", "HIGH")
        medium = build_entry_plan("ETH", 2000, snapshot, "NORMAL", "MEDIUM")
        # The approved amount is final: MEDIUM portfolio confidence must not
        # shrink the staged dollars a second time (factor 0.7 would).
        self.assertAlmostEqual(medium.planned_amount_usd, high.planned_amount_usd)
        self.assertGreater(medium.planned_amount_usd, 0)
        self.assertAlmostEqual(
            confidence_deployment_factor(0.7), 0.7
        )  # the upstream cap that approved dollars already consumed
        # The snapshot's own MEDIUM data confidence is an independent basis
        # measured after approval, so it still scales staging to 70%.
        medium_tech = build_entry_plan("ETH", 2000, replace(snapshot, data_confidence="MEDIUM"), "NORMAL", "HIGH")
        self.assertAlmostEqual(medium_tech.planned_amount_usd, high.planned_amount_usd * 0.7, places=6)


class F5RelativeStrengthUnitTests(unittest.TestCase):
    def test_scores_are_read_on_the_single_0_100_unit(self):
        # No magnitude guessing: 0.5 is score 0.5 (maximal underperformance),
        # 1 is score 1, and 1.01 is score 1.01.
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 0.5}), 0.5)
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 1}), 1.0)
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 1.01}), 1.01)
        self.assertEqual(relative_strength_score({"relative_strength_vs_btc": 70}), 70.0)

    def test_out_of_range_scores_are_rejected(self):
        for invalid in (-0.2, 100.5, float("inf")):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "must be finite|in \\[0, 100\\]"):
                    relative_strength_score({"relative_strength_vs_btc": invalid})

    def test_satellite_numeric_score_interpretation(self):
        common = {"weighted_score": 85, "confidence": "HIGH", "critical_data_complete": True}
        self.assertEqual(satellite_eligibility({**common, "relative_strength_vs_btc": 30}), "INELIGIBLE")
        self.assertEqual(satellite_eligibility({**common, "relative_strength_vs_btc": 0.5}), "INELIGIBLE")
        self.assertEqual(satellite_eligibility({**common, "relative_strength_vs_btc": 50}), "ELIGIBLE_INCREASE")
        self.assertEqual(satellite_eligibility({**common, "relative_strength_vs_btc": 70}), "ELIGIBLE_INCREASE")
        with self.assertRaisesRegex(ValueError, "in \\[0, 100\\]"):
            satellite_eligibility({**common, "relative_strength_vs_btc": -0.2})

    def test_assessment_model_rejects_out_of_range_scores(self):
        from crypto_portfolio.models.evidence import AssetAssessment

        with self.assertRaisesRegex(ValueError, "in \\[0, 100\\]"):
            AssetAssessment("SOL", {"trend": 80}, relative_strength_vs_btc=120)
        self.assertEqual(
            AssetAssessment("SOL", {"trend": 80}, relative_strength_vs_btc=70).relative_strength_vs_btc,
            70.0,
        )


class F6PostActionConstraintTests(unittest.TestCase):
    def test_stable_shortfall_stays_visible_under_hold_band(self):
        result = recommend_rebalance(
            {"BTC": 0.50, "ETH": 0.36, "USDT": 0.14},
            {"BTC": 0.50, "ETH": 0.35, "USDT": 0.15},
            10000.0,
        )
        # Turnover thresholds keep the NO_TRADE outcome (sub-threshold repair
        # trades are a pending 8E policy decision), but the post-action
        # projection must expose the unrepaired shortfall instead of
        # reporting the compliant strategic target.
        self.assertEqual(result.decision, "NO_TRADE")
        self.assertTrue(all(action.action == "HOLD" for action in result.actions))
        projection = result.post_action_projection
        self.assertAlmostEqual(projection["projected_stable_weight"], 0.14)
        self.assertEqual(len(projection["unresolved_constraints"]), 1)
        self.assertIn("STABLECOIN_FLOOR_UNRESOLVED", projection["unresolved_constraints"][0])
        self.assertEqual(
            result.no_trade_attribution.primary_reason, "STABLECOIN_FLOOR_CONSTRAINT"
        )

    def test_capped_buys_leave_undeployed_dollars_in_the_stable_sleeve(self):
        result = recommend_rebalance(
            {"BTC": 0.40, "ETH": 0.20, "USDT": 0.40},
            {"BTC": 0.50, "ETH": 0.25, "USDT": 0.25},
            10000.0,
            deployment_caps={"BTC": 0.7, "ETH": 0.7},
        )
        weights = result.post_action_projection["projected_weights"]
        # Buy caps shrink the increases but the stable REDUCE was sized to
        # fund the uncapped buys: the undeployed dollars must stay in the
        # stable sleeve instead of disappearing from the projection.
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
        self.assertGreater(weights["USDT"], 0.25)

    def test_projection_reflects_approved_dollars_and_conserves_total(self):
        result = recommend_rebalance(
            {"BTC": 0.40, "ETH": 0.20, "USDT": 0.40},
            {"BTC": 0.50, "ETH": 0.25, "USDT": 0.25},
            10000.0,
        )
        projection = result.post_action_projection
        buys = sum(
            action.amount_usd for action in result.actions
            if action.action == "INCREASE" and action.symbol != "USDT"
        )
        sells = sum(
            action.amount_usd for action in result.actions
            if action.action in {"REDUCE", "EXIT"} and action.symbol != "USDT"
        )
        self.assertGreater(buys, 0.0)
        weights = projection["projected_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
        self.assertAlmostEqual(
            weights.get("USDT", 0.0),
            (4000.0 - buys + sells) / 10000.0,
            places=6,
        )
        self.assertEqual(projection["unresolved_constraints"], ())


class S1StressDiagnosticTests(unittest.TestCase):
    WEIGHTS = {"BTC": 0.42, "ETH": 0.18, "SOL": 0.25, "USDT": 0.15}

    def test_scenario_return_is_weighted_and_fail_closed(self):
        from crypto_portfolio.engine.risk import scenario_portfolio_return

        scenario = {"BTC": -0.30, "ETH": -0.45, "SOL": -0.60, "USDT": 0.0}
        self.assertAlmostEqual(scenario_portfolio_return(self.WEIGHTS, scenario), -0.357)
        with self.assertRaisesRegex(ValueError, "missing for exposed asset"):
            scenario_portfolio_return(self.WEIGHTS, {"BTC": -0.30, "ETH": -0.45, "SOL": -0.60})
        with self.assertRaisesRegex(ValueError, "cannot be below -100%"):
            scenario_portfolio_return(self.WEIGHTS, {**scenario, "USDT": -1.5})

    def test_projected_drawdown_and_remaining_capacity(self):
        from crypto_portfolio.engine.risk import (
            projected_peak_drawdown,
            remaining_drawdown_capacity,
        )

        projection = projected_peak_drawdown(-0.10, -0.357)
        self.assertAlmostEqual(projection["projected_drawdown"], 0.9 * 0.643 - 1.0, places=9)
        projection_gain = projected_peak_drawdown(-0.10, 0.20)
        self.assertEqual(projection_gain["projected_drawdown"], 0.0)
        self.assertGreater(projection_gain["change_vs_previous_peak"], 0.0)
        # Review formula: d=-10%, D=15% leaves ~5.56% of further loss space.
        self.assertAlmostEqual(remaining_drawdown_capacity(-0.10, 0.15), 0.055555, places=5)
        self.assertEqual(remaining_drawdown_capacity(-0.16, 0.15), 0.0)
        self.assertIsNone(remaining_drawdown_capacity(-1.0, 0.15))

    def test_stress_diagnostic_combines_contribution_and_budget(self):
        from crypto_portfolio.engine.risk import stress_diagnostic

        diagnostic = stress_diagnostic(
            self.WEIGHTS,
            {"BTC": -0.30, "ETH": -0.45, "SOL": -0.60, "USDT": 0.0},
            current_drawdown=-0.10,
            risk_budget=0.15,
        )
        self.assertEqual(diagnostic["status"], "DIAGNOSTIC_ONLY")
        self.assertAlmostEqual(
            sum(diagnostic["asset_contributions"].values()), diagnostic["scenario_return"]
        )
        self.assertTrue(diagnostic["budget_breach"])
        self.assertAlmostEqual(diagnostic["remaining_capacity"], 0.055555, places=5)


class F7RegimeNotchTests(unittest.TestCase):
    def test_same_evidence_advances_one_notch_per_review_pending_8d(self):
        evidence = RegimeInputs(btc_trend="BEARISH", volatility_state="HIGH", flow_state="OUTFLOW")
        first = determine_regime(evidence, previous="NORMAL")
        second = determine_regime(evidence, previous=first)
        self.assertEqual(first.regime, "DEFENSIVE")
        self.assertEqual(second.regime, "CAPITAL_PRESERVATION")

    def test_drawdown_floor_is_never_delayed(self):
        evidence = RegimeInputs(btc_trend="BULLISH", portfolio_drawdown_band=-0.10)
        capped = determine_regime(evidence, previous="NORMAL")
        self.assertEqual(capped.regime, "DEFENSIVE")
        breach = determine_regime(
            RegimeInputs(btc_trend="BULLISH", portfolio_drawdown_band=-0.16), previous="NORMAL"
        )
        self.assertEqual(breach.regime, "CAPITAL_PRESERVATION")


class A2ConfidenceLabelTests(unittest.TestCase):
    def test_band_is_derived_not_labeled(self):
        # A LOW caller label cannot pin the band while the numeric evidence
        # is complete: the band comes from coverage gates plus the numeric
        # data-confidence score, and only the more defensive one wins.
        result = score_factors(ALL_FACTORS, symbol="SOL")
        self.assertEqual(result.confidence, "HIGH")
        self.assertEqual(result.data_confidence_band, "HIGH")

    def test_supplied_low_label_cannot_override_numeric_high(self):
        from crypto_portfolio.models.evidence import AssetAssessment

        from crypto_portfolio.engine.scoring import score_assessment

        assessment, result = score_assessment(
            AssetAssessment("SOL", dict(ALL_FACTORS), confidence="LOW")
        )
        self.assertEqual(assessment.confidence, "HIGH")  # input label cannot pin LOW
        self.assertEqual(result.confidence, "HIGH")
        self.assertEqual(result.data_confidence_band, "HIGH")

    def test_low_numeric_evidence_lowers_band_without_label(self):
        factors = dict(ALL_FACTORS)
        factors["trend"] = {"score": 80, "freshness": "UNKNOWN"}
        weights = {
            "trend": 0.3, "valuation": 0.15, "fundamentals": 0.2,
            "onchain": 0.1, "capital_flows": 0.1, "relative_strength_btc": 0.15,
        }
        result = score_factors(factors, weights, symbol="SOL")
        # Trend reliability collapses to 0: coverage 0.7 (gate MEDIUM) and a
        # freshness dimension of 0 drag the numeric score to ~0.42 (band LOW).
        self.assertAlmostEqual(result.data_confidence_score, 0.42, places=2)
        self.assertEqual(result.confidence, "LOW")


class A3ScopeMismatchTests(unittest.TestCase):
    def _packet(self, scope_action="NO_TRADE"):
        decision_confidence = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"BTC": 0.9}, "weights": {"BTC": 1.0}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope(scope_action, ("BTC",), {"BTC": 0.5}),
        )
        return build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
            target_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.0, "USDT": 0.3},
            assessments={"AAVE": {"weighted_score": 60, "confidence": "MEDIUM"}},
            actions=[{
                "symbol": "AAVE", "action": "REDUCE", "amount_usd": 1000.0,
                "current_weight": 0.1, "target_weight": 0.0,
            }],
            decision_confidence=decision_confidence,
        )

    def test_reduce_action_with_no_trade_scope_is_rejected(self):
        from crypto_portfolio.models.decision import Decision
        from crypto_portfolio.state.decisions import append_decision

        with self.assertRaisesRegex(ValueError, "contradicts"):
            self._packet()

        no_trade_scoped = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"BTC": 0.9}, "weights": {"BTC": 0.5}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope("NO_TRADE", ("BTC",), {"BTC": 0.5}),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "contradicts"):
                append_decision(
                    Decision(
                        "2026-09-13T00:00:00Z",
                        "NORMAL",
                        {"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
                        {"BTC": 0.5, "ETH": 0.2, "AAVE": 0.0, "USDT": 0.3},
                        actions=[{
                            "symbol": "AAVE", "action": "REDUCE", "amount_usd": 1000.0,
                            "current_weight": 0.1, "target_weight": 0.0, "priority": "NORMAL",
                        }],
                        decision_confidence=no_trade_scoped,
                    ),
                    Path(directory) / "decisions.jsonl",
                )

    def test_matching_scope_is_accepted(self):
        decision_confidence = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"AAVE": 0.9}, "weights": {"AAVE": 0.1}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope("REDUCE", ("AAVE",), {"AAVE": 0.1}),
        )
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
            target_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.0, "USDT": 0.3},
            assessments={"AAVE": {"weighted_score": 60, "confidence": "MEDIUM"}},
            actions=[{
                "symbol": "AAVE", "action": "REDUCE", "amount_usd": 1000.0,
                "current_weight": 0.1, "target_weight": 0.0,
            }],
            decision_confidence=decision_confidence,
        )
        self.assertEqual(packet.decision_confidence.scope["action"], "REDUCE")

    def test_scope_omitting_executable_asset_is_rejected(self):
        decision_confidence = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"BTC": 0.9}, "weights": {"BTC": 0.5}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope("REDUCE", ("BTC",), {"BTC": 0.5}),
        )
        with self.assertRaisesRegex(ValueError, "omits executable asset"):
            build_decision_review_packet(
                review_type="SNAPSHOT_REVIEW",
                market_regime="NORMAL",
                current_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.1, "USDT": 0.2},
                target_weights={"BTC": 0.5, "ETH": 0.2, "AAVE": 0.0, "USDT": 0.3},
                assessments={"AAVE": {"weighted_score": 60, "confidence": "MEDIUM"}},
                actions=[{
                    "symbol": "AAVE", "action": "REDUCE", "amount_usd": 1000.0,
                    "current_weight": 0.1, "target_weight": 0.0,
                }],
                decision_confidence=decision_confidence,
            )


if __name__ == "__main__":
    unittest.main()


class ReviewRound2Regressions(unittest.TestCase):
    """Regressions for the PR #2 Codex review findings."""

    def test_projection_reaches_review_and_report_packets(self):
        from crypto_portfolio.engine.report_packet import build_report_packet

        result = recommend_rebalance(
            {"BTC": 0.50, "ETH": 0.36, "USDT": 0.14},
            {"BTC": 0.50, "ETH": 0.35, "USDT": 0.15},
            10000.0,
        )
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.50, "ETH": 0.36, "USDT": 0.14},
            target_weights={"BTC": 0.50, "ETH": 0.35, "USDT": 0.15},
            post_action_projection=dict(result.post_action_projection),
        )
        self.assertIsNotNone(packet.post_action_projection)
        report = build_report_packet(packet)
        self.assertIsNotNone(report.post_action_projection)
        self.assertIn(
            "STABLECOIN_FLOOR_UNRESOLVED",
            "".join(report.post_action_projection["unresolved_constraints"]),
        )
        output = __import__("crypto_portfolio.engine.report_packet", fromlist=["build_final_review_output"]).build_final_review_output(report)
        self.assertIn("post_action_projection", output["rebalance"])

    def test_stable_funding_leg_does_not_flip_the_confidence_action(self):
        # A routine de-risk: SOL REDUCE funds a USDT INCREASE. The confidence
        # scope must follow the risky leg (REDUCE), not the settlement leg.
        decision_confidence = calculate_decision_confidence(
            {
                "portfolio_data": 0.9,
                "regime_confidence": 0.9,
                "asset_evidence": {"assets": {"SOL": 0.9}, "weights": {"SOL": 0.1}},
                "portfolio_accounting": 1.0,
                "signal_agreement": 1.0,
            },
            scope=DecisionScope("REDUCE", ("SOL",), {"SOL": 0.1}),
        )
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"BTC": 0.5, "SOL": 0.1, "USDT": 0.4},
            target_weights={"BTC": 0.5, "SOL": 0.05, "USDT": 0.45},
            assessments={"SOL": {"weighted_score": 60, "confidence": "MEDIUM"}},
            actions=[
                {"symbol": "SOL", "action": "REDUCE", "amount_usd": 500.0, "current_weight": 0.1, "target_weight": 0.05},
                {"symbol": "USDT", "action": "INCREASE", "amount_usd": 500.0, "current_weight": 0.4, "target_weight": 0.45},
            ],
            decision_confidence=decision_confidence,
        )
        self.assertEqual(packet.decision_confidence.scope["action"], "REDUCE")

    def test_missing_scoring_factor_preserves_held_satellite(self):
        # Only the trend factor is missing: the shrunk neutral score (47)
        # must not exit a held position even though the coarse
        # critical_data_complete flag is still True.
        from crypto_portfolio.models.evidence import AssetAssessment

        from crypto_portfolio.engine.scoring import score_assessment

        factors = {"trend": None, "valuation": 60, "fundamentals": 60, "onchain": 60,
                   "capital_flows": 60, "relative_strength_btc": 60}
        scored, _ = score_assessment(
            AssetAssessment("SOL", factors, critical_data_complete=True,
                            relative_strength_vs_btc="OUTPERFORM")
        )
        # 0.3*50 + 0.7*60 = 57: inside the soft-exit band, but the missing
        # factor makes this incomplete evidence, not evidenced weakness.
        self.assertLess(scored.weighted_score, 67)
        self.assertEqual(satellite_eligibility(scored, current_weight=0.1), "HOLD_OR_REDUCE")

    def test_risk_tier_source_is_representation_independent(self):
        from crypto_portfolio.models.evidence import AssetAssessment

        common = {"weighted_score": 85, "confidence": "HIGH", "relative_strength_vs_btc": "OUTPERFORM"}
        mapping_result = build_target_allocation(
            assessments={"SOL": {**common, "risk_tier": "normal"}})
        typed_result = build_target_allocation(
            assessments={"SOL": AssetAssessment(
                "SOL",
                {"trend": 85, "valuation": 85, "fundamentals": 85, "onchain": 85,
                 "capital_flows": 85, "relative_strength_btc": 85},
                confidence="HIGH", relative_strength_vs_btc="OUTPERFORM")})
        self.assertEqual(
            mapping_result.deployment_allowances["SOL"]["risk_tier_source"],
            typed_result.deployment_allowances["SOL"]["risk_tier_source"],
        )
        self.assertEqual(
            typed_result.deployment_allowances["SOL"]["risk_tier_source"], "POLICY_DEFAULT"
        )

    def test_model_rejects_unknown_relative_strength_states(self):
        from crypto_portfolio.models.evidence import AssetAssessment

        with self.assertRaisesRegex(ValueError, "recognized state"):
            AssetAssessment("SOL", {"trend": 80}, relative_strength_vs_btc="FOO")


class ReviewRound2RiskTests(unittest.TestCase):
    def test_impossible_drawdowns_are_rejected(self):
        from crypto_portfolio.engine.risk import (
            projected_peak_drawdown,
            remaining_drawdown_capacity,
        )

        with self.assertRaisesRegex(ValueError, "in \\[-1, 0\\]"):
            projected_peak_drawdown(-1.5, -0.2)
        with self.assertRaisesRegex(ValueError, "in \\[-1, 0\\]"):
            remaining_drawdown_capacity(-1.5, 0.15)
        # -1 exactly is the legitimate total-loss boundary.
        self.assertIsNone(remaining_drawdown_capacity(-1.0, 0.15))
        self.assertEqual(projected_peak_drawdown(-1.0, 0.0)["projected_drawdown"], -1.0)
