"""Full-pipeline replay harness regressions (phase 10).

The replay must be deterministic, closed-loop, fail-closed on missing
labels, and structurally unable to leak next-period returns into the
decision path.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from crypto_portfolio.engine.strategy_replay import (
    ReplayReview,
    compare_policies,
    load_replay_reviews,
    replay_benchmarks,
    replay_strategy,
)
from crypto_portfolio.models.policy import load_policy

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_replay_basic.json"


def _reviews():
    return load_replay_reviews(json.loads(FIXTURE.read_text(encoding="utf-8")))


class ReplayRecordTests(unittest.TestCase):
    def test_records_parse_and_validate(self):
        reviews = _reviews()
        self.assertEqual(len(reviews), 8)
        self.assertEqual(reviews[0].current_weights["BTC"], 0.45)

    def test_naive_timestamps_and_unknown_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ReplayReview("2026-01-05T12:00:00", {"USDT": 1.0}, 100.0)
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            ReplayReview.from_mapping({
                "as_of": "2026-01-05T12:00:00Z",
                "current_weights": {"USDT": 1.0},
                "portfolio_value": 100.0,
                "future_return_hint": 0.5,
            })

    def test_decision_view_excludes_labels(self):
        view = _reviews()[0].decision_view()
        self.assertFalse(hasattr(view, "next_returns"))
        dumped = json.dumps(view.__dict__, default=str)
        self.assertNotIn("next_returns", dumped)


class ReplayStrategyTests(unittest.TestCase):
    def test_replay_is_deterministic_and_self_consistent(self):
        reviews = _reviews()
        first = replay_strategy(reviews, fee_bps=10, slippage_bps=5)
        second = replay_strategy(reviews, fee_bps=10, slippage_bps=5)
        self.assertEqual(first, second)
        # NAV compounds the per-period returns exactly.
        compounded = 1.0
        for row in first["review_detail"]:
            compounded *= 1.0 + row["period_return"]
        self.assertAlmostEqual(first["final_nav"], compounded, places=9)
        self.assertLess(first["max_drawdown"], 0.0)
        self.assertGreater(first["total_turnover"], 0.0)
        self.assertGreater(first["total_cost"], 0.0)
        self.assertIn("NORMAL", first["regime_counts"])
        self.assertLessEqual(first["average_stable_weight"], 1.0)

    def test_regime_transitions_follow_the_one_notch_cap(self):
        result = replay_strategy(_reviews())
        regimes = [row["regime"] for row in result["review_detail"]]
        for previous, current in zip(regimes, regimes[1:]):
            levels = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}
            self.assertLessEqual(abs(levels[current] - levels[previous]), 1)

    def test_closed_loop_carries_simulated_positions(self):
        # The record's weights stay frozen; after period 0's positive
        # returns the second review must start from the drifted/simulated
        # weights instead of resetting to the record.
        import crypto_portfolio.engine.strategy_replay as module

        original = module.recommend_rebalance
        seen: list[dict[str, float]] = []

        def spy(current, target, value, **kwargs):
            seen.append(dict(current))
            return original(current, target, value, **kwargs)

        with mock.patch.object(module, "recommend_rebalance", side_effect=spy):
            replay_strategy(_reviews()[:3])
        self.assertAlmostEqual(seen[0]["BTC"], 0.45, places=6)
        # Period 0's realized returns were non-zero for every risky sleeve:
        # the second review starts from drifted weights, not the frozen 45%.
        self.assertGreater(abs(seen[1]["BTC"] - 0.45), 1e-6)

    def test_missing_label_for_held_risky_asset_fails_closed(self):
        reviews = list(_reviews())
        damaged = ReplayReview(
            as_of=reviews[3].as_of,
            current_weights=reviews[3].current_weights,
            portfolio_value=reviews[3].portfolio_value,
            assessments=reviews[3].assessments,
            regime_inputs=reviews[3].regime_inputs,
            next_returns={"BTC": -0.05, "ETH": -0.07, "SOL": -0.12},  # AAVE label dropped
        )
        with self.assertRaisesRegex(ValueError, "AAVE"):
            replay_strategy(reviews[:3] + [damaged])

    def test_future_returns_never_reach_the_decision_path(self):
        import crypto_portfolio.engine.strategy_replay as module

        reviews = _reviews()
        functions = ("determine_regime", "build_target_allocation", "recommend_rebalance")
        captured: list[str] = []
        originals = {name: getattr(module, name) for name in functions}

        def make_wrapper(fn):
            def wrapper(*args, **kwargs):
                captured.append(json.dumps({"args": args, "kwargs": kwargs}, default=str))
                return fn(*args, **kwargs)
            return wrapper

        for name in functions:
            setattr(module, name, make_wrapper(originals[name]))
        try:
            replay_strategy(reviews)
        finally:
            for name, fn in originals.items():
                setattr(module, name, fn)
        dumped = "\n".join(captured)
        # Match whole numeric tokens only: a label like 0.04 must not be
        # flagged merely because some drifted weight happens to start with
        # the same digits (e.g. 0.0404...).
        for mark in {"0.04", "-0.095", "0.036"}:
            pattern = re.compile(rf"(?<![0-9.-]){re.escape(mark)}(?![0-9])")
            self.assertIsNone(
                pattern.search(dumped), f"label value {mark} leaked into decision inputs"
            )

    def test_benchmarks_and_policy_comparison_share_periods(self):
        reviews = _reviews()
        result = compare_policies(
            reviews,
            baseline_policy=load_policy(),
            candidate_policy=load_policy(),
            fee_bps=10,
        )
        self.assertAlmostEqual(
            result["baseline"]["final_nav"], result["candidate"]["final_nav"]
        )
        self.assertAlmostEqual(result["excess_return"]["candidate_vs_baseline"], 0.0)
        self.assertIn("total_return", result["benchmarks"]["btc_buy_and_hold"])
        self.assertIn("total_return", result["benchmarks"]["btc_eth_buy_and_hold"])
        benchmarks = replay_benchmarks(reviews)
        self.assertAlmostEqual(
            result["excess_return"]["baseline_vs_btc"],
            result["baseline"]["total_return"] - benchmarks["btc_buy_and_hold"]["total_return"],
        )


class ReplayCliTests(unittest.TestCase):
    def test_cli_runs_on_the_synthetic_fixture(self):
        completed = subprocess.run(
            [sys.executable, "scripts/evaluate_strategy.py", str(FIXTURE), "--fee-bps", "10"],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        for key in (
            "final_nav", "total_return", "max_drawdown", "annualized_volatility",
            "total_turnover", "total_cost", "average_stable_weight", "regime_counts",
            "rebalances", "benchmarks",
        ):
            self.assertIn(key, payload)

    def test_cli_comparison_and_split_selection(self):
        completed = subprocess.run(
            [
                sys.executable, "scripts/evaluate_strategy.py", str(FIXTURE),
                "--candidate", "config/policy.json", "--split", "train",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        self.assertIn("baseline", payload)
        self.assertIn("excess_return", payload)
        self.assertLess(payload["baseline"]["reviews"], 8)


if __name__ == "__main__":
    unittest.main()
