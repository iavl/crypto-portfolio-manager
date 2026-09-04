#!/usr/bin/env python3
"""Inspect configured provider capabilities without making network calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.providers.config import load_provider_config, provider_status
from crypto_portfolio.providers.routes import provider_chain
from crypto_portfolio.providers.router import ProviderRouter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list provider capabilities")
    parser.add_argument("--status", action="store_true", help="show enabled state and credential presence")
    parser.add_argument("--metric", help="show the deterministic provider chain for a metric")
    parser.add_argument("--asset", help="asset used with --plan")
    parser.add_argument("--plan", action="store_true", help="show a local metric collection plan")
    args = parser.parse_args()
    config = load_provider_config()
    router = ProviderRouter(config=config)
    if args.status or not any((args.list, args.metric, args.plan)):
        for row in provider_status(config):
            enabled = "ENABLED" if row["enabled"] else "DISABLED"
            credential = "yes" if row["credential_present"] else "no"
            requirement = "key configured" if row["api_key_env"] else "no key required"
            print(f"{row['provider']:<22} {enabled:<8} {requirement}; credential present: {credential}")
    if args.list:
        for name, provider in sorted(router.providers.items()):
            capabilities = router.capabilities(name)
            print(json.dumps({"provider": name, "capabilities": capabilities.as_dict() if capabilities else None}, sort_keys=True))
    if args.metric:
        print(json.dumps({"metric": args.metric, "priority": list(provider_chain(args.metric))}))
    if args.plan:
        asset = args.asset or "BTC"
        plan = build_metric_collection_plan({"positions": [{"symbol": asset, "value_usd": 1}]})
        print(json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
