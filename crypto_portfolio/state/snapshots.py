"""Append-only portfolio snapshot storage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ._jsonl import append_record, read_records


def runtime_data_dir() -> Path:
    configured = os.environ.get("CRYPTO_PORTFOLIO_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "crypto-portfolio-manager"


def default_snapshot_path() -> Path:
    return runtime_data_dir() / "portfolio" / "snapshots.jsonl"


def append_snapshot(snapshot: Mapping[str, Any] | Any, path: str | Path | None = None) -> Path:
    record = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(snapshot)
    if not isinstance(record, dict):
        raise ValueError("snapshot must be an object or a model with as_dict()")
    if "timestamp" not in record or not record["timestamp"] or "policy_version" not in record:
        raise ValueError("snapshot must preserve timestamp and policy_version")
    if isinstance(record["policy_version"], bool) or not isinstance(record["policy_version"], int) or record["policy_version"] < 1:
        raise ValueError("snapshot policy_version must be a positive integer")
    return append_record(path or default_snapshot_path(), record)


def read_snapshots(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_records(path or default_snapshot_path())


__all__ = ["append_snapshot", "default_snapshot_path", "read_snapshots", "runtime_data_dir"]
