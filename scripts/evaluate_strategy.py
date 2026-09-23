#!/usr/bin/env python3
"""Offline full-pipeline strategy replay CLI.

Reads frozen review records (JSON) and replays the deterministic
scoring -> regime -> allocation -> staged rebalance pipeline over them
without lookahead, optionally comparing two policy files over identical
periods.  Research tooling only: it never calls live providers, never
places trades, and never auto-optimizes policy parameters.

Examples:

    python3 scripts/evaluate_strategy.py tests/fixtures/strategy_replay_basic.json
    python3 scripts/evaluate_strategy.py reviews.json --policy config/policy.json \\
        --candidate candidate_policy.json --fee-bps 10 --slippage-bps 5 \\
        --split holdout --output metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_portfolio.engine.evaluation import sequential_splits  # noqa: E402
from crypto_portfolio.engine.strategy_replay import (  # noqa: E402
    compare_policies,
    load_replay_reviews,
    replay_benchmarks,
    replay_strategy,
    research_readiness,
)
from crypto_portfolio.models.policy import load_policy  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", help="JSON file of frozen replay review records")
    parser.add_argument("--policy", default=None, help="baseline policy JSON (default: canonical config)")
    parser.add_argument("--candidate", default=None, help="candidate policy JSON for comparison mode")
    parser.add_argument("--fee-bps", type=float, default=0.0, help="fee in basis points on traded notional")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="slippage in basis points")
    parser.add_argument(
        "--split",
        choices=("all", "train", "validation", "holdout"),
        default="all",
        help="chronological segment to replay (calibration belongs on train/validation only)",
    )
    parser.add_argument("--output", default=None, help="write the JSON result here instead of stdout")
    parser.add_argument("--research-variant", choices=("baseline", "volume_mean_3", "confirm_2"),
                        default="baseline", help="offline research variant; never changes canonical policy")
    parser.add_argument("--cost-sensitivity", action="store_true",
                        help="run fixed 0/10/25 bps cost sensitivity")
    return parser.parse_args(argv)


def _select_split(reviews, split: str):
    if split == "all":
        return reviews
    dates = [review.moment.date() for review in reviews]
    if len(set(dates)) < 3:
        raise SystemExit("chronological splits need at least three distinct review dates")
    parts = sequential_splits(sorted(set(dates)))
    chosen = set(parts[split])
    selected = tuple(review for review in reviews if review.moment.date() in chosen)
    if not selected:
        raise SystemExit(f"split {split!r} selected zero reviews; provide more history")
    return selected


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw = json.loads(Path(args.records).read_text(encoding="utf-8"))
    reviews = load_replay_reviews(raw)
    reviews = _select_split(reviews, args.split)
    baseline = load_policy(args.policy) if args.policy else None
    if args.cost_sensitivity:
        result = {str(bps): replay_strategy(reviews, policy=baseline, fee_bps=bps,
                                             slippage_bps=0.0,
                                             research_variant=args.research_variant)
                  for bps in (0.0, 10.0, 25.0)}
    elif args.candidate:
        result = compare_policies(
            reviews,
            baseline_policy=baseline,
            candidate_policy=load_policy(args.candidate),
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            research_variant=args.research_variant,
        )
    else:
        result = replay_strategy(
            reviews, policy=baseline, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            research_variant=args.research_variant
        )
        result["benchmarks"] = replay_benchmarks(reviews)
    if isinstance(result, dict) and "research_readiness" not in result:
        baseline_result = result.get("baseline", result)
        detail = baseline_result.get("review_detail", ()) if isinstance(baseline_result, dict) else ()
        result["research_readiness"] = research_readiness(
            reviews, regimes=[row["regime"] for row in detail if "regime" in row]
        )
    payload = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
