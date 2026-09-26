"""Structural point-in-time factor panel (Strategy V2.2 Phase D).

Research-only diagnostics: raw structural factor values per (symbol,
moment) from the Phase D DeFiLlama series, every read point-in-time through
the ObservationSeries availability contract. Nothing here grants position
authority; the panel exists so structural ranking power can be measured
before any of it may influence conviction (plan 7.8 / 8.7).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

from ..models.time import parse_timestamp
from .evidence_series import EvidenceContext

# Preregistered panel factors per symbol. A factor whose lookback is not
# available at a moment stays MISSING (absent); it is never neutralized.
PANEL_FACTORS: Mapping[str, tuple[str, ...]] = {
    "AAVE": (
        "protocol_tvl_growth_30d",
        "protocol_tvl_growth_90d",
        "protocol_tvl_growth_180d",
        "protocol_borrow_growth_90d",
        "protocol_utilization",
        "protocol_utilization_change_90d",
        "protocol_fees_growth_30d",
        "protocol_fees_growth_90d",
        "protocol_fees_30d_usd",
    ),
    "SOL": (
        "chain_tvl_growth_30d",
        "chain_tvl_growth_90d",
        "chain_tvl_growth_180d",
        "chain_fees_growth_30d",
        "chain_fees_growth_90d",
        "chain_fees_30d_usd",
        "chain_stablecoin_growth_90d",
        "chain_stablecoin_growth_180d",
    ),
    "BNB": (
        "chain_tvl_growth_30d",
        "chain_tvl_growth_90d",
        "chain_tvl_growth_180d",
        "chain_fees_growth_30d",
        "chain_fees_growth_90d",
        "chain_fees_30d_usd",
        "chain_stablecoin_growth_90d",
        "chain_stablecoin_growth_180d",
    ),
}


def _growth(series: Any, as_of: str, days: int) -> float | None:
    return series.change_over_days(as_of, days) if series is not None else None


def _sum(series: Any, as_of: str, days: int) -> float | None:
    return series.sum_over_days(as_of, days) if series is not None else None


def structural_factors(
    context: EvidenceContext, symbol: str, as_of: str
) -> dict[str, float | None]:
    """Raw panel factor values for one symbol at one review moment."""
    series = (context.structural or {}).get(symbol)
    if not series:
        return {name: None for name in PANEL_FACTORS.get(symbol, ())}
    if symbol == "AAVE":
        tvl = series.get("tvl")
        borrowed = series.get("borrowed")
        fees = series.get("fees")
        latest_tvl = tvl.latest_as_of(as_of) if tvl is not None else None
        latest_borrowed = (
            borrowed.latest_as_of(as_of) if borrowed is not None else None
        )
        utilization = (
            latest_borrowed.value / latest_tvl.value
            if latest_tvl is not None and latest_borrowed is not None
            and latest_tvl.value > 0
            else None
        )
        utilization_change = None
        if tvl is not None and borrowed is not None:
            ninety_days_ago = (
                parse_timestamp(as_of) - timedelta(days=90)
            ).isoformat().replace("+00:00", "Z")
            base_tvl = tvl.latest_as_of(ninety_days_ago)
            base_borrowed = borrowed.latest_as_of(ninety_days_ago)
            if (
                base_tvl is not None and base_borrowed is not None
                and base_tvl.value > 0 and utilization is not None
            ):
                utilization_change = utilization - base_borrowed.value / base_tvl.value
        return {
            "protocol_tvl_growth_30d": _growth(tvl, as_of, 30),
            "protocol_tvl_growth_90d": _growth(tvl, as_of, 90),
            "protocol_tvl_growth_180d": _growth(tvl, as_of, 180),
            "protocol_borrow_growth_90d": _growth(borrowed, as_of, 90),
            "protocol_utilization": utilization,
            "protocol_utilization_change_90d": utilization_change,
            "protocol_fees_growth_30d": _growth(fees, as_of, 30),
            "protocol_fees_growth_90d": _growth(fees, as_of, 90),
            "protocol_fees_30d_usd": _sum(fees, as_of, 30),
        }
    chain_tvl = series.get("chain_tvl")
    fees = series.get("fees")
    stablecoins = series.get("stablecoins")
    return {
        "chain_tvl_growth_30d": _growth(chain_tvl, as_of, 30),
        "chain_tvl_growth_90d": _growth(chain_tvl, as_of, 90),
        "chain_tvl_growth_180d": _growth(chain_tvl, as_of, 180),
        "chain_fees_growth_30d": _growth(fees, as_of, 30),
        "chain_fees_growth_90d": _growth(fees, as_of, 90),
        "chain_fees_30d_usd": _sum(fees, as_of, 30),
        "chain_stablecoin_growth_90d": _growth(stablecoins, as_of, 90),
        "chain_stablecoin_growth_180d": _growth(stablecoins, as_of, 180),
    }


def structural_panel(
    context: EvidenceContext,
    moments: Sequence[str],
    *,
    symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Panel rows plus coverage diagnostics over the review moments.

    Coverage is reported per symbol as the share of moments with at least
    one available factor and the per-factor availability shares, so the
    strict-replay coverage claim is explicit instead of implied.
    """
    selected = tuple(symbols) if symbols is not None else tuple(PANEL_FACTORS)
    rows: list[dict[str, Any]] = []
    for raw_moment in moments:
        as_of = str(raw_moment)
        for symbol in selected:
            factors = structural_factors(context, symbol, as_of)
            rows.append({
                "symbol": symbol,
                "timestamp": as_of,
                "factors": factors,
            })
    coverage: dict[str, Any] = {}
    for symbol in selected:
        symbol_rows = [row for row in rows if row["symbol"] == symbol]
        names = PANEL_FACTORS.get(symbol, ())
        per_factor = {
            name: (
                sum(
                    1 for row in symbol_rows if row["factors"].get(name) is not None
                ) / len(symbol_rows)
                if symbol_rows else None
            )
            for name in names
        }
        coverage[symbol] = {
            "moments": len(symbol_rows),
            "moments_with_any_factor": sum(
                1 for row in symbol_rows
                if any(value is not None for value in row["factors"].values())
            ),
            "per_factor_availability": per_factor,
        }
    return {
        "contract": "POINT_IN_TIME_STRUCTURAL_PANEL",
        "missing_semantics": "MISSING factors stay missing; no neutral backfill",
        "rows": rows,
        "coverage": coverage,
    }


__all__ = [
    "PANEL_FACTORS",
    "structural_factors",
    "structural_panel",
]
