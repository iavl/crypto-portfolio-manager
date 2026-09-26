"""Per-asset BTC opportunity-cost attribution (Strategy V2.2 Phase C).

Every satellite dollar answers one question: did holding it beat holding
BTC instead? This module reconstructs that answer from the replay record
alone — the ledger trades, boundary prices, and realized-return labels the
backtest already produced — with no lookahead: entries are measured from
the daily close containing the fill, forward windows only use candles that
actually printed, and the portfolio-level attribution uses the exposure the
book carried into each period. Research diagnostics only; nothing here
feeds back into allocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from ..models.time import parse_timestamp

HORIZONS = (30, 90, 180)


@dataclass(frozen=True)
class PricePoint:
    timestamp: datetime
    close: float


@dataclass(frozen=True)
class SatelliteEntry:
    asset: str
    decision_as_of: str
    fill_timestamp: str
    amount_usd: float
    portfolio_value_usd: float
    allocated_weight: float
    entry_price_asset: float
    entry_price_btc: float
    conviction_state: str | None
    reason: str


def _price_history(value: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, tuple[PricePoint, ...]]:
    result: dict[str, tuple[PricePoint, ...]] = {}
    for raw_symbol, rows in value.items():
        points = sorted(
            (
                PricePoint(parse_timestamp(row["timestamp"]), float(row["close"]))
                for row in rows
            ),
            key=lambda item: item.timestamp,
        )
        if any(point.close <= 0 for point in points):
            raise ValueError("opportunity-cost prices must be positive")
        result[str(raw_symbol).strip().upper()] = tuple(points)
    return result


def _close_at_or_after(points: Sequence[PricePoint], moment: datetime) -> PricePoint | None:
    return next((point for point in points if point.timestamp >= moment), None)


def _forward_return(
    points: Sequence[PricePoint], base: PricePoint, horizon: int
) -> float | None:
    endpoint = _close_at_or_after(points, base.timestamp + timedelta(days=horizon))
    if endpoint is None:
        return None
    return endpoint.close / base.close - 1.0


def satellite_entries(
    rows: Sequence[Mapping[str, Any]],
    reviews: Sequence[Any],
    *,
    satellite_symbols: Sequence[str],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[SatelliteEntry]:
    """Every satellite BUY fill with its entry context (plan 6.2).

    The allocated weight is the fill notional over the portfolio value at
    the period start (the economic base the decision could see), and the
    entry prices are the daily closes containing the fill for the asset and
    BTC alike, so the forward comparison is apples-to-apples.
    """
    history = _price_history(prices)
    satellites = {str(symbol).strip().upper() for symbol in satellite_symbols}
    entries: list[SatelliteEntry] = []
    previous_end_value: float | None = None
    for row, review in zip(rows, reviews):
        period_start_value = (
            float(review.portfolio_value) if previous_end_value is None
            else previous_end_value
        )
        previous_end_value = float(row["end_value_usd"])
        allowances = (row.get("allocation") or {}).get("deployment_allowances") or {}
        for trade in row.get("trades") or ():
            symbol = str(trade["symbol"]).strip().upper()
            if symbol not in satellites or str(trade["side"]).upper() != "BUY":
                continue
            fill = parse_timestamp(trade["timestamp"])
            asset_point = _close_at_or_after(history.get(symbol, ()), fill)
            btc_point = _close_at_or_after(history.get("BTC", ()), fill)
            # The base must be the daily close covering the fill, not a
            # later close that already contains post-fill drift.
            tolerance = timedelta(days=2)
            if (
                asset_point is None
                or btc_point is None
                or asset_point.timestamp - fill > tolerance
                or btc_point.timestamp - fill > tolerance
            ):
                raise ValueError(
                    f"no priced daily candle covers the {symbol} fill at {trade['timestamp']}"
                )
            entries.append(SatelliteEntry(
                asset=symbol,
                decision_as_of=str(row["as_of"]),
                fill_timestamp=str(trade["timestamp"]),
                amount_usd=float(trade["gross_notional_usd"]),
                portfolio_value_usd=period_start_value,
                allocated_weight=float(trade["gross_notional_usd"]) / period_start_value,
                entry_price_asset=asset_point.close,
                entry_price_btc=btc_point.close,
                conviction_state=(
                    str(allowances[symbol].get("conviction_state"))
                    if symbol in allowances else None
                ),
                reason=str(trade.get("reason", "")),
            ))
    return entries


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def forward_opportunity_cost(
    entries: Sequence[SatelliteEntry],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    horizons: Sequence[int] = HORIZONS,
) -> list[dict[str, Any]]:
    """Forward ETH-free BTC-relative excess per entry (plan 6.3).

    Raw BTC-relative return is primary; a risk-adjusted view belongs to the
    strategy's own risk engine, not to this label.
    """
    history = _price_history(prices)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        fill = parse_timestamp(entry.fill_timestamp)
        asset_base = _close_at_or_after(history.get(entry.asset, ()), fill)
        btc_base = _close_at_or_after(history.get("BTC", ()), fill)
        labels: dict[str, Any] = {}
        for raw_horizon in horizons:
            horizon = int(raw_horizon)
            if asset_base is None or btc_base is None:
                labels[str(horizon)] = {"status": "PENDING"}
                continue
            asset_forward = _forward_return(history.get(entry.asset, ()), asset_base, horizon)
            btc_forward = _forward_return(history.get("BTC", ()), btc_base, horizon)
            if asset_forward is None or btc_forward is None:
                labels[str(horizon)] = {"status": "PENDING"}
            else:
                labels[str(horizon)] = {
                    "status": "AVAILABLE",
                    "relative_alpha_vs_btc": asset_forward - btc_forward,
                    "asset_forward_return": asset_forward,
                    "btc_forward_return": btc_forward,
                }
        rows.append({
            "asset": entry.asset,
            "decision_as_of": entry.decision_as_of,
            "fill_timestamp": entry.fill_timestamp,
            "allocated_weight": entry.allocated_weight,
            "amount_usd": entry.amount_usd,
            "conviction_state": entry.conviction_state,
            "labels": labels,
        })
    return rows


def _satellite_weight_path(
    rows: Sequence[Mapping[str, Any]],
    reviews: Sequence[Any],
    *,
    symbol: str,
) -> list[float]:
    """Start-of-period portfolio weight of one satellite, per review.

    Quantities evolve only through the replay's own trades (buys add, sells
    subtract); the weight is that quantity marked at the boundary price over
    the boundary portfolio value, so partial exits shrink the exposure the
    next period is attributed against.
    """
    quantities: list[float] = []
    result: list[float] = []
    quantity = 0.0
    previous_end_value: float | None = None
    for index, (row, review) in enumerate(zip(rows, reviews)):
        if symbol not in review.current_prices:
            # Never traded and never priced at a boundary: zero exposure.
            result.append(0.0)
            continue
        start_price = float(review.current_prices[symbol])
        if index == 0:
            initial_weight = float(review.current_weights.get(symbol, 0.0))
            quantity = (
                initial_weight * float(review.portfolio_value) / start_price
            )
        period_start_value = (
            float(review.portfolio_value) if previous_end_value is None
            else previous_end_value
        )
        previous_end_value = float(row["end_value_usd"])
        result.append(quantity * start_price / period_start_value)
        for trade in row.get("trades") or ():
            if str(trade["symbol"]).strip().upper() != symbol:
                continue
            if str(trade["side"]).upper() == "BUY":
                quantity += float(trade["quantity"])
            else:
                quantity -= float(trade["quantity"])
        quantities.append(quantity)
    return result


def satellite_alpha_attribution(
    rows: Sequence[Mapping[str, Any]],
    reviews: Sequence[Any],
    *,
    satellite_symbols: Sequence[str],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, Any]:
    """Full satellite opportunity-cost report (plans 6.4-6.6).

    Per asset: entry count, average allocated weight, mean/median forward
    excess at every horizon, win rate vs BTC, and the position-weighted
    portfolio contribution (sum over periods of the start-of-period weight
    times the asset's BTC-relative realized return). The total satellite
    alpha contribution aggregates the per-asset contributions, and the BTC
    foregone return states what the displaced BTC exposure would have
    earned on the same weights.
    """
    history = _price_history(prices)
    entries = satellite_entries(
        rows, reviews, satellite_symbols=satellite_symbols, prices=prices,
    )
    labeled = forward_opportunity_cost(entries, prices, horizons=horizons)
    assets = sorted({str(symbol).strip().upper() for symbol in satellite_symbols})
    per_asset: dict[str, Any] = {}
    total_contribution = 0.0
    total_btc_foregone = 0.0
    for symbol in assets:
        symbol_entries = [row for row in labeled if row["asset"] == symbol]
        horizon_stats: dict[str, Any] = {}
        for raw_horizon in horizons:
            horizon = str(int(raw_horizon))
            values = [
                row["labels"][horizon]["relative_alpha_vs_btc"]
                for row in symbol_entries
                if row["labels"][horizon]["status"] == "AVAILABLE"
            ]
            horizon_stats[horizon] = {
                "samples": len(values),
                "mean_excess": sum(values) / len(values) if values else None,
                "median_excess": _median(values),
                "win_rate_vs_btc": (
                    sum(1 for value in values if value > 0) / len(values)
                    if values else None
                ),
            }
        weights = _satellite_weight_path(rows, reviews, symbol=symbol)
        contribution = 0.0
        btc_foregone = 0.0
        held_periods = 0
        for weight, review in zip(weights, reviews):
            label = review.next_returns
            asset_return = label.get(symbol)
            btc_return = label.get("BTC")
            if asset_return is None or btc_return is None:
                continue
            held_periods += 1
            contribution += weight * (float(asset_return) - float(btc_return))
            btc_foregone += weight * float(btc_return)
        per_asset[symbol] = {
            "entries": len(symbol_entries),
            "average_allocated_weight": (
                sum(row["allocated_weight"] for row in symbol_entries) / len(symbol_entries)
                if symbol_entries else None
            ),
            "horizons": horizon_stats,
            "position_weighted_contribution": contribution,
            "btc_foregone_return": btc_foregone,
            "periods_held": held_periods,
        }
        total_contribution += contribution
        total_btc_foregone += btc_foregone
    # TACTICAL_ONLY entries are reported separately from full-conviction
    # ones (plan 6.5): the two paths must never be judged as one sample.
    by_conviction: dict[str, dict[str, int]] = {}
    for row in labeled:
        state = row["conviction_state"] or "UNKNOWN"
        bucket = by_conviction.setdefault(state, {"entries": 0, "wins_90d": 0})
        bucket["entries"] += 1
        label = row["labels"].get("90")
        if label and label["status"] == "AVAILABLE" and label["relative_alpha_vs_btc"] > 0:
            bucket["wins_90d"] += 1
    return {
        "contract": "REPLAY_RECORD_ONLY_NO_LOOKAHEAD",
        "horizons": [int(horizon) for horizon in horizons],
        "assets": per_asset,
        "total_satellite_alpha_contribution": total_contribution,
        "btc_foregone_return": total_btc_foregone,
        "entries_by_conviction": by_conviction,
        "entries": [
            {
                "asset": row["asset"],
                "decision_as_of": row["decision_as_of"],
                "fill_timestamp": row["fill_timestamp"],
                "allocated_weight": row["allocated_weight"],
                "conviction_state": row["conviction_state"],
                "labels": row["labels"],
            }
            for row in labeled
        ],
        "priced_symbols": sorted(history),
    }


__all__ = [
    "HORIZONS",
    "SatelliteEntry",
    "forward_opportunity_cost",
    "satellite_alpha_attribution",
    "satellite_entries",
]
