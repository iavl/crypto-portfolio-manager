import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from crypto_portfolio.engine.feasibility import (
    DRAWDOWN_BUDGET_INFEASIBLE,
    ENTRY_LOCKED_BY_COVERAGE,
    REGIME_BELOW_REQUIRED_STABLE_TARGET,
    SCENARIO_CORE_ANCHOR,
    SCENARIO_WORST_ASSET,
    SCORE_THRESHOLD_UNREACHABLE,
    check_policy_feasibility,
    coverage_required_for,
    drawdown_budget_feasibility,
    profile_score_bands,
    score_band,
    score_thresholds,
)
from crypto_portfolio.models.policy import load_policy, policy_from_mapping
from crypto_portfolio.research.validity_gate import (
    COVERAGE_BELOW_INVESTABLE,
    NO_TRADES_IN_WINDOW,
    ONE_WAY_RATCHER,
    SORTING_UNDERPOWERED,
    TRADING_STALLED,
    VERDICT_NOT_A_TEST,
    build_validity_report,
    coverage_summary,
    observed_drawdowns,
    profile_coverage,
    sorting_power,
    trade_direction_summary,
)


class ScoreBandTests(unittest.TestCase):
    """The reachable band is 50 +/- 50 * coverage, pinned to the real run."""

    def test_band_matches_the_observed_run_exactly(self):
        # core/btc readings: coverage 0.35 -> the run's min/max were 32.5/67.5
        self.assertAlmostEqual(score_band(0.35).low, 32.5)
        self.assertAlmostEqual(score_band(0.35).high, 67.5)
        # core/default readings: coverage 0.45 -> the run's min/max were 27.5/72.5
        self.assertAlmostEqual(score_band(0.45).low, 27.5)
        self.assertAlmostEqual(score_band(0.45).high, 72.5)

    def test_full_coverage_gives_the_whole_scale(self):
        band = score_band(1.0)
        self.assertAlmostEqual(band.low, 0.0)
        self.assertAlmostEqual(band.high, 100.0)

    def test_band_contains(self):
        self.assertTrue(score_band(0.5).contains(50.0))
        self.assertTrue(score_band(0.5).contains(75.0))
        self.assertFalse(score_band(0.5).contains(75.5))

    def test_invalid_band_inputs_fail_closed(self):
        for value in (-0.1, 1.1, float("nan"), True, "0.5"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    score_band(value)

    def test_required_coverage_is_the_inverse_of_the_band_top(self):
        self.assertAlmostEqual(coverage_required_for(85.0), 0.70)
        self.assertAlmostEqual(coverage_required_for(67.0), 0.34)
        self.assertAlmostEqual(coverage_required_for(51.0), 0.02)
        for threshold in (50.0, 40.0, 30.0):
            with self.subTest(threshold=threshold):
                self.assertEqual(coverage_required_for(threshold), 0.0)

    def test_invalid_threshold_fails_closed(self):
        for value in (-1.0, 100.1, float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    coverage_required_for(value)


class ScoreThresholdTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.thresholds = {item.name: item for item in score_thresholds(self.policy)}

    def test_canonical_thresholds_are_all_collected(self):
        self.assertEqual(self.thresholds["satellite_entry_score"].value, 67.0)
        self.assertEqual(self.thresholds["satellite_full_score"].value, 85.0)
        self.assertEqual(self.thresholds["relative_strength.increase_min_score"].value, 50.0)
        self.assertEqual(self.thresholds["core_allocation.eth.increase_min_score"].value, 55.0)

    def test_satellite_thresholds_govern_the_satellite_profiles(self):
        threshold = self.thresholds["satellite_entry_score"]
        self.assertEqual(threshold.profiles, ("default", "defi_protocol"))
        self.assertTrue(threshold.entry_style)

    def test_eth_thresholds_do_not_govern_the_btc_profile(self):
        threshold = self.thresholds["core_allocation.eth.increase_min_score"]
        self.assertEqual(threshold.profiles, ("default",))
        self.assertNotIn("btc", threshold.profiles)

    def test_relative_strength_thresholds_skip_profiles_with_zero_weight(self):
        threshold = self.thresholds["relative_strength.increase_min_score"]
        self.assertNotIn("btc", threshold.profiles)

    def test_full_score_is_entry_style_and_exit_scores_are_not(self):
        self.assertTrue(self.thresholds["satellite_full_score"].entry_style)
        self.assertFalse(self.thresholds["satellite_exit_score"].entry_style)
        self.assertFalse(self.thresholds["satellite_soft_exit_score"].entry_style)


class ProfileBandTests(unittest.TestCase):
    def test_profile_bands_accept_a_scalar(self):
        bands = profile_score_bands(load_policy(), 0.45)
        self.assertEqual(sorted(bands), ["btc", "default", "defi_protocol"])
        for band in bands.values():
            self.assertAlmostEqual(band.high, 72.5)

    def test_profile_bands_accept_a_mapping(self):
        bands = profile_score_bands(load_policy(), {"btc": 0.35, "default": 0.45})
        self.assertAlmostEqual(bands["btc"].high, 67.5)
        self.assertAlmostEqual(bands["default"].high, 72.5)
        self.assertAlmostEqual(bands["defi_protocol"].high, 50.0)


class DrawdownBudgetTests(unittest.TestCase):
    """Budget feasibility: the ladder holds what the one-shot projection breaches."""

    def setUp(self):
        self.policy = load_policy()
        rows, findings = drawdown_budget_feasibility(self.policy)
        self.rows = {
            (row["regime"], row["scenario"]): row
            for row in rows if row.get("status") == "AVAILABLE"
        }
        self.findings = findings
        raw = copy.deepcopy(self.policy.as_dict())
        raw["risk"]["drawdown_budget_overlay"]["enabled"] = False
        self.static_policy = policy_from_mapping(raw)
        static_rows, static_findings = drawdown_budget_feasibility(self.static_policy)
        self.static_rows = {
            (row["regime"], row["scenario"]): row
            for row in static_rows if row.get("status") == "AVAILABLE"
        }
        self.static_findings = static_findings

    def test_static_projection_per_regime_without_the_overlay(self):
        # Strategy V2.2: the core composition is the BTC baseline, so the
        # moderate-scenario projection is the risky share times BTC -0.20.
        expected = {
            "NORMAL": -0.17,
            "DEFENSIVE": -0.14,
            "CAPITAL_PRESERVATION": -0.10,
        }
        for regime, value in expected.items():
            with self.subTest(regime=regime):
                row = self.static_rows[(regime, SCENARIO_CORE_ANCHOR)]
                self.assertAlmostEqual(row["projected_drawdown"], value, places=4)

    def test_static_breach_pattern_without_the_overlay(self):
        self.assertTrue(self.static_rows[("NORMAL", SCENARIO_CORE_ANCHOR)]["budget_breach"])
        self.assertFalse(self.static_rows[("DEFENSIVE", SCENARIO_CORE_ANCHOR)]["budget_breach"])
        self.assertFalse(
            self.static_rows[("CAPITAL_PRESERVATION", SCENARIO_CORE_ANCHOR)]["budget_breach"]
        )
        for regime in ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"):
            with self.subTest(regime=regime):
                self.assertTrue(self.static_rows[(regime, SCENARIO_WORST_ASSET)]["budget_breach"])

    def test_ladder_projection_shallows_every_scenario(self):
        # With the overlay enabled the projected drawdown is the stepped
        # ladder path. A reactive overlay earns its keep exactly where the
        # one-shot projection breaches the budget; in shallow scenarios it
        # may overshoot the one-shot number by at most one ladder step. It
        # must stay within the budget on every configured scenario.
        for key, row in self.rows.items():
            with self.subTest(regime=key[0], scenario=key[1]):
                static = row["static_projected_drawdown"]
                if static <= -row["budget"]:
                    self.assertGreater(row["projected_drawdown"], static)
                else:
                    tolerance = row["ladder"]["tolerance"]
                    self.assertGreaterEqual(
                        row["projected_drawdown"], static - tolerance
                    )
                self.assertFalse(row["budget_breach"])
                self.assertIn("ladder", row)
        self.assertFalse(
            [item for item in self.findings if item.code == DRAWDOWN_BUDGET_INFEASIBLE]
        )

    def test_infeasible_findings_are_errors_when_the_ladder_cannot_hold(self):
        codes = [item.code for item in self.static_findings]
        self.assertIn(DRAWDOWN_BUDGET_INFEASIBLE, codes)
        for item in self.static_findings:
            if item.code == DRAWDOWN_BUDGET_INFEASIBLE:
                self.assertEqual(item.severity, "ERROR")
                self.assertIn("scenario_name", item.values)

    def test_required_stable_target_matches_hand_arithmetic(self):
        anchor = self.static_rows[("NORMAL", SCENARIO_CORE_ANCHOR)]
        # 1 - 0.15 / 0.20, with the BTC-baseline core stressed at 1.0*-0.20
        self.assertAlmostEqual(anchor["required_stablecoin_target"], 0.25, places=4)
        worst = self.static_rows[("NORMAL", SCENARIO_WORST_ASSET)]
        self.assertAlmostEqual(worst["required_stablecoin_target"], 0.6250, places=4)

    def test_raising_the_defensive_target_clears_the_breach(self):
        raw = copy.deepcopy(load_policy().as_dict())
        raw["regimes"]["NORMAL"]["stablecoin_target"] = 0.75
        raw["regimes"]["DEFENSIVE"]["stablecoin_target"] = 0.75
        raw["regimes"]["CAPITAL_PRESERVATION"]["stablecoin_target"] = 0.75
        relaxed = policy_from_mapping(raw)
        rows, findings = drawdown_budget_feasibility(relaxed)
        anchor_rows = [
            row for row in rows
            if row.get("status") == "AVAILABLE" and row["scenario"] == SCENARIO_CORE_ANCHOR
        ]
        self.assertTrue(anchor_rows)
        for row in anchor_rows:
            with self.subTest(regime=row["regime"]):
                self.assertFalse(row["budget_breach"])
        self.assertFalse(
            [item for item in findings if item.code == DRAWDOWN_BUDGET_INFEASIBLE]
        )


class CheckPolicyFeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.report = check_policy_feasibility(self.policy, coverage=0.45)

    def test_thin_data_blocks_entries_regardless_of_score(self):
        locked = [
            item for item in self.report.findings if item.code == ENTRY_LOCKED_BY_COVERAGE
        ]
        self.assertEqual(
            sorted(item.subject for item in locked), ["btc", "default", "defi_protocol"]
        )
        self.assertEqual(locked[0].severity, "ERROR")
        self.assertIn("LOW", locked[0].values["zeroed_bands"])

    def test_satellite_full_score_is_out_of_reach_at_observed_coverage(self):
        unreachable = [
            item for item in self.report.findings
            if item.code == SCORE_THRESHOLD_UNREACHABLE
        ]
        names = {item.subject for item in unreachable}
        self.assertIn("satellite_full_score", names)
        entry = next(item for item in unreachable if item.subject == "satellite_full_score")
        self.assertEqual(entry.severity, "ERROR")
        self.assertAlmostEqual(entry.values["required_coverage"], 0.70)

    def test_report_is_not_ok_for_the_canonical_policy(self):
        self.assertFalse(self.report.ok)
        self.assertTrue(self.report.errors)

    def test_coverage_is_skipped_when_not_supplied(self):
        report = check_policy_feasibility(self.policy)
        self.assertEqual(report.score_bands, {})
        self.assertFalse(
            [item for item in report.findings if item.code == ENTRY_LOCKED_BY_COVERAGE]
        )
        # The canonical overlay holds the ladder inside the budget, so the
        # one-shot infeasibility errors are gone; the warning that documents
        # the gap the overlay closes is still present.
        self.assertFalse(
            [item for item in report.findings if item.code == DRAWDOWN_BUDGET_INFEASIBLE]
        )
        self.assertTrue(
            [item for item in report.findings if item.code == REGIME_BELOW_REQUIRED_STABLE_TARGET]
        )

    def test_realized_history_is_compared_against_the_configured_stress(self):
        report = check_policy_feasibility(
            self.policy, realized_drawdown_by_asset={"BTC": -0.5306, "ETH": -0.6759}
        )
        stress = [item for item in report.findings if item.code == "STRESS_UNDERSTATES_REALIZED"]
        self.assertTrue(stress)
        # BTC's ratio (2.65x) exceeds ETH's (2.25x), so BTC is the headline.
        self.assertEqual(stress[0].subject, "BTC")
        self.assertAlmostEqual(stress[0].values["per_asset"]["BTC"]["ratio"], 2.653, places=3)
        self.assertAlmostEqual(stress[0].values["per_asset"]["ETH"]["ratio"], 2.253, places=3)
        realized_rows = [
            row for row in report.drawdown_rows
            if row.get("scenario_name") == "REALIZED_HISTORY"
        ]
        self.assertTrue(realized_rows)
        # What actually happened breaches in one shot; the ladder projection
        # answers whether the overlay would have contained the same path.
        self.assertTrue(all(row["static_projected_drawdown"] <= row["budget"] for row in realized_rows))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _series(symbol: str, closes: list[float]) -> dict:
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": f"2026-01-{index + 1:02d}T00:00:00Z", "close": value}
            for index, value in enumerate(closes)
        ],
    }


def _score_row(
    symbol: str, coverage: float, score: float, reliability: float,
    available: tuple[str, ...] = ("trend",),
) -> dict:
    factor = {"availability": "AVAILABLE", "reliability": reliability, "score": 60.0}
    missing = {"availability": "MISSING", "reliability": 0.0, "score": None}
    names = (
        "trend", "valuation", "fundamentals", "onchain", "capital_flows",
        "relative_strength_btc", "btc_valuation", "macro_liquidity",
    )
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "symbol": symbol,
        "score": score,
        "coverage": coverage,
        "coverage_band": "LOW",
        "factor_scores": {
            name: dict(factor if name in available else missing) for name in names
        },
    }


def _build_run(root: Path, *, sides: list[str], last_trade: str) -> Path:
    _write_json(root / "spec.json", {
        "run_id": "fixture-run", "start_at": "2026-01-01T00:00:00Z",
        "end_at": "2026-12-31T00:00:00Z",
    })
    _write_json(root / "manifest.json", {
        "blockers": [], "strict_ready": True,
        "series": [{"metric": "market.ohlcv", "series_id": "binance:BTC:1D"}],
    })
    rows = [_score_row("BTC", 0.35, 60.0, 1.0), _score_row("ETH", 0.45, 55.0, 1.0)]
    _write_json(root / "score-evaluation.json", {
        "scopes": {
            "core": {
                "rows": rows,
                "horizons": {"180": {
                    "available_samples": 100, "non_overlapping_samples": 5,
                    "pending_samples": 3, "score_forward_spearman": -0.2,
                    "score_relative_spearman": -0.1,
                }},
            }
        }
    })
    _write_json(root / "decision-evaluation.json", {
        "valid_decisions": 0, "excluded_decisions": 3,
        "exclusions": [{"reason": "PolicyError: policy is missing fields: x"}],
    })
    series_dir = root / "series"
    _write_json(series_dir / "normalized-BTC-USD-1D.json", _series("BTC", [100, 120, 60, 90]))
    report_dir = root / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    with (report_dir / "core_core_existing_strict_main_cost.trades.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "side", "gross_notional_usd", "reason"])
        for index, side in enumerate(sides):
            writer.writerow([f"{last_trade}T00:00:0{index}.000001Z", "BTC", side, 1000, "REGIME_DERISK"])
    return root


class TradeDirectionTests(unittest.TestCase):
    def test_missing_log_is_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = trade_direction_summary(Path(directory) / "nope.trades.csv")
        self.assertEqual(summary["status"], "MISSING")

    def test_side_and_reason_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL", "SELL"], last_trade="2026-02-01")
            summary = trade_direction_summary(
                root / "report" / "core_core_existing_strict_main_cost.trades.csv"
            )
        self.assertEqual(summary["trades"], 2)
        self.assertEqual(summary["sides"], {"SELL": 2})
        self.assertEqual(summary["reasons"], {"REGIME_DERISK": 2})
        self.assertEqual(summary["first_trade"], "2026-02-01")


class ObservedDrawdownTests(unittest.TestCase):
    def test_drawdown_is_peak_to_trough(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL"], last_trade="2026-02-01")
            values = observed_drawdowns(root / "series")
        self.assertAlmostEqual(values["BTC"], 60 / 120 - 1.0)

    def test_window_excludes_candles_outside_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL"], last_trade="2026-02-01")
            values = observed_drawdowns(root / "series", ("2026-01-03", "2026-01-04"))
        self.assertAlmostEqual(values["BTC"], 0.0)

    def test_missing_series_directory_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(observed_drawdowns(Path(directory) / "absent"), {})


class ScoreArtifactTests(unittest.TestCase):
    def test_coverage_summary_reports_bands(self):
        payload = {"scopes": {"core": {"rows": [
            {"coverage": 0.35, "coverage_band": "LOW"},
            {"coverage": 0.45, "coverage_band": "LOW"},
        ]}}}
        stats = coverage_summary(payload)["core"]
        self.assertEqual(stats["samples"], 2.0)
        self.assertAlmostEqual(stats["median"], 0.40)
        self.assertEqual(stats["bands"], {"LOW": 2})

    def test_profile_coverage_recomputes_from_reliabilities(self):
        payload = {"scopes": {"core": {"rows": [
            _score_row("BTC", 0.35, 60.0, 1.0),
            _score_row("ETH", 0.30, 55.0, 1.0),
        ]}}}
        result = profile_coverage(payload, load_policy())
        self.assertAlmostEqual(result["core:btc"]["coverage"], 0.35)
        self.assertAlmostEqual(result["core:default"]["coverage"], 0.30)

    def test_relative_strength_availability_lifts_the_default_profile(self):
        payload = {"scopes": {"core": {"rows": [
            _score_row("ETH", 0.45, 55.0, 1.0, ("trend", "relative_strength_btc")),
        ]}}}
        result = profile_coverage(payload, load_policy())
        self.assertAlmostEqual(result["core:default"]["coverage"], 0.45)

    def test_btc_relative_strength_weight_is_zero_so_it_cannot_lift_btc(self):
        payload = {"scopes": {"core": {"rows": [
            _score_row("BTC", 0.35, 60.0, 1.0, ("trend", "relative_strength_btc")),
        ]}}}
        result = profile_coverage(payload, load_policy())
        self.assertAlmostEqual(result["core:btc"]["coverage"], 0.35)

    def test_sorting_power_reads_the_independent_block_count(self):
        payload = {
            "scopes": {
                "core": {
                    "horizons": {
                        "180": {
                            "available_samples": 1632,
                            "non_overlapping_samples": 10,
                            "score_forward_spearman": -0.182,
                        }
                    }
                }
            }
        }
        rows = sorting_power(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["non_overlapping_samples"], 10)
        self.assertEqual(rows[0]["horizon_days"], 180)


class ValidityGateTests(unittest.TestCase):
    def test_one_way_run_is_declared_not_a_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL", "SELL"], last_trade="2026-02-01")
            report = build_validity_report(root)
        codes = {item.code for item in report.findings}
        self.assertIn(ONE_WAY_RATCHER, codes)
        self.assertIn(COVERAGE_BELOW_INVESTABLE, codes)
        self.assertIn(SORTING_UNDERPOWERED, codes)
        self.assertEqual(report.verdict, VERDICT_NOT_A_TEST)
        self.assertFalse(report.errors == ())

    def test_stalled_tail_is_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL", "BUY"], last_trade="2026-02-01")
            report = build_validity_report(root)
        codes = {item.code for item in report.findings}
        self.assertIn(TRADING_STALLED, codes)
        self.assertNotIn(ONE_WAY_RATCHER, codes)

    def test_run_without_trades_is_flagged_and_not_a_ratchet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=[], last_trade="2026-02-01")
            report = build_validity_report(root)
        codes = {item.code for item in report.findings}
        self.assertIn(NO_TRADES_IN_WINDOW, codes)
        self.assertNotIn(ONE_WAY_RATCHER, codes)

    def test_report_surfaces_observed_drawdown_and_feasibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _build_run(Path(directory), sides=["SELL", "BUY"], last_trade="2026-06-01")
            report = build_validity_report(root)
        self.assertAlmostEqual(report.observed_drawdowns["BTC"], -0.5)
        self.assertFalse(report.feasibility["ok"])
        self.assertTrue(report.feasibility["drawdown_rows"])

    def test_empty_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = build_validity_report(Path(directory))
        codes = {item.code for item in report.findings}
        self.assertIn("MANIFEST_MISSING", codes)
        self.assertIn(COVERAGE_BELOW_INVESTABLE, codes)
        self.assertEqual(report.verdict, VERDICT_NOT_A_TEST)


if __name__ == "__main__":
    unittest.main()
