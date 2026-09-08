import unittest

from crypto_portfolio.engine.confidence import (
    calculate_data_confidence,
    calculate_decision_confidence,
    calculate_freshness,
    calculate_regime_confidence,
    signal_consistency_score,
)
from crypto_portfolio.engine.benchmark import build_aligned_benchmark_result
from crypto_portfolio.engine.ledger import build_nav_history_result
from crypto_portfolio.engine.cash_flow import resolve_cash_flow_issue
from crypto_portfolio.models.confidence import ConfidenceCap, ConfidenceResult, confidence_band
from crypto_portfolio.models.policy import load_policy


class ConfidenceTests(unittest.TestCase):
    def test_bands_and_invalid_numbers(self):
        self.assertEqual(confidence_band(0.60), "MEDIUM")
        self.assertEqual(confidence_band(0.80), "HIGH")
        with self.assertRaises(ValueError):
            confidence_band(float("nan"))
        with self.assertRaises(ValueError):
            confidence_band(True)

    def test_freshness_decays_and_expires(self):
        current, status = calculate_freshness(
            "2026-09-08T00:00:00Z",
            as_of="2026-09-08T00:00:00Z",
            max_age_seconds=86400,
            half_life_seconds=3600,
        )
        stale, stale_status = calculate_freshness(
            "2026-09-06T00:00:00Z",
            as_of="2026-09-08T00:00:00Z",
            max_age_seconds=86400,
            half_life_seconds=3600,
        )
        self.assertEqual((current, status), (1.0, "CURRENT"))
        self.assertEqual(stale, 0.0)
        self.assertEqual(stale_status, "STALE")

    def test_data_confidence_keeps_fixed_denominator_and_hard_cap(self):
        policy = load_policy()
        result = calculate_data_confidence(
            [{
                "metric_key": "market.spot_price",
                "value": 100,
                "observed_at": "2026-09-08T00:00:00Z",
                "freshness": "CURRENT",
                "source": "PRIMARY",
                "source_group": "exchange",
                "observation_id": "spot-1",
            }],
            metric_weights={"market.spot_price": 0.5, "market.ma200": 0.5},
            as_of="2026-09-08T00:00:00Z",
            policy=policy,
            hard_critical_metrics=("market.ma200",),
        )
        self.assertLessEqual(result.score, 0.59)
        self.assertIn("HARD_CRITICAL_MISSING", result.cap_reasons)

    def test_regime_and_decision_caps_are_not_boosts(self):
        regime = calculate_regime_confidence(
            {"trend": 1, "volatility": 1, "breadth": 1, "flows": 1, "portfolio_drawdown": 0, "systemic_risk": 1},
            caps=(ConfidenceCap("DRAWDOWN_UNKNOWN", 0.59, "PORTFOLIO", "unknown"),),
        )
        decision = calculate_decision_confidence(
            {"portfolio_data": 1, "regime_confidence": regime, "asset_evidence": 1, "portfolio_accounting": 1, "signal_agreement": 1},
            caps=(ConfidenceCap("SECURITY_UNKNOWN", 0.59, "ACTION:INCREASE", "unknown"),),
        )
        self.assertEqual(regime.band, "LOW")
        self.assertEqual(decision.band, "LOW")
        self.assertIn("ACTION:INCREASE", decision.blocked_actions)

    def test_consistency_does_not_count_same_group_twice(self):
        observations = [
            {"value": 1, "source": "a", "source_group": "same"},
            {"value": 1, "source": "b", "source_group": "same"},
        ]
        self.assertEqual(signal_consistency_score(observations), 0.5)

    def test_confidence_cap_round_trip_keeps_scope(self):
        result = ConfidenceResult(
            1,
            0.9,
            0.59,
            "LOW",
            caps=(ConfidenceCap("SECURITY_UNKNOWN", 0.59, "ACTION:INCREASE", "unknown"),),
        )
        restored = ConfidenceResult.from_mapping(result.as_dict())
        self.assertEqual(restored.caps[0].scope, "ACTION:INCREASE")

    def test_unresolved_nav_is_provisional_and_benchmark_stays_unavailable(self):
        result = build_nav_history_result([
            {"timestamp": "2026-09-01T00:00:00Z", "portfolio_value": 100, "external_cash_flow": 0, "external_cash_flow_type": "NONE"},
            {"timestamp": "2026-09-02T00:00:00Z", "portfolio_value": 150, "external_cash_flow": 0, "external_cash_flow_type": "UNRESOLVED"},
        ])
        self.assertEqual(result.status, "PROVISIONAL")
        self.assertIsNone(result.nav_return)
        self.assertEqual(build_aligned_benchmark_result(result, [100, 110]).benchmark_status, "PROVISIONAL")

    def test_cash_flow_resolution_requires_explicit_user_facts(self):
        resolution = resolve_cash_flow_issue(
            resolution_id="resolution-1",
            snapshot_id="snapshot-1",
            timestamp="2026-09-03T00:00:00Z",
            cash_flow_type="DEPOSIT",
            amount=50,
            rationale="confirmed external transfer",
        )
        self.assertEqual(resolution.cash_flow_type, "DEPOSIT")
        with self.assertRaises(ValueError):
            resolve_cash_flow_issue(
                resolution_id="resolution-2",
                snapshot_id="snapshot-2",
                timestamp="2026-09-03T00:00:00Z",
                cash_flow_type="DEPOSIT",
                amount=0,
                rationale="not enough evidence",
            )


if __name__ == "__main__":
    unittest.main()
