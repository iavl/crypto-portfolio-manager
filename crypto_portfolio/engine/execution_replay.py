"""Conservative, no-lookahead simulation of conditional limit proposals."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from ..models.time import parse_timestamp


def simulate_execution_plan(
    plan: Mapping[str, Any],
    bars: Iterable[Mapping[str, Any]],
    *,
    decision_as_of: str,
) -> dict[str, Any]:
    """Simulate proposal fills after the decision boundary.

    A wick touching a zone is insufficient. A bar must open or close at or
    below the zone high, and at most one tranche progresses per bar. An
    optional ``fill_fraction`` models partial liquidity and defaults to one.
    ``plan_valid=false`` expires or invalidates the remaining proposal before
    that bar can fill it. These future fields are evaluation labels only.
    """

    if str(plan.get("action", "")).upper() == "WAIT":
        return {
            "status": "WAIT",
            "filled_amount_usd": 0.0,
            "events": [],
            "unfilled_amount_usd": float(plan.get("approved_amount_usd", 0.0)),
        }
    tranches = sorted(plan.get("tranches", ()), key=lambda item: item["sequence"])
    remaining = {item["sequence"]: float(item["amount_usd"]) for item in tranches}
    decision_time = parse_timestamp(decision_as_of)
    prior_time = decision_time
    events: list[dict[str, Any]] = []
    invalidated = False
    for raw_bar in bars:
        if not isinstance(raw_bar, Mapping):
            raise ValueError("execution bars must be objects")
        required = {"timestamp", "open", "high", "low", "close"}
        if required - set(raw_bar):
            raise ValueError("execution bar is missing OHLC fields")
        moment = parse_timestamp(raw_bar["timestamp"])
        if moment <= prior_time:
            raise ValueError("execution bars must be unique, ordered, and after the decision")
        prior_time = moment
        if raw_bar.get("plan_valid", True) is not True:
            invalidated = True
            events.append({"timestamp": raw_bar["timestamp"], "status": "PLAN_INVALIDATED"})
            break
        values = {}
        for name in ("open", "high", "low", "close"):
            value = raw_bar[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"execution bar {name} must be numeric")
            values[name] = float(value)
            if not math.isfinite(values[name]) or values[name] <= 0:
                raise ValueError(f"execution bar {name} must be finite and positive")
        if not values["low"] <= min(values["open"], values["close"]) <= max(
            values["open"], values["close"]
        ) <= values["high"]:
            raise ValueError("execution bar OHLC values are inconsistent")
        raw_fraction = raw_bar.get("fill_fraction", 1.0)
        if (
            isinstance(raw_fraction, bool)
            or not isinstance(raw_fraction, (int, float))
            or not math.isfinite(float(raw_fraction))
            or not 0 < float(raw_fraction) <= 1
        ):
            raise ValueError("fill_fraction must be in (0, 1]")
        fraction = float(raw_fraction)
        for tranche in tranches:
            sequence = tranche["sequence"]
            if remaining[sequence] <= 1e-9:
                continue
            low, high = float(tranche["price_low"]), float(tranche["price_high"])
            touched = values["low"] <= high and values["high"] >= low
            confirmed = touched and (values["open"] <= high or values["close"] <= high)
            if touched and not confirmed:
                events.append(
                    {
                        "timestamp": raw_bar["timestamp"],
                        "sequence": sequence,
                        "status": "WICK_TOUCH_NOT_FILLED",
                    }
                )
                break
            if confirmed:
                amount = remaining[sequence] * fraction
                remaining[sequence] -= amount
                fill_price = min(
                    float(tranche["reference_price"]),
                    values["open"] if values["open"] <= high else values["close"],
                )
                events.append(
                    {
                        "timestamp": raw_bar["timestamp"],
                        "sequence": sequence,
                        "status": "FILLED" if remaining[sequence] <= 1e-9 else "PARTIAL_FILL",
                        "amount_usd": amount,
                        "fill_price": fill_price,
                    }
                )
                break
    filled = sum(float(item["amount_usd"]) - remaining[item["sequence"]] for item in tranches)
    planned = float(plan.get("planned_amount_usd", sum(float(item["amount_usd"]) for item in tranches)))
    if filled > planned + 1e-7:
        raise ValueError("simulated fills exceed the planned amount")
    return {
        "status": (
            "INVALIDATED"
            if invalidated
            else "FILLED"
            if math.isclose(filled, planned, abs_tol=1e-7) and planned > 0
            else "PARTIAL_FILL"
            if filled > 0
            else "NOT_FILLED"
        ),
        "filled_amount_usd": filled,
        "unfilled_amount_usd": max(0.0, planned - filled),
        "events": events,
    }


__all__ = ["simulate_execution_plan"]
