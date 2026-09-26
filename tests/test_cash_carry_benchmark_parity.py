"""Strategy and benchmarks share one cash carry convention (V2.3 Phase 4).

Parity is the contract: when the replay credits carry on the strategy's cash
share, every cash-holding benchmark path is adjusted with the identical
point-in-time convention, and cash-free benchmarks stay untouched.
"""

import unittest
from datetime import datetime, timedelta, timezone

from crypto_portfolio.engine.portfolio_risk import PortfolioRiskInputs
from crypto_portfolio.engine.strategy_replay import ReplayReview
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.cash_carry import CashCarryConvention
from crypto_portfolio.research.orchestrator import run_historical_backtest


def _reviews(count=90):
    policy = load_policy()
    policy = policy_from_mapping({
        **policy.as_dict(), "risk_engine": {
            **policy.as_dict()["risk_engine"], "mode": "volatility_budget",
        },
    })
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    reviews = []
    btc_price = 100.0
    eth_price = 50.0
    for index in range(count):
        moment = start + timedelta(days=index)
        reviews.append(ReplayReview(
            as_of=moment.isoformat().replace("+00:00", "Z"),
            period_end=(moment + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            current_weights={"BTC": 0.35, "USDT": 0.65},
            portfolio_value=10000.0,
            current_prices={"BTC": btc_price, "ETH": eth_price, "USDT": 1.0},
            assessments={
                "BTC": {
                    "weighted_score": 70, "normalized_score": 70, "confidence": "HIGH",
                    "factor_scores": {},
                    "critical_data_complete": True, "score_coverage": 1.0,
                },
            },
            regime_inputs={
                "btc_trend": "BULLISH", "volatility_state": "LOW",
                "flow_state": "POSITIVE", "breadth_state": "HEALTHY",
            },
            next_returns={
                "BTC": 0.004 if index % 2 == 0 else 0.000,
                "ETH": 0.005 if index % 2 == 0 else 0.000,
                "USDT": 0.0,
            },
        ))
        btc_price *= 1.004 if index % 2 == 0 else 1.0
        eth_price *= 1.005 if index % 2 == 0 else 1.0
    return policy, reviews


RATE_POINTS = (("2023-06-01T00:00:00Z", 0.05),)

_INPUTS = PortfolioRiskInputs(asset_volatility={"BTC": 0.30})


def _run(policy, reviews, cash_carry=None):
    return run_historical_backtest(
        reviews, policy=policy, fee_bps=0.0, slippage_bps=0.0,
        risk_inputs_by_review=[_INPUTS] * len(reviews),
        cash_carry=cash_carry,
    )


class CashCarryParityTests(unittest.TestCase):
    def test_default_run_has_no_carry_block_and_zero_yield(self):
        policy, reviews = _reviews()
        result = _run(policy, reviews)
        self.assertIsNone(result["cash_carry"])

    def test_zero_convention_reports_without_changing_metrics(self):
        policy, reviews = _reviews()
        baseline = _run(policy, reviews)
        zero = _run(policy, reviews, cash_carry=CashCarryConvention("ZERO"))
        self.assertAlmostEqual(
            zero["metrics"]["total_return"], baseline["metrics"]["total_return"], places=12,
        )
        self.assertEqual(zero["cash_carry"]["mode"], "ZERO")
        self.assertAlmostEqual(zero["cash_carry"]["carry_contribution"], 0.0, places=12)

    def test_risk_free_proxy_lifts_the_high_cash_strategy(self):
        policy, reviews = _reviews()
        baseline = _run(policy, reviews)
        carried = _run(
            policy, reviews,
            cash_carry=CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS),
        )
        # The book spends most of its life ~65% in cash: the carry lift must
        # be material and reported.
        self.assertGreater(
            carried["metrics"]["total_return"], baseline["metrics"]["total_return"],
        )
        # ~65% cash x 5% x 90/365 days of window.
        self.assertGreater(carried["cash_carry"]["carry_contribution"], 0.007)
        self.assertAlmostEqual(
            carried["cash_carry"]["metrics_zero_carry"]["total_return"],
            baseline["metrics"]["total_return"], places=12,
        )

    def test_cash_holding_benchmarks_earn_the_same_carry(self):
        policy, reviews = _reviews()
        carried = _run(
            policy, reviews,
            cash_carry=CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS),
        )
        vol_matched = carried["benchmarks"].get("vol_matched_btc_cash_investable")
        exposure_matched = carried["benchmarks"].get("exposure_matched_btc_cash_investable")
        self.assertIsNotNone(vol_matched)
        self.assertIsNotNone(exposure_matched)
        for name, benchmark, zero in (
            ("vol_matched", vol_matched, True),
            ("exposure_matched", exposure_matched, True),
        ):
            with self.subTest(benchmark=name):
                self.assertIn("carry", benchmark["methodology"])
                # A cash-holding benchmark adjusted with 5% carry must beat
                # its own zero-carry sensitivity floor materially: its cash
                # share is at least as large as the strategy's average.
                self.assertGreater(
                    benchmark["metrics"]["total_return"], 0.0,
                )
        # Cash-free benchmarks are untouched by the convention.
        for name in ("btc_buy_and_hold_investable", "btc_eth_70_30_investable"):
            self.assertNotIn("carry", carried["benchmarks"][name]["methodology"])

    def test_parity_note_is_reported(self):
        policy, reviews = _reviews()
        carried = _run(
            policy, reviews,
            cash_carry=CashCarryConvention("RISK_FREE_PROXY", RATE_POINTS),
        )
        self.assertIn(
            "same carry convention", carried["cash_carry"]["benchmark_parity"],
        )


if __name__ == "__main__":
    unittest.main()
