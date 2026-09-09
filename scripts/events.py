#!/usr/bin/env python3
"""Plan, fetch, classify, and smoke-test allowlisted event sources only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.events import (  # noqa: E402
    EventResolver,
    EventScanner,
    EventSourceScanRequest,
    build_exchange_document,
    classifier_from_environment,
    parse_exchange_document,
    validate_exchange_responses,
)
from crypto_portfolio.events.transports import EventTransportCache, StructuredEventTransport  # noqa: E402
from crypto_portfolio.models.time import normalize_timestamp, parse_timestamp  # noqa: E402
from crypto_portfolio.providers.base import FetchMode  # noqa: E402


ASSET_DEFAULT = ("BTC", "ETH")
CATEGORIES = ("security", "governance", "regulatory")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _assets(values: Iterable[str] | None) -> tuple[str, ...]:
    values = values or ASSET_DEFAULT
    result = tuple(dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip()))
    if not result:
        raise ValueError("at least one asset is required")
    return result


def _requests(scanner: EventScanner, assets: tuple[str, ...], as_of: str, review_type: str) -> tuple[EventSourceScanRequest, ...]:
    result: list[EventSourceScanRequest] = []
    for asset in assets:
        for category in ("security", "governance"):
            result.extend(scanner.build_requests(asset, category, as_of, review_type=review_type))
    result.extend(scanner.build_requests("MARKET", "regulatory", as_of, review_type=review_type))
    return tuple(result)


def _transport(fetch_mode: FetchMode) -> StructuredEventTransport:
    transport = StructuredEventTransport(
        cache=EventTransportCache(),
        github_token=os.environ.get("GITHUB_TOKEN"),
    )
    if fetch_mode == FetchMode.CACHE_ONLY:
        transport.fetch = transport.fetch_cached  # type: ignore[method-assign]
    return transport


def _write_or_print(value: MappingLike, output: str | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output:
        Path(output).expanduser().write_text(rendered, encoding="utf-8")
    print(rendered, end="")


MappingLike = dict[str, Any] | list[Any]


def _fetch(args: argparse.Namespace, scanner: EventScanner, requests: tuple[EventSourceScanRequest, ...]) -> int:
    mode = FetchMode.parse(args.fetch_mode)
    transport = _transport(mode)
    responses = tuple(transport.fetch(request) for request in requests)
    classifier = classifier_from_environment()
    document = build_exchange_document(
        requests,
        responses,
        classification_mode="host",
        classifier_backend=getattr(classifier, "backend", None),
        classifier_model=getattr(classifier, "model", None),
    )
    _write_or_print(document, args.output)
    return 0


def _validate_catalog_requests(scanner: EventScanner, requests: tuple[EventSourceScanRequest, ...]) -> None:
    for request in requests:
        days = (parse_timestamp(request.as_of) - parse_timestamp(request.lookback_start)).total_seconds() / 86400
        if days < 1 or not days.is_integer():
            raise ValueError(f"invalid lookback for {request.source_id}")
        known = scanner.build_requests(
            request.asset,
            request.category,
            request.as_of,
            lookback_days=int(days),
            review_type="EVENT_REVIEW",
        )
        match = next((item for item in known if item.source_id == request.source_id), None)
        if (
            match is None
            or match.source_url != request.source_url
            or match.source_urls != request.source_urls
            or match.transport_kind != request.transport_kind
            or match.source_group != request.source_group
            or match.authority != request.authority
            or match.source_name != request.source_name
        ):
            raise ValueError(f"request is not from the fixed event source catalog: {request.source_id}")


def _resolve(args: argparse.Namespace, scanner: EventScanner) -> int:
    value = json.loads(Path(args.resolve).expanduser().read_text(encoding="utf-8"))
    requests, responses, pending = parse_exchange_document(value)
    _validate_catalog_requests(scanner, requests)
    requested_assets = _assets(args.asset)
    document_assets = {request.asset for request in requests if request.asset != "MARKET"}
    if not set(requested_assets).issubset(document_assets):
        raise ValueError("classified response does not cover the requested assets")
    responses = validate_exchange_responses(requests, responses, pending)
    by_id = {response.source_id: response for response in responses}
    results: dict[str, dict[str, Any]] = {}
    for asset in sorted(document_assets):
        for category in ("security", "governance"):
            group = scanner.build_requests(asset, category, next(item.as_of for item in requests if item.asset == asset), review_type="EVENT_REVIEW")
            results[f"{asset}:{category}"] = scanner.scan(
                asset,
                category,
                group[0].as_of,
                responses=tuple(by_id[item.source_id] for item in group),
                review_type="EVENT_REVIEW",
            ).as_dict()
    regulatory_assets = sorted(document_assets)
    market_requests = tuple(item for item in requests if item.asset == "MARKET" and item.category == "regulatory")
    market_as_of = market_requests[0].as_of
    regulatory = scanner.scan_shared_regulatory(
        regulatory_assets,
        market_as_of,
        responses=tuple(by_id[item.source_id] for item in market_requests),
        review_type="EVENT_REVIEW",
    )
    results.update({f"{asset}:regulatory": scan.as_dict() for asset, scan in regulatory.items()})
    _write_or_print({"results": results, "pending_external_resolution": 0}, None)
    return 0


def _smoke(args: argparse.Namespace, scanner: EventScanner, requests: tuple[EventSourceScanRequest, ...]) -> int:
    mode = FetchMode.parse(args.fetch_mode)
    transport = _transport(mode)
    classifier = None if mode == FetchMode.CACHE_ONLY else classifier_from_environment()
    resolver = EventResolver(transport=transport, classifier=classifier)
    responses = tuple(resolver.resolve(request) for request in requests)
    by_id = {response.source_id: response for response in responses}
    results: dict[str, str] = {}
    errors: list[str] = []

    assets = _assets(args.asset)
    for asset in assets:
        for category in ("security", "governance"):
            group = tuple(item for item in requests if item.asset == asset and item.category == category)
            try:
                scan = scanner.scan(asset, category, group[0].as_of, responses=tuple(by_id[item.source_id] for item in group), review_type="EVENT_REVIEW")
            except ValueError as exc:
                results[f"{asset}:{category}"] = "PENDING" if any("CANDIDATE" in str(item.get("materiality", "")) for response in group for item in by_id[response.source_id].items) else "INSUFFICIENT"
                errors.append(f"{asset}:{category}: {exc}")
            else:
                results[f"{asset}:{category}"] = "INSUFFICIENT" if scan.status == "INSUFFICIENT_SOURCE_COVERAGE" else "SUCCESS"
    market = tuple(item for item in requests if item.asset == "MARKET" and item.category == "regulatory")
    try:
        regulatory = scanner.scan_shared_regulatory(
            assets,
            market[0].as_of,
            responses=tuple(by_id[item.source_id] for item in market),
            review_type="EVENT_REVIEW",
        )
    except ValueError as exc:
        errors.append(f"MARKET:regulatory: {exc}")
        for asset in assets:
            results[f"{asset}:regulatory"] = "PENDING"
    else:
        for asset, scan in regulatory.items():
            results[f"{asset}:regulatory"] = "INSUFFICIENT" if scan.status == "INSUFFICIENT_SOURCE_COVERAGE" else "SUCCESS"

    diagnostics = [item.as_dict() for item in resolver.diagnostics]
    _write_or_print({
        "results": results,
        "source_fetches": len(diagnostics),
        "github_authentication": "authenticated" if os.environ.get("GITHUB_TOKEN") else "unauthenticated",
        "candidates": sum(item["candidate_count"] for item in diagnostics),
        "classified_candidates": sum(item["classified_count"] for item in diagnostics),
        "pending_classifications": sum(item["status"] == "CLASSIFICATION_PENDING" for item in diagnostics),
        "coverage": {item["source_id"]: item["coverage_ratio"] for item in diagnostics},
        "confidence": {item["source_id"]: item["confidence"] for item in diagnostics},
        "diagnostics": diagnostics,
        "errors": errors,
    }, None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--plan", action="store_true", help="list fixed event requests")
    operation.add_argument("--fetch", action="store_true", help="fetch bounded candidates only")
    operation.add_argument("--resolve", metavar="JSON", help="validate and scan a classified exchange file")
    operation.add_argument("--smoke", action="store_true", help="run event-only acquisition and report statuses")
    parser.add_argument("--asset", action="append", help="asset; repeat for multiple assets (default: BTC, ETH)")
    parser.add_argument("--as-of", default=None, help="timezone-aware RFC3339 scan time")
    parser.add_argument("--fetch-mode", default="AUTO", choices=("AUTO", "CACHE_ONLY", "REFRESH"), type=str.upper)
    parser.add_argument("--output", help="write fetch output to this JSON path")
    parser.add_argument("--review-type", default="EVENT_REVIEW", choices=("SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW"))
    args = parser.parse_args(argv)
    try:
        as_of = normalize_timestamp(args.as_of or _now(), "as_of")
        assets = _assets(args.asset)
        scanner = EventScanner()
        requests = _requests(scanner, assets, as_of, args.review_type)
        if args.plan:
            _write_or_print([request.as_dict() for request in requests], None)
            return 0
        if args.fetch:
            return _fetch(args, scanner, requests)
        if args.resolve:
            return _resolve(args, scanner)
        if args.smoke:
            return _smoke(args, scanner, requests)
        parser.error("choose one of --plan, --fetch, --resolve, or --smoke")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
