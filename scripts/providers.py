#!/usr/bin/env python3
"""Inspect providers offline, or run an explicit read-only network probe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.providers.config import load_provider_config
from crypto_portfolio.providers.health import (
    contract_providers,
    diagnostic_exit_code,
    doctor_providers,
    smoke_provider,
)
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.providers.recording import RecordingTransport, load_recording
from crypto_portfolio.providers.routes import provider_chain
from crypto_portfolio.providers.router import ProviderRouter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list provider capabilities")
    parser.add_argument("--status", action="store_true", help="show offline config, adapter, credential, and runtime readiness")
    parser.add_argument("--probe", metavar="PROVIDER", help="opt-in network probe for a provider or all")
    parser.add_argument("--doctor", nargs="?", const="all", metavar="PROVIDER", help="diagnose one provider or all")
    parser.add_argument("--contract", nargs="?", const="all", metavar="PROVIDER", help="run a minimal live provider contract check")
    parser.add_argument("--smoke", metavar="ASSET", help="run the production router for one asset")
    parser.add_argument("--record", nargs="?", const=".data/provider-recordings", metavar="PATH", help="record safe probe responses into PATH")
    parser.add_argument("--replay", metavar="PATH", help="validate and print one safe provider recording")
    parser.add_argument("--metric", help="show the deterministic provider chain for a metric")
    parser.add_argument("--asset", help="asset used with --plan or a targeted --probe")
    parser.add_argument("--plan", action="store_true", help="show a local metric collection plan")
    args = parser.parse_args(argv)
    if args.record and not args.probe:
        parser.error("--record requires --probe")
    if args.replay:
        try:
            recording = load_recording(args.replay)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(recording.as_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    config = load_provider_config()
    http_client = HttpClient(opener=RecordingTransport(Path(args.record))) if args.record else None
    router = ProviderRouter(config=config, http_client=http_client)
    requested = args.probe or args.doctor or args.contract
    if requested and requested != "all":
        configured_names = set(config.get("providers", {}))
        if requested.strip().lower() not in router.providers and requested.strip().lower() not in configured_names:
            parser.error(f"unknown or unregistered provider: {requested}")
    if args.status or not any((args.list, args.metric, args.plan, args.probe, args.doctor, args.contract, args.smoke)):
        print("provider               config    adapter   credential   runtime    reason")
        for row in router.provider_status():
            config_state = "ENABLED" if row["config_enabled"] else "DISABLED"
            adapter = "YES" if row["adapter_available"] else "NO"
            credential = "N/A" if not row["credential_required"] else ("YES" if row["credential_present"] else "NO")
            runtime = "READY" if row["runtime_ready"] else "NOT_READY"
            print(f"{row['provider']:<22} {config_state:<9} {adapter:<9} {credential:<12} {runtime:<10} {row.get('reason') or '-'}")
    if args.list:
        for name, provider in sorted(router.providers.items()):
            capabilities = router.capabilities(name)
            print(json.dumps({"provider": name, "capabilities": capabilities.as_dict() if capabilities else None}, sort_keys=True))
    if args.metric and not args.smoke:
        print(json.dumps({"metric": args.metric, "priority": list(provider_chain(args.metric))}))
    if args.plan:
        asset = args.asset or "BTC"
        plan = build_metric_collection_plan({"positions": [{"symbol": asset, "value_usd": 1}]})
        print(json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True))
    if args.probe:
        requested = args.probe.strip().lower()
        try:
            results = router.probe(requested, asset=args.asset)
        except ValueError as exc:
            parser.error(str(exc))
        for result in results:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return diagnostic_exit_code(results)
    if args.doctor:
        try:
            results = doctor_providers(router, args.doctor.strip().lower(), asset=args.asset)
        except ValueError as exc:
            parser.error(str(exc))
        for result in results:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return diagnostic_exit_code(results)
    if args.contract:
        try:
            results = contract_providers(router, args.contract.strip().lower(), asset=args.asset)
        except ValueError as exc:
            parser.error(str(exc))
        for result in results:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2 if any(result.get("status") == "FAIL" for result in results) else 0
    if args.smoke:
        try:
            result = smoke_provider(router, args.smoke, args.metric or "market.spot_price")
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] in {"PASS", "SKIPPED"} else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
