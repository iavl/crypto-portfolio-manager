"""Deterministic portfolio calculations."""

from .position_pnl import (
    calculate_portfolio_position_performance,
    calculate_position_performance,
)
from .cash_flow import cash_flow_adjusted_performance, detect_external_cash_flow, resolve_cash_flow_issue
from .ledger import build_nav_history_result
from .benchmark import build_aligned_benchmark_result
from .volume_profile import build_multi_horizon_profiles, build_volume_profile
from .decision_packet import build_decision_review_packet, should_run_high_impact_review, validate_decision_review_packet
from .factor_packet import build_asset_factor_packet, validate_asset_factor_packet
from .metric_plan import build_metric_collection_plan, build_metric_collection_request, scoring_metric_enabled_for_asset
from .metric_normalization import (
    normalize_collection_results,
    normalize_metric_observation,
    normalize_metric_result,
    persist_collection_results,
)
from .report_packet import build_final_review_output, build_report_packet, validate_final_review_output, validate_report_packet
from .scoring import calculate_factor_reliability, ensure_acquisition_ready
from .confidence import (
    DecisionScope,
    aggregate_asset_evidence_confidence,
    calculate_data_confidence,
    calculate_decision_confidence,
    calculate_freshness,
    calculate_regime_confidence,
    calculate_signal_consistency,
    confidence_deployment_factor,
    freshness_score,
    redundancy_score,
    signal_consistency_score,
    source_quality_score,
)
from .regime_inputs import build_regime_inputs
from .positioning import build_positioning_facts
from .cycle import build_btc_cycle_context, halving_context_for_days
from .overlays import (
    OverlayDeployment,
    apply_overlay_deployment_cap,
    build_market_overlays,
    cycle_deployment_factor,
    effective_deployment_factor,
    overlay_wait_required,
    positioning_deployment_factor,
)
from .risk import apply_chain_liveness_deployment_cap, chain_liveness_deployment_factor, event_risk_deployment_factor
from .core_eligibility import CORE_ELIGIBILITY_STATES, eth_core_eligibility, relative_strength_score

__all__ = [
    "calculate_portfolio_position_performance",
    "calculate_position_performance",
    "cash_flow_adjusted_performance",
    "detect_external_cash_flow",
    "resolve_cash_flow_issue",
    "build_nav_history_result",
    "build_aligned_benchmark_result",
    "build_multi_horizon_profiles",
    "build_volume_profile",
    "build_asset_factor_packet",
    "validate_asset_factor_packet",
    "build_decision_review_packet",
    "validate_decision_review_packet",
    "build_metric_collection_plan",
    "build_metric_collection_request",
    "scoring_metric_enabled_for_asset",
    "build_regime_inputs",
    "build_positioning_facts",
    "build_btc_cycle_context",
    "halving_context_for_days",
    "OverlayDeployment",
    "apply_overlay_deployment_cap",
    "build_market_overlays",
    "cycle_deployment_factor",
    "effective_deployment_factor",
    "overlay_wait_required",
    "positioning_deployment_factor",
    "apply_chain_liveness_deployment_cap",
    "chain_liveness_deployment_factor",
    "event_risk_deployment_factor",
    "CORE_ELIGIBILITY_STATES",
    "eth_core_eligibility",
    "relative_strength_score",
    "build_report_packet",
    "build_final_review_output",
    "validate_final_review_output",
    "ensure_acquisition_ready",
    "calculate_factor_reliability",
    "DecisionScope",
    "aggregate_asset_evidence_confidence",
    "calculate_data_confidence",
    "calculate_decision_confidence",
    "confidence_deployment_factor",
    "calculate_freshness",
    "calculate_regime_confidence",
    "calculate_signal_consistency",
    "freshness_score",
    "redundancy_score",
    "signal_consistency_score",
    "source_quality_score",
    "validate_report_packet",
    "normalize_metric_observation",
    "normalize_metric_result",
    "normalize_collection_results",
    "persist_collection_results",
    "should_run_high_impact_review",
]
