#!/usr/bin/env python3
"""Thin CLI for explicit cash-flow resolutions over runtime snapshots.

Read-only commands never mutate state; resolve commands only append to the
append-only cash-flow-resolutions JSONL and never rewrite snapshots.jsonl.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.engine.cash_flow import (
    apply_cash_flow_resolutions,
    find_unresolved_cash_flow_snapshots,
)
from crypto_portfolio.state.cash_flows import (
    append_cash_flow_resolution,
    read_cash_flow_resolutions,
)
from crypto_portfolio.state.snapshots import read_snapshots


def _load_records(path: str | Path | None):
    # Raw persisted records: the engine overlay extracts the ledger fields it
    # needs, so history replay never revalidates an old embedded policy blob.
    return read_snapshots(path)


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_list_unresolved(args) -> int:
    records = _load_records(args.snapshots)
    blocking = find_unresolved_cash_flow_snapshots(records, read_cash_flow_resolutions(args.resolutions))
    _print_json({"unresolved_count": len(blocking), "unresolved": blocking})
    return 1 if blocking else 0


def cmd_validate(args) -> int:
    records = _load_records(args.snapshots)
    resolutions = read_cash_flow_resolutions(args.resolutions)
    _, diagnostics = apply_cash_flow_resolutions(records, resolutions)
    _print_json({"valid": True, "lineage": diagnostics["lineage"]})
    return 0


def _append(args, *, status: str, external_cash_flow: float | None, external_cash_flow_type: str | None) -> int:
    resolution = {
        "resolution_id": args.resolution_id or f"cfr-{uuid.uuid4().hex}",
        "snapshot_id": args.snapshot_id,
        "timestamp": args.timestamp,
        "cash_flow_resolution_status": status,
        "external_cash_flow": external_cash_flow,
        "external_cash_flow_type": external_cash_flow_type,
        "rationale": args.rationale,
    }
    destination = append_cash_flow_resolution(resolution, args.resolutions)
    _print_json({"appended": True, "path": str(destination), "resolution": resolution})
    return 0


def cmd_resolve_none(args) -> int:
    return _append(args, status="CONFIRMED_NONE", external_cash_flow=0.0, external_cash_flow_type="NONE")


def cmd_resolve_amount(args) -> int:
    amount = args.amount if args.type == "DEPOSIT" else -abs(args.amount)
    return _append(args, status="CONFIRMED_AMOUNT", external_cash_flow=amount, external_cash_flow_type=args.type)


def cmd_baseline_reset(args) -> int:
    return _append(args, status="BASELINE_RESET", external_cash_flow=0.0, external_cash_flow_type="NONE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=None, help="snapshots JSONL (default: runtime path)")
    parser.add_argument("--resolutions", type=Path, default=None, help="resolutions JSONL (default: runtime path)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-unresolved", help="list snapshots that still block final NAV")
    sub.add_parser("validate", help="apply the resolution overlay read-only and print lineage")

    for name, handler, extra in (
        ("resolve-none", cmd_resolve_none, "confirm a snapshot had no external cash flow"),
        ("resolve-amount", cmd_resolve_amount, "confirm an explicit deposit/withdrawal"),
        ("baseline-reset", cmd_baseline_reset, "start a new verified NAV baseline at a snapshot"),
    ):
        command = sub.add_parser(name, help=extra)
        command.add_argument("--snapshot-id", required=True)
        command.add_argument("--rationale", required=True)
        command.add_argument("--timestamp", default=None, help="resolution time (default: now)")
        if name == "resolve-amount":
            command.add_argument("--type", choices=("DEPOSIT", "WITHDRAWAL"), required=True)
            command.add_argument("--amount", type=float, required=True)
        command.add_argument("--resolution-id", default=None)

    args = parser.parse_args()
    if getattr(args, "timestamp", None) is None:
        args.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    handlers = {
        "list-unresolved": cmd_list_unresolved,
        "validate": cmd_validate,
        "resolve-none": cmd_resolve_none,
        "resolve-amount": cmd_resolve_amount,
        "baseline-reset": cmd_baseline_reset,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
