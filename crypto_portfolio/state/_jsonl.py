"""Small append-only JSONL primitive shared by state stores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def append_record(path: str | Path, record: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        stream.write("\n")
    return destination


def read_records(path: str | Path) -> list[dict[str, Any]]:
    destination = Path(path)
    if not destination.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(destination.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL record at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL record at line {line_number} must be an object")
        records.append(record)
    return records
