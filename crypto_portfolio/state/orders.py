"""Current-state snapshot of exchange resting orders (read-only intake).

Open orders are ephemeral point-in-time state, not append-only history: every
persisted fetch replaces the previous observation. Presence of a symbol key in
``orders_by_symbol`` means the open-order book was fetched for that symbol —
an empty list is a confident zero, matching the fill-store coverage contract
an absent key means "not fetched".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..models.order import OpenOrderRecord
from .market_data import _atomic_write
from .snapshots import runtime_data_dir


def default_orders_path() -> Path:
    return runtime_data_dir() / "orders" / "open-orders.json"


def write_open_orders(
    orders_by_symbol: Mapping[str, Any],
    *,
    fetched_at: str,
    path: str | Path | None = None,
) -> Path:
    """Atomically replace the resting-order snapshot for all supplied symbols.

    ``orders_by_symbol`` maps every fetched symbol to its order records; keep
    empty lists so a fetched-but-zero book stays distinguishable from a symbol
    that was never queried.
    """
    normalized: dict[str, list[dict[str, Any]]] = {}
    for symbol, orders in orders_by_symbol.items():
        key = str(symbol).strip().upper()
        records = []
        for order in orders:
            record = order if isinstance(order, OpenOrderRecord) else OpenOrderRecord.from_mapping(order)
            records.append(record.as_dict())
        normalized[key] = records
    destination = Path(path) if path is not None else default_orders_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": fetched_at, "orders_by_symbol": normalized}
    _atomic_write(destination, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return destination


def read_open_orders(path: str | Path | None = None) -> dict[str, Any]:
    """Load the latest snapshot as ``{"fetched_at", "orders_by_symbol"}``.

    A missing file reads as an empty observation (never fetched). Records are
    normalized through :class:`OpenOrderRecord`, so invalid persisted orders
    fail clearly instead of reaching the matcher.
    """
    source = Path(path) if path is not None else default_orders_path()
    if not source.exists():
        return {"fetched_at": None, "orders_by_symbol": {}}
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or "orders_by_symbol" not in raw:
        raise ValueError("open-orders state file is invalid")
    fetched_at = raw.get("fetched_at")
    if fetched_at is not None and not isinstance(fetched_at, str):
        raise ValueError("open-orders fetched_at must be a timestamp string")
    by_symbol = raw["orders_by_symbol"]
    if not isinstance(by_symbol, Mapping):
        raise ValueError("open-orders orders_by_symbol must be an object")
    normalized: dict[str, tuple[OpenOrderRecord, ...]] = {}
    for symbol, orders in by_symbol.items():
        if not isinstance(orders, list):
            raise ValueError("open-orders records must be a list")
        normalized[str(symbol).strip().upper()] = tuple(
            OpenOrderRecord.from_mapping(order) for order in orders
        )
    return {"fetched_at": fetched_at, "orders_by_symbol": normalized}


__all__ = ["default_orders_path", "read_open_orders", "write_open_orders"]
