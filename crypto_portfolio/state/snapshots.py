"""Append-only portfolio snapshot storage."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ..models.policy import Policy, policy_from_mapping, policy_hash, resolve_policy
from ..models.portfolio import PortfolioSnapshot, snapshot_from_mapping
from ..models.time import parse_timestamp
from ._jsonl import append_record, read_records


def runtime_data_dir() -> Path:
    configured = os.environ.get("CRYPTO_PORTFOLIO_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "crypto-portfolio-manager"


def default_snapshot_path() -> Path:
    return runtime_data_dir() / "portfolio" / "snapshots.jsonl"


def _validated_snapshot(
    snapshot: PortfolioSnapshot | Mapping[str, Any], policy: Policy | None = None
) -> tuple[dict[str, Any], Policy]:
    if isinstance(snapshot, Mapping):
        if policy is not None:
            supplied_policy = policy
        elif snapshot.get("resolved_policy") is not None:
            supplied_policy = policy_from_mapping(snapshot["resolved_policy"])
        else:
            supplied_policy = resolve_policy(snapshot.get("config"))
        model, resolved, _ = snapshot_from_mapping(snapshot, policy=supplied_policy)
    elif isinstance(snapshot, PortfolioSnapshot):
        model = snapshot
        if policy is not None:
            resolved = policy
        elif snapshot.resolved_policy is not None:
            resolved = policy_from_mapping(snapshot.resolved_policy)
        else:
            resolved = resolve_policy()
    else:
        raise ValueError("snapshot must be a PortfolioSnapshot or mapping")
    if model.timestamp == "UNSPECIFIED":
        raise ValueError("snapshot timestamp must be a timezone-aware RFC3339 timestamp")
    parse_timestamp(model.timestamp)
    if model.policy_version != resolved.policy_version:
        raise ValueError("snapshot policy_version does not match resolved policy")
    expected_hash = policy_hash(resolved)
    if model.policy_hash is not None and model.policy_hash != expected_hash:
        raise ValueError("snapshot policy_hash does not match resolved policy")
    record = model.as_dict()
    record["policy_hash"] = expected_hash
    record["resolved_policy"] = resolved.as_dict()
    return record, resolved


def append_snapshot(
    snapshot: Mapping[str, Any] | PortfolioSnapshot,
    path: str | Path | None = None,
    *,
    policy: Policy | None = None,
) -> Path:
    record, resolved = _validated_snapshot(snapshot, policy)
    destination = Path(path or default_snapshot_path())
    existing = read_records(destination)
    if record.get("snapshot_id") and any(item.get("snapshot_id") == record["snapshot_id"] for item in existing):
        raise ValueError(f"duplicate snapshot_id {record['snapshot_id']}")
    comparable = {key: value for key, value in record.items() if key != "snapshot_id"}
    if any(
        {key: value for key, value in item.items() if key != "snapshot_id"} == comparable
        for item in existing
    ):
        raise ValueError("duplicate snapshot record")
    if not record.get("snapshot_id"):
        from uuid import uuid4

        record["snapshot_id"] = str(uuid4())
    record["policy_version"] = resolved.policy_version
    return append_record(destination, record)


def read_snapshots(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_records(path or default_snapshot_path())


__all__ = ["append_snapshot", "default_snapshot_path", "read_snapshots", "runtime_data_dir"]
