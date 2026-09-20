#!/usr/bin/env python3
"""Fetch portfolio holdings from the Binance read-only API into a snapshot.

Runs one fail-closed acquisition: spot balances, Simple Earn positions,
ETH staking (WBETH), USD valuation via public tickers, and completed
deposit/withdrawal history since the previous snapshot.  Prints the
normalized snapshot; pass --persist to append it to the snapshot store.

Credentials come exclusively from BINANCE_API_KEY / BINANCE_API_SECRET
(read-only key, no trade or withdrawal permission), typically loaded via:
    zsh -ic 'python3 scripts/binance_snapshot.py --persist'
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

from crypto_portfolio.importers.binance_balance import (
    STABLE_USD_SYMBOLS,
    BinanceAccountFetch,
    snapshot_from_binance_account,
)
from crypto_portfolio.models.portfolio import normalize_snapshot
from crypto_portfolio.models.time import parse_timestamp
from crypto_portfolio.providers.binance_account import BinanceAccountClient
from crypto_portfolio.providers.config import (
    load_provider_config,
    provider_api_key,
    provider_api_secret,
    provider_enabled,
)
from crypto_portfolio.providers.http import HttpClient
from crypto_portfolio.state.snapshots import append_snapshot, default_snapshot_path, read_snapshots

PROVIDER_NAME = "binance_account"
_STABLE_PEG_WARNING = 0.005
_EARN_CROSS_CHECK_WARNING = 0.05


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _utc_day_start_ms(timestamp_ms: int) -> int:
    day = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(day.timestamp() * 1000)


def _previous_snapshot_ms(snapshot_path: Path) -> int | None:
    records = read_snapshots(snapshot_path) if snapshot_path.exists() else []
    if not records:
        return None
    latest = max(parse_timestamp(record["timestamp"]) for record in records)
    return int(latest.timestamp() * 1000)


def _market_prices(
    client: BinanceAccountClient, symbols: set[str], warnings: list[str]
) -> dict[str, float]:
    """USD price per symbol: stables 1.0, others the <SYM>USDT ticker."""
    prices: dict[str, float] = {}
    eth_price = None
    for symbol in sorted(symbols):
        if symbol in STABLE_USD_SYMBOLS:
            prices[symbol] = 1.0
            continue
        if symbol == "WBETH":
            continue
        prices[symbol] = client.ticker_price(f"{symbol}USDT")
        if symbol == "ETH":
            eth_price = prices[symbol]
    if "WBETH" in symbols:
        rate = client.wbeth_exchange_rate()
        if rate is not None and eth_price is not None:
            prices["WBETH"] = rate * eth_price
        else:
            prices["WBETH"] = client.ticker_price("WBETHUSDT")
    if "USDT" in symbols or "USDC" in symbols:
        usdc_price = client.ticker_price("USDCUSDT")
        if abs(usdc_price - 1.0) > _STABLE_PEG_WARNING:
            warnings.append(
                f"stablecoin peg cross-check: USDCUSDT at {usdc_price:.4f} deviates from "
                f"1.0 by {abs(usdc_price - 1.0):.2%}; stable assets still valued at 1.0"
            )
    return prices


def _earn_cross_check(client: BinanceAccountClient, earn_value_usd: float, warnings: list[str]) -> None:
    totals = client.simple_earn_totals()
    if not isinstance(totals, dict):
        return
    reported = totals.get("totalAmountInUSDT")
    try:
        reported = float(reported) if reported is not None else None
    except (TypeError, ValueError):
        return
    if reported is None or reported <= 0 or earn_value_usd <= 0:
        return
    gap = abs(earn_value_usd - reported) / max(earn_value_usd, reported)
    if gap > _EARN_CROSS_CHECK_WARNING:
        warnings.append(
            f"Simple Earn valuation cross-check: computed ${earn_value_usd:,.2f} vs Binance "
            f"total ${reported:,.2f} (gap {gap:.2%})"
        )


def _default_client(config: dict[str, Any]) -> BinanceAccountClient | None:
    return BinanceAccountClient.from_environment(
        HttpClient(),
        api_key=provider_api_key(PROVIDER_NAME, config),
        api_secret=provider_api_secret(PROVIDER_NAME, config),
    )


def acquire(
    snapshot_path: Path,
    *,
    manual_flow: bool = False,
    exclude_symbols: list[str] | None = None,
    min_value_usd: float = 0.0,
    client_factory=_default_client,
    now: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the canonical snapshot mapping and its normalized report."""
    exclude_symbols = list(exclude_symbols or [])
    now = now or datetime.now(timezone.utc)
    config = load_provider_config()
    if not provider_enabled(PROVIDER_NAME, config):
        _fail(
            f"{PROVIDER_NAME} is not enabled; set BINANCE_API_KEY and BINANCE_API_SECRET "
            "(read-only key) in the environment"
        )
    client = client_factory(config)
    if client is None:
        _fail("binance_account credentials are missing")

    captured_at = now.isoformat()
    spot = client.spot_balances()
    flexible = client.flexible_earn_positions()
    locked = client.locked_earn_positions()
    wbeth_staking = client.eth_staking_wbeth()

    symbols = {balance.asset for balance in (*spot, *flexible, *locked)}
    if wbeth_staking is not None:
        symbols.add(wbeth_staking.asset)
    warnings: list[str] = []
    prices = _market_prices(client, symbols, warnings)

    since_ms = _previous_snapshot_ms(snapshot_path)
    now_ms = int(now.timestamp() * 1000)
    flow_events = ()
    if since_ms is not None:
        flow_events = client.deposit_history(since_ms, now_ms) + client.withdrawal_history(
            since_ms, now_ms
        )

    fetch = BinanceAccountFetch(
        captured_at=captured_at,
        spot=spot,
        flexible=flexible,
        locked=locked,
        wbeth_staking=wbeth_staking,
        prices=prices,
        flow_events=flow_events,
        flow_price=lambda asset, event: client.daily_close(
            f"{asset}USDT", _utc_day_start_ms(event.timestamp_ms)
        ),
    )
    _, _, _, imported = snapshot_from_binance_account(
        fetch,
        manual_flow=manual_flow,
        exclude_symbols=exclude_symbols,
        min_value_usd=min_value_usd,
    )
    _earn_cross_check(client, imported.flow_summary.get("earn_value_usd", 0.0), warnings)
    normalized = normalize_snapshot(imported.snapshot_mapping)
    normalized["warnings"].extend(warnings)
    normalized["flow_summary"] = imported.flow_summary
    return imported.snapshot_mapping, normalized


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true", help="append the snapshot to the store")
    parser.add_argument("--data-dir", type=Path, default=None, help="runtime data directory override")
    parser.add_argument("--exclude", action="append", default=[], help="symbol to exclude (repeatable)")
    parser.add_argument("--min-value-usd", type=float, default=0.0, help="dust exclusion threshold")
    parser.add_argument("--flow-manual", action="store_true", help="mark the flow UNRESOLVED instead of auto-confirming")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    if args.data_dir is not None:
        os.environ["CRYPTO_PORTFOLIO_DATA_DIR"] = str(args.data_dir)
    snapshot_path = default_snapshot_path()

    mapping, normalized = acquire(
        snapshot_path,
        manual_flow=args.flow_manual,
        exclude_symbols=args.exclude,
        min_value_usd=args.min_value_usd,
    )
    if args.persist:
        append_snapshot(mapping, snapshot_path)
        records = read_snapshots(snapshot_path)
        normalized["persisted_snapshot_id"] = records[-1]["snapshot_id"]
    print(json.dumps(normalized, ensure_ascii=False, indent=None if args.compact else 2))


if __name__ == "__main__":
    main()
