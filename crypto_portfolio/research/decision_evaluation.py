"""Post-decision mark-to-market evaluation without rewriting decision state."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..models.decision import Decision
from ..models.time import parse_timestamp
from ..state.decisions import _validated_decision, reconstruct_decision_status


_HORIZONS = (7, 30, 90, 180)


def _price_rows(value: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, tuple[tuple[Any, float], ...]]:
    result: dict[str, tuple[tuple[Any, float], ...]] = {}
    for raw_symbol, rows in value.items():
        symbol = str(raw_symbol).strip().upper()
        parsed = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"timestamp", "price"}:
                raise ValueError("price rows must contain exactly timestamp and price")
            moment = parse_timestamp(row["timestamp"])
            price = float(row["price"])
            if price <= 0:
                raise ValueError("prices must be positive")
            parsed.append((moment, price))
        parsed.sort(key=lambda item: item[0])
        if len({item[0] for item in parsed}) != len(parsed):
            raise ValueError(f"price history for {symbol} contains duplicate timestamps")
        result[symbol] = tuple(parsed)
    return result


def _first_after(rows: Sequence[tuple[Any, float]], moment: Any) -> tuple[Any, float] | None:
    return next((item for item in rows if item[0] > moment), None)


def _first_at_or_after(rows: Sequence[tuple[Any, float]], moment: Any) -> tuple[Any, float] | None:
    return next((item for item in rows if item[0] >= moment), None)


def _path_stats(rows: Sequence[tuple[Any, float]], start: Any, end: Any, anchor: float) -> dict[str, float | None]:
    window = [price for moment, price in rows if start <= moment <= end]
    if not window:
        return {"minimum_return": None, "maximum_return": None}
    returns = [price / anchor - 1.0 for price in window]
    return {"minimum_return": min(returns), "maximum_return": max(returns)}


def evaluate_decision_history(
    decisions: Iterable[Mapping[str, Any]],
    status_events: Iterable[Mapping[str, Any]],
    prices: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    artifact_root: str | Path | None = None,
    horizons: Sequence[int] = _HORIZONS,
) -> dict[str, Any]:
    """Evaluate only current-contract decisions and report every exclusion."""
    price_history = _price_rows(prices)
    events = list(status_events)
    parsed: list[Decision] = []
    exclusions: list[dict[str, str]] = []
    for index, raw in enumerate(decisions):
        identifier = str(raw.get("decision_id", f"row-{index}"))
        try:
            _validated_decision(raw, artifact_root=artifact_root)
            model = reconstruct_decision_status(Decision.from_mapping(raw), events)
        except Exception as exc:
            exclusions.append({"decision_id": identifier, "reason": f"{exc.__class__.__name__}: {exc}"})
            continue
        parsed.append(model)

    rows: list[dict[str, Any]] = []
    seen_primary: set[tuple[str, str, str, str]] = set()
    for decision in sorted(parsed, key=lambda item: parse_timestamp(item.timestamp)):
        moment = parse_timestamp(decision.timestamp)
        day = moment.date().isoformat()
        snapshot_key = decision.based_on_snapshot_id or decision.decision_id or day
        for action in decision.actions:
            if action.symbol in {"USD", "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD1", "CASH", "U"}:
                continue
            key = (snapshot_key, day, action.symbol, action.action)
            is_primary = key not in seen_primary
            seen_primary.add(key)
            symbol_rows = price_history.get(action.symbol, ())
            reference = _first_after(symbol_rows, moment)
            record: dict[str, Any] = {
                "decision_id": decision.decision_id,
                "based_on_snapshot_id": decision.based_on_snapshot_id,
                "timestamp": decision.timestamp,
                "symbol": action.symbol,
                "action": action.action,
                "amount_usd": action.amount_usd,
                "decision_status": decision.status,
                "primary_sample": is_primary,
                "performance_class": (
                    "REALIZED_ELIGIBLE" if decision.status == "CONFIRMED" else "PAPER_ONLY"
                ),
                "reference": None,
                "horizons": {},
            }
            if reference is None:
                record["exclusion_reason"] = "NO_POST_DECISION_REFERENCE_PRICE"
                rows.append(record)
                continue
            reference_at, reference_price = reference
            record["reference"] = {"timestamp": reference_at.isoformat().replace("+00:00", "Z"), "price": reference_price}
            btc_reference = _first_at_or_after(price_history.get("BTC", ()), reference_at)
            for raw_horizon in horizons:
                if isinstance(raw_horizon, bool) or int(raw_horizon) <= 0:
                    raise ValueError("horizons must contain positive integers")
                horizon = int(raw_horizon)
                target_time = reference_at + timedelta(days=horizon)
                endpoint = _first_at_or_after(symbol_rows, target_time)
                if endpoint is None:
                    record["horizons"][str(horizon)] = {"status": "PENDING"}
                    continue
                endpoint_at, endpoint_price = endpoint
                asset_return = endpoint_price / reference_price - 1.0
                btc_endpoint = _first_at_or_after(price_history.get("BTC", ()), endpoint_at)
                btc_return = (
                    btc_endpoint[1] / btc_reference[1] - 1.0
                    if btc_reference is not None and btc_endpoint is not None else None
                )
                path = _path_stats(symbol_rows, reference_at, endpoint_at, reference_price)
                record["horizons"][str(horizon)] = {
                    "status": "AVAILABLE", "endpoint_at": endpoint_at.isoformat().replace("+00:00", "Z"),
                    "asset_return": asset_return, "btc_return": btc_return,
                    "relative_return_vs_btc": asset_return - btc_return if btc_return is not None else None,
                    **path,
                }
            rows.append(record)
    return {
        "contract": "CURRENT_ONLY", "valid_decisions": len(parsed),
        "excluded_decisions": len(exclusions), "exclusions": exclusions,
        "raw_action_rows": len(rows), "primary_sample_rows": sum(bool(row["primary_sample"]) for row in rows),
        "realized_performance_rule": "CONFIRMED still requires independent fill quantity, price, and time evidence",
        "rows": rows,
    }


__all__ = ["evaluate_decision_history"]
