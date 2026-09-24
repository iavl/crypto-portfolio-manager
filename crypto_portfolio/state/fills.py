"""Append-only exchange-confirmed trade-fill history."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..models.fill import TradeFill
from ._jsonl import append_record, read_records
from .snapshots import runtime_data_dir


def default_fills_dir() -> Path:
    return runtime_data_dir() / "fills"


def default_fill_path() -> Path:
    return default_fills_dir() / "trades.jsonl"


def _fill(value: TradeFill | Mapping[str, Any]) -> TradeFill:
    return value if isinstance(value, TradeFill) else TradeFill.from_mapping(value)


def append_fill(fill: TradeFill | Mapping[str, Any], path: str | Path | None = None) -> Path:
    model = _fill(fill)
    destination = Path(path) if path is not None else default_fill_path()
    existing = existing_fill_keys(destination)
    if model.dedup_key in existing:
        raise ValueError(f"trade fill {model.dedup_key} is already recorded")
    return append_record(destination, model.as_dict())


def append_fills(
    fills: list[TradeFill | Mapping[str, Any]], path: str | Path | None = None
) -> tuple[Path, int]:
    """Append only fills whose dedup key is new; return the path and count."""
    destination = Path(path) if path is not None else default_fill_path()
    existing = existing_fill_keys(destination)
    appended = 0
    for value in fills:
        model = _fill(value)
        if model.dedup_key in existing:
            continue
        append_record(destination, model.as_dict())
        existing.add(model.dedup_key)
        appended += 1
    return destination, appended


def read_fills(path: str | Path | None = None) -> list[TradeFill]:
    return [_fill(record) for record in read_records(Path(path) if path is not None else default_fill_path())]


def existing_fill_keys(path: str | Path | None = None) -> set[str]:
    return {fill.dedup_key for fill in read_fills(path)}


def fills_by_symbol(
    path: str | Path | None = None, *, symbols: tuple[str, ...] = ()
) -> dict[str, list[TradeFill]]:
    """Group persisted fills by asset symbol, optionally restricted to a set.

    A requested symbol with no persisted trades is present with an empty list,
    which callers use to mean "fetched/known-empty" rather than "never looked".
    """
    wanted = {symbol.strip().upper() for symbol in symbols}
    grouped: dict[str, list[TradeFill]] = {symbol: [] for symbol in sorted(wanted)}
    for fill in read_fills(path):
        if wanted and fill.symbol not in wanted:
            continue
        grouped.setdefault(fill.symbol, []).append(fill)
    return grouped


__all__ = [
    "append_fill",
    "append_fills",
    "default_fill_path",
    "default_fills_dir",
    "existing_fill_keys",
    "fills_by_symbol",
    "read_fills",
]
