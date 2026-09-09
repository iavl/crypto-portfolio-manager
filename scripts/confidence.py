#!/usr/bin/env python3
"""Read-only confidence/history explanation wrapper."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.state.context import build_history_context


def build_output(data_dir: str | None = None) -> dict:
    if data_dir is not None:
        os.environ["CRYPTO_PORTFOLIO_DATA_DIR"] = data_dir
    policy = resolve_policy()
    history = build_history_context()
    decision = history.get("latest_decision") or {}
    nav = history.get("nav_history_result") or {}
    return {
        "policy_hash": policy.canonical_hash,
        "history": {
            "performance_status": history.get("performance_status"),
            "performance_finality": history.get("performance_finality"),
            "cash_flow_resolution_status": history.get("cash_flow_resolution_status"),
            "nav_history": nav,
            "baseline_boundary": next(
                (item.get("start") for item in nav.get("segments", ()) if item.get("archived") is not True),
                None,
            ),
            "unresolved_reason": (nav.get("explanations") or [None])[0],
            "last_full_review": history.get("last_full_review"),
            "full_review_due": history.get("full_review_due"),
        },
        "regime_confidence": decision.get("regime_confidence"),
        "decision_confidence": decision.get("decision_confidence"),
        "status": "AVAILABLE" if decision.get("decision_confidence") else "PROVISIONAL",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--explain", action="store_true", help="emit the same bounded read-only explanation")
    args = parser.parse_args(argv)
    result = build_output(args.data_dir)
    if args.json or args.explain:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    else:
        print(f"confidence status: {result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
