import math
import unittest

from crypto_portfolio.engine.backtest import (
    buy_and_hold_benchmark,
    constant_weight_rebalanced_benchmark,
)
from crypto_portfolio.engine.benchmark import vol_matched_cash_weight


def _stamps(count):
    return [f"2026-01-{index + 1:02d}T00:00:00Z" for index in range(count)]


def _series(prices):
    """One aligned price point per timestamp, each carrying the same assets."""
    return list(zip(_stamps(len(prices)), prices))


def _flat(values):
    return [{"BTC": value} for value in values]


def _annualized_volatility(returns, periods_per_year):
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance * periods_per_year)


class ConstantWeightRebalancedBenchmarkTests(unittest.TestCase):
    def test_single_asset_matches_buy_and_hold_exactly(self):
        prices = _flat([100.0, 120.0, 90.0, 150.0])
        rebalanced = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 1.0}, initial_value_usd=10000.0,
        )
        holding = buy_and_hold_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 1.0}, initial_value_usd=10000.0,
        )
        self.assertEqual(
            rebalanced["metrics"]["total_return"], holding["metrics"]["total_return"]
        )
        # Float dust must not manufacture rebalance trades.
        self.assertEqual(len(rebalanced["trades"]), 1)

    def test_target_weights_are_restored(self):
        # BTC doubles, then holds still, so the mark after the rebalance must
        # show the target weights exactly.
        points = [(timestamp, {"BTC": price}) for timestamp, price in zip(_stamps(3), [100.0, 200.0, 200.0])]
        result = constant_weight_rebalanced_benchmark(
            prices_by_time=points, weights={"BTC": 0.5, "USD": 0.5}, initial_value_usd=1000.0,
        )
        drifted = result["valuations"][1]["weights"]
        self.assertAlmostEqual(drifted["BTC"], 2.0 / 3.0)
        restored = result["valuations"][2]["weights"]
        self.assertAlmostEqual(restored["BTC"], 0.5, places=12)
        self.assertAlmostEqual(restored["USD"], 0.5, places=12)

    def test_costs_lower_the_return(self):
        prices = _flat([100.0, 200.0, 200.0, 100.0, 100.0])
        free = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 0.6, "USD": 0.4}, initial_value_usd=1000.0,
        )
        charged = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 0.6, "USD": 0.4}, initial_value_usd=1000.0,
            fee_bps=10.0, slippage_bps=5.0,
        )
        self.assertLess(charged["metrics"]["total_return"], free["metrics"]["total_return"])
        self.assertGreater(len(charged["trades"]), 1)

    def test_cash_only_weights_never_trade(self):
        result = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(_flat([100.0, 200.0, 50.0])),
            weights={"USD": 1.0}, initial_value_usd=1000.0,
        )
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["metrics"]["total_return"], 0.0)
        self.assertEqual(result["metrics"]["annualized_volatility"], 0.0)

    def test_rebalance_every_reduces_trade_count(self):
        prices = _flat([100.0, 130.0, 90.0, 140.0, 110.0, 160.0])
        every = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 0.5, "USD": 0.5},
            initial_value_usd=1000.0, rebalance_every=1,
        )
        every_other = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(prices), weights={"BTC": 0.5, "USD": 0.5},
            initial_value_usd=1000.0, rebalance_every=2,
        )
        self.assertGreater(len(every["trades"]), len(every_other["trades"]))

    def test_methodology_states_the_cash_assumption(self):
        result = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(_flat([100.0, 110.0])), weights={"BTC": 1.0},
            initial_value_usd=1000.0,
        )
        self.assertIn("earns nothing", result["methodology"])
        self.assertIn("every 1 aligned price point", result["methodology"])

    def test_invalid_inputs_are_rejected(self):
        points = _series(_flat([100.0, 110.0]))
        with self.assertRaisesRegex(ValueError, "at least two aligned price points"):
            constant_weight_rebalanced_benchmark(
                prices_by_time=points[:1], weights={"BTC": 1.0}, initial_value_usd=1000.0,
            )
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            constant_weight_rebalanced_benchmark(
                prices_by_time=points, weights={"BTC": 0.5}, initial_value_usd=1000.0,
            )
        for invalid in (0, -1, True, 1.5):
            with self.assertRaisesRegex(ValueError, "rebalance_every"):
                constant_weight_rebalanced_benchmark(
                    prices_by_time=points, weights={"BTC": 1.0},
                    initial_value_usd=1000.0, rebalance_every=invalid,
                )
        with self.assertRaisesRegex(ValueError, "missing for ETH"):
            constant_weight_rebalanced_benchmark(
                prices_by_time=points, weights={"BTC": 0.5, "ETH": 0.5}, initial_value_usd=1000.0,
            )

    def test_missing_price_during_rebalance_is_rejected(self):
        points = [
            ("2026-01-01T00:00:00Z", {"BTC": 100.0, "ETH": 10.0}),
            ("2026-01-02T00:00:00Z", {"BTC": 110.0}),
        ]
        with self.assertRaisesRegex(ValueError, "missing held asset price"):
            constant_weight_rebalanced_benchmark(
                prices_by_time=points, weights={"BTC": 0.5, "ETH": 0.5}, initial_value_usd=1000.0,
            )


class VolMatchedCashWeightTests(unittest.TestCase):
    RETURNS = [0.02, -0.03, 0.05, -0.01, 0.04, -0.02, 0.01, 0.03, -0.05, 0.02]

    def test_target_equal_to_btc_volatility_returns_full_weight(self):
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        self.assertEqual(
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, target_volatility=volatility, periods_per_year=365.25
            ),
            1.0,
        )

    def test_target_above_btc_volatility_is_capped(self):
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        self.assertEqual(
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, target_volatility=volatility * 2.0, periods_per_year=365.25
            ),
            1.0,
        )

    def test_zero_target_returns_no_btc(self):
        self.assertEqual(
            vol_matched_cash_weight(btc_returns=self.RETURNS, target_volatility=0.0), 0.0
        )

    def test_riskless_cash_solution_is_the_volatility_ratio(self):
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        target = volatility / 4.0
        weight = vol_matched_cash_weight(
            btc_returns=self.RETURNS, target_volatility=target, periods_per_year=365.25
        )
        self.assertAlmostEqual(weight, target / volatility, places=12)
        self.assertAlmostEqual(
            _annualized_volatility([value * weight for value in self.RETURNS], 365.25),
            target,
            places=12,
        )

    def test_lower_target_gives_lower_weight(self):
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        weights = [
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, target_volatility=volatility * fraction,
                periods_per_year=365.25,
            )
            for fraction in (0.2, 0.5, 0.8)
        ]
        self.assertEqual(weights, sorted(weights))
        self.assertLess(weights[0], weights[-1])

    def test_risky_cash_leg_is_solved_to_the_target(self):
        # A cash leg that moves against BTC gives a finite riskless mix, so the
        # quadratic really does have two reachable roots and the least risky
        # one must win.
        cash = [-0.5 * value for value in self.RETURNS]
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        target = volatility * 0.3
        weight = vol_matched_cash_weight(
            btc_returns=self.RETURNS, cash_returns=cash,
            target_volatility=target, periods_per_year=365.25,
        )
        self.assertAlmostEqual(weight, 2.0 / 15.0, places=10)
        mixed = [
            weight * btc + (1.0 - weight) * cash_value
            for btc, cash_value in zip(self.RETURNS, cash)
        ]
        self.assertAlmostEqual(_annualized_volatility(mixed, 365.25), target, places=10)

    def test_least_volatile_mix_is_returned_when_the_target_is_too_low(self):
        cash = [-0.5 * value for value in self.RETURNS]
        self.assertAlmostEqual(
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, cash_returns=cash, target_volatility=0.0
            ),
            1.0 / 3.0,
            places=10,
        )

    def test_identical_legs_are_undefined(self):
        with self.assertRaisesRegex(ValueError, "identical risk"):
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, cash_returns=self.RETURNS,
                target_volatility=0.1, periods_per_year=365.25,
            )

    def test_tuples_are_accepted(self):
        volatility = _annualized_volatility(self.RETURNS, 365.25)
        self.assertAlmostEqual(
            vol_matched_cash_weight(
                btc_returns=tuple(self.RETURNS), target_volatility=volatility / 2.0,
                periods_per_year=365.25,
            ),
            0.5,
        )

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least two observations"):
            vol_matched_cash_weight(btc_returns=[0.01], target_volatility=0.2)
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, cash_returns=[0.0, 0.0], target_volatility=0.2
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            vol_matched_cash_weight(btc_returns=self.RETURNS, target_volatility=-0.1)
        with self.assertRaisesRegex(ValueError, "must be > 0"):
            vol_matched_cash_weight(
                btc_returns=self.RETURNS, target_volatility=0.2, periods_per_year=0.0
            )
        with self.assertRaisesRegex(ValueError, "below -100%"):
            vol_matched_cash_weight(btc_returns=[0.01, -1.5], target_volatility=0.2)
        with self.assertRaisesRegex(ValueError, "must be finite"):
            vol_matched_cash_weight(btc_returns=[0.01, float("nan")], target_volatility=0.2)


class VolMatchedBenchmarkIntegrationTests(unittest.TestCase):
    def test_rebalanced_benchmark_hits_the_solved_volatility(self):
        returns = VolMatchedCashWeightTests.RETURNS
        prices = [100.0]
        for value in returns:
            prices.append(prices[-1] * (1.0 + value))
        periods_per_year = 365.25
        target = _annualized_volatility(returns, periods_per_year) / 3.0
        weight = vol_matched_cash_weight(
            btc_returns=returns, target_volatility=target, periods_per_year=periods_per_year
        )
        result = constant_weight_rebalanced_benchmark(
            prices_by_time=_series(_flat(prices)),
            weights={"BTC": weight, "USD": 1.0 - weight},
            initial_value_usd=1000.0,
        )
        realized = result["metrics"]["annualized_volatility"]
        # The closed form assumes the same annualization the metrics use, so
        # the benchmark really is risk matched rather than approximately so.
        self.assertAlmostEqual(realized, target, places=10)


class OrchestratorBenchmarkWiringTests(unittest.TestCase):
    """A historical run must publish the two fair comparisons, not just BTC."""

    WINDOW_END = "2024-04-01T00:00:00Z"

    @classmethod
    def _result(cls):
        from datetime import datetime, timedelta, timezone

        from crypto_portfolio.models.market import Candle, OHLCVSeries
        from crypto_portfolio.models.policy import load_policy
        from crypto_portfolio.research.historical_builder import build_historical_reviews
        from crypto_portfolio.research.orchestrator import run_historical_backtest

        daily_start = datetime(2023, 5, 1, tzinfo=timezone.utc)
        daily = {}
        for symbol, base in (("BTC", 30_000), ("ETH", 2_000)):
            candles = []
            for index in range(340):
                drift = 1.0 + 0.02 * ((index % 7) - 3)
                price = base * drift + index * 5
                candles.append(Candle(
                    (daily_start + timedelta(days=index)).isoformat(),
                    price, price * 1.01, price * 0.99, price, 100,
                ))
            daily[symbol] = OHLCVSeries(
                symbol, "1D", tuple(candles), "test", "2026-09-22T00:00:00Z", "TEST", "spot", "USD",
            )
        reviews = build_historical_reviews(
            daily_by_symbol=daily, execution_by_symbol=daily, execution_timeframe="1D",
            symbols=("BTC", "ETH", "USD"), initial_weights={"BTC": 0.3, "ETH": 0.1, "USD": 0.6},
            initial_value=100_000, start_at="2024-01-01T00:00:00Z",
            end_at=cls.WINDOW_END, policy=load_policy(), semantic_score=None,
        )
        return run_historical_backtest(reviews, fee_bps=10, slippage_bps=5)

    def test_fair_benchmarks_are_published(self):
        benchmarks = self._result()["benchmarks"]
        self.assertIn("static_initial_weights_investable", benchmarks)
        self.assertIn("vol_matched_btc_cash_investable", benchmarks)
        self.assertIn("btc_buy_and_hold_investable", benchmarks)

    def test_static_benchmark_buys_the_starting_allocation(self):
        benchmark = self._result()["benchmarks"]["static_initial_weights_investable"]
        buys = {trade["symbol"]: trade for trade in benchmark["trades"]}
        self.assertEqual(set(buys), {"BTC", "ETH"})
        self.assertAlmostEqual(buys["BTC"]["gross_notional_usd"], 30_000.0, delta=200.0)
        self.assertAlmostEqual(buys["ETH"]["gross_notional_usd"], 10_000.0, delta=200.0)
        # Cash is never bought, so the uninvested leg can never pick up a trade.
        self.assertNotIn("USD", buys)

    def test_vol_matched_benchmark_really_matches_the_strategy(self):
        result = self._result()
        target = result["metrics"]["annualized_volatility"]
        matched = result["benchmarks"]["vol_matched_btc_cash_investable"]["metrics"]
        self.assertGreater(target, 0.0)
        # Rebalancing costs are charged after the weight is solved, so the match
        # approaches the target with the sample instead of being exact. Measured
        # on the historical run (995 returns) the gap is ~1e-5.
        self.assertLess(abs(matched["annualized_volatility"] - target), 1e-3)
        self.assertIn("solved to match", result["benchmarks"]["vol_matched_btc_cash_investable"]["methodology"])

    def test_vol_matched_benchmark_is_below_full_btc_exposure(self):
        result = self._result()
        weights = result["benchmarks"]["vol_matched_btc_cash_investable"]["valuations"][-1]["weights"]
        self.assertGreater(weights["BTC"], 0.0)
        self.assertLess(weights["BTC"], 1.0)
        self.assertAlmostEqual(weights["BTC"] + weights["USD"], 1.0, places=9)

    def test_comparison_carries_annualized_and_risk_deltas(self):
        result = self._result()
        comparison = result["benchmark_comparison"]["vol_matched_btc_cash_investable"]
        self.assertAlmostEqual(
            comparison["excess_return_annualized"],
            result["metrics"]["cagr"] - comparison["cagr"],
            places=12,
        )
        self.assertLess(abs(comparison["volatility_delta"]), 1e-3)
        self.assertEqual(
            comparison["sharpe_delta"],
            result["metrics"]["sharpe_rf_zero"] - comparison["sharpe_rf_zero"],
        )
        self.assertIn("annualized_volatility", comparison)

    def test_comparison_keeps_the_cumulative_fields(self):
        result = self._result()
        comparison = result["benchmark_comparison"]["btc_buy_and_hold_zero_cost"]
        self.assertEqual(
            comparison["excess_return"],
            result["metrics"]["total_return"] - comparison["total_return"],
        )
        self.assertIn("maximum_drawdown", comparison)


if __name__ == "__main__":
    unittest.main()
