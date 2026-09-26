"""Structural point-in-time contracts (Strategy V2.2 Phase D).

Every structural series carries the day+1 publication lag: a row stamped
day D aggregates through the end of that UTC day and cannot be visible to
a review on day D. Parsers are validated against llama payload shapes.
"""

import unittest

from crypto_portfolio.research.evidence_series import (
    EvidencePoint,
    ObservationSeries,
    parse_chain_fees_history_published,
    parse_chain_tvl_history,
    parse_protocol_borrowed_history,
    parse_protocol_tvl_history,
    parse_stablecoin_chain_history,
)


def _series(points):
    return ObservationSeries(
        "test:series", "test.metric", "defillama", "USD",
        "2026-09-26T00:00:00Z", tuple(points),
    )


class PublicationLagTests(unittest.TestCase):
    def test_protocol_tvl_aggregates_base_chains_only(self):
        payload = {"chainTvls": {
            "Ethereum": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 100},
                {"date": 1704153600, "totalLiquidityUSD": 110},
            ]},
            "Avalanche": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 50},
            ]},
            "Ethereum-borrowed": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 999},
            ]},
            "Ethereum-staking": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 777},
            ]},
            "staking": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 555},
            ]},
        }}
        points = parse_protocol_tvl_history(payload)
        self.assertEqual(len(points), 2)
        # 2024-01-01: Ethereum + Avalanche only.
        self.assertAlmostEqual(points[0].value, 150.0)
        self.assertAlmostEqual(points[1].value, 110.0)
        self.assertEqual(points[0].observed_at, "2024-01-01T00:00:00Z")
        self.assertEqual(points[0].available_at, "2024-01-02T00:00:00Z")

    def test_protocol_borrowed_sums_borrowed_entries(self):
        payload = {"chainTvls": {
            "Ethereum": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 100},
            ]},
            "Ethereum-borrowed": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 60},
            ]},
            "Avalanche-borrowed": {"tvl": [
                {"date": 1704067200, "totalLiquidityUSD": 40},
            ]},
        }}
        points = parse_protocol_borrowed_history(payload)
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0].value, 100.0)

    def test_chain_tvl_rows_carry_publication_lag(self):
        payload = [
            {"date": 1704067200, "tvl": 148988798},
            {"date": 1704153600, "tvl": 151000000},
        ]
        points = parse_chain_tvl_history(payload)
        self.assertAlmostEqual(points[0].value, 148988798.0)
        self.assertEqual(points[0].available_at, "2024-01-02T00:00:00Z")

    def test_published_fees_reuse_the_fees_parser_shape(self):
        payload = {"totalDataChart": [[1704067200, 131], [1704153600, 334]]}
        points = parse_chain_fees_history_published(payload)
        self.assertAlmostEqual(points[0].value, 131.0)
        self.assertEqual(points[0].available_at, "2024-01-02T00:00:00Z")

    def test_stablecoin_chain_history_publishes_next_day(self):
        payload = [{"date": 1704067200, "totalCirculatingUSD": {"peggedUSD": 2_000_000}}]
        points = parse_stablecoin_chain_history(payload)
        self.assertAlmostEqual(points[0].value, 2_000_000.0)
        self.assertEqual(points[0].available_at, "2024-01-02T00:00:00Z")

    def test_malformed_payloads_fail_closed(self):
        with self.assertRaises(ValueError):
            parse_protocol_tvl_history({"nope": 1})
        with self.assertRaises(ValueError):
            parse_protocol_tvl_history({"chainTvls": {}})
        with self.assertRaises(ValueError):
            parse_chain_tvl_history([{"date": 1}])
        with self.assertRaises(ValueError):
            parse_chain_tvl_history([])


class AvailabilityContractTests(unittest.TestCase):
    def _series_with_lag(self):
        # Observed day D, available D+1 (2024-01-02).
        return _series([
            EvidencePoint("2024-01-01T00:00:00Z", 100.0, "2024-01-02T00:00:00Z"),
            EvidencePoint("2024-01-02T00:00:00Z", 110.0, "2024-01-03T00:00:00Z"),
            EvidencePoint("2024-01-03T00:00:00Z", 120.0, "2024-01-04T00:00:00Z"),
        ])

    def test_a_review_never_sees_same_day_aggregates(self):
        series = self._series_with_lag()
        self.assertIsNone(series.latest_as_of("2024-01-01T12:00:00Z"))
        self.assertAlmostEqual(series.latest_as_of("2024-01-02T00:00:00Z").value, 100.0)
        self.assertAlmostEqual(series.latest_as_of("2024-01-03T09:00:00Z").value, 110.0)

    def test_change_over_days_respects_availability(self):
        series = self._series_with_lag()
        # At the 01-03 review the 01-01 point is visible but nothing earlier.
        self.assertIsNone(series.change_over_days("2024-01-03T00:00:00Z", 5))

    def test_evidence_point_rejects_available_before_observed(self):
        with self.assertRaises(ValueError):
            EvidencePoint(
                "2024-01-02T00:00:00Z", 1.0, "2024-01-01T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
