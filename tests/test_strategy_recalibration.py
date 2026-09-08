import unittest

from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.factors.trend import calculate_trend_factor
from crypto_portfolio.engine.allocation import build_target_allocation
from crypto_portfolio.engine.rebalance import build_no_trade_attribution, recommend_rebalance
from crypto_portfolio.engine.report_packet import build_report_packet
from crypto_portfolio.engine.regime import RegimeInputs, determine_regime
from crypto_portfolio.engine.technical import _trend_state
from crypto_portfolio.models.market import TechnicalSnapshot
from crypto_portfolio.models.report_packet import ReportPacket


def snapshot(**values):
    defaults = {
        "symbol": "ETH",
        "as_of": "2026-09-08T00:00:00Z",
        "current_spot_price": 100,
        "last_completed_close": 100,
        "history_days": 240,
        "data_quality": "FULL",
        "data_confidence": "HIGH",
        "technical_confidence": "HIGH",
        "history_sufficient": True,
        "market_data_fresh": True,
        "cadence_valid": True,
        "source_known": True,
        "spot_time_valid": True,
        "volume_reliable": True,
        "provenance_consistent": True,
        "data_quality_flags": (),
        "trend_state": "NEUTRAL",
        "volatility_state": "NORMAL",
        "volume_state": "UNKNOWN",
    }
    defaults.update(values)
    return TechnicalSnapshot(**defaults)


class StrategyRecalibrationTests(unittest.TestCase):
    def test_trend_ma_authority_and_coverage(self):
        ma50_up = calculate_trend_factor(snapshot(ma50=90))
        ma50_down = calculate_trend_factor(snapshot(ma50=110))
        ma200_up = calculate_trend_factor(snapshot(ma200=90))
        ma200_down = calculate_trend_factor(snapshot(ma200=110))
        self.assertEqual(ma50_up.score - ma50_down.score, 16)
        self.assertEqual(ma200_up.score - ma200_down.score, 4)

        full = calculate_trend_factor(snapshot(
            ma20=95, ma50=90, ma100=85, ma200=80,
            return_30d=0.05, return_90d=0.10, return_180d=0.20,
        ))
        missing_ma50 = calculate_trend_factor(snapshot(
            ma20=95, ma50=None, ma100=85, ma200=80,
            return_30d=0.05, return_90d=0.10, return_180d=0.20,
        ))
        missing_ma200 = calculate_trend_factor(snapshot(
            ma20=95, ma50=90, ma100=85, ma200=None,
            return_30d=0.05, return_90d=0.10, return_180d=0.20,
        ))
        missing_180d = calculate_trend_factor(snapshot(
            ma20=95, ma50=90, ma100=85, ma200=80,
            return_30d=0.05, return_90d=0.10, return_180d=None,
        ))
        self.assertGreater(full.coverage, missing_ma50.coverage)
        self.assertGreater(missing_ma200.coverage, missing_ma50.coverage)
        self.assertGreater(full.coverage, missing_180d.coverage)

    def test_trend_alignment_and_continuous_momentum(self):
        bullish = snapshot(ma20=110, ma50=100, ma100=90, ma200=130)
        self.assertEqual(_trend_state([100], {"MA20": 110, "MA50": 100, "MA100": 90, "MA200": 130}, 120), "STRONG_UPTREND")
        self.assertGreater(calculate_trend_factor(bullish).score, 50)
        small = calculate_trend_factor(snapshot(return_90d=0.06))
        large = calculate_trend_factor(snapshot(return_90d=0.30))
        self.assertGreater(large.score, small.score)
        negative_small = calculate_trend_factor(snapshot(return_90d=-0.06))
        negative_large = calculate_trend_factor(snapshot(return_90d=-0.30))
        self.assertGreater(negative_small.score, negative_large.score)

    def test_relative_strength_is_30_90_180_and_missing_180_reduces_coverage(self):
        btc = [100.0] * 181
        asset = [100.0 * 1.001**index for index in range(181)]
        complete = calculate_relative_strength(asset, btc, symbol="ETH")
        partial = calculate_relative_strength(asset[:91], btc[:91], symbol="ETH")
        self.assertEqual(set(complete.risk_adjusted_excess_returns), {"30d", "90d", "180d"})
        self.assertEqual(complete.coverage, 1.0)
        self.assertAlmostEqual(partial.coverage, 0.6)
        self.assertIsNone(getattr(complete, "relative_365d", None))

    def test_recent_spike_without_90d_or_180d_confirmation_is_not_strong(self):
        btc = [100.0 * 1.001**index for index in range(181)]
        asset = [100.0 * 0.97**index for index in range(151)]
        base = asset[-1]
        asset.extend(base * 1.02**index for index in range(1, 31))
        result = calculate_relative_strength(asset, btc, symbol="AAVE")
        self.assertGreater(result.relative_30d, 0)
        self.assertLess(result.relative_90d, 0)
        self.assertLess(result.relative_180d, 0)
        self.assertLess(result.score, 50)

    def test_no_trade_attribution_is_deterministic_and_persisted(self):
        result = recommend_rebalance(
            {"BTC": 0.49, "USDT": 0.51},
            {"BTC": 0.50, "USDT": 0.50},
            1000,
        )
        attribution = result.no_trade_attribution
        self.assertIsNotNone(attribution)
        self.assertEqual(attribution.primary_reason, "TARGET_DELTA_BELOW_HOLD_BAND")
        self.assertEqual(attribution.allocation_delta_gate, "BLOCKED")
        self.assertIn("NO_APPROVED_INCREASE", attribution.secondary_reasons)

        explicit = build_no_trade_attribution(
            {"BTC": 0.49, "USDT": 0.51},
            {"BTC": 0.50, "USDT": 0.50},
            decision_confidence={"score": 0.9, "band": "HIGH"},
        )
        packet = build_decision_review_packet(
            market_regime="NORMAL",
            current_weights={"BTC": 0.49, "USDT": 0.51},
            target_weights={"BTC": 0.50, "USDT": 0.50},
            assessments={"BTC": {"weighted_score": 90, "confidence": "HIGH"}},
            no_trade_attribution=explicit,
        )
        self.assertEqual(packet.no_trade_attribution, explicit)
        report = build_report_packet(packet)
        self.assertEqual(ReportPacket.from_mapping(report.as_dict()).no_trade_attribution, explicit)

    def test_severe_event_remains_a_primary_no_trade_gate(self):
        attribution = build_no_trade_attribution(
            {"SOL": 0.0, "USDT": 1.0},
            {"SOL": 0.0, "USDT": 1.0},
            assessments={
                "SOL": {
                    "weighted_score": 100,
                    "confidence": "HIGH",
                    "event_risk": {"state": "SEVERE"},
                    "relative_strength_vs_btc": "OUTPERFORM",
                }
            },
            decision_confidence={"score": 0.9, "band": "HIGH"},
        )
        self.assertEqual(attribution.primary_reason, "EVENT_RISK_BLOCK")
        self.assertEqual(attribution.event_gate, "BLOCKED")

    def test_confidence_event_and_drawdown_safety_scenarios(self):
        common = {
            "weighted_score": 100,
            "relative_strength_vs_btc": "OUTPERFORM",
        }
        self.assertEqual(
            build_target_allocation(
                assessments={"SOL": {**common, "confidence": "LOW"}}
            ).target_weights.get("SOL", 0),
            0,
        )
        self.assertEqual(
            build_target_allocation(
                assessments={"SOL": {**common, "confidence": "HIGH", "event_risk": {"state": "SEVERE"}}}
            ).target_weights.get("SOL", 0),
            0,
        )
        self.assertEqual(
            determine_regime(RegimeInputs("HEALTHY", "LOW", -0.09, "NEUTRAL", "HEALTHY", False)).regime,
            "DEFENSIVE",
        )
        self.assertEqual(
            determine_regime(RegimeInputs("HEALTHY", "LOW", -0.12, "NEUTRAL", "HEALTHY", False)).regime,
            "CAPITAL_PRESERVATION",
        )


if __name__ == "__main__":
    unittest.main()
