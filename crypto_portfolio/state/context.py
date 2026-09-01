"""Historical state helpers used before a new portfolio review."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from ..engine.ledger import PortfolioSnapshot as LedgerSnapshot
from ..engine.ledger import build_nav_history
from ..models.decision import Decision
from ..models.portfolio import snapshot_from_mapping
from ..models.time import parse_timestamp
from .decisions import read_decisions
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
    *,
    as_of: str | None = None,
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
    }


__all__ = [
    "build_history_context",
    "last_full_review",
    "latest_decision",
    "latest_snapshot",
    "portfolio_nav_history",
    "previous_asset_assessment",
]
