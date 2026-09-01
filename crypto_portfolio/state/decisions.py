"""Append-only portfolio decision storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ._jsonl import append_record, read_records
from .snapshots import runtime_data_dir


_STATUSES = {"PENDING", "CONFIRMED", "NOT_EXECUTED"}


def default_decision_path() -> Path:
    return runtime_data_dir() / "decisions" / "decisions.jsonl"


def append_decision(decision: Mapping[str, Any] | Any, path: str | Path | None = None) -> Path:
    record = decision.as_dict() if hasattr(decision, "as_dict") else dict(decision)
    if not isinstance(record, dict):
        raise ValueError("decision must be an object or a model with as_dict()")
    for field in ("timestamp", "policy_version", "market_regime"):
        if field not in record:
            raise ValueError(f"decision must preserve {field}")
    status = str(record.get("status", "PENDING")).upper()
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {sorted(_STATUSES)}")
    record["status"] = status
    return append_record(path or default_decision_path(), record)


def read_decisions(path: str | Path | None = None) -> list[dict[str, Any]]:
    return read_records(path or default_decision_path())


__all__ = ["append_decision", "default_decision_path", "read_decisions"]
