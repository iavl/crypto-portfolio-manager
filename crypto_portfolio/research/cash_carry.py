"""Point-in-time cash carry for high-cash strategies (V2.3 Phase 4).

The replay used to book the stable sleeve at exactly zero return, which
understates a mandate that spends most of its life in cash and biases every
strategy-vs-benchmark comparison. This module defines the carry conventions
and the single deterministic adjustment applied IDENTICALLY to the strategy
path and to every cash-holding benchmark path:

- ``ZERO`` — the frozen prior behavior; nothing is credited.
- ``RISK_FREE_PROXY`` — a point-in-time risk-free proxy (e.g. FRED DFF,
  the effective federal funds rate) credited on the cash share of each
  valuation period.
- ``INVESTABLE_STABLE_SENSITIVITY`` — research-only sensitivity: an
  investable stablecoin yield is NOT assumed to equal the risk-free rate;
  it is the proxy minus an explicit haircut that prices issuer, depeg,
  custody, smart-contract, and liquidity risk.

The convention is accounting, never an engine input: allocations, scores,
and risk sizing never see it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..engine.backtest import ValuationPoint
from ..models.time import parse_timestamp

CASH_CARRY_MODES = ("ZERO", "RISK_FREE_PROXY", "INVESTABLE_STABLE_SENSITIVITY")

# The risk classes an investable stablecoin yield must be haircut for before
# it may be compared with a Treasury-backed risk-free rate. Research-only.
STABLE_HAIRCUT_COMPONENTS = (
    "issuer_risk",
    "depeg_risk",
    "custody_risk",
    "smart_contract_risk",
    "liquidity_risk",
)

_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class CashCarryConvention:
    """Validated carry convention plus its point-in-time rate source.

    ``rate_points`` is a strictly increasing (timestamp, annual-rate decimal
    fraction) sequence; the rate applied to a period is the last point at or
    before the period start, publication-aware when callers derive it from
    an ``ObservationSeries``. Required for RISK_FREE_PROXY; an absent rate
    credits zero carry for that period (never a fabricated rate).
    """

    mode: str
    rate_points: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in CASH_CARRY_MODES:
            raise ValueError(
                "cash carry mode must be one of " + ", ".join(CASH_CARRY_MODES)
            )
        points: list[tuple[str, float]] = []
        previous = None
        for timestamp, rate in self.rate_points:
            moment = parse_timestamp(timestamp)
            if previous is not None and moment <= previous:
                raise ValueError("rate points must be strictly increasing")
            previous = moment
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
                    or not math.isfinite(float(rate)) or float(rate) <= -1.0:
                raise ValueError("rate points must be finite fractions > -100%")
            points.append((moment.isoformat().replace("+00:00", "Z"), float(rate)))
        object.__setattr__(self, "rate_points", tuple(points))

    def rate_at(self, timestamp: str) -> float | None:
        """Annual rate fraction in force at ``timestamp`` (None = unknown)."""
        if self.mode == "ZERO":
            return 0.0
        moment = parse_timestamp(timestamp)
        current = None
        for point_timestamp, rate in self.rate_points:
            if parse_timestamp(point_timestamp) <= moment:
                current = rate
            else:
                break
        return current

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "rate_points": list(self.rate_points)}


def rate_points_from_percent_series(series: Any) -> tuple[tuple[str, float], ...]:
    """(timestamp, fraction) points from a percent-unit ObservationSeries."""
    points = tuple(series.points)
    if not points:
        raise ValueError("rate series is empty")
    return tuple(
        (point.observed_at, float(point.value) / 100.0) for point in points
    )


def period_carry_return(annual_rate: float, days: float) -> float:
    """Fractional carry of one period: ``(1 + r) ** (days / 365.25) - 1``."""
    if days < 0:
        raise ValueError("period length must be non-negative")
    return (1.0 + float(annual_rate)) ** (days / _DAYS_PER_YEAR) - 1.0


def investable_stable_yield(
    risk_free_rate: float, haircut_fraction: float
) -> dict[str, Any]:
    """Investable stable yield = risk-free proxy minus an explicit haircut.

    Research-only sensitivity input: the haircut must price every component
    in ``STABLE_HAIRCUT_COMPONENTS``; assuming ``USDT = risk-free 5%`` is
    exactly what this function forbids.
    """
    rate = float(risk_free_rate)
    haircut = float(haircut_fraction)
    if haircut < 0 or haircut > 1:
        raise ValueError("haircut_fraction must be in [0, 1]")
    return {
        "risk_free_rate": rate,
        "haircut_fraction": haircut,
        "haircut_components": list(STABLE_HAIRCUT_COMPONENTS),
        "investable_stable_yield": rate - haircut,
    }


def _valuation_point(value: Any) -> ValuationPoint:
    if isinstance(value, ValuationPoint):
        return value
    return ValuationPoint(
        timestamp=value["timestamp"],
        total_value_usd=float(value["total_value_usd"]),
        cash_usd=float(value.get("cash_usd", 0.0)),
        positions_usd=dict(value.get("positions_usd", {})),
        weights=dict(value.get("weights", {})),
        drawdown=float(value.get("drawdown", 0.0)),
    )


def carry_adjusted_valuations(
    valuations: Sequence[Any],
    *,
    cash_symbols: Sequence[str],
    convention: CashCarryConvention,
) -> list[ValuationPoint]:
    """The same valuation path with each period's cash share earning carry.

    Period ``k`` grows by ``gross_return + cash_share x period_carry`` —
    the identical additive formula the existing zero-yield sensitivity
    diagnostic uses, so adjusted and unadjusted paths stay directly
    comparable. Drawdowns are recomputed on the adjusted path; a period
    whose rate is unknown credits zero carry (never a fabricated rate).
    """
    if len(valuations) < 2:
        raise ValueError("at least two valuation points are required")
    points = [_valuation_point(item) for item in valuations]
    cash = {str(symbol).strip().upper() for symbol in cash_symbols}
    adjusted: list[ValuationPoint] = []
    peak = points[0].total_value_usd
    adjusted.append(ValuationPoint(
        points[0].timestamp, points[0].total_value_usd,
        points[0].cash_usd, dict(points[0].positions_usd),
        dict(points[0].weights), 0.0,
    ))
    for left, right in zip(points, points[1:]):
        gross = right.total_value_usd / left.total_value_usd - 1.0
        cash_share = sum(
            max(0.0, float(left.weights.get(symbol, 0.0))) for symbol in cash
        )
        rate = convention.rate_at(left.timestamp)
        days = (
            parse_timestamp(right.timestamp) - parse_timestamp(left.timestamp)
        ).total_seconds() / 86400.0
        carry = 0.0 if rate is None else cash_share * period_carry_return(rate, days)
        growth = gross + carry
        value = adjusted[-1].total_value_usd * (1.0 + growth)
        peak = max(peak, value)
        scale = (1.0 + growth) / (1.0 + gross) if gross > -1.0 else 1.0
        adjusted.append(ValuationPoint(
            right.timestamp, value,
            right.cash_usd * scale,
            {symbol: amount * scale for symbol, amount in right.positions_usd.items()},
            dict(right.weights),
            value / peak - 1.0,
        ))
    return adjusted


def carry_contribution(
    valuations: Sequence[Any],
    *,
    cash_symbols: Sequence[str],
    convention: CashCarryConvention,
) -> dict[str, Any]:
    """Total carry added by the convention, for reporting and parity audits."""
    points = [_valuation_point(item) for item in valuations]
    adjusted = carry_adjusted_valuations(
        points, cash_symbols=cash_symbols, convention=convention,
    )
    raw_total = points[-1].total_value_usd / points[0].total_value_usd - 1.0
    adjusted_total = (
        adjusted[-1].total_value_usd / adjusted[0].total_value_usd - 1.0
    )
    return {
        "mode": convention.mode,
        "rate_points": len(convention.rate_points),
        "total_return_zero_carry": raw_total,
        "total_return_with_carry": adjusted_total,
        "carry_contribution": adjusted_total - raw_total,
    }


def benchmark_uses_cash(
    weights: Mapping[str, float], cash_symbols: Sequence[str]
) -> bool:
    """Whether a benchmark path holds a cash leg that must earn the carry."""
    cash = {str(symbol).strip().upper() for symbol in cash_symbols} | {"USD"}
    return any(
        float(weight) > 0 and str(symbol).strip().upper() in cash
        for symbol, weight in weights.items()
    )


def rate_at_factory(points: Sequence[tuple[str, float]]) -> Callable[[str], float | None]:
    convention = CashCarryConvention(mode="RISK_FREE_PROXY", rate_points=tuple(points))
    return convention.rate_at


__all__ = [
    "CASH_CARRY_MODES",
    "CashCarryConvention",
    "STABLE_HAIRCUT_COMPONENTS",
    "benchmark_uses_cash",
    "carry_adjusted_valuations",
    "carry_contribution",
    "investable_stable_yield",
    "period_carry_return",
    "rate_at_factory",
    "rate_points_from_percent_series",
]
