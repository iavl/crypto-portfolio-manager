"""Canonical timezone-aware timestamp parsing for persisted portfolio data."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


def parse_timestamp(value: Any, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty RFC3339 timestamp")
    text = value.strip()
    if len(text) == 10:
        try:
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"{field} must be a valid RFC3339 timestamp") from exc
    if len(text) < 11 or text[10] not in {"T", "t"}:
        raise ValueError(f"{field} must be a valid RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_timestamp(value: Any, field: str = "timestamp") -> str:
    parsed = parse_timestamp(value, field)
    timespec = "microseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


__all__ = ["normalize_timestamp", "parse_timestamp"]
