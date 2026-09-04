#!/usr/bin/env python3
"""Inspect or manually prune the local provider cache."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.providers.cache import ProviderCache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", action="store_true", help="show cache counts")
    parser.add_argument("--prune-expired", action="store_true", help="remove expired mutable response entries")
    args = parser.parse_args()
    cache = ProviderCache()
    if args.prune_expired:
        print(f"removed_expired={cache.prune_expired()}")
    if args.stats or not args.prune_expired:
        for key, value in sorted(cache.stats().items()):
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
