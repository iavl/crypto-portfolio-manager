"""Historical state helpers used before a new portfolio review."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from ..engine.ledger import build_nav_history_result
from ..engine.cash_flow import (
    apply_cash_flow_resolutions,
    detect_external_cash_flow,
)
from ..engine.position_pnl import calculate_portfolio_position_performance
from ..models.performance import PositionPerformance
from ..models.decision import Decision
from ..models.portfolio import PortfolioSnapshot, Position
from ..models.time import parse_timestamp
from .decisions import read_decisions
from .cash_flows import read_cash_flow_resolutions
from .metrics import metric_history_context, read_metric_observations
from .snapshots import read_snapshots


def _latest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Tie-break on append order so equal timestamps resolve to the newest
    # persisted record, not an arbitrary one.
    indexed = enumerate(records)
    return max(indexed, key=lambda item: (parse_timestamp(item[1]["timestamp"]), item[0]))[1] if records else None


def latest_snapshot(path: str | Path | None = None) -> dict[str, Any] | None:
    return _latest(read_snapshots(path))


def latest_decision(path: str | Path | None = None) -> dict[str, Any] | None:
    return _latest(read_decisions(path))


def _cash_flow_snapshots(
    path: str | Path | None = None,
    resolution_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Effective ledger history: raw records overlaid with explicit resolutions.

    Raw persisted records go straight into the engine overlay, which extracts
    the ledger fields (no old embedded policy revalidation) and normalizes
    pre-contract flow shapes. A persisted UNRESOLVED snapshot stays UNRESOLVED
    until the user resolves it; this overlay never guesses a legacy flow.
    """
    effective, _ = apply_cash_flow_resolutions(read_snapshots(path), read_cash_flow_resolutions(resolution_path))
    ordered = sorted(
        enumerate(effective),
        key=lambda item: (parse_timestamp(item[1]["timestamp"]), item[0]),
    )
    return [dict(item) for _, item in ordered]


def portfolio_nav_history(
    path: str | Path | None = None,
    *,
    resolution_path: str | Path | None = None,
):
    records = _cash_flow_snapshots(path, resolution_path)
    if not records:
        return []
    result = build_nav_history_result(records)
    return list(result.states) if result.performance_finality == "FINAL" else []


def portfolio_nav_history_result(
    path: str | Path | None = None,
    *,
    resolution_path: str | Path | None = None,
):
    return build_nav_history_result(_cash_flow_snapshots(path, resolution_path))


def external_cash_flow_review(
    path: str | Path | None = None,
    *,
    resolution_path: str | Path | None = None,
) -> dict[str, Any]:
    records = []
    for index, snapshot in enumerate(_cash_flow_snapshots(path, resolution_path)):
        records.append((parse_timestamp(snapshot["timestamp"]), index, snapshot))
    records.sort(key=lambda item: (item[0], item[1]))
    transitions = [
        detect_external_cash_flow(previous[2], current[2])
        for previous, current in zip(records, records[1:])
    ]
    unresolved = next((item for item in transitions if item["requires_confirmation"]), None)
    if unresolved is None:
        first_unresolved = next(
            (snapshot for _, _, snapshot in records if snapshot["cash_flow_resolution_status"] == "UNRESOLVED"),
            None,
        )
        if first_unresolved is not None:
            unresolved = {
                "status": "UNRESOLVED",
                "requires_confirmation": True,
                "cash_flow_resolution_status": "UNRESOLVED",
                "snapshot_id": first_unresolved["snapshot_id"],
                "reason": "cash_flow_resolution_status is UNRESOLVED",
            }
    if not records:
        return {
            "status": "UNAVAILABLE",
            "performance_finality": "UNAVAILABLE",
            "requires_confirmation": False,
            "transitions": [],
            "reason": "no portfolio snapshots are available",
        }
    return {
        "status": "PROVISIONAL" if unresolved else "AVAILABLE",
        "performance_finality": "PROVISIONAL" if unresolved else "FINAL",
        "requires_confirmation": unresolved is not None,
        "transitions": transitions,
        "reason": unresolved.get("reason") if unresolved else None,
    }


def previous_asset_assessment(
    symbol: str, path: str | Path | None = None
):
    normalized = symbol.strip().upper()
    for record in reversed(read_decisions(path)):
        decision = Decision.from_mapping(record)
        assessment = decision.factor_scores.get(normalized)
        if assessment is not None:
            return assessment
    return None


def _history_snapshot(record: dict[str, Any]) -> PortfolioSnapshot:
    """Rebuild a snapshot model for position P&L without revalidating history.

    Position P&L needs quantities, values, and cost basis — not the embedded
    policy blob old records carry. Classification defaults to ``other`` here;
    resolved classification stays the current policy's job on fresh snapshots.
    """
    positions = tuple(
        Position(
            symbol=raw["symbol"],
            quantity=raw.get("quantity"),
            value_usd=raw.get("value_usd", 0.0),
            cost_basis_usd=raw.get("cost_basis_usd"),
            asset_type_hint=raw.get("asset_type_hint", raw.get("asset_type")),
            current_price_usd=raw.get("current_price_usd"),
            average_cost_price_usd=raw.get("average_cost_price_usd"),
            exchange_unrealized_pnl_usd=raw.get("exchange_unrealized_pnl_usd"),
        )
        for raw in record.get("positions", ())
        if isinstance(raw, dict) and raw.get("symbol")
    )
    status = record.get("cash_flow_resolution_status")
    flow = record.get("external_cash_flow")
    flow_type = record.get("external_cash_flow_type")
    if status is None:
        if str(flow_type or "").strip().upper() == "UNRESOLVED":
            status, flow, flow_type = "UNRESOLVED", None, None
        else:
            status, flow, flow_type = "ASSUMED_NONE", 0.0, "NONE"
    return PortfolioSnapshot(
        timestamp=record.get("timestamp", ""),
        base_currency=record.get("base_currency", "USD"),
        positions=positions,
        external_cash_flow=flow,
        external_cash_flow_type=flow_type,
        total_value=record.get("total_value"),
        source=record.get("source"),
        snapshot_id=record.get("snapshot_id"),
        cash_flow_resolution_status=status,
    )


def _position_performance_records(
    path: str | Path | None = None,
) -> dict[str, list[tuple[str, PositionPerformance]]]:
    records = []
    for index, record in enumerate(read_snapshots(path)):
        snapshot = _history_snapshot(record)
        records.append((snapshot.timestamp, index, calculate_portfolio_position_performance(snapshot)))
    records.sort(
        key=lambda item: (
            parse_timestamp(item[0]),
            item[1],
        )
    )
    result: dict[str, list[tuple[str, PositionPerformance]]] = {}
    for timestamp, _, summary in records:
        for position in summary.positions:
            result.setdefault(position.symbol, []).append((timestamp, position))
    return result


def latest_position_performance(
    symbol: str,
    path: str | Path | None = None,
) -> PositionPerformance | None:
    normalized = symbol.strip().upper()
    history = _position_performance_records(path).get(normalized, [])
    return history[-1][1] if history else None


def position_performance_history(
    symbol: str,
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    normalized = symbol.strip().upper()
    return [
        {"timestamp": timestamp, **performance.as_dict()}
        for timestamp, performance in _position_performance_records(path).get(normalized, [])
    ]


def build_position_pnl_context(
    path: str | Path | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for symbol, records in _position_performance_records(path).items():
        history = [
            {"timestamp": timestamp, **performance.as_dict()}
            for timestamp, performance in records
        ]
        latest = history[-1]
        previous = history[-2] if len(history) > 1 else None
        latest_return = latest["unrealized_return"]
        previous_return = previous["unrealized_return"] if previous else None
        change_pp = (
            (latest_return - previous_return) * 100
            if latest_return is not None and previous_return is not None
            else None
        )
        context[symbol] = {
            "latest": latest,
            "previous": previous,
            "unrealized_return_change_pp": change_pp,
            "history": history,
        }
    return context


def last_full_review(path: str | Path | None = None) -> dict[str, Any] | None:
    reviews = [
        record
        for record in read_decisions(path)
        if str(record.get("review_type", "")).upper() == "FULL_REVIEW"
    ]
    return _latest(reviews)


def build_history_context(
    snapshot_path: str | Path | None = None,
    decision_path: str | Path | None = None,
    metrics_path: str | Path | None = None,
    *,
    as_of: str | None = None,
    metric_keys: tuple[str, ...] | list[str] | None = None,
    cash_flow_resolution_path: str | Path | None = None,
) -> dict[str, Any]:
    snapshot = latest_snapshot(snapshot_path)
    applied_snapshots = _cash_flow_snapshots(snapshot_path, cash_flow_resolution_path)
    if applied_snapshots:
        effective_latest = max(applied_snapshots, key=lambda item: parse_timestamp(item["timestamp"]))
        if snapshot is not None and snapshot.get("snapshot_id") == effective_latest.get("snapshot_id"):
            # Keep the full persisted record (positions, provenance) but let
            # the resolution overlay own the effective cash-flow classification.
            snapshot = {
                **snapshot,
                "cash_flow_resolution_status": effective_latest["cash_flow_resolution_status"],
                "external_cash_flow": effective_latest["external_cash_flow"],
                "external_cash_flow_type": effective_latest["external_cash_flow_type"],
                "cash_flow_classification_source": effective_latest.get("cash_flow_classification_source"),
            }
    decision = latest_decision(decision_path)
    nav = portfolio_nav_history(snapshot_path, resolution_path=cash_flow_resolution_path)
    nav_result = portfolio_nav_history_result(snapshot_path, resolution_path=cash_flow_resolution_path)
    full_review = last_full_review(decision_path)
    reference = as_of or (snapshot or decision or {}).get("timestamp")
    full_review_due = False
    if reference is not None:
        current_time = parse_timestamp(reference)
        full_review_due = full_review is None or current_time - parse_timestamp(
            full_review["timestamp"]
        ) >= timedelta(days=14)
    previous_assessments = {}
    if decision is not None:
        parsed_decision = Decision.from_mapping(decision)
        previous_assessments = dict(parsed_decision.factor_scores)
    position_pnl = build_position_pnl_context(snapshot_path)
    cash_flow_review = external_cash_flow_review(snapshot_path, resolution_path=cash_flow_resolution_path)
    observations = read_metric_observations(metrics_path)
    assets = {
        position.get("symbol", "").strip().upper()
        for position in (snapshot or {}).get("positions", ())
        if isinstance(position, dict) and isinstance(position.get("symbol"), str)
    }
    if decision:
        assets.update(
            str(symbol).strip().upper()
            for symbol in (decision.get("factor_scores") or {})
            if str(symbol).strip()
        )
    if not assets:
        assets.update(item.asset for item in observations)
    keys_by_asset: dict[str, set[str]] = {}
    for item in observations:
        if item.asset in assets:
            keys_by_asset.setdefault(item.asset, set()).add(item.metric_key)
    if metric_keys is not None:
        requested_keys = tuple(metric_keys)
        for asset in assets:
            keys_by_asset.setdefault(asset, set()).update(requested_keys)
    metric_history_summary = {
        asset: metric_history_context(
            asset,
            sorted(keys),
            path=metrics_path,
        )
        for asset, keys in sorted(keys_by_asset.items())
        if keys
    }
    return {
        "latest_snapshot": snapshot,
        "latest_decision": decision,
        "nav_history": nav,
        "external_cash_flow_review": cash_flow_review,
        "performance_status": nav_result.status,
        "performance_finality": nav_result.performance_finality,
        "cash_flow_resolution_status": (snapshot or {}).get("cash_flow_resolution_status"),
        "nav_history_result": nav_result.as_dict(),
        "current_drawdown": nav[-1].current_drawdown if nav else None,
        "max_drawdown": nav[-1].max_drawdown if nav else None,
        "previous_target_weights": (decision or {}).get("target_weights"),
        "previous_actions": (decision or {}).get("actions"),
        "previous_status": (decision or {}).get("status"),
        "previous_assessments": previous_assessments,
        "last_full_review": full_review,
        "full_review_due": full_review_due,
        "position_pnl": position_pnl,
        "metric_history_summary": metric_history_summary,
    }


__all__ = [
    "build_history_context",
    "build_position_pnl_context",
    "external_cash_flow_review",
    "last_full_review",
    "latest_decision",
    "latest_position_performance",
    "latest_snapshot",
    "position_performance_history",
    "portfolio_nav_history",
    "portfolio_nav_history_result",
    "previous_asset_assessment",
]
