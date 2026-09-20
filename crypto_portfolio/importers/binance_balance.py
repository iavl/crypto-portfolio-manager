"""Convert read-only Binance account API fetches into a portfolio snapshot.

Pure, offline conversion: the signed client (providers.binance_account)
performs all network I/O and produces WalletBalance / FlowEvent records;
this module merges wallets, applies deterministic USD valuation, derives
the exchange-confirmed cash-flow state, and emits the same canonical
snapshot mapping the screenshot importer produces.  Cost-basis fields are
intentionally absent: the API does not expose average cost, so position
P&L cost checks degrade to INSUFFICIENT_DATA instead of guessing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ..models.policy import Policy
from ..models.portfolio import PortfolioSnapshot, normalize_snapshot, snapshot_from_mapping
from ..models.time import normalize_timestamp
from ..providers.binance_account import FlowEvent, WalletBalance


BINANCE_API_ACCOUNT_SOURCE = "binance_api_account"
# USD-pegged assets valued at 1.0 when no explicit price is supplied.
STABLE_USD_SYMBOLS = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP"}
# Spot and the eth-staking endpoint can both report WBETH; beyond this
# relative gap the endpoints disagree about the holding itself.
_WBETH_CROSS_CHECK_TOLERANCE = 0.05


def _price_for(symbol: str, prices: Mapping[str, float]) -> float:
    price = 1.0 if symbol in STABLE_USD_SYMBOLS else prices.get(symbol)
    if price is None:
        raise ValueError(
            f"no USD price available for {symbol}; provide a price or exclude the symbol explicitly"
        )
    price = float(price)
    if not math.isfinite(price) or price < 0:
        raise ValueError(f"price for {symbol} must be a finite non-negative number")
    return price


@dataclass(frozen=True)
class ValuedFlowEvent:
    """A completed deposit/withdrawal with its deterministic USD value."""

    event: FlowEvent
    usd_value: float  # signed: positive for deposits, negative for withdrawals

    def __post_init__(self) -> None:
        if not isinstance(self.event, FlowEvent):
            raise ValueError("ValuedFlowEvent.event must be a FlowEvent")
        value = float(self.usd_value)
        if not math.isfinite(value) or value == 0:
            raise ValueError("ValuedFlowEvent.usd_value must be finite and non-zero")
        object.__setattr__(self, "usd_value", value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.event.direction,
            "asset": self.event.asset,
            "amount": self.event.amount,
            "timestamp": datetime.fromtimestamp(
                self.event.timestamp_ms / 1000, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "usd_value": self.usd_value,
        }


@dataclass(frozen=True)
class BinanceAccountFetch:
    """Everything one read-only account fetch observed, pre-valuation."""

    captured_at: str
    spot: tuple[WalletBalance, ...] = ()
    flexible: tuple[WalletBalance, ...] = ()
    locked: tuple[WalletBalance, ...] = ()
    wbeth_staking: WalletBalance | None = None
    prices: Mapping[str, float] = field(default_factory=dict)
    flow_events: tuple[FlowEvent, ...] = ()
    # (asset, event) -> USD price for a completed non-stable flow event.
    # Typically the UTC-day 1d close from the public klines endpoint.
    flow_price: Callable[[str, FlowEvent], float] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "captured_at", normalize_timestamp(self.captured_at, "captured_at"))
        for name in ("spot", "flexible", "locked"):
            value = tuple(getattr(self, name))
            if any(not isinstance(item, WalletBalance) for item in value):
                raise ValueError(f"Binance account fetch {name} must contain WalletBalance objects")
            object.__setattr__(self, name, value)
        flow_events = tuple(self.flow_events)
        if any(not isinstance(item, FlowEvent) for item in flow_events):
            raise ValueError("Binance account fetch flow_events must contain FlowEvent objects")
        object.__setattr__(self, "flow_events", flow_events)
        if self.wbeth_staking is not None and not isinstance(self.wbeth_staking, WalletBalance):
            raise ValueError("Binance account fetch wbeth_staking must be a WalletBalance or null")
        if not isinstance(self.prices, Mapping):
            raise ValueError("Binance account fetch prices must be a mapping")


@dataclass(frozen=True)
class BinanceAccountImport:
    """Conversion output: canonical snapshot mapping plus diagnostics."""

    snapshot_mapping: dict[str, Any]
    warnings: tuple[str, ...]
    flow_summary: dict[str, Any]

    def to_snapshot(self, *, policy: Policy | None = None) -> PortfolioSnapshot:
        return snapshot_from_mapping(self.snapshot_mapping, policy=policy)[0]

    def normalize(self, *, policy: Policy | None = None) -> dict[str, Any]:
        result = normalize_snapshot(self.snapshot_mapping, policy=policy)
        result["warnings"].extend(self.warnings)
        return result


def _merge_positions(
    fetch: BinanceAccountFetch,
    *,
    exclude_symbols: Iterable[str],
    min_value_usd: float,
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Merge wallet balances per asset and value them; fail closed on gaps."""
    exclusions = {str(symbol).strip().upper() for symbol in exclude_symbols}
    quantities: dict[str, float] = {}
    for wallet in (*fetch.spot, *fetch.flexible, *fetch.locked):
        quantities[wallet.asset] = quantities.get(wallet.asset, 0.0) + wallet.quantity
    warnings: list[str] = []
    if fetch.wbeth_staking is not None:
        staking_quantity = fetch.wbeth_staking.quantity
        spot_quantity = quantities.get("WBETH")
        if spot_quantity is None:
            quantities["WBETH"] = staking_quantity
        else:
            # Spot is authoritative; the staking endpoint is a cross-check.
            gap = abs(spot_quantity - staking_quantity) / max(spot_quantity, staking_quantity, 1e-12)
            if gap > _WBETH_CROSS_CHECK_TOLERANCE:
                raise ValueError(
                    f"WBETH cross-check failed: spot {spot_quantity} vs eth-staking "
                    f"{staking_quantity} (relative gap {gap:.2%}); refusing to pick one"
                )
            if gap > 1e-6:
                warnings.append(
                    f"WBETH spot {spot_quantity} and eth-staking {staking_quantity} differ "
                    f"by {gap:.4%}; using the spot quantity"
                )
    positions: dict[str, float] = {}
    prices_used: dict[str, float] = {}
    for symbol, quantity in sorted(quantities.items()):
        if quantity <= 0:
            continue
        price = _price_for(symbol, fetch.prices)
        value = quantity * price
        if symbol in exclusions:
            warnings.append(
                f"{symbol} excluded by explicit request (quantity {quantity}, value ${value:,.2f})"
            )
            continue
        if value < min_value_usd:
            warnings.append(
                f"{symbol} excluded by dust threshold (value ${value:,.2f} < ${min_value_usd:,.2f})"
            )
            continue
        positions[symbol] = quantity
        prices_used[symbol] = price
    if not positions:
        raise ValueError("Binance account fetch produced no valued positions")
    return positions, prices_used, warnings


def _derive_flow(
    fetch: BinanceAccountFetch, *, manual_flow: bool, warnings: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return snapshot cash-flow fields and a printable event summary."""
    completed: list[ValuedFlowEvent] = []
    in_flight: list[FlowEvent] = []
    for event in fetch.flow_events:
        if not event.completed:
            in_flight.append(event)
            continue
        if event.asset in STABLE_USD_SYMBOLS:
            price = 1.0
        elif fetch.flow_price is not None:
            price = float(fetch.flow_price(event.asset, event))
            if not math.isfinite(price) or price < 0:
                raise ValueError(f"flow price for {event.asset} must be finite and non-negative")
        else:
            raise ValueError(
                f"cannot value completed {event.direction} of {event.asset} without a "
                "flow price source; rerun with manual flow resolution instead"
            )
        signed = event.amount * price * (1.0 if event.direction == "DEPOSIT" else -1.0)
        completed.append(ValuedFlowEvent(event=event, usd_value=signed))
    for event in in_flight:
        warnings.append(
            f"{event.direction} of {event.amount} {event.asset} is still in flight "
            f"(raw status {event.raw_status}) and is not counted in the flow"
        )
    if manual_flow:
        net = None
        flow_fields = {
            "external_cash_flow": None,
            "external_cash_flow_type": None,
            "cash_flow_resolution_status": "UNRESOLVED",
        }
        summary_status = "UNRESOLVED"
    else:
        net = sum(item.usd_value for item in completed)
        if net > 0:
            flow_type = "DEPOSIT"
            status = "CONFIRMED_AMOUNT"
        elif net < 0:
            flow_type = "WITHDRAWAL"
            status = "CONFIRMED_AMOUNT"
        else:
            flow_type = "NONE"
            status = "CONFIRMED_NONE"
        flow_fields = {
            "external_cash_flow": float(net),
            "external_cash_flow_type": flow_type,
            "cash_flow_resolution_status": status,
            "cash_flow_classification_source": "EXCHANGE_DERIVED",
        }
        summary_status = status
    summary: dict[str, Any] = {
        "status": summary_status,
        "external_cash_flow": flow_fields["external_cash_flow"],
        "external_cash_flow_type": flow_fields["external_cash_flow_type"],
        "net_usd": net,
        "completed_events": [item.as_dict() for item in completed],
        "in_flight_events": [
            {
                "direction": item.direction,
                "asset": item.asset,
                "amount": item.amount,
                "raw_status": item.raw_status,
            }
            for item in in_flight
        ],
    }
    return flow_fields, summary


def snapshot_from_binance_account(
    fetch: BinanceAccountFetch,
    *,
    policy: Policy | None = None,
    manual_flow: bool = False,
    exclude_symbols: Iterable[str] = (),
    min_value_usd: float = 0.0,
) -> tuple[PortfolioSnapshot, Policy, list[str], BinanceAccountImport]:
    """Build the canonical snapshot from an account fetch (pure conversion)."""
    if not isinstance(fetch, BinanceAccountFetch):
        raise ValueError("fetch must be a BinanceAccountFetch")
    if (
        isinstance(min_value_usd, bool)
        or not isinstance(min_value_usd, (int, float))
        or not math.isfinite(float(min_value_usd))
        or min_value_usd < 0
    ):
        raise ValueError("min_value_usd must be a non-negative number")
    positions, prices, warnings = _merge_positions(
        fetch, exclude_symbols=exclude_symbols, min_value_usd=min_value_usd
    )
    flow_fields, flow_summary = _derive_flow(fetch, manual_flow=manual_flow, warnings=warnings)
    earn_quantities: dict[str, float] = {}
    for wallet in (*fetch.flexible, *fetch.locked):
        earn_quantities[wallet.asset] = earn_quantities.get(wallet.asset, 0.0) + wallet.quantity
    flow_summary["earn_value_usd"] = sum(
        quantity * _price_for(symbol, fetch.prices)
        for symbol, quantity in earn_quantities.items()
    )
    position_mappings = [
        {
            "symbol": symbol,
            "quantity": quantity,
            "current_price_usd": prices[symbol],
            "value_usd": quantity * prices[symbol],
        }
        for symbol, quantity in positions.items()
    ]
    total = sum(item["value_usd"] for item in position_mappings)
    mapping: dict[str, Any] = {
        "timestamp": fetch.captured_at,
        "source": BINANCE_API_ACCOUNT_SOURCE,
        "base_currency": "USD",
        "total_value": total,
        "positions": position_mappings,
        **flow_fields,
    }
    snapshot, resolved_policy, model_warnings = snapshot_from_mapping(mapping, policy=policy)
    warnings = model_warnings + warnings
    imported = BinanceAccountImport(
        snapshot_mapping=mapping,
        warnings=tuple(warnings),
        flow_summary=flow_summary,
    )
    return snapshot, resolved_policy, warnings, imported


__all__ = [
    "BINANCE_API_ACCOUNT_SOURCE",
    "BinanceAccountFetch",
    "BinanceAccountImport",
    "STABLE_USD_SYMBOLS",
    "ValuedFlowEvent",
    "snapshot_from_binance_account",
]
