"""Structural no-lookahead guarantees (Strategy V2.2 Phase D).

Appending future observations to a series must not change any earlier
review's factor readings, and unavailable aggregates are invisible to the
review they belong to.
"""

import unittest
import datetime as dt

from crypto_portfolio.research.evidence_series import (
    EvidenceContext,
    EvidencePoint,
    ObservationSeries,
)
from crypto_portfolio.research.structural_panel import structural_factors

UTC = dt.timezone.utc


def _values(count: int, growth: float = 0.001, base: float = 1e9):
    start = dt.datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(count):
        day = (start + dt.timedelta(days=index)).isoformat().replace("+00:00", "Z")
        available = (start + dt.timedelta(days=index + 1)).isoformat().replace("+00:00", "Z")
        rows.append((day, base * (1.0 + growth * index), available))
    return rows


def _context(rows_by_id: dict):
    series = {
        series_id: ObservationSeries(
            series_id, "test.metric", "defillama", "USD",
            "2026-09-26T00:00:00Z",
            tuple(EvidencePoint(*row) for row in rows),
        )
        for series_id, rows in rows_by_id.items()
    }
    return EvidenceContext.from_series(series, include_structural=True)


class NoLookaheadTests(unittest.TestCase):
    def test_appending_future_data_does_not_change_past_reads(self):
        rows = _values(120)
        short = _context({
            "defillama:protocol:aave:tvl": rows,
            "defillama:protocol:aave:borrowed": rows,
            "defillama:fees:aave": _values(120, growth=0.0, base=1e6),
        })
        extended = _context({
            "defillama:protocol:aave:tvl": _values(200),
            "defillama:protocol:aave:borrowed": _values(200),
            "defillama:fees:aave": _values(200, growth=0.0, base=1e6),
        })
        as_of = "2024-04-01T00:00:00Z"
        self.assertEqual(
            structural_factors(short, "AAVE", as_of),
            structural_factors(extended, "AAVE", as_of),
        )

    def test_same_day_aggregate_is_invisible(self):
        # The latest row is observed 2024-04-01, available 2024-04-02: a
        # review at 2024-04-01 must not see it.
        rows = _values(92)  # last row: 2024-04-01 observed
        context = _context({
            "defillama:protocol:aave:tvl": rows,
            "defillama:protocol:aave:borrowed": rows,
            "defillama:fees:aave": _values(92, growth=0.0, base=1e6),
        })
        before = structural_factors(context, "AAVE", "2024-04-01T00:00:00Z")
        after = structural_factors(context, "AAVE", "2024-04-02T00:00:00Z")
        # The 90d growth window shifts by one observation between the two.
        self.assertAlmostEqual(
            before["protocol_tvl_growth_90d"],
            after["protocol_tvl_growth_90d"],
            places=2,
        )
        # The utilization level at the earlier review excludes the 04-01 row.
        tvl = context.structural["AAVE"]["tvl"]
        self.assertAlmostEqual(
            tvl.latest_as_of("2024-04-01T00:00:00Z").value,
            1e9 * (1.0 + 0.001 * 90),
        )

    def test_partial_series_availability_never_fabricates(self):
        # Only fees exist: TVL-derived factors stay MISSING, fees factors work.
        context = _context({
            "defillama:fees:aave": _values(120, growth=0.0, base=1e6),
        })
        factors = structural_factors(context, "AAVE", "2024-04-01T00:00:00Z")
        self.assertIsNone(factors["protocol_tvl_growth_30d"])
        self.assertIsNone(factors["protocol_utilization"])
        self.assertAlmostEqual(factors["protocol_fees_30d_usd"], 30e6)

    def test_unknown_symbol_panel_is_all_missing(self):
        context = _context({})
        factors = structural_factors(context, "SOL", "2024-04-01T00:00:00Z")
        self.assertTrue(all(value is None for value in factors.values()))


if __name__ == "__main__":
    unittest.main()
