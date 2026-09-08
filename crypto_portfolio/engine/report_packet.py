"""Build final report inputs without recalculating portfolio conclusions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping
import json

from ..models.decision_packet import DecisionReviewPacket, SolReview
from ..models.market_overlays import MarketOverlays
from ..models.report_packet import ReportPacket


REPORT_PROMPT_RULE = "DO NOT recompute or alter numeric conclusions. Use the supplied structured outputs as authoritative."


def _require_acquisition_finalized(acquisition: Any) -> None:
    if hasattr(acquisition, "require_finalized"):
        acquisition.require_finalized()
        return
    if isinstance(acquisition, Mapping):
        summary = acquisition.get("summary", {})
        pending = acquisition.get("pending_external_resolution")
        if pending is None and isinstance(summary, Mapping):
            pending = summary.get("pending_external_resolution", 0)
        if pending is None:
            pending = len(acquisition.get("pending_event_scans", ())) + len(
                acquisition.get("pending_web_fallbacks", ())
            )
        if acquisition.get("finalized") is False or pending:
            from ..acquisition import AcquisitionResolutionRequired

            raise AcquisitionResolutionRequired(
                "acquisition requires external resolution before final reporting"
            )


def _failed_script_executions(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        values = (value,)
    elif hasattr(value, "as_dict"):
        values = (value.as_dict(),)
    elif isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("script_executions must be a sequence")
    else:
        values = tuple(value)
    failures = []
    for index, item in enumerate(values):
        if hasattr(item, "as_dict"):
            item = item.as_dict()
        if not isinstance(item, Mapping):
            raise ValueError(f"script_executions[{index}] must be an object")
        if str(item.get("status", "")).strip().upper() != "SUCCESS":
            failures.append(item)
    return tuple(failures)


def build_report_packet(
    decision_packet: DecisionReviewPacket | Mapping[str, Any],
    sol_review: SolReview | Mapping[str, Any] | None = None,
    *,
    scores: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, Any] | None = None,
    overlays: MarketOverlays | Mapping[str, Any] | None = None,
    acquisition: Any | None = None,
    script_executions: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
) -> ReportPacket:
    if acquisition is not None:
        _require_acquisition_finalized(acquisition)
    overlay = overlays
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
    from ..data_collection import build_failed_data_fetches

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
        failed_data_fetches=build_failed_data_fetches(acquisition, review_type=packet.review_type),
        script_failures=_failed_script_executions(script_executions),
        positioning_summaries=positioning_summaries,
        btc_cycle_summary=btc_cycle_summary,
        overlay_confidence=overlay_confidence,
        overlay_warnings=overlay_warnings,
        effective_deployment_caps=deployment_caps,
        regime_confidence=packet.regime_confidence,
        decision_confidence=packet.decision_confidence,
        confidence_caps=(
            tuple(packet.decision_confidence.caps)
            if packet.decision_confidence is not None and hasattr(packet.decision_confidence, "caps")
            else ()
        ),
        nav_performance=packet.nav_performance,
        benchmark_performance=packet.benchmark_performance,
        event_scan_summary=packet.event_scan_summary,
        no_trade_attribution=packet.no_trade_attribution,
    )


def validate_report_packet(value: ReportPacket | Mapping[str, Any]) -> bool:
    ReportPacket.from_mapping(value) if not isinstance(value, ReportPacket) else value
    return True


def build_final_review_output(
    report_packet: ReportPacket | Mapping[str, Any],
    *,
    acquisition: Any | None = None,
    snapshot: Any | None = None,
) -> dict[str, Any]:
    """Assemble and validate the immutable output before a caller persists it."""
    packet = report_packet if isinstance(report_packet, ReportPacket) else ReportPacket.from_mapping(report_packet)
    packet_value = packet.as_dict()
    if acquisition is not None:
        _require_acquisition_finalized(acquisition)
        if hasattr(acquisition, "require_scoring_ready"):
            acquisition.require_scoring_ready()
        elif isinstance(acquisition, Mapping) and acquisition.get("ready_for_scoring") is False:
            from ..acquisition import AcquisitionResolutionRequired

            raise AcquisitionResolutionRequired("hard-critical event scan resolution required")
    acquisition_value = acquisition.as_dict() if hasattr(acquisition, "as_dict") else dict(acquisition or {})
    pnl = None
    if snapshot is not None:
        from .position_pnl import calculate_portfolio_position_performance

        pnl = calculate_portfolio_position_performance(snapshot).as_dict()
    collection = dict(acquisition_value.get("summary", {}))
    collection["failed_data_fetches"] = packet_value["failed_data_fetches"]
    result = {
        "portfolio": {
            "current_weights": packet_value["current_weights"],
            "target_weights": packet_value["target_weights"],
        },
        "pnl": pnl,
        "collection": collection,
        "debug_report": {
            "data_fetch_failures": packet_value["failed_data_fetches"],
            "script_failures": packet_value["script_failures"],
        },
        "scores": packet_value["scores"],
        "regime": packet.market_regime,
        "allocation": dict(packet.target_weights),
        "risk": {
            "flags": list(packet.risk_flags),
            "critical_missing_data": list(packet.critical_missing_data),
        },
        "rebalance": {
            "actions": packet_value["actions"],
            "approved_amounts": packet_value["approved_amounts"],
            "no_trade_attribution": packet_value["no_trade_attribution"],
        },
        "overlays": {
            "positioning": packet_value["positioning_summaries"],
            "btc_cycle": packet_value["btc_cycle_summary"],
            "effective_deployment_caps": packet_value["effective_deployment_caps"],
        },
        "confidence": {
            "regime": packet_value["regime_confidence"],
            "decision": packet_value["decision_confidence"],
            "caps": packet_value["confidence_caps"],
        },
        "performance": {
            "nav": packet_value["nav_performance"],
            "benchmark": packet_value["benchmark_performance"],
        },
        "event_scans": acquisition_value.get("event_scans", []),
        "no_trade_attribution": packet_value["no_trade_attribution"],
        "report_packet": packet_value,
    }
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("final review output must be finite JSON") from exc
    return result


def validate_final_review_output(value: Mapping[str, Any]) -> bool:
    build_final_review_output(value["report_packet"], acquisition=value)
    return True


__all__ = [
    "REPORT_PROMPT_RULE",
    "build_final_review_output",
    "build_report_packet",
    "validate_final_review_output",
    "validate_report_packet",
]
