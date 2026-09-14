"""Continuous satellite score-to-target curve regressions (phase 1/2).

The score-to-strategic-target mapping must be continuous and monotonic for a
fixed holding state, regime, risk tier, confidence, and event state.  The
2026-09-14 review exposed two structural defects these tests pin:

- F1/8A: a held satellite crossing ``satellite_entry_score`` used to lose its
  entire target (HOLD-current below, score-strength 0 above);
- 8C: ``high_beta x 0.5`` used to multiply every score-derived target instead
  of capping maximum exposure.
"""

import unittest

from crypto_portfolio.engine.allocation import (
    build_target_allocation,
    satellite_eligibility,
    satellite_target_fraction,
)
from crypto_portfolio.models.policy import load_policy

CORE = {
    "BTC": {"weighted_score": 80, "confidence": "HIGH"},
    "ETH": {"weighted_score": 80, "confidence": "HIGH", "relative_strength_vs_btc": 70},
}
CURRENT = {"BTC": 0.5, "ETH": 0.2, "USDT": 0.2, "SOL": 0.1}

# Fixed external conditions for every monotonicity probe: evidence-complete
# held SOL, NORMAL regime, normal risk tier, HIGH confidence, no events.
def _sol_target(score: float, *, held: bool = True, risk_tier: str | None = None) -> float:
    weights = dict(CURRENT if held else {"BTC": 0.5, "ETH": 0.3, "USDT": 0.2})
    assessment = {
        "weighted_score": score,
        "confidence": "HIGH",
        "relative_strength_vs_btc": "OUTPERFORM",
    }
    if risk_tier is not None:
        assessment["risk_tier"] = risk_tier
    result = build_target_allocation(
        regime="NORMAL",
        assessments={**CORE, "SOL": assessment},
        current_weights=weights,
    )
    return result.target_weights.get("SOL", 0.0)


SCORE_GRID = (50, 56.9, 57, 60, 62, 64, 66.9, 67, 70, 73.88, 80, 85, 90)


class SatelliteTargetFractionTests(unittest.TestCase):
    def test_curve_is_monotonic_non_decreasing_across_the_score_domain(self):
        targets = [_sol_target(score) for score in SCORE_GRID]
        for earlier, later in zip(targets, targets[1:]):
            self.assertGreaterEqual(
                later + 1e-12, earlier,
                msg=f"target fell as score rose: {list(zip(SCORE_GRID, targets))}",
            )

    def test_curve_is_continuous_at_the_entry_breakpoint(self):
        # The 2026-09-14 defect: 66.9 kept the held 10% while 67 collapsed to
        # 0.  Adjacent points must sit on the same curve.
        below = _sol_target(66.9)
        above = _sol_target(67.0)
        self.assertGreater(above, 0.0)
        self.assertLess(above - below, 0.02)  # adjacent curve points, not a cliff

    def test_curve_is_continuous_at_every_configured_breakpoint(self):
        policy = load_policy()
        entry = policy.allocation["satellite_entry_score"]
        full = policy.allocation["satellite_full_score"]
        for breakpoint in (57.0, 62.0, entry, full):
            left = _sol_target(breakpoint - 0.01)
            right = _sol_target(breakpoint + 0.01)
            self.assertLess(abs(right - left), 0.01, msg=f"discontinuity near {breakpoint}")

    def test_held_target_never_collapses_to_zero_above_soft_exit(self):
        # Exactly at the soft-exit floor the curve is 0 by design; every
        # score strictly above it keeps a positive held target.
        self.assertEqual(_sol_target(57.0), 0.0)
        for score in (57.1, 60, 62, 64, 66.9, 67, 70, 85):
            with self.subTest(score=score):
                self.assertGreater(_sol_target(score), 0.0)

    def test_unheld_satellite_below_entry_has_no_strategic_target(self):
        # Nothing to hold or reduce: the strategic target stays 0 until the
        # entry score allows new risk.
        self.assertEqual(_sol_target(64, held=False), 0.0)
        self.assertGreater(_sol_target(67, held=False), 0.0)


class SatelliteTargetCurveFunctionTests(unittest.TestCase):
    """Unit tests for the pure piecewise-linear fraction helper."""

    CURVE = {"soft_exit_fraction": 0.0, "exit_fraction": 0.20, "entry_fraction": 0.40, "full_fraction": 1.0}

    def test_breakpoints_and_interpolation(self):
        cases = {
            50: 0.0,
            57: 0.0,
            59.5: 0.10,
            62: 0.20,
            64.5: 0.30,
            67: 0.40,
            76: 0.70,
            85: 1.0,
            90: 1.0,
        }
        for score, expected in cases.items():
            with self.subTest(score=score):
                self.assertAlmostEqual(
                    satellite_target_fraction(
                        score,
                        soft_exit_score=57,
                        exit_score=62,
                        entry_score=67,
                        full_score=85,
                        curve=self.CURVE,
                    ),
                    expected,
                )

    def test_bounds_and_monotonicity(self):
        previous = -1.0
        score = 0.0
        while score <= 100.0001:
            fraction = satellite_target_fraction(
                score,
                soft_exit_score=57,
                exit_score=62,
                entry_score=67,
                full_score=85,
                curve=self.CURVE,
            )
            self.assertGreaterEqual(fraction, 0.0)
            self.assertLessEqual(fraction, 1.0)
            self.assertGreaterEqual(fraction + 1e-12, previous)
            previous = fraction
            score += 0.25

    def test_invalid_curve_shapes_are_rejected(self):
        for curve in (
            {"soft_exit_fraction": -0.1, "exit_fraction": 0.2, "entry_fraction": 0.4, "full_fraction": 1.0},
            {"soft_exit_fraction": 0.0, "exit_fraction": 0.5, "entry_fraction": 0.4, "full_fraction": 1.0},
            {"soft_exit_fraction": 0.0, "exit_fraction": 0.2, "entry_fraction": 0.4, "full_fraction": 1.2},
            {"soft_exit_fraction": 0.0, "exit_fraction": 0.2, "entry_fraction": 0.4, "full_fraction": 0.9},
        ):
            with self.subTest(curve=curve):
                with self.assertRaises(ValueError):
                    satellite_target_fraction(
                        70, soft_exit_score=57, exit_score=62, entry_score=67, full_score=85, curve=curve,
                    )

    def test_invalid_score_breakpoints_are_rejected(self):
        with self.assertRaises(ValueError):
            satellite_target_fraction(
                70, soft_exit_score=62, exit_score=57, entry_score=67, full_score=85, curve=self.CURVE,
            )
        with self.assertRaises(ValueError):
            satellite_target_fraction(
                70, soft_exit_score=57, exit_score=62, entry_score=90, full_score=85, curve=self.CURVE,
            )


class SatelliteCurvePolicyTests(unittest.TestCase):
    def test_canonical_policy_defines_the_curve(self):
        policy = load_policy()
        curve = policy.allocation["satellite_target_curve"]
        self.assertEqual(curve["full_fraction"], 1.0)
        self.assertLessEqual(curve["soft_exit_fraction"], curve["exit_fraction"])
        self.assertLessEqual(curve["exit_fraction"], curve["entry_fraction"])
        self.assertLessEqual(curve["entry_fraction"], curve["full_fraction"])


class RiskTierCapTests(unittest.TestCase):
    """Risk tier caps maximum exposure; it never scales mid-score targets."""

    def _aave(self, score: float = 73.88):
        # The 2026-09-14 review asset: high_beta tier, held ~12.9%.
        return build_target_allocation(
            regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 65.52, "confidence": "HIGH"},
                "ETH": {"weighted_score": 83.78, "confidence": "HIGH", "relative_strength_vs_btc": 70},
                "AAVE": {
                    "weighted_score": score, "confidence": "HIGH",
                    "risk_tier": "high_beta", "relative_strength_vs_btc": "OUTPERFORM",
                },
            },
            current_weights={"BTC": 0.44, "ETH": 0.24, "AAVE": 0.129, "USDT": 0.191},
        )

    def test_high_beta_target_comes_from_the_cap_not_a_half_scaled_curve(self):
        result = self._aave()
        allowance = result.deployment_allowances["AAVE"]
        # Raw score target 0.6293 of the 25% envelope; high_beta cap 0.5 of
        # the envelope binds: 4.78% (score-strength x 0.5) is gone.
        self.assertAlmostEqual(allowance["raw_score_target_weight"], 0.157333, places=5)
        self.assertAlmostEqual(allowance["risk_tier_cap_weight"], 0.125)
        self.assertTrue(allowance["risk_cap_applied"])
        self.assertAlmostEqual(allowance["requested_strategic_weight"], 0.125)
        self.assertAlmostEqual(result.target_weights["AAVE"], 0.125)

    def test_cap_does_not_halve_below_cap_score_targets(self):
        result = self._aave(score=68)
        allowance = result.deployment_allowances["AAVE"]
        # Curve at 68 is 0.4333 of the envelope = 10.83%, below the 12.5%
        # high-beta cap: the target must NOT be scaled to ~5.4%.
        self.assertAlmostEqual(allowance["raw_score_target_weight"], 0.108333, places=5)
        self.assertFalse(allowance["risk_cap_applied"])
        self.assertAlmostEqual(allowance["requested_strategic_weight"], 0.108333, places=5)
        self.assertAlmostEqual(result.target_weights["AAVE"], 0.108333, places=5)

    def test_high_beta_strategic_target_stays_monotonic_and_under_the_cap(self):
        previous = -1.0
        for score in SCORE_GRID:
            with self.subTest(score=score):
                result = self._aave(score=score)
                target = result.target_weights.get("AAVE", 0.0)
                self.assertLessEqual(target, 0.125 + 1e-9)
                self.assertGreaterEqual(target + 1e-12, previous)
                previous = target

    def test_normal_tier_keeps_the_full_envelope_available(self):
        result = self._aave()
        result_normal = build_target_allocation(
            regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 65.52, "confidence": "HIGH"},
                "ETH": {"weighted_score": 83.78, "confidence": "HIGH", "relative_strength_vs_btc": 70},
                "AAVE": {
                    "weighted_score": 73.88, "confidence": "HIGH",
                    "risk_tier": "normal", "relative_strength_vs_btc": "OUTPERFORM",
                },
            },
            current_weights={"BTC": 0.44, "ETH": 0.24, "AAVE": 0.129, "USDT": 0.191},
        )
        allowance = result_normal.deployment_allowances["AAVE"]
        self.assertFalse(allowance["risk_cap_applied"])
        self.assertAlmostEqual(result_normal.target_weights["AAVE"], 0.157333, places=5)
        self.assertGreater(result_normal.target_weights["AAVE"], result.target_weights["AAVE"])


class RelativeStrengthGateTests(unittest.TestCase):
    """Two-threshold BTC-relative gate: moderate weakness is not a hard block."""

    COMMON = {"weighted_score": 85, "confidence": "HIGH", "critical_data_complete": True}

    def test_numeric_threshold_split(self):
        cases = {
            20: "INELIGIBLE",
            29.9: "INELIGIBLE",
            30: "HOLD_OR_REDUCE",
            40: "HOLD_OR_REDUCE",
            49.9: "HOLD_OR_REDUCE",
            50: "ELIGIBLE_INCREASE",
            70: "ELIGIBLE_INCREASE",
        }
        for relative, expected in cases.items():
            with self.subTest(relative=relative):
                # Held: the moderate band keeps the position reducible; the
                # unheld case receives no target at all.
                self.assertEqual(
                    satellite_eligibility(
                        {**self.COMMON, "relative_strength_vs_btc": relative}, current_weight=0.10
                    ),
                    expected,
                )
                unheld = "INELIGIBLE" if expected != "ELIGIBLE_INCREASE" else expected
                self.assertEqual(
                    satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": relative}),
                    unheld,
                )

    def test_missing_relative_evidence_is_fail_defensive(self):
        self.assertEqual(
            satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": None}, current_weight=0.10),
            "HOLD_OR_REDUCE",
        )
        self.assertEqual(
            satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": None}),
            "INELIGIBLE",
        )

    def test_string_states_map_onto_the_same_bands(self):
        self.assertEqual(
            satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": "MATERIALLY_WEAK"}),
            "INELIGIBLE",
        )
        self.assertEqual(
            satellite_eligibility(
                {**self.COMMON, "relative_strength_vs_btc": "UNDERPERFORM"}, current_weight=0.10
            ),
            "HOLD_OR_REDUCE",
        )
        self.assertEqual(
            satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": "NEUTRAL"}),
            "ELIGIBLE_INCREASE",
        )
        self.assertEqual(
            satellite_eligibility({**self.COMMON, "relative_strength_vs_btc": "OUTPERFORM"}),
            "ELIGIBLE_INCREASE",
        )

    def test_moderate_weakness_preserves_a_held_target_without_new_risk(self):
        def target(relative, held):
            weights = {"BTC": 0.5, "ETH": 0.3, "SOL": 0.1, "USDT": 0.1} if held else {
                "BTC": 0.5, "ETH": 0.3, "USDT": 0.2,
            }
            result = build_target_allocation(
                regime="NORMAL",
                assessments={
                    "BTC": {"weighted_score": 80, "confidence": "HIGH"},
                    "ETH": {"weighted_score": 80, "confidence": "HIGH", "relative_strength_vs_btc": 70},
                    "SOL": {**self.COMMON, "relative_strength_vs_btc": relative},
                },
                current_weights=weights,
            )
            return result.target_weights.get("SOL", 0.0), result.deployment_allowances["SOL"]

        held_target, held_allowance = target(40, held=True)
        self.assertAlmostEqual(held_target, 0.25)  # score 85 rides the curve
        self.assertEqual(held_allowance["eligibility_state"], "HOLD_OR_REDUCE")
        self.assertEqual(held_allowance["deployment_factor"], 0.0)
        unheld_target, unheld_allowance = target(40, held=False)
        self.assertEqual(unheld_target, 0.0)
        self.assertEqual(unheld_allowance["eligibility_state"], "INELIGIBLE")

    def test_severe_weakness_hard_blocks_at_any_score(self):
        result = build_target_allocation(
            regime="NORMAL",
            assessments={
                "BTC": {"weighted_score": 80, "confidence": "HIGH"},
                "ETH": {"weighted_score": 80, "confidence": "HIGH", "relative_strength_vs_btc": 70},
                "SOL": {**self.COMMON, "relative_strength_vs_btc": 20},
            },
            current_weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.1, "USDT": 0.1},
        )
        self.assertEqual(result.target_weights.get("SOL", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
