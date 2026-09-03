"""Build final report inputs without recalculating portfolio conclusions."""

from __future__ import annotations

from typing import Any, Mapping

from ..models.decision_packet import DecisionReviewPacket, SolReview
from ..models.market_overlays import MarketOverlays
from ..models.report_packet import ReportPacket


REPORT_PROMPT_RULE = "DO NOT recompute or alter numeric conclusions. Use the supplied structured outputs as authoritative."


def build_report_packet(
    decision_packet: DecisionReviewPacket | Mapping[str, Any],
    sol_review: SolReview | Mapping[str, Any] | None = None,
    *,
    scores: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, Any] | None = None,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    market_overlays: MarketOverlays | Mapping[str, Any] | None = None,
) -> ReportPacket:
    if overlays is not None and market_overlays is not None:
        raise ValueError("provide only one of overlays or market_overlays")
    overlay = overlays if overlays is not None else market_overlays
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
    positioning_summaries = packet.positioning_summaries
    btc_cycle_summary = packet.btc_cycle_summary
    overlay_confidence = packet.overlay_confidence
    overlay_warnings = packet.overlay_warnings
    deployment_caps = packet.effective_deployment_caps
    if overlay is not None:
        value = overlay if isinstance(overlay, MarketOverlays) else MarketOverlays.from_mapping(overlay)
        compact = value.compact_summary()
        if not compact["effective_deployment_caps"]:
            from .overlays import effective_deployment_factor

            compact["effective_deployment_caps"] = {
                symbol: effective_deployment_factor(1.0, positioning=facts, btc_cycle=value.btc_cycle)
                for symbol, facts in value.positioning_by_asset.items()
            }
            if value.btc_cycle is not None and "BTC" not in compact["effective_deployment_caps"]:
                compact["effective_deployment_caps"]["BTC"] = effective_deployment_factor(
                    1.0, btc_cycle=value.btc_cycle
                )
        positioning_summaries = compact["positioning"]
        btc_cycle_summary = compact["btc_cycle"]
        overlay_confidence = compact["overlay_confidence"]
        overlay_warnings = tuple(compact["warnings"])
        deployment_caps = compact["effective_deployment_caps"]
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
        positioning_summaries=positioning_summaries,
        btc_cycle_summary=btc_cycle_summary,
        overlay_confidence=overlay_confidence,
        overlay_warnings=overlay_warnings,
        effective_deployment_caps=deployment_caps,
    )


report_packet = build_report_packet
build_report_payload = build_report_packet


def validate_report_packet(value: ReportPacket | Mapping[str, Any]) -> bool:
    ReportPacket.from_mapping(value) if not isinstance(value, ReportPacket) else value
    return True


__all__ = ["REPORT_PROMPT_RULE", "build_report_packet", "build_report_payload", "report_packet", "validate_report_packet"]
