#!/usr/bin/env python3
"""Read-only inspection for model-routing profiles and effective routes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.model_routing import (  # noqa: E402
    RoutingError,
    load_model_routing,
    resolve_all_routes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="local routing override JSON")
    parser.add_argument("--profile", help="profile to inspect")
    parser.add_argument("--runtime", help="logical runtime capability adapter")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--show-effective", action="store_true")
    parser.add_argument("--validate", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        routing = load_model_routing(
            path=args.config,
            profile=args.profile,
            runtime=args.runtime,
        )
    except RoutingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.validate:
        print(f"valid: routing policy v{routing.routing_policy_version}")
    if args.list_profiles:
        for name in routing.profiles:
            marker = " (active)" if name == routing.profile else ""
            print(f"{name}{marker}")
    if args.show_effective or not (args.validate or args.list_profiles):
        routes = resolve_all_routes(routing=routing)
        print(f"profile: {routing.profile}")
        print(f"runtime: {routing.runtime}")
        print(f"config_hash: {routing.config_hash}")
        print(
            "stage | requested preset | requested model | requested effort | "
            "effective model | effective effort | fallback"
        )
        for route in routes.values():
            print(
                f"{route.stage} | {route.requested_preset} | "
                f"{route.requested_model or 'PYTHON'} | "
                f"{route.requested_reasoning_effort or 'none'} | "
                f"{route.effective_model or 'PYTHON'} | "
                f"{route.effective_reasoning_effort or 'none'} | "
                f"{route.fallback_reason or 'none'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
