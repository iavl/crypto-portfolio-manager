"""Tests for harvested evidence series and their replay factor proxies."""

import json
import unittest
from pathlib import Path

from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.research.evidence_series import (
    EvidenceContext,
    EvidencePoint,
    ObservationSeries,
    btc_valuation_score,
    capital_flows_score,
    etf_net_to_aum,
    load_evidence_series,
    macro_liquidity_score,
    market_flow_state,
    onchain_score,
    parse_chain_fees_history,
    parse_coinmetrics_mvrv_history,
    parse_etf_flow_history,
    parse_fred_vintage_history,
    parse_stablecoin_supply_history,
)
from crypto_portfolio.research.historical_builder import build_historical_reviews

CONFIG = Path(__file__).resolve().parents[1] / "config" / "policy.json"


def _series(series_id: str, metric: str, source: str, unit: str, rows) -> ObservationSeries:
    return ObservationSeries(
        series_id, metric, source, unit, "2026-01-01T00:00:00Z",
        tuple(EvidencePoint(observed, value, available) for observed, value, available in rows),
    )


class ObservationSeriesTests(unittest.TestCase):
    def setUp(self):
        self.series = _series("t", "m", "s", "u", [
            ("2026-01-01T00:00:00Z", 100.0, None),
            ("2026-01-02T00:00:00Z", 102.0, None),
            ("2026-01-03T00:00:00Z", 101.0, None),
            ("2026-01-04T00:00:00Z", 104.0, "2026-01-05T00:00:00Z"),
        ])

    def test_as_of_selection_respects_availability(self):
        self.assertIsNone(self.series.latest_as_of("2025-12-31T23:59:59Z"))
        point = self.series.latest_as_of("2026-01-04T00:00:00Z")
        self.assertEqual(point.value, 101.0)
        point = self.series.latest_as_of("2026-01-05T00:00:00Z")
        self.assertEqual(point.value, 104.0)

    def test_change_over_days(self):
        growth = self.series.change_over_days("2026-01-05T00:00:00Z", 3)
        self.assertAlmostEqual(growth, 104.0 / 100.0 - 1.0, places=12)

    def test_sum_over_days(self):
        total = self.series.sum_over_days("2026-01-05T00:00:00Z", 2)
        self.assertAlmostEqual(total, 101.0 + 104.0, places=9)

    def test_validation(self):
        with self.assertRaises(ValueError):
            ObservationSeries("t", "m", "s", "u", "f", ())
        with self.assertRaises(ValueError):
            _series("t", "m", "s", "u", [
                ("2026-01-02T00:00:00Z", 1.0, None),
                ("2026-01-01T00:00:00Z", 2.0, None),
            ])
        with self.assertRaises(ValueError):
            EvidencePoint("2026-01-02T00:00:00Z", 1.0, "2026-01-01T00:00:00Z")
        # Publication lag is legal: available at or after observed.
        EvidencePoint("2026-01-01T00:00:00Z", 1.0, "2026-01-03T00:00:00Z")
        with self.assertRaises(ValueError):
            EvidencePoint("2026-01-01T00:00:00Z", float("nan"))
        with self.assertRaises(ValueError):
            self.series.change_over_days("2026-01-05T00:00:00Z", 0)

    def test_roundtrip_and_hash(self):
        clone = ObservationSeries.from_mapping(json.loads(json.dumps(self.series.as_dict())))
        self.assertEqual(clone.content_hash, self.series.content_hash)


class ParserTests(unittest.TestCase):
    def test_stablecoin_supply_history(self):
        payload = [
            {"date": 1767225600, "totalCirculatingUSD": {"peggedUSD": 1.5e11}},
            {"date": 1767312000, "totalCirculatingUSD": {"peggedUSD": 1.51e11}},
            {"date": 1767398400, "totalCirculatingUSD": {}},
        ]
        points = parse_stablecoin_supply_history(payload)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].observed_at, "2026-01-01T00:00:00Z")
        self.assertEqual(points[1].value, 1.51e11)

    def test_chain_fees_history(self):
        payload = {"totalDataChart": [[1767225600000, 5.0e6], [1767312000000, 5.5e6]]}
        points = parse_chain_fees_history(payload)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[1].value, 5.5e6)

    def test_etf_flow_history_skips_pending_rows(self):
        payload = {"code": "0", "data": [
            {"date": "2026-01-05", "totalNetInflow": None, "totalNetAssets": 4.0e10},
            {"date": "2026-01-06", "totalNetInflow": 2.5e8, "totalNetAssets": 4.02e10},
        ]}
        flows, aum = parse_etf_flow_history(payload)
        self.assertEqual(len(flows), 1)
        self.assertAlmostEqual(flows[0].value, 2.5e8)
        self.assertAlmostEqual(aum[0].value, 4.02e10)

    def test_fred_vintage_history_keeps_first_publication(self):
        payload = {"observations": [
            {"date": "2026-01-03", "value": "5.33", "realtime_start": "2026-01-06", "realtime_end": "2026-01-12"},
            {"date": "2026-01-03", "value": "5.35", "realtime_start": "2026-01-13", "realtime_end": "9999-12-31"},
            {"date": "2026-01-10", "value": ".", "realtime_start": "2026-01-13", "realtime_end": "9999-12-31"},
            {"date": "2026-01-10", "value": "5.40", "realtime_start": "2026-01-14", "realtime_end": "9999-12-31"},
        ]}
        points = parse_fred_vintage_history(payload)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].available_at, "2026-01-06T00:00:00Z")
        self.assertEqual(points[0].value, 5.33)
        # The revision published on the 13th never existed at decision time.
        self.assertEqual(self.assertFirstPublished(points, "2026-01-12T00:00:00Z"), 5.33)
        self.assertEqual(self.assertFirstPublished(points, "2026-01-14T00:00:00Z"), 5.40)

    @staticmethod
    def assertFirstPublished(points, as_of):
        eligible = [p for p in points if (p.available_at or p.observed_at) <= as_of]
        return eligible[-1].value

    def test_coinmetrics_mvrv_history(self):
        payload = {"data": [
            {"time": "2026-01-05", "CapMVRVCur": "1.85"},
            {"time": "2026-01-06", "CapMVRVCur": "1.87"},
            {"time": "2026-01-07"},
        ]}
        points = parse_coinmetrics_mvrv_history(payload)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[1].value, 1.87)


class ProxyScoreTests(unittest.TestCase):
    def test_capital_flows_score(self):
        both = capital_flows_score(0.02, 0.05)
        self.assertAlmostEqual(both[0], 90.0, places=9)
        self.assertEqual(both[1], 0.9)
        stable_only = capital_flows_score(-0.02, None)
        self.assertAlmostEqual(stable_only[0], 30.0, places=9)
        self.assertEqual(stable_only[1], 0.6)
        self.assertIsNone(capital_flows_score(None, 0.05))
        clipped = capital_flows_score(0.20, 0.20)
        self.assertEqual(clipped[0], 100.0)

    def test_macro_liquidity_score(self):
        easing = macro_liquidity_score(0.02, -0.01)
        self.assertAlmostEqual(easing[0], 90.0, places=9)
        tightening = macro_liquidity_score(-0.02, 0.01)
        self.assertAlmostEqual(tightening[0], 10.0, places=9)
        self.assertIsNone(macro_liquidity_score(None, 0.0))

    def test_onchain_score(self):
        score, reliability = onchain_score(0.20)
        self.assertAlmostEqual(score, 70.0, places=9)
        self.assertEqual(reliability, 0.7)

    def test_btc_valuation_score(self):
        self.assertAlmostEqual(btc_valuation_score(0.8)[0], 75.0)
        self.assertAlmostEqual(btc_valuation_score(3.5)[0], 20.0)
        midpoint = btc_valuation_score(2.0)[0]
        self.assertAlmostEqual(midpoint, 75.0 - 27.5, places=9)

    def test_market_flow_state(self):
        self.assertEqual(market_flow_state(0.01, 5.0e8), "POSITIVE")
        self.assertEqual(market_flow_state(-0.01, -5.0e8), "NEGATIVE")
        self.assertEqual(market_flow_state(0.01, -5.0e8), "NEUTRAL")
        self.assertEqual(market_flow_state(0.02, None), "POSITIVE")
        self.assertEqual(market_flow_state(0.01, None), "NEUTRAL")
        self.assertIsNone(market_flow_state(None, 5.0e8))


class EvidenceContextTests(unittest.TestCase):
    def setUp(self):
        self.context = EvidenceContext(
            stablecoin=_series("s", "market.stablecoin_supply", "defillama", "USD", [
                ("2026-01-01T00:00:00Z", 100.0e9, None),
                ("2026-01-15T00:00:00Z", 101.0e9, None),
                ("2026-02-01T00:00:00Z", 103.0e9, None),
            ]),
            etf_flows={
                "BTC": _series("f", "flows.etf_net", "sosovalue", "USD", [
                    ("2026-01-05T00:00:00Z", 1.0e8, None),
                    ("2026-01-20T00:00:00Z", 2.0e8, None),
                    ("2026-02-01T00:00:00Z", 1.5e8, None),
                ]),
            },
            etf_aum={
                "BTC": _series("a", "flows.etf_aum", "sosovalue", "USD", [
                    ("2026-01-05T00:00:00Z", 1.0e10, None),
                    ("2026-02-01T00:00:00Z", 1.05e10, None),
                ]),
            },
            fred={
                "WALCL": _series("w", "macro.walcl", "fred", "index", [
                    ("2025-08-01T00:00:00Z", 6300.0, "2025-08-08T00:00:00Z"),
                    ("2025-11-01T00:00:00Z", 6360.0, "2025-11-08T00:00:00Z"),
                    ("2026-01-15T00:00:00Z", 6450.0, "2026-01-22T00:00:00Z"),
                ]),
                "DFF": _series("d", "macro.dff", "fred", "index", [
                    ("2025-05-01T00:00:00Z", 4.6, "2025-05-02T00:00:00Z"),
                    ("2025-11-01T00:00:00Z", 4.5, "2025-11-02T00:00:00Z"),
                    ("2026-01-30T00:00:00Z", 4.3, "2026-01-31T00:00:00Z"),
                ]),
            },
            mvrv=_series("m", "btc_valuation.mvrv", "coinmetrics", "ratio", [
                ("2026-01-15T00:00:00Z", 2.1, None),
                ("2026-02-01T00:00:00Z", 2.2, None),
            ]),
        )

    def test_btc_gets_the_full_harvested_profile(self):
        scores = self.context.factor_scores("BTC", "2026-02-01T00:00:00Z")
        self.assertEqual(
            set(scores), {"capital_flows", "macro_liquidity", "btc_valuation"},
        )
        for factor, score in scores.items():
            self.assertEqual(score.availability, "AVAILABLE")
            self.assertIsNotNone(score.score)

    def test_other_assets_get_stablecoin_only_capital_flows(self):
        scores = self.context.factor_scores("BNB", "2026-02-01T00:00:00Z")
        self.assertEqual(set(scores), {"capital_flows"})
        self.assertEqual(scores["capital_flows"].reliability, 0.6)

    def test_market_flow_state_uses_stablecoin_and_btc_etf(self):
        self.assertEqual(self.context.market_flow_state("2026-02-01T00:00:00Z"), "POSITIVE")

    def test_before_any_data_no_factors_are_fabricated(self):
        self.assertEqual(self.context.factor_scores("BTC", "2025-01-01T00:00:00Z"), {})
        self.assertIsNone(self.context.market_flow_state("2025-01-01T00:00:00Z"))


class BuilderEvidenceIntegrationTests(unittest.TestCase):
    """The builder must fill harvested factors and complete BTC critical data."""

    @staticmethod
    def _candles(series_id: str, source: str):
        from crypto_portfolio.models.market import Candle, OHLCVSeries
        import datetime as dt
        candles = []
        price = 100.0
        start = dt.datetime(2025, 11, 1, tzinfo=dt.timezone.utc)
        for index in range(120):
            price *= 1.0 + (0.002 if index % 3 else -0.001)
            candles.append(Candle(
                (start + dt.timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                open=price, high=price * 1.01, low=price * 0.99, close=price,
                volume=10.0, completed=True,
            ))
        return OHLCVSeries(series_id, "1D", tuple(candles), source, "2026-02-02T00:00:00Z")

    def test_btc_critical_data_completes_with_harvested_evidence(self):
        policy = load_policy()
        btc = self._candles("BTC", "binance")
        eth = self._candles("ETH", "binance")
        rows = [
            ("2025-11-01T00:00:00Z", 100.0e9, None),
            ("2025-11-20T00:00:00Z", 101.0e9, None),
            ("2025-12-01T00:00:00Z", 102.0e9, None),
            ("2025-12-20T00:00:00Z", 103.0e9, None),
            ("2026-01-05T00:00:00Z", 104.0e9, None),
            ("2026-01-20T00:00:00Z", 105.0e9, None),
            ("2026-02-01T00:00:00Z", 106.0e9, None),
        ]
        evidence = EvidenceContext(
            stablecoin=_series("s", "market.stablecoin_supply", "defillama", "USD", rows),
            etf_flows={"BTC": _series("f", "flows.etf_net", "sosovalue", "USD", [
                ("2026-01-25T00:00:00Z", 3.0e8, None),
                ("2026-01-30T00:00:00Z", 2.0e8, None),
                ("2026-02-01T00:00:00Z", 2.5e8, None),
            ])},
            etf_aum={"BTC": _series("a", "flows.etf_aum", "sosovalue", "USD", [
                ("2026-01-25T00:00:00Z", 1.0e10, None),
                ("2026-02-01T00:00:00Z", 1.05e10, None),
            ])},
            fred={
                "WALCL": _series("w", "macro.walcl", "fred", "index", [
                    ("2025-08-01T00:00:00Z", 6300.0, "2025-08-08T00:00:00Z"),
                    ("2025-11-01T00:00:00Z", 6360.0, "2025-11-08T00:00:00Z"),
                    ("2026-01-15T00:00:00Z", 6450.0, "2026-01-22T00:00:00Z"),
                ]),
                "DFF": _series("d", "macro.dff", "fred", "index", [
                    ("2025-05-01T00:00:00Z", 4.6, "2025-05-02T00:00:00Z"),
                    ("2025-11-01T00:00:00Z", 4.5, "2025-11-02T00:00:00Z"),
                    ("2026-01-30T00:00:00Z", 4.3, "2026-01-31T00:00:00Z"),
                ]),
            },
            mvrv=_series("m", "btc_valuation.mvrv", "coinmetrics", "ratio", [
                ("2026-01-15T00:00:00Z", 2.1, None),
                ("2026-02-01T00:00:00Z", 2.2, None),
            ]),
        )
        reviews = build_historical_reviews(
            daily_by_symbol={"BTC": btc, "ETH": eth},
            execution_by_symbol={"BTC": btc, "ETH": eth},
            execution_timeframe="1D",
            symbols=["BTC", "ETH", "USD"],
            initial_weights={"BTC": 0.6, "ETH": 0.25, "USD": 0.15},
            initial_value=100_000.0,
            start_at="2026-02-01T00:00:00Z",
            end_at="2026-02-02T00:00:00Z",
            policy=policy,
            evidence=evidence,
        )
        self.assertTrue(reviews)
        review = reviews[-1]
        btc_factors = review.assessments["BTC"]["factor_scores"]
        for factor in ("trend", "capital_flows", "macro_liquidity", "btc_valuation"):
            self.assertEqual(btc_factors[factor]["availability"], "AVAILABLE", factor)
        self.assertTrue(review.assessments["BTC"]["critical_data_complete"])
        self.assertIn(review.regime_inputs["flow_state"], {"POSITIVE", "NEUTRAL", "NEGATIVE"})
        # ETH keeps judgmental factors MISSING and critical data incomplete.
        eth_factors = review.assessments["ETH"]["factor_scores"]
        self.assertEqual(eth_factors["capital_flows"]["availability"], "AVAILABLE")
        self.assertFalse(review.assessments["ETH"]["critical_data_complete"])

    def test_without_evidence_the_old_missing_contract_holds(self):
        policy = load_policy()
        btc = self._candles("BTC", "binance")
        eth = self._candles("ETH", "binance")
        reviews = build_historical_reviews(
            daily_by_symbol={"BTC": btc, "ETH": eth},
            execution_by_symbol={"BTC": btc, "ETH": eth},
            execution_timeframe="1D",
            symbols=["BTC", "ETH", "USD"],
            initial_weights={"BTC": 0.6, "ETH": 0.25, "USD": 0.15},
            initial_value=100_000.0,
            start_at="2026-02-01T00:00:00Z",
            end_at="2026-02-02T00:00:00Z",
            policy=policy,
        )
        review = reviews[-1]
        self.assertEqual(
            review.assessments["BTC"]["factor_scores"]["capital_flows"]["availability"],
            "MISSING",
        )
        self.assertFalse(review.assessments["BTC"]["critical_data_complete"])
        self.assertEqual(review.regime_inputs["flow_state"], "UNKNOWN")


class EvidenceDatasetLoadTests(unittest.TestCase):
    def test_hash_mismatch_and_missing_series_fail_closed(self):
        import tempfile
        series = _series("defillama:stablecoins:totalCirculatingUSD", "m", "s", "u", [
            ("2026-01-01T00:00:00Z", 1.0, None),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "series").mkdir()
            (root / "series" / "defillama-stablecoins-totalCirculatingUSD.json").write_text(
                json.dumps(series.as_dict()), encoding="utf-8",
            )
            entry = {
                **series.as_dict(), "series_id": series.series_id,
                "status": "AVAILABLE", "row_count": 1,
                "content_sha256": series.content_hash,
            }
            (root / "manifest.json").write_text(
                json.dumps({"series": [entry]}), encoding="utf-8",
            )
            loaded = load_evidence_series(root)
            self.assertEqual(set(loaded), {series.series_id})
            entry["content_sha256"] = "0" * 64
            (root / "manifest.json").write_text(
                json.dumps({"series": [entry]}), encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_evidence_series(root)

    def test_etf_net_to_aum(self):
        flows = _series("f", "flows.etf_net", "sosovalue", "USD", [
            ("2026-01-20T00:00:00Z", 1.0e8, None),
            ("2026-01-25T00:00:00Z", 1.0e8, None),
            ("2026-01-30T00:00:00Z", 1.0e8, None),
        ])
        aum = _series("a", "flows.etf_aum", "sosovalue", "USD", [
            ("2026-01-30T00:00:00Z", 1.0e10, None),
        ])
        ratio = etf_net_to_aum(flows, aum, "2026-01-30T00:00:00Z", days=30)
        self.assertAlmostEqual(ratio, 3.0e8 / 1.0e10, places=12)


if __name__ == "__main__":
    unittest.main()
