import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.backtest import QuantityLedger, performance_metrics
from crypto_portfolio.engine.execution_replay import simulate_execution_plan
from crypto_portfolio.engine.strategy_replay import load_replay_reviews, replay_strategy
from crypto_portfolio.models.backtest import default_backtest_spec
from crypto_portfolio.models.market import Candle, OHLCVSeries
from crypto_portfolio.models.policy import load_policy, policy_hash
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.binance import BinanceProvider
from crypto_portfolio.providers.fred import FREDProvider
from crypto_portfolio.research.dataset import convert_usdt_series_to_usd
from crypto_portfolio.research.data_audit import audit_ohlcv_series, build_historical_manifest
from crypto_portfolio.research.historical_builder import build_historical_reviews
from crypto_portfolio.research.orchestrator import run_historical_backtest
from crypto_portfolio.research.score_evaluation import evaluate_scores
from crypto_portfolio.research.stress import drawdown_boundary_stress


ROOT = Path(__file__).parents[1]


class BacktestContractTests(unittest.TestCase):
    def test_default_spec_and_manifest_round_trip_schemas(self):
        spec = default_backtest_spec(
            run_id="test", end_at="2026-09-22T00:00:00Z",
            policy_hash=policy_hash(load_policy()), git_sha="68c13b28a05ecc9",
        )
        candles = tuple(
            Candle(
                (datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat(),
                100 + index, 101 + index, 99 + index, 100 + index, 10,
            )
            for index in range(3)
        )
        series = OHLCVSeries("BTC", "1D", candles, "test", "2026-09-22T00:00:00Z", "TEST", "spot", "USD")
        entry = audit_ohlcv_series(series)
        manifest = build_historical_manifest(spec, (entry,))
        for name, value in (
            ("backtest-spec.schema.json", spec.as_dict()),
            ("historical-data-manifest.schema.json", manifest.as_dict()),
        ):
            schema = json.loads((ROOT / "schemas" / name).read_text())
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
            self.assertEqual(errors, [])
        self.assertFalse(manifest.strict_ready)
        self.assertIn("BTC:HOURLY_EXECUTION_OHLCV_REQUIRED", manifest.blockers)

    def test_usdt_without_conversion_is_blocked(self):
        series = OHLCVSeries(
            "BTC", "1D",
            (Candle("2024-01-01T00:00:00Z", 100, 101, 99, 100, 1),),
            "binance", "2024-01-02T00:00:00Z", "BINANCE", "spot", "USDT",
        )
        result = audit_ohlcv_series(series)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("UNVERIFIED_USD_CONVERSION", result.limitations)


class QuantityAccountingTests(unittest.TestCase):
    def test_buy_uses_execution_price_and_conserves_cash(self):
        ledger = QuantityLedger(1_000.0)
        ledger.mark("2024-01-01T00:00:00Z", {"BTC": 100.0})
        trade = ledger.execute(
            timestamp="2024-01-01T01:00:00Z", symbol="BTC", side="BUY",
            amount_usd=500, reference_price=100, fee_bps=10, slippage_bps=20,
            reason="test",
        )
        self.assertAlmostEqual(trade.execution_price, 100.2)
        self.assertAlmostEqual(ledger.quantities["BTC"], 500 / 100.2)
        self.assertAlmostEqual(ledger.cash_usd, 499.5)
        ledger.mark("2024-01-02T00:00:00Z", {"BTC": 90.0})
        metrics = performance_metrics(ledger.valuations)
        self.assertLess(metrics["total_return"], 0)
        self.assertLess(metrics["maximum_drawdown"], 0)

    def test_missing_held_price_fails_closed(self):
        ledger = QuantityLedger(0, {"BTC": 1})
        with self.assertRaisesRegex(ValueError, "BTC"):
            ledger.mark("2024-01-01T00:00:00Z", {})


class ExecutionTimingTests(unittest.TestCase):
    def test_close_confirmation_fills_on_next_bar_open(self):
        plan = {
            "action": "INCREASE", "planned_amount_usd": 100,
            "tranches": [{"sequence": 1, "amount_usd": 100, "price_low": 90,
                          "price_high": 95, "reference_price": 94}],
        }
        result = simulate_execution_plan(plan, (
            {"timestamp": "2024-01-01T01:00:00Z", "open": 100, "high": 101, "low": 93, "close": 94},
            {"timestamp": "2024-01-01T02:00:00Z", "open": 94.5, "high": 96, "low": 93, "close": 95},
        ), decision_as_of="2024-01-01T00:00:00Z")
        fills = [item for item in result["events"] if item["status"] == "FILLED"]
        self.assertEqual(fills[0]["timestamp"], "2024-01-01T02:00:00Z")
        self.assertEqual(fills[0]["fill_price"], 94.5)


class ReplayBoundaryTests(unittest.TestCase):
    def test_replay_uses_own_drawdown_and_explicit_last_period_end(self):
        fixture = json.loads((ROOT / "tests/fixtures/strategy_replay_basic.json").read_text())
        reviews = load_replay_reviews(fixture)
        result = replay_strategy(reviews)
        self.assertEqual(result["review_detail"][0]["portfolio_drawdown_input"], 0.0)
        self.assertNotEqual(
            result["review_detail"][2]["portfolio_drawdown_input"],
            reviews[2].regime_inputs["portfolio_drawdown_band"],
        )
        self.assertIn("explicit as_of-to-period_end", result["assumptions"]["annualization"])


class ProviderHistoryTests(unittest.TestCase):
    def test_usdt_conversion_produces_auditable_usd_series(self):
        asset = OHLCVSeries(
            "BTC", "1H", (Candle("2024-01-01T00:00:00Z", 100, 101, 99, 100, 1),),
            "binance", "2024-01-02T00:00:00Z", "BINANCE", "spot", "USDT",
        )
        fx = OHLCVSeries(
            "USDT", "1H", (Candle("2024-01-01T00:00:00Z", 0.999, 1.001, 0.998, 1.0, 1),),
            "coinbase", "2024-01-02T00:00:00Z", "COINBASE", "spot", "USD",
        )
        converted = convert_usdt_series_to_usd(asset, fx)
        self.assertEqual(converted.quote_currency, "USD")
        self.assertEqual(converted.candles[0].close, 100)
        self.assertEqual(converted.source, "binance+coinbase_fx")

    def test_binance_paginates_historical_range(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, _url, *, params=None, headers=None):
                self.calls.append(dict(params))
                start = params["startTime"]
                count = 1000 if len(self.calls) == 1 else 1
                rows = []
                for index in range(count):
                    opened = start + index * 86_400_000
                    rows.append([opened, "100", "101", "99", "100", "1", opened + 86_400_000 - 1])
                return rows

        client = Client()
        provider = BinanceProvider(client=client, clock=lambda: "2027-01-01T00:00:00Z")
        series = provider.candles(
            "BTC", timeframe="1D", start="2024-01-01T00:00:00Z", end="2026-12-31T00:00:00Z",
        )
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(series.candles), 1001)
        self.assertGreater(client.calls[1]["startTime"], client.calls[0]["startTime"])

    def test_fred_requests_point_in_time_vintage(self):
        class Client:
            def __init__(self):
                self.params = None

            def get_json(self, _url, *, params=None, headers=None):
                self.params = params
                return {"observations": [{"date": "2024-01-01", "value": "1"}]}

        client = Client()
        FREDProvider(client=client, api_key="fake", clock=lambda: "2024-02-01T00:00:00Z").collect(
            ProviderRequest("fred", "macro", "BTC", {"as_of": "2024-01-15T00:00:00Z"}, ("macro.dff",))
        )
        self.assertEqual(client.params["realtime_start"], "2024-01-15")
        self.assertEqual(client.params["realtime_end"], "2024-01-15")


class ResearchDiagnosticsTests(unittest.TestCase):
    def test_strict_historical_path_runs_with_quantity_ledger(self):
        daily_start = datetime(2023, 5, 1, tzinfo=timezone.utc)
        daily = {}
        hourly = {}
        for symbol, base in (("BTC", 30_000), ("ETH", 2_000)):
            daily_candles = tuple(
                Candle(
                    (daily_start + timedelta(days=index)).isoformat(),
                    base + index, base + index + 10, base + index - 10, base + index, 100,
                )
                for index in range(255)
            )
            hourly_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            hourly_candles = tuple(
                Candle(
                    (hourly_start + timedelta(hours=index)).isoformat(),
                    base + 245 + index / 24, base + 246 + index / 24,
                    base + 244 + index / 24, base + 245 + index / 24, 10,
                )
                for index in range(72)
            )
            daily[symbol] = OHLCVSeries(
                symbol, "1D", daily_candles, "test", "2026-09-22T00:00:00Z", "TEST", "spot", "USD",
            )
            hourly[symbol] = OHLCVSeries(
                symbol, "1H", hourly_candles, "test", "2026-09-22T00:00:00Z", "TEST", "spot", "USD",
            )
        reviews = build_historical_reviews(
            daily_by_symbol=daily, hourly_by_symbol=hourly,
            symbols=("BTC", "ETH", "USD"), initial_weights={"USD": 1.0},
            initial_value=100_000, start_at="2024-01-01T00:00:00Z",
            end_at="2024-01-04T00:00:00Z", policy=load_policy(), semantic_score=None,
        )
        result = run_historical_backtest(reviews, fee_bps=10, slippage_bps=5)
        self.assertEqual(result["engine"], "quantity_cash_closed_loop")
        self.assertEqual(result["metrics"]["initial_value_usd"], 100_000)
        self.assertEqual(result["metrics"]["final_value_usd"], 100_000)
        self.assertEqual(result["trades"], [])

    def test_stress_boundaries_are_immediate_and_monotonic(self):
        result = drawdown_boundary_stress()
        self.assertTrue(all(result["checks"].values()))
        self.assertTrue(result["worsening_is_monotonic"])

    def test_score_evaluation_excludes_synthetic_and_reports_pending(self):
        observations = [
            {"timestamp": "2024-01-01T00:00:00Z", "symbol": "BTC", "score": 60,
             "coverage": 1.0, "factor_scores": {}, "synthetic": False},
            {"timestamp": "2024-01-02T00:00:00Z", "symbol": "BTC", "score": 90,
             "coverage": 1.0, "factor_scores": {}, "synthetic": True},
        ]
        prices = {"BTC": [
            {"timestamp": "2024-01-01T00:00:00Z", "price": 100},
            {"timestamp": "2024-02-01T00:00:00Z", "price": 110},
        ]}
        result = evaluate_scores(observations, prices)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["labels"]["30"]["status"], "AVAILABLE")
        self.assertEqual(result["rows"][0]["labels"]["90"]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
