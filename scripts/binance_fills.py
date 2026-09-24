#!/usr/bin/env python3
"""Fetch executed spot trades from the Binance read-only API into the fill store.

Appends exchange-confirmed executions (price, quantity, fee, timestamp) to the
append-only fill history used to attribute prior execution plans. Deduplicates
by symbol and exchange trade id, so re-running with an overlapping window only
adds new fills. Prints a summary; pass --persist to append.

Symbols default to the latest snapshot's non-stable holdings plus every symbol
with an executable tranche plan in recent decisions. Credentials come
exclusively from BINANCE_API_KEY / BINANCE_API_SECRET (read-only key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.importers.binance_balance import STABLE_USD_SYMBOLS
from crypto_portfolio.models.fill import TradeFill
from crypto_portfolio.models.time import parse_timestamp
from crypto_portfolio.providers.binance_account import BinanceAccountClient
from crypto_portfolio.providers.config import (
    load_provider_config,
    provider_api_key,
    provider_api_secret,
    provider_enabled,
)
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.state.decisions import read_decisions
from crypto_portfolio.state.fills import append_fills, read_fills
from crypto_portfolio.state.snapshots import default_snapshot_path, read_snapshots

PROVIDER_NAME = "binance_account"
_DEFAULT_LOOKBACK_DAYS = 30
_PLAN_ACTION_SYMBOLS = {"INCREASE", "REDUCE", "EXIT"}


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _default_symbols() -> list[str]:
    """Held non-stable assets from the latest snapshot, plus planned symbols."""
    symbols: set[str] = set()
    snapshots = read_snapshots(default_snapshot_path()) if default_snapshot_path().exists() else []
    if snapshots:
        for position in snapshots[-1].get("positions", ()):
            symbol = str(position.get("symbol", "")).strip().upper()
            if symbol and symbol not in STABLE_USD_SYMBOLS:
                symbols.add(symbol)
    for decision in read_decisions():
        for symbol, plan in (decision.get("execution_plans") or {}).items():
            action = str((plan or {}).get("action", "")).strip().upper()
            if action in _PLAN_ACTION_SYMBOLS:
                symbols.add(str(symbol).strip().upper())
    return sorted(symbols)


def _default_since_ms() -> int:
    """Earliest window the disposition needs: the oldest recent plan snapshot.

    Considers only the last 30 decisions (any resting order must come from a
    plan that recent); falls back to the latest persisted fill plus one day,
    else 30 days back.
    """
    starts: list[datetime] = []
    snapshots = read_snapshots(default_snapshot_path()) if default_snapshot_path().exists() else []
    by_id = {snapshot.get("snapshot_id"): snapshot for snapshot in snapshots}
    for decision in read_decisions()[-30:]:
        reference = by_id.get(decision.get("based_on_snapshot_id"))
        if reference is not None:
            starts.append(parse_timestamp(reference["timestamp"]))
    if starts:
        oldest = min(starts)
        lookback = max(starts) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        since = min(oldest, lookback)
        return int(since.timestamp() * 1000)
    fills = read_fills()
    if fills:
        latest = max(parse_timestamp(fill.executed_at) for fill in fills)
        return int((latest - timedelta(days=1)).timestamp() * 1000)
    return int((datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).timestamp() * 1000)


def acquire(
    *,
    symbols: list[str],
    since_ms: int,
    until_ms: int,
    client: BinanceAccountClient,
    now: datetime,
) -> list[TradeFill]:
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fills: list[TradeFill] = []
    for symbol in symbols:
        trades = client.my_trades(symbol, since_ms, until_ms)
        fills.extend(
            TradeFill.from_account_trade(trade, fetched_at=fetched_at) for trade in trades
        )
    return fills


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true", help="append new fills to the store")
    parser.add_argument("--data-dir", type=Path, default=None, help="runtime data directory override")
    parser.add_argument("--symbols", default=None, help="comma-separated symbols (default: derived)")
    parser.add_argument(
        "--since",
        default=None,
        help="window start, ISO datetime or Nn/Nd ago (default: derived from plans)",
    )
    parser.add_argument("--until", default=None, help="window end, ISO datetime (default: now)")
    args = parser.parse_args()

    if args.data_dir is not None:
        os.environ["CRYPTO_PORTFOLIO_DATA_DIR"] = str(args.data_dir)

    now = datetime.now(timezone.utc)
    until_ms = (
        int(parse_timestamp(args.until, "until").timestamp() * 1000)
        if args.until else int(now.timestamp() * 1000)
    )
    since_ms = _parse_since(args.since) if args.since else _default_since_ms()
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

    fills = acquire(symbols=symbols, since_ms=since_ms, until_ms=until_ms, client=client, now=now)
    summary: dict[str, Any] = {
        "window": {
            "since": datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).isoformat(),
            "until": datetime.fromtimestamp(until_ms / 1000, tz=timezone.utc).isoformat(),
        },
        "symbols": symbols,
        "fetched": {
            symbol: [
                fill.as_dict()
                for fill in fills
                if fill.symbol == symbol
            ]
            for symbol in symbols
        },
    }
    if args.persist:
        path, appended = append_fills(fills)
        summary["persisted"] = {"path": str(path), "appended": appended}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _parse_since(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("d") and normalized[:-1].isdigit():
        days = int(normalized[:-1])
        return int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    return int(parse_timestamp(value, "since").timestamp() * 1000)


if __name__ == "__main__":
    main()
