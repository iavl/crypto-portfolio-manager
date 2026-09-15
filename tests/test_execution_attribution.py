"""Explicit execution sizing attribution (post-PR#3 semantics fix).

The 2026-09-15 review reported BTC "staged execution closes 50% of the
5.54pp gap" while the approved dollars only closed ~34.8% of it.  The root
cause was never a hidden 0.70 multiplier: staged stable-sleeve sells fund
less than the staged core increases, and the funding competition truncated
the smaller INCREASE without any attribution or rationale note.

These tests pin the single-pass sizing pipeline
(strategic gap -> staged gap -> composed deployment allowance -> executable
amount -> funding-constrained approved amount) and require every layer to
explain itself in the ``ExecutionSizingAttribution``.
"""

import unittest

from crypto_portfolio.engine.confidence import compose_deployment_factors
from crypto_portfolio.engine.rebalance import (
    ExecutionSizingAttribution,
    recommend_rebalance,
)


def _action(result, symbol):
    return next(action for action in result.actions if action.symbol == symbol)


# 2026-09-15T01:05Z snapshot review, copied from the runtime decision record
# (decision 20260915T010500Z-snapshot-review).  Sums are exactly 1.0 within
# 1e-9; portfolio_value reproduces the runtime post-cash total.
RUNTIME_CURRENT = {
    "BTC": 0.4277272492680106,
    "ETH": 0.17079868920284316,
    "AAVE": 0.13117362528844614,
    "BNB": 6.930156262387156e-05,
    "U": 0.10710456741193115,
    "USDT": 0.13898328621891207,
    "USD1": 0.020140151436550895,
    "USDC": 0.004003129610682178,
}
RUNTIME_TARGET = {
    "BTC": 0.4831273201114048,
    "ETH": 0.24180337832597132,
    "AAVE": 0.125,
    "BNB": 6.930156262387156e-05,
    "U": 0.059451643612156546,
    "USDT": 0.0771468948523353,
    "USD1": 0.011179402843623202,
    "USDC": 0.0022220586917849417,
}
RUNTIME_PORTFOLIO_VALUE = 75178.68


class Fixture20260915Tests(unittest.TestCase):
    def test_btc_discrepancy_is_funding_truncation_not_a_hidden_factor(self):
        result = recommend_rebalance(
            RUNTIME_CURRENT,
            RUNTIME_TARGET,
            RUNTIME_PORTFOLIO_VALUE,
            regime="NORMAL",
        )
        btc = _action(result, "BTC")
        eth = _action(result, "ETH")
        self.assertEqual(btc.action, "INCREASE")
        self.assertEqual(eth.action, "INCREASE")

        # Staging closes 50% of each core gap: BTC 5.54pp -> 2.77pp staged.
        self.assertAlmostEqual(btc.sizing_attribution.strategic_gap, 0.0554, places=4)
        self.assertAlmostEqual(btc.sizing_attribution.staging_gap_close_fraction, 0.5, places=6)
        self.assertAlmostEqual(btc.sizing_attribution.staged_gap, 0.0277, places=4)
        self.assertTrue(btc.staging_applied)

        # No deployment allowance applies: decision confidence was HIGH and
        # no per-symbol caps were supplied.  The 34.8% effective close must
        # come entirely from funding competition, and be explained.
        self.assertEqual(btc.sizing_attribution.effective_deployment_factor, 1.0)
        self.assertEqual(dict(btc.sizing_attribution.deployment_factors), {})
        self.assertAlmostEqual(
            btc.sizing_attribution.executable_amount_usd,
            abs(btc.sizing_attribution.executable_gap) * RUNTIME_PORTFOLIO_VALUE,
            places=6,
        )
        self.assertGreater(btc.sizing_attribution.funding_shortfall_usd, 0.0)
        self.assertAlmostEqual(
            btc.sizing_attribution.approved_amount_usd, 1446.61169, places=4
        )
        self.assertAlmostEqual(
            btc.sizing_attribution.effective_strategic_gap_close, 0.3475, places=3
        )
        self.assertIn("fund", btc.rationale)
        self.assertIn("executable amount", btc.rationale)

        # ETH (the larger staged increase) is funded in full and needs no
        # funding note.
        self.assertEqual(eth.sizing_attribution.funding_shortfall_usd, 0.0)
        self.assertAlmostEqual(eth.amount_usd, 2669.01940, places=4)
        self.assertAlmostEqual(eth.sizing_attribution.effective_strategic_gap_close, 0.5, places=6)
        self.assertNotIn("fund", eth.rationale)

        # Dollars conserve: with no external new cash, the approved buys are
        # funded exactly by the stable sleeve's staged reductions.
        reconciliation = result.reconciliation
        self.assertEqual(reconciliation["external_new_cash"], 0.0)
        self.assertEqual(reconciliation["planned_sells"], 0.0)
        stable_reductions = sum(
            action.amount_usd
            for action in result.actions
            if action.action == "REDUCE" and action.symbol in {"U", "USDT", "USD1", "USDC"}
        )
        self.assertAlmostEqual(
            reconciliation["planned_buys"], stable_reductions, places=6
        )
        self.assertAlmostEqual(
            reconciliation["residual_stablecoin_change"], -stable_reductions, places=6
        )

        # AAVE stays HOLD: 13.12% vs the 12.5% strategic target is a 0.62pp
        # deviation, inside the hold band.
        aave = _action(result, "AAVE")
        self.assertEqual(aave.action, "HOLD")
        self.assertIsNone(aave.sizing_attribution.funding_available_usd)

    def test_every_action_carries_attribution(self):
        result = recommend_rebalance(
            RUNTIME_CURRENT,
            RUNTIME_TARGET,
            RUNTIME_PORTFOLIO_VALUE,
            regime="NORMAL",
        )
        for action in result.actions:
            attribution = action.sizing_attribution
            self.assertIsInstance(attribution, ExecutionSizingAttribution)
            self.assertAlmostEqual(
                attribution.strategic_gap,
                attribution.strategic_target_weight - attribution.current_weight,
                places=12,
            )
            self.assertEqual(
                attribution.deployment_composition_mode, "minimum_cap"
            )
            # The final close fraction always describes the real move.
            expected_close = (
                abs(attribution.execution_target_weight - attribution.current_weight)
                / abs(attribution.strategic_gap)
                if abs(attribution.strategic_gap) > 1e-12
                else 0.0
            )
            self.assertAlmostEqual(
                attribution.effective_strategic_gap_close, expected_close, places=12
            )
            round_tripped = ExecutionSizingAttribution.from_mapping(attribution.as_dict())
            self.assertEqual(round_tripped, attribution)


class SinglePassPipelineTests(unittest.TestCase):
    def test_fifty_percent_staging_closes_exactly_half_when_unconstrained(self):
        # 5.54pp gap, ample stable funding, no deployment caps: the executable
        # gap must be exactly 50% of the strategic gap.
        result = recommend_rebalance(
            {"BTC": 0.4277, "USDT": 0.5723},
            {"BTC": 0.4831, "USDT": 0.5169},
            100000.0,
            regime="NORMAL",
        )
        btc = _action(result, "BTC")
        self.assertAlmostEqual(btc.sizing_attribution.staging_gap_close_fraction, 0.5)
        self.assertAlmostEqual(
            btc.sizing_attribution.executable_gap, btc.sizing_attribution.strategic_gap * 0.5
        )
        self.assertEqual(btc.sizing_attribution.funding_shortfall_usd, 0.0)
        self.assertAlmostEqual(btc.sizing_attribution.approved_amount_usd, btc.amount_usd)
        self.assertAlmostEqual(btc.sizing_attribution.effective_strategic_gap_close, 0.5)

    def test_named_deployment_cap_applies_exactly_once(self):
        # A real additional 70% allowance: executable gap = staged * 0.70,
        # applied exactly once, with the source named in the attribution.
        result = recommend_rebalance(
            {"BTC": 0.4277, "USDT": 0.5723},
            {"BTC": 0.4831, "USDT": 0.5169},
            100000.0,
            regime="NORMAL",
            deployment_caps={"BTC": 0.70},
        )
        btc = _action(result, "BTC")
        self.assertEqual(
            dict(btc.sizing_attribution.deployment_factors), {"deployment_allowance": 0.70}
        )
        self.assertEqual(btc.sizing_attribution.effective_deployment_factor, 0.70)
        self.assertAlmostEqual(
            btc.sizing_attribution.executable_gap,
            btc.sizing_attribution.strategic_gap * 0.5 * 0.70,
        )
        self.assertAlmostEqual(btc.sizing_attribution.effective_strategic_gap_close, 0.35)
        self.assertIn("deployment allowance caps", btc.rationale)

    def test_decision_confidence_cap_is_named_and_applied_once(self):
        # MEDIUM decision confidence (0.70 factor) reaches rebalance through a
        # single named factor; the funding pool sees the reduced demand, so a
        # capped increase cannot also be funding-truncated by its own cap.
        result = recommend_rebalance(
            {"BTC": 0.4277, "USDT": 0.5723},
            {"BTC": 0.4831, "USDT": 0.5169},
            100000.0,
            regime="NORMAL",
            decision_confidence={"score": 0.7, "band": "MEDIUM"},
        )
        btc = _action(result, "BTC")
        self.assertEqual(
            dict(btc.sizing_attribution.deployment_factors), {"decision_confidence": 0.70}
        )
        self.assertAlmostEqual(
            btc.sizing_attribution.executable_gap,
            btc.sizing_attribution.strategic_gap * 0.5 * 0.70,
        )
        self.assertEqual(btc.sizing_attribution.funding_shortfall_usd, 0.0)

    def test_confidence_and_allowance_cannot_both_apply(self):
        # Both parameters can express the same deployment cap on the same
        # dollar basis; supplying both would consume it twice.
        with self.assertRaises(ValueError):
            recommend_rebalance(
                {"BTC": 0.4277, "USDT": 0.5723},
                {"BTC": 0.4831, "USDT": 0.5169},
                100000.0,
                decision_confidence={"score": 0.9},
                deployment_caps={"BTC": 0.7},
            )

    def test_hard_exit_overshoots_gap_with_full_attribution(self):
        # A broken-thesis EXIT sells the entire position even when the
        # strategic gap is tiny; the attribution reports the overshoot
        # honestly instead of bounding the close fraction at 1.
        result = recommend_rebalance(
            {"AAVE": 0.12, "USDT": 0.88},
            {"AAVE": 0.115, "USDT": 0.885},
            10000.0,
            thesis_broken=["AAVE"],
        )
        aave = _action(result, "AAVE")
        self.assertEqual(aave.action, "EXIT")
        self.assertEqual(aave.action_reason, "THESIS_BROKEN")
        self.assertFalse(aave.sizing_attribution.staging_enabled)
        self.assertAlmostEqual(aave.amount_usd, 1200.0)
        self.assertGreater(aave.sizing_attribution.effective_strategic_gap_close, 1.0)
        self.assertAlmostEqual(aave.execution_target_weight, 0.0)


class DeploymentCompositionTests(unittest.TestCase):
    def test_minimum_cap_mode_selects_the_smallest_allowance(self):
        self.assertEqual(
            compose_deployment_factors(
                {"confidence": 1.0, "positioning": 0.75, "event": 0.5}
            ),
            0.5,
        )

    def test_empty_composition_is_unconstrained(self):
        self.assertEqual(compose_deployment_factors({}), 1.0)

    def test_factors_are_bounded_and_named(self):
        with self.assertRaises(ValueError):
            compose_deployment_factors({"a": 1.5})
        with self.assertRaises(ValueError):
            compose_deployment_factors({"": 0.5})
        with self.assertRaises(ValueError):
            compose_deployment_factors(
                {"mode": 0.5},
                policy={"execution": {"deployment_factor_composition": "bogus"}},
            )

    def test_multiplicative_mode_requires_explicit_policy(self):
        policy = {"execution": {"deployment_factor_composition": "multiplicative"}}
        self.assertAlmostEqual(
            compose_deployment_factors({"a": 0.5, "b": 0.7}, policy=policy), 0.35
        )
        self.assertAlmostEqual(
            compose_deployment_factors({"a": 0.5, "b": 0.7}), 0.5
        )


if __name__ == "__main__":
    unittest.main()
