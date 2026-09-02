"""Build final report inputs without recalculating portfolio conclusions."""

from __future__ import annotations

from typing import Any, Mapping

from ..models.decision_packet import DecisionReviewPacket, SolReview
from ..models.report_packet import ReportPacket


REPORT_PROMPT_RULE = "DO NOT recompute or alter numeric conclusions. Use the supplied structured outputs as authoritative."


def build_report_packet(
    decision_packet: DecisionReviewPacket | Mapping[str, Any],
    sol_review: SolReview | Mapping[str, Any] | None = None,
    *,
    scores: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, Any] | None = None,
) -> ReportPacket:
    packet = decision_packet if isinstance(decision_packet, DecisionReviewPacket) else DecisionReviewPacket.from_mapping(decision_packet)
    final_scores = scores
    if final_scores is None:
        final_scores = {
            item.symbol: item.score if item.score is not None else dict(item.factor_scores)
            for item in packet.assets
        }
    actions = tuple(item.as_dict() for item in packet.assets)
    approved = {
        item.symbol: item.approved_amount_usd
        for item in packet.assets
        if item.approved_amount_usd > 0
    }
    zones = {}
    if packet.execution_summary:
        symbol = packet.execution_summary.get("symbol")
        if symbol and "selected_zones" in packet.execution_summary:
            zones[str(symbol).strip().upper()] = packet.execution_summary["selected_zones"]
    historical = {item.symbol: item.historical_changes for item in packet.assets if item.historical_changes}
    return ReportPacket(
        review_type=packet.review_type,
        market_regime=packet.market_regime,
        scores=final_scores,
        current_weights=packet.current_weights,
        target_weights=packet.target_weights,
        actions=actions,
        approved_amounts=approved,
        execution_zones=zones,
        historical_changes=historical,
        risk_flags=packet.risk_flags,
        sol_review=sol_review,
        critical_missing_data=packet.critical_missing_data,
        data_quality=data_quality or {},
    )


report_packet = build_report_packet
build_report_payload = build_report_packet


def validate_report_packet(value: ReportPacket | Mapping[str, Any]) -> bool:
    ReportPacket.from_mapping(value) if not isinstance(value, ReportPacket) else value
    return True


__all__ = ["REPORT_PROMPT_RULE", "build_report_packet", "build_report_payload", "report_packet", "validate_report_packet"]
