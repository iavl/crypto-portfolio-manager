"""Signal ownership diagnostics (Strategy V2 Phase 3)."""

import unittest

from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.signal_ownership import signal_ownership_report
from crypto_portfolio.models.policy import load_policy


class SignalOwnershipReportTests(unittest.TestCase):
    def test_every_signal_has_a_primary_owner(self):
        report = signal_ownership_report(load_policy())
        for row in report["signals"]:
            self.assertNotEqual(row["primary_owner"], "unassigned", row["signal"])

    def test_residual_overlaps_are_flagged_not_hidden(self):
        report = signal_ownership_report(load_policy())
        flagged = set(report["multiple_policy_authority_signals"])
        # The two known residuals after the Phase 3 authority reduction.
        self.assertIn("trend_momentum", flagged)
        self.assertIn("macro_liquidity", flagged)

    def test_hard_risk_signals_have_exactly_one_consumer(self):
        report = signal_ownership_report(load_policy())
        by_signal = {row["signal"]: row for row in report["signals"]}
        for signal in ("event_security", "liveness"):
            row = by_signal[signal]
            self.assertEqual(row["consumers"], ["hard_risk_gate"])
            self.assertFalse(row["multiple_policy_authority"])

    def test_report_is_deterministic(self):
        first = signal_ownership_report(load_policy())
        second = signal_ownership_report(load_policy())
        self.assertEqual(first, second)


class RegimeTrendAuthorityTests(unittest.TestCase):
    def test_btc_trend_alone_cannot_leave_the_normal_regime(self):
        # With the Phase 3 weight reduction the trend domain (0.15) cannot
        # push systemic severity past normal_max (0.35) on its own: the
        # regime answers the systemic question, not "did BTC trend well".
        policy = load_policy()
        weight = float(policy.regime_model["domain_weights"]["trend"])
        self.assertLess(weight, 0.35)
        result = determine_regime(
            RegimeInputs(
                btc_trend="BEARISH",
                volatility_state="LOW",
                flow_state="POSITIVE",
                breadth_state="HEALTHY",
                portfolio_drawdown_band=0.0,
            ),
            policy=policy,
            previous="NORMAL",
        )
        self.assertEqual(result.regime, "NORMAL")

    def test_systemic_domains_still_drive_defense(self):
        # Volatility and breadth (the systemic owners) still move the label:
        # extreme volatility + weak breadth + negative flows reaches defense
        # with the trend domain fully bullish.
        policy = load_policy()
        result = determine_regime(
            RegimeInputs(
                btc_trend="BULLISH",
                volatility_state="EXTREME",
                flow_state="NEGATIVE",
                breadth_state="WEAK",
                portfolio_drawdown_band=0.0,
            ),
            policy=policy,
            previous="NORMAL",
        )
        self.assertEqual(result.regime, "DEFENSIVE")

    def test_domain_weights_sum_to_one_with_reduced_trend(self):
        weights = load_policy().regime_model["domain_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertAlmostEqual(weights["trend"], 0.15)
        self.assertGreater(weights["volatility"], weights["trend"])
        self.assertGreater(weights["flows"], weights["trend"])


if __name__ == "__main__":
    unittest.main()
