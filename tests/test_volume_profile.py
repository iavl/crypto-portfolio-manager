import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.engine.entry import build_entry_plan, rank_support_zones
from crypto_portfolio.engine.technical import build_structural_zones, build_technical_snapshot
from crypto_portfolio.engine.volume_profile import (
    build_multi_horizon_profiles,
    build_volume_profile,
    profile_levels,
)
from crypto_portfolio.models.execution import PriceZone
from crypto_portfolio.models.market import Candle, OHLCVSeries, SpotPrice
from crypto_portfolio.models.volume_profile import VolumeProfile
from crypto_portfolio.state.market_data import cache_volume_profile, load_volume_profile


def bars(timeframe="1H", values=None):
    values = values or ((100, 10), (110, 40), (120, 10), (110, 35), (100, 8), (120, 12))
    interval = timedelta(hours=1 if timeframe == "1H" else 4 if timeframe == "4H" else 24)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OHLCVSeries(
        "ETH",
        timeframe,
        tuple(
            Candle(
                start + interval * index,
                price - 1,
                price + 1,
                price - 1,
                price,
                volume,
            )
            for index, (price, volume) in enumerate(values)
        ),
        source="synthetic",
        fetched_at="2026-01-02T00:00:00Z",
    )


class VolumeProfileTests(unittest.TestCase):
    def test_timeframes_and_completed_cadence(self):
        for timeframe in ("1H", "4H", "1D"):
            series = bars(timeframe)
            self.assertEqual(series.timeframe, timeframe)
            self.assertEqual(len(series.completed_candles("2026-01-10T00:00:00Z")), len(series.candles))
        with self.assertRaises(ValueError):
            OHLCVSeries("ETH", "15M", bars().candles)

    def test_profile_conserves_volume_and_has_deterministic_poc_value_area(self):
        profile = build_volume_profile(
            bars(), lookback_days=1, price_bins=5, value_area_fraction=0.70,
            as_of="2026-01-02T00:00:00Z",
        )
        self.assertIsNotNone(profile)
        self.assertAlmostEqual(sum(item.volume for item in profile.bins), profile.total_volume)
        self.assertAlmostEqual(sum(item.volume_fraction for item in profile.bins), 1.0)
        self.assertEqual(profile.poc, profile.bins[2].midpoint)
        self.assertLessEqual(profile.value_area_low, profile.poc)
        self.assertGreaterEqual(profile.value_area_high, profile.poc)
        self.assertEqual(VolumeProfile.from_mapping(profile.as_dict()), profile)
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "volume-profile.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(profile.as_dict()))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_hvn_lvn_merge_and_daily_confidence_cap(self):
        profile = build_volume_profile(
            bars("1H", ((100, 2), (101, 38), (102, 37), (115, 2), (116, 38), (117, 37), (130, 2))),
            lookback_days=1,
            price_bins=7,
            minimum_node_separation_atr=1.0,
            atr_value=1,
            as_of="2026-01-02T00:00:00Z",
        )
        self.assertGreaterEqual(len(profile.high_volume_nodes), 2)
        self.assertTrue(all(node.kind == "HVN" for node in profile.high_volume_nodes))
        self.assertTrue(all(node.kind == "LVN" for node in profile.low_volume_nodes))
        daily = build_volume_profile(
            bars("1D"), lookback_days=1, price_bins=5, as_of="2026-01-10T00:00:00Z"
        )
        self.assertIn(daily.data_confidence, {"LOW", "MEDIUM"})
        self.assertNotEqual(daily.data_confidence, "HIGH")

    def test_replay_excludes_future_bars_and_profile_cache_is_immutable(self):
        cutoff = "2026-01-01T05:00:00Z"
        historical = bars("1H")
        with_future = OHLCVSeries(
            "ETH", "1H", historical.candles + (Candle("2026-01-01T10:00:00Z", 1, 1000, 1, 900, 9999),), source="synthetic", fetched_at=historical.fetched_at
        )
        first = build_volume_profile(historical, lookback_days=1, price_bins=8, as_of=cutoff)
        replay = build_volume_profile(with_future, lookback_days=1, price_bins=8, as_of=cutoff)
        self.assertEqual(first, replay)
        with tempfile.TemporaryDirectory() as directory:
            cache_volume_profile(first, directory)
            self.assertEqual(load_volume_profile(first.profile_hash, directory), first)
            # Different provenance is different content: it gets its own entry.
            changed = replace(first, source="other", profile_hash=None)
            self.assertNotEqual(changed.profile_hash, first.profile_hash)
            cache_volume_profile(changed, directory)
            self.assertEqual(
                load_volume_profile(changed.profile_hash, directory), changed
            )

    def test_multi_horizon_levels_and_zone_confluence(self):
        series = bars("4H", tuple((100 + (index % 4) * 2, 10 + index) for index in range(80)))
        profiles = build_multi_horizon_profiles(series, lookback_days=(1, 2))
        self.assertEqual(set(profiles), {1, 2})
        levels = profile_levels(profiles, atr_value=5)
        self.assertTrue(any(source == "VOLUME_POC" for _, source, _ in levels))
        zone = build_structural_zones(
            130,
            10,
            moving_averages={"MA50": 120},
            volume_levels=((120, "VOLUME_POC", 70), (121, "VOLUME_POC", 70), (120, "VOLUME_PROFILE_CONFLUENCE", 80)),
        )[0]
        self.assertIn("VOLUME_POC", zone.sources)
        self.assertIn("VOLUME_PROFILE_CONFLUENCE", zone.sources)
        baseline = PriceZone(118, 122, kind="SUPPORT", strength=70, sources=("MA50",))
        confluence = PriceZone(
            118, 122, kind="SUPPORT", strength=70,
            sources=("MA50", "VOLUME_POC", "VOLUME_PROFILE_CONFLUENCE"),
        )
        baseline_snapshot = replace(
            build_technical_snapshot(
                replace(bars("1D", tuple((100 + index, 100) for index in range(365))), fetched_at="2027-01-01T00:00:00Z"),
                SpotPrice("ETH", 130, "2027-01-01T00:00:00Z", "synthetic", "2027-01-01T00:00:00Z"),
            ),
            support_zones=(baseline,),
        )
        confluence_snapshot = replace(baseline_snapshot, support_zones=(confluence,))
        self.assertGreater(rank_support_zones(confluence_snapshot)[0][1], rank_support_zones(baseline_snapshot)[0][1])

    def test_profile_fields_flow_into_snapshot_and_entry_plan(self):
        daily = replace(bars("1D", tuple((100 + index, 100) for index in range(365))), fetched_at="2027-01-01T00:00:00Z")
        hourly = replace(bars("4H", tuple((100 + (index % 10), 100 + index) for index in range(365))), fetched_at="2027-01-01T00:00:00Z")
        spot = SpotPrice("ETH", 465, "2027-01-01T00:00:00Z", "synthetic", "2027-01-01T00:00:00Z")
        snapshot = build_technical_snapshot(daily, spot, profile_series=hourly)
        self.assertIsNotNone(snapshot.volume_profile_hash)
        self.assertIn(snapshot.volume_profile_confidence, {"HIGH", "MEDIUM", "LOW", "UNAVAILABLE"})
        self.assertIn("volume_profile_poc", snapshot.technical_summary())
        support = PriceZone(460, 462, kind="SUPPORT", strength=90, sources=("MA50", "VOLUME_POC", "VOLUME_HVN"))
        enriched = replace(snapshot, support_zones=(support,), setup_quality=85, volume_state="SUPPORTIVE")
        plan = build_entry_plan("ETH", 1000, enriched, "NORMAL", "HIGH")
        self.assertEqual(plan.action, "INCREASE")
        self.assertTrue({"MA50", "VOLUME_POC", "VOLUME_HVN"}.issubset(plan.tranches[0].structural_sources))
        self.assertLessEqual(max(rank_support_zones(enriched)[0][1], 100), 100)

    def test_profile_alone_or_far_support_cannot_create_risk(self):
        daily = replace(bars("1D", tuple((100 + index, 100) for index in range(365))), fetched_at="2027-01-01T00:00:00Z")
        snapshot = build_technical_snapshot(
            daily,
            SpotPrice("ETH", 465, "2027-01-01T00:00:00Z", "synthetic", "2027-01-01T00:00:00Z"),
        )
        profile_only = replace(
            snapshot,
            support_zones=(PriceZone(460, 462, kind="SUPPORT", strength=100, sources=("VOLUME_POC", "VOLUME_HVN")),),
            setup_quality=100,
        )
        self.assertEqual(build_entry_plan("ETH", 1000, profile_only, "NORMAL", "HIGH").action, "WAIT")
        self.assertEqual(build_entry_plan("ETH", 0, profile_only, "NORMAL", "HIGH").action, "WAIT")
        far = replace(
            snapshot,
            support_zones=(PriceZone(400, 405, kind="SUPPORT", strength=100, sources=("MA200", "VOLUME_HVN")),),
            setup_quality=100,
        )
        self.assertEqual(build_entry_plan("ETH", 1000, far, "NORMAL", "HIGH").action, "WAIT")


if __name__ == "__main__":
    unittest.main()
