#!/usr/bin/env python3
"""Thin compatibility CLI for portfolio snapshot normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.models.portfolio import classify_symbol, normalize_snapshot


_CONFIG_MISSING = object()


def resolve_config(raw_config: Any = _CONFIG_MISSING) -> dict[str, Any]:
    """Return the legacy config shape from the canonical policy."""
    return resolve_policy(None if raw_config is _CONFIG_MISSING else raw_config).legacy_config()


def classify(symbol: str, config: dict[str, Any] | None = None) -> str:
    return classify_symbol(symbol, policy=resolve_policy(config))


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    return normalize_snapshot(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = normalize(json.loads(args.snapshot.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))


if __name__ == "__main__":
    main()
