#!/usr/bin/env python3
"""Check or append an auditable v3-to-v4 confidence migration sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


RECORD_FILES = (
    Path("portfolio/snapshots.jsonl"),
    Path("decisions/decisions.jsonl"),
    Path("metrics/observations.jsonl"),
    Path("metrics/collection-events.jsonl"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_data_dir(data_dir: str | os.PathLike[str]) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    files: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    legacy_count = 0
    current_count = 0
    for relative in RECORD_FILES:
        path = root / relative
        if not path.exists():
            continue
        file_hash = _sha256(path)
        records = 0
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw_line.strip():
                continue
            records += 1
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                unresolved.append({"path": str(relative), "line": line_number, "reason": f"invalid JSON: {exc.msg}"})
                continue
            if not isinstance(record, dict):
                unresolved.append({"path": str(relative), "line": line_number, "reason": "record is not an object"})
                continue
            if int(record.get("policy_version", 3)) < 4:
                legacy_count += 1
            else:
                current_count += 1
            if record.get("external_cash_flow_type") == "UNRESOLVED":
                unresolved.append({"path": str(relative), "line": line_number, "reason": "unresolved historical cash flow"})
        files.append({"path": str(relative), "sha256": file_hash, "line_count": records})
    return {
        "migration": "confidence-v4",
        "data_dir": str(root),
        "files": files,
        "legacy_count": legacy_count,
        "current_count": current_count,
        "unresolved": unresolved,
        "ready": not unresolved,
    }


def write_sidecar(report: dict[str, Any]) -> Path:
    root = Path(report["data_dir"])
    destination = root / "migration" / "confidence-v4.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = {"migration_id": identity, **report}
    existing_ids = set()
    if destination.exists():
        for line in destination.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing_ids.add(json.loads(line).get("migration_id"))
                except json.JSONDecodeError:
                    continue
    if identity not in existing_ids:
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate and report only")
    mode.add_argument("--dry-run", action="store_true", help="show migration without writing")
    mode.add_argument("--write", action="store_true", help="append a versioned migration sidecar")
    parser.add_argument("--data-dir", default=os.environ.get("CRYPTO_PORTFOLIO_DATA_DIR", "~/.local/share/crypto-portfolio-manager"))
    args = parser.parse_args(argv)
    report = inspect_data_dir(args.data_dir)
    if args.write:
        report["sidecar"] = str(write_sidecar(report))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
