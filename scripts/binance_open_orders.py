#!/usr/bin/env python3
"""Fetch currently resting spot orders from the Binance read-only API.

Snapshots the open-order book (order id, side, type, price, remaining
quantity) into the current-state orders store used to reconcile resting
orders with execution plans: tranches already covered by a resting limit
order render as already-placed reminders instead of new proposals. Open
orders are point-in-time state, so --persist replaces the previous snapshot
(never appended). Symbols default to the same derivation as the fill store.

Credentials come exclusively from BINANCE_API_KEY / BINANCE_API_SECRET
(read-only key). The account client is GET-only; this script never places,
cancels, or modifies orders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binance_fills import _default_symbols
from crypto_portfolio.models.order import OpenOrderRecord
from crypto_portfolio.providers.binance_account import BinanceAccountClient
from crypto_portfolio.providers.config import (
    load_provider_config,
    provider_api_key,
    provider_api_secret,
    provider_enabled,
)
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.state.orders import write_open_orders

PROVIDER_NAME = "binance_account"


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def acquire(*, symbols: list[str], client: BinanceAccountClient, now: datetime) -> dict[str, list[OpenOrderRecord]]:
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    orders_by_symbol: dict[str, list[OpenOrderRecord]] = {}
    for symbol in symbols:
        orders_by_symbol[symbol] = [
            OpenOrderRecord.from_account_order(order, fetched_at=fetched_at)
            for order in client.open_orders(symbol)
        ]
    return orders_by_symbol


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true", help="replace the current-state orders snapshot")
    parser.add_argument("--data-dir", type=Path, default=None, help="runtime data directory override")
    parser.add_argument("--symbols", default=None, help="comma-separated symbols (default: derived)")
    args = parser.parse_args()

    if args.data_dir is not None:
        os.environ["CRYPTO_PORTFOLIO_DATA_DIR"] = str(args.data_dir)

    symbols = (
        [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        if args.symbols else _default_symbols()
    )
    if not symbols:
        _fail("no symbols to fetch: pass --symbols or persist a snapshot first")

    config = load_provider_config()
    if not provider_enabled(PROVIDER_NAME, config):
        _fail(
            f"{PROVIDER_NAME} is not enabled; set BINANCE_API_KEY and BINANCE_API_SECRET "
            "(read-only key) in the environment"
        )
    client = BinanceAccountClient.from_environment(
        HttpClient(),
        api_key=provider_api_key(PROVIDER_NAME, config),
        api_secret=provider_api_secret(PROVIDER_NAME, config),
    )
    if client is None:
        _fail("binance_account credentials are missing")

    now = datetime.now(timezone.utc)
    orders_by_symbol = acquire(symbols=symbols, client=client, now=now)
    summary: dict[str, Any] = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": symbols,
        "orders": {
            symbol: [order.as_dict() for order in orders_by_symbol[symbol]]
            for symbol in symbols
        },
    }
    if args.persist:
        path = write_open_orders(orders_by_symbol, fetched_at=summary["fetched_at"])
        summary["persisted"] = {"path": str(path)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
