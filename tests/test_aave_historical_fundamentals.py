"""AAVE historical fundamentals panel (Strategy V2.2 Phase D).

The panel derives TVL / borrow / utilization / fee features strictly from
the point-in-time series, and the optional structural factor scores stay
research-gated: default EvidenceContext construction never includes them.
"""

import unittest

from crypto_portfolio.models.evidence import FactorScore
from crypto_portfolio.research.evidence_series import (
    EvidenceContext,
    EvidencePoint,
    ObservationSeries,
    _structural_series,
)
from crypto_portfolio.research.structural_panel import (
    PANEL_FACTORS,
    structural_factors,
    structural_panel,
)


def _series(series_id: str, values: list[tuple[str, float]]) -> ObservationSeries:
    return ObservationSeries(
        series_id, "test.metric", "defillama", "USD", "2026-09-26T00:00:00Z",
        tuple(
            EvidencePoint(observed, value, available)
            for observed, value, available in values
        ),
    )


def _aave_context(include_structural: bool = True) -> EvidenceContext:
    # 200 days of slow growth in TVL, faster borrow growth, steady fees.
    import datetime as dt

    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    tvl, borrowed, fees = [], [], []
    for index in range(200):
        day = (start + dt.timedelta(days=index)).isoformat().replace("+00:00", "Z")
        next_day = (start + dt.timedelta(days=index + 1)).isoformat().replace("+00:00", "Z")
        tvl.append((day, 1_000_000_000 * (1.0 + 0.001 * index), next_day))
        borrowed.append((day, 300_000_000 * (1.0 + 0.002 * index), next_day))
        fees.append((day, 1_000_000.0, next_day))
    series = {
        "defillama:protocol:aave:tvl": _series("defillama:protocol:aave:tvl", tvl),
        "defillama:protocol:aave:borrowed": _series("defillama:protocol:aave:borrowed", borrowed),
        "defillama:fees:aave": _series("defillama:fees:aave", fees),
    }
    return EvidenceContext.from_series(series, include_structural=include_structural)


class AaveFundamentalsTests(unittest.TestCase):
    def test_panel_factors_track_growth_and_utilization(self):
        context = _aave_context()
        as_of = "2024-06-18T00:00:00Z"  # 199 days in, availability shifted 1d
        factors = structural_factors(context, "AAVE", as_of)
        self.assertGreater(factors["protocol_tvl_growth_90d"], 0.05)
        self.assertGreater(
            factors["protocol_borrow_growth_90d"],
            factors["protocol_tvl_growth_90d"],
        )
        self.assertAlmostEqual(factors["protocol_fees_30d_usd"], 30_000_000.0)
        self.assertAlmostEqual(factors["protocol_fees_growth_30d"], 0.0, places=6)
        self.assertAlmostEqual(
            factors["protocol_utilization"],
            factors_borrowed_ratio(context, as_of),
            places=6,
        )
        self.assertGreater(factors["protocol_utilization_change_90d"], 0.0)

    def test_missing_series_keep_factors_missing(self):
        context = EvidenceContext.from_series({}, include_structural=True)
        factors = structural_factors(context, "AAVE", "2024-06-18T00:00:00Z")
        self.assertEqual(
            factors, {name: None for name in PANEL_FACTORS["AAVE"]},
        )

    def test_panel_reports_coverage_diagnostics(self):
        context = _aave_context()
        report = structural_panel(
            context, ["2024-03-01T00:00:00Z", "2024-07-18T00:00:00Z"], symbols=["AAVE"],
        )
        coverage = report["coverage"]["AAVE"]
        self.assertEqual(coverage["moments"], 2)
        self.assertEqual(coverage["moments_with_any_factor"], 2)
        for name in PANEL_FACTORS["AAVE"]:
            # The late moment has full lookback; the early one may not, so
            # every factor is available on at least one of the two moments.
            self.assertGreater(coverage["per_factor_availability"][name], 0.0)

    def test_structural_scores_are_research_gated(self):
        plain = _aave_context(include_structural=False)
        self.assertIsNone(plain.structural)
        self.assertEqual(plain.structural_factor_scores("AAVE", "2024-06-18T00:00:00Z"), {})
        enabled = _aave_context(include_structural=True)
        scores = enabled.structural_factor_scores("AAVE", "2024-06-18T00:00:00Z")
        self.assertIn("fundamentals", scores)
        self.assertIn("onchain", scores)
        for factor in scores.values():
            self.assertIsInstance(factor, FactorScore)
            self.assertTrue(0.0 <= factor.score <= 100.0)

    def test_structural_scores_only_fill_gaps(self):
        context = _aave_context()
        # ETH already has an onchain factor from its chain fees series; a
        # structural factor for ETH (absent by construction) changes nothing.
        eth = context.factor_scores("ETH", "2024-06-18T00:00:00Z")
        self.assertNotIn("fundamentals", eth)

    def test_series_picker_selects_only_available_ids(self):
        picked = _structural_series({
            "defillama:protocol:aave:tvl": _series("defillama:protocol:aave:tvl", [
                ("2024-01-01T00:00:00Z", 1.0, "2024-01-02T00:00:00Z"),
            ]),
        })
        self.assertEqual(list(picked), ["AAVE"])
        self.assertEqual(list(picked["AAVE"]), ["tvl"])


def factors_borrowed_ratio(context: EvidenceContext, as_of: str) -> float:
    series = context.structural["AAVE"]
    borrowed = series["borrowed"].latest_as_of(as_of)
    tvl = series["tvl"].latest_as_of(as_of)
    return borrowed.value / tvl.value


if __name__ == "__main__":
    unittest.main()
