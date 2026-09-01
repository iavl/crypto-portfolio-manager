"""Small append-only JSONL primitive shared by state stores."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Mapping


def append_record(path: str | Path, record: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ) + "\n"
    # ponytail: file-level lock keeps JSONL writes safe; use a database if throughput requires it.
    with destination.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return destination


def read_records(path: str | Path) -> list[dict[str, Any]]:
    destination = Path(path)
    if not destination.exists():
        return []
    with destination.open("r", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            content = stream.read()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    if content and not content.endswith("\n"):
        raise ValueError("JSONL file ends with an incomplete record")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(content.splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL record at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record at line {line_number} must be an object")
        records.append(record)
    return records
