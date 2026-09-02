"""Historical state helpers used before a new portfolio review."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from ..engine.ledger import PortfolioSnapshot as LedgerSnapshot
from ..engine.ledger import build_nav_history
from ..engine.position_pnl import calculate_portfolio_position_performance
from ..models.performance import PositionPerformance
from ..models.decision import Decision
from ..models.portfolio import snapshot_from_mapping
from ..models.time import parse_timestamp
from .decisions import read_decisions
from .metrics import metric_history_context, read_metric_observations
from .snapshots import read_snapshots


def _latest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(records, key=lambda item: parse_timestamp(item["timestamp"])) if records else None


def latest_snapshot(path: str | Path | None = None) -> dict[str, Any] | None:
    return _latest(read_snapshots(path))


def latest_decision(path: str | Path | None = None) -> dict[str, Any] | None:
    return _latest(read_decisions(path))


def portfolio_nav_history(path: str | Path | None = None):
    snapshots = []
    for record in read_snapshots(path):
        snapshot, _, _ = snapshot_from_mapping(record)
        snapshots.append(
            LedgerSnapshot(
                snapshot.timestamp,
                snapshot.total_value_usd,
                snapshot.external_cash_flow,
            )
        )
    return build_nav_history(snapshots) if snapshots else []


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


def _position_performance_records(
    path: str | Path | None = None,
) -> dict[str, list[tuple[str | None, PositionPerformance]]]:
    records = []
    for index, record in enumerate(read_snapshots(path)):
        snapshot, _, _ = snapshot_from_mapping(record)
        timestamp = None if snapshot.timestamp == "UNSPECIFIED" else snapshot.timestamp
        records.append((timestamp, index, calculate_portfolio_position_performance(snapshot)))
    records.sort(
        key=lambda item: (
            item[0] is None,
            parse_timestamp(item[0]) if item[0] is not None else None,
            item[1],
        )
    )
    result: dict[str, list[tuple[str | None, PositionPerformance]]] = {}
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
        latest_return = latest["unrealized_return_pct"]
        previous_return = previous["unrealized_return_pct"] if previous else None
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
) -> dict[str, Any]:
    snapshot = latest_snapshot(snapshot_path)
    decision = latest_decision(decision_path)
    nav = portfolio_nav_history(snapshot_path)
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
    "last_full_review",
    "latest_decision",
    "latest_position_performance",
    "latest_snapshot",
    "position_performance_history",
    "portfolio_nav_history",
    "previous_asset_assessment",
]
