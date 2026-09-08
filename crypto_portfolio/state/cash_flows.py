"""Append-only runtime storage for confirmed cash-flow resolutions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..models.cash_flow import CashFlowResolution
from ._jsonl import append_record, read_records
from .snapshots import runtime_data_dir


def default_cash_flow_resolution_path() -> Path:
    return runtime_data_dir() / "portfolio" / "cash-flow-resolutions.jsonl"


def append_cash_flow_resolution(
    resolution: CashFlowResolution | Mapping[str, Any],
    path: str | Path | None = None,
) -> Path:
    model = resolution if isinstance(resolution, CashFlowResolution) else CashFlowResolution.from_mapping(dict(resolution))
    destination = Path(path or default_cash_flow_resolution_path())
    existing = read_records(destination)
    for item in existing:
        if item.get("resolution_id") == model.resolution_id:
            if item == model.as_dict():
                return destination
            raise ValueError(f"duplicate cash-flow resolution {model.resolution_id}")
    if any(item.get("snapshot_id") == model.snapshot_id for item in existing):
        raise ValueError(f"snapshot {model.snapshot_id} already has a cash-flow resolution")
    return append_record(destination, model.as_dict())


def read_cash_flow_resolutions(path: str | Path | None = None) -> list[CashFlowResolution]:
    return [CashFlowResolution.from_mapping(item) for item in read_records(path or default_cash_flow_resolution_path())]


__all__ = [
    "append_cash_flow_resolution",
    "default_cash_flow_resolution_path",
    "read_cash_flow_resolutions",
]
