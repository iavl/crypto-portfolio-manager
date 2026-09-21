"""Reproducible calculation receipts and shared final-decision validation."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from typing import Any, Mapping

from ..models.evidence import AssetAssessment, Evidence
from ..models.factor_packet import thaw_packet_value
from ..models.policy import Policy, policy_from_mapping, policy_hash
from ..models.time import normalize_timestamp, parse_timestamp

TREND_SOURCE = "crypto_portfolio.engine.factors.trend"
FLOW_SOURCE = "crypto_portfolio.engine.factors.flows"
RELATIVE_STRENGTH_SOURCE = "crypto_portfolio.engine.factors.relative_strength"


def calculation_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(thaw_packet_value(value), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def trend_calculation_evidence(
    snapshot: Any,
    policy: Policy,
    *,
    previous_relative_volumes: Any = None,
) -> Evidence:
    """Bind the actual normalized technical input, not unrelated cached scalars.

    This is a calculation receipt, not independent corroboration of a provider.
    Dataset hashes remain provenance; they are never masqueraded as Evidence IDs.
    """
    value = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(vars(snapshot))
    metadata = {
        "policy_hash": policy_hash(policy),
        "ohlcv_hash": snapshot.ohlcv_hash,
        "volume_profile_hash": snapshot.volume_profile_hash,
        "calculation_input_hash": calculation_hash(value),
    }
    if previous_relative_volumes is not None:
        history = list(previous_relative_volumes)
        if any(not isinstance(item, Mapping) for item in history):
            raise ValueError(
                "persisted trend calculations require dated previous volume history entries"
            )
        normalized_history = []
        prior_timestamp = None
        current_close = (snapshot.ohlcv_metadata or {}).get("latest_candle_timestamp")
        current_close_time = parse_timestamp(current_close) if current_close else None
        for item in history:
            if set(item) != {"observed_at", "relative_volume"}:
                raise ValueError(
                    "previous volume history entries must contain observed_at and relative_volume"
                )
            normalized_observed_at = normalize_timestamp(
                item["observed_at"], "previous volume observed_at"
            )
            observed_at = parse_timestamp(normalized_observed_at)
            reading = item["relative_volume"]
            if (
                isinstance(reading, bool)
                or not isinstance(reading, (int, float))
                or not math.isfinite(float(reading))
                or float(reading) < 0
            ):
                raise ValueError("previous_relative_volumes must contain finite non-negative numbers")
            if prior_timestamp is not None and observed_at >= prior_timestamp:
                raise ValueError("previous volume history must be most recent first with distinct closes")
            if current_close_time is not None and observed_at >= current_close_time:
                raise ValueError("previous volume history must precede the current completed close")
            prior_timestamp = observed_at
            normalized_history.append(
                {
                    "observed_at": normalized_observed_at,
                    "relative_volume": float(reading),
                }
            )
        metadata["previous_relative_volume_history"] = normalized_history
    identity = calculation_hash({"input": value, "metadata": metadata})
    return Evidence(identity, snapshot.symbol, "trend", TREND_SOURCE,
                    snapshot.as_of, snapshot.as_of,
                    "CURRENT" if snapshot.market_data_fresh else "STALE",
                    snapshot.data_confidence, value=value, metadata=metadata)


def validate_trend_calculation(factor: Any, evidence: Mapping[str, Evidence],
                               *, symbol: str, as_of: str, policy: Policy) -> dict[str, Any]:
    from .factors.trend import calculate_trend_factor
    from .technical import _volume_state, expected_latest_completed_date

    receipts = [evidence[key] for key in factor.evidence_ids if key in evidence
                and evidence[key].source == TREND_SOURCE]
    if len(receipts) != 1 or len(factor.evidence_ids) != 1:
        raise ValueError(f"CALCULATION_EVIDENCE_MISSING: {symbol}.trend requires its exact calculation receipt")
    receipt = receipts[0]
    if receipt.asset != symbol or receipt.factor != "trend":
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: asset/factor")
    snapshot = receipt.value
    if not isinstance(snapshot, Mapping) or snapshot.get("symbol") != symbol:
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: snapshot")
    previous_relative_volumes = (receipt.metadata or {}).get("previous_relative_volume_history")
    result = calculate_trend_factor(
        snapshot,
        policy=policy,
        previous_relative_volumes=previous_relative_volumes,
    )
    expected = result.evidence_records[0]
    if expected.as_dict() != receipt.as_dict():
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: input, policy or hash")
    if not math.isclose(result.score, factor.score, abs_tol=1e-9, rel_tol=0):
        raise ValueError("CALCULATION_SCORE_MISMATCH: trend")
    moment = parse_timestamp(as_of)
    for timestamp in (snapshot["as_of"], snapshot.get("spot_observed_at"),
                      snapshot.get("spot_fetched_at"), receipt.fetched_at):
        if timestamp is None or parse_timestamp(timestamp) > moment:
            raise ValueError("CALCULATION_TIME_INVALID: missing or future timestamp")
    if not snapshot.get("source") or not snapshot.get("spot_source"):
        raise ValueError("CALCULATION_SOURCE_MISSING")
    digest = snapshot.get("ohlcv_hash", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("CALCULATION_HASH_INVALID")
    metadata = snapshot.get("ohlcv_metadata") or {}
    tail = metadata.get("latest_candle_timestamp")
    if snapshot.get("timeframe") != "1D" or not tail:
        raise ValueError("CALCULATION_DAILY_BOUNDARY_MISSING")
    tail_time = parse_timestamp(tail)
    if tail_time != tail_time.replace(hour=0, minute=0, second=0, microsecond=0) or tail_time + timedelta(days=1) > parse_timestamp(snapshot["as_of"]):
        raise ValueError("CALCULATION_DAILY_BOUNDARY_INVALID")
    lag = (expected_latest_completed_date(as_of) - tail_time.date()).days
    if receipt.freshness == "CURRENT" and lag > policy.execution["maximum_daily_candle_lag_days"]:
        raise ValueError("CALCULATION_STALE_DAILY_INPUT")
    if metadata.get("ohlcv_hash", digest) != digest:
        raise ValueError("CALCULATION_HASH_MISMATCH")
    supportive = policy.execution["breakout"]["minimum_relative_volume"]
    if snapshot["volume_state"] != _volume_state(snapshot.get("relative_volume"), supportive):
        raise ValueError("CALCULATION_VOLUME_STATE_MISMATCH")
    return {"score": result.score, "contributions": dict(result.contributions),
            "volume_thresholds": dict(result.volume_thresholds),
            "calculation_input_hash": receipt.metadata["calculation_input_hash"],
            "completed_through": (tail_time + timedelta(days=1)).isoformat(),
            "evidence_ids": list(factor.evidence_ids)}


def _calculation_receipt(
    *,
    source: str,
    symbol: str,
    factor: str,
    as_of: str,
    policy: Policy,
    value: Mapping[str, Any],
) -> Evidence:
    metadata = {
        "policy_hash": policy_hash(policy),
        "calculation_input_hash": calculation_hash(value),
    }
    identity = calculation_hash({"input": value, "metadata": metadata})
    return Evidence(
        identity,
        symbol,
        factor,
        source,
        as_of,
        as_of,
        "CURRENT",
        "HIGH",
        value=value,
        metadata=metadata,
    )


def flow_calculation_evidence(
    value: Mapping[str, Any], *, symbol: str, as_of: str, policy: Policy
) -> Evidence:
    """Create a receipt for a normalized deterministic flow calculation."""
    from .factors.flows import calculate_flow_factor

    if not isinstance(value, Mapping):
        raise ValueError("flow calculation input must be an object")
    result = calculate_flow_factor(value, symbol=symbol, policy=policy)
    receipt = _calculation_receipt(
        source=FLOW_SOURCE,
        symbol=symbol,
        factor="capital_flows",
        as_of=as_of,
        policy=policy,
        value=dict(value),
    )
    # Force calculation now so invalid inputs cannot produce a receipt.
    if result.score is None:
        raise ValueError("flow calculation receipt requires an available score")
    return receipt


def validate_flow_calculation(
    factor: Any,
    evidence: Mapping[str, Evidence],
    *,
    symbol: str,
    as_of: str,
    policy: Policy,
) -> dict[str, Any]:
    from .factors.flows import calculate_flow_factor

    receipts = [
        evidence[key]
        for key in factor.evidence_ids
        if key in evidence and evidence[key].source == FLOW_SOURCE
    ]
    if len(receipts) != 1:
        raise ValueError(f"CALCULATION_EVIDENCE_MISSING: {symbol}.capital_flows requires one receipt")
    receipt = receipts[0]
    if receipt.asset != symbol or receipt.factor != "capital_flows":
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: flow asset/factor")
    if not isinstance(receipt.value, Mapping):
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: flow input")
    if (receipt.metadata or {}).get("policy_hash") != policy_hash(policy):
        raise ValueError("CALCULATION_POLICY_MISMATCH: flow")
    if (receipt.metadata or {}).get("calculation_input_hash") != calculation_hash(receipt.value):
        raise ValueError("CALCULATION_HASH_MISMATCH: flow")
    if parse_timestamp(receipt.observed_at) > parse_timestamp(as_of) or parse_timestamp(receipt.fetched_at) > parse_timestamp(as_of):
        raise ValueError("CALCULATION_FUTURE_EVIDENCE: flow")
    result = calculate_flow_factor(receipt.value, symbol=symbol, policy=policy)
    if result.score is None or not math.isclose(result.score, factor.score, abs_tol=1e-9, rel_tol=0):
        raise ValueError("CALCULATION_SCORE_MISMATCH: capital_flows")
    # The source-confidence carried by a normalized flow result is part of
    # the deterministic contract.  A caller may not publish a fully reliable
    # FactorScore after the provider has declared MEDIUM/LOW evidence quality.
    from .scoring import calculate_factor_reliability

    expected_reliability = calculate_factor_reliability(
        result.coverage,
        result.facts.freshness,
        result.source_confidence,
    )
    claimed_reliability = getattr(factor, "reliability", None)
    if claimed_reliability is not None and not math.isclose(
        float(claimed_reliability), expected_reliability, abs_tol=1e-9, rel_tol=0
    ):
        raise ValueError("CALCULATION_RELIABILITY_MISMATCH: capital_flows")
    return {
        "score": result.score,
        "method": result.method,
        "normalized_flow": result.normalized_flow,
        "horizon_ratios": dict(result.horizon_ratios or {}),
        "horizons": {key: dict(value) for key, value in (result.horizons or {}).items()},
        "calculation_input_hash": receipt.metadata["calculation_input_hash"],
        "evidence_ids": list(factor.evidence_ids),
        "reliability": expected_reliability,
    }


def relative_strength_calculation_evidence(
    asset_history: Any,
    btc_history: Any,
    *,
    symbol: str,
    as_of: str,
    policy: Policy,
) -> Evidence:
    """Create a receipt for a deterministic asset-versus-BTC calculation."""
    from .factors.relative_strength import calculate_relative_strength

    normalized = {
        "asset_history": thaw_packet_value(asset_history.as_dict() if hasattr(asset_history, "as_dict") else asset_history),
        "btc_history": thaw_packet_value(btc_history.as_dict() if hasattr(btc_history, "as_dict") else btc_history),
    }
    result = calculate_relative_strength(
        normalized["asset_history"], normalized["btc_history"],
        symbol=symbol, policy=policy, as_of=as_of,
    )
    if result.score is None:
        raise ValueError("relative-strength calculation receipt requires an available score")
    return _calculation_receipt(
        source=RELATIVE_STRENGTH_SOURCE,
        symbol=symbol,
        factor="relative_strength_btc",
        as_of=as_of,
        policy=policy,
        value=normalized,
    )


def validate_relative_strength_calculation(
    factor: Any,
    evidence: Mapping[str, Evidence],
    *,
    symbol: str,
    as_of: str,
    policy: Policy,
) -> dict[str, Any]:
    from .factors.relative_strength import calculate_relative_strength

    receipts = [
        evidence[key]
        for key in factor.evidence_ids
        if key in evidence and evidence[key].source == RELATIVE_STRENGTH_SOURCE
    ]
    if len(receipts) != 1:
        raise ValueError(f"CALCULATION_EVIDENCE_MISSING: {symbol}.relative_strength_btc requires one receipt")
    receipt = receipts[0]
    if receipt.asset != symbol or receipt.factor != "relative_strength_btc":
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: relative-strength asset/factor")
    if not isinstance(receipt.value, Mapping) or set(receipt.value) != {"asset_history", "btc_history"}:
        raise ValueError("CALCULATION_EVIDENCE_MISMATCH: relative-strength input")
    if (receipt.metadata or {}).get("policy_hash") != policy_hash(policy):
        raise ValueError("CALCULATION_POLICY_MISMATCH: relative_strength_btc")
    if (receipt.metadata or {}).get("calculation_input_hash") != calculation_hash(receipt.value):
        raise ValueError("CALCULATION_HASH_MISMATCH: relative_strength_btc")
    if parse_timestamp(receipt.observed_at) > parse_timestamp(as_of) or parse_timestamp(receipt.fetched_at) > parse_timestamp(as_of):
        raise ValueError("CALCULATION_FUTURE_EVIDENCE: relative_strength_btc")
    result = calculate_relative_strength(
        receipt.value["asset_history"], receipt.value["btc_history"],
        symbol=symbol, policy=policy, as_of=as_of,
    )
    if not math.isclose(result.score, factor.score, abs_tol=1e-9, rel_tol=0):
        raise ValueError("CALCULATION_SCORE_MISMATCH: relative_strength_btc")
    return {
        "score": result.score,
        "coverage": result.coverage,
        "state": result.state,
        "relative_30d": result.relative_30d,
        "relative_90d": result.relative_90d,
        "relative_180d": result.relative_180d,
        "calculation_input_hash": receipt.metadata["calculation_input_hash"],
        "evidence_ids": list(factor.evidence_ids),
    }


def validate_calculation_context(
    context: Mapping[str, Any],
    *,
    require_trend: bool = False,
    expected_policy_hash: str | None = None,
    expected_as_of: str | None = None,
    expected_symbols: set[str] | None = None,
    expected_assessments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Used by Decision, review packet and report; never trust a validation flag."""
    from ..models.decision import validate_factor_evidence_binding
    from .scoring import score_factors

    required = {"as_of", "resolved_policy", "assessments", "evidence"}
    if not isinstance(context, Mapping) or set(context) != required:
        raise ValueError("calculation_context must contain as_of, resolved_policy, assessments, evidence")
    moment = parse_timestamp(context["as_of"])
    policy = policy_from_mapping(thaw_packet_value(context["resolved_policy"]))
    if expected_policy_hash is not None and policy_hash(policy) != expected_policy_hash:
        raise ValueError("CALCULATION_POLICY_MISMATCH")
    if expected_as_of is not None and moment > parse_timestamp(expected_as_of):
        raise ValueError("CALCULATION_CONTEXT_IN_FUTURE")
    context_symbols = {str(symbol).strip().upper() for symbol in context["assessments"]}
    required_symbols = {str(symbol).strip().upper() for symbol in (expected_symbols or ())}
    if not required_symbols.issubset(context_symbols):
        missing = ", ".join(sorted(required_symbols - context_symbols))
        raise ValueError(f"CALCULATION_ASSESSMENT_MISSING: {missing}")
    records = [Evidence(**thaw_packet_value(item)) for item in context["evidence"]]
    evidence = {item.id: item for item in records}
    if len(evidence) != len(records):
        raise ValueError("calculation_context has duplicate evidence")
    summaries = {}
    for symbol, raw in context["assessments"].items():
        context_assessment_mapping = thaw_packet_value(raw)
        assessment = AssetAssessment.from_mapping(symbol, context_assessment_mapping)
        if expected_assessments is not None:
            expected = expected_assessments.get(symbol)
            if expected is None:
                expected = expected_assessments.get(str(symbol).strip().upper())
            if expected is None:
                raise ValueError(f"CALCULATION_ASSESSMENT_MISSING: {symbol}")
            expected_assessment = AssetAssessment.from_mapping(
                symbol,
                expected if isinstance(expected, AssetAssessment) else thaw_packet_value(expected),
            )
            actual_values = assessment.as_dict()
            expected_values = expected_assessment.as_dict()
            # Contexts created before optional confidence explanation fields
            # were added remain current when all fields they actually carry
            # agree.  Every material score/eligibility field is present in
            # the context and therefore still participates in the binding.
            context_keys = {
                key
                for key, value in context_assessment_mapping.items()
                if value is not None
                or key
                in {
                    "symbol",
                    "factor_scores",
                    "weighted_score",
                    "confidence",
                    "asset_type",
                    "relative_strength_vs_btc",
                    "thesis_broken",
                    "critical_data_complete",
                    "risk_tier",
                    "risk_tier_source",
                    "event_risk",
                    "scoring_profile_name",
                    "score_coverage",
                    "confidence_score",
                }
            }
            if any(
                expected_values.get(key) != actual_values.get(key)
                for key in context_keys
                if key in expected_values
            ):
                raise ValueError(f"CALCULATION_ASSESSMENT_MISMATCH: {symbol}")
        validate_factor_evidence_binding(assessment.factor_scores, evidence, symbol=symbol)
        for factor in assessment.factor_scores.values():
            for key in factor.evidence_ids:
                item = evidence[key]
                if parse_timestamp(item.observed_at) > moment or parse_timestamp(item.fetched_at) > moment:
                    raise ValueError("CALCULATION_FUTURE_EVIDENCE")
        trend = assessment.factor_scores.get("trend")
        detail = {}
        if trend is not None and trend.availability == "AVAILABLE":
            if require_trend or any(
                evidence[key].source == TREND_SOURCE
                for key in trend.evidence_ids
                if key in evidence
            ):
                detail = validate_trend_calculation(
                    trend,
                    evidence,
                    symbol=symbol,
                    as_of=context["as_of"],
                    policy=policy,
                )
        flow = assessment.factor_scores.get("capital_flows")
        if flow is not None and flow.availability == "AVAILABLE":
            if require_trend or any(
                evidence[key].source == FLOW_SOURCE
                for key in flow.evidence_ids
                if key in evidence
            ):
                detail["capital_flows"] = validate_flow_calculation(
                    flow,
                    evidence,
                    symbol=symbol,
                    as_of=context["as_of"],
                    policy=policy,
                )
        relative = assessment.factor_scores.get("relative_strength_btc")
        if relative is not None and relative.availability == "AVAILABLE":
            if require_trend or any(
                evidence[key].source == RELATIVE_STRENGTH_SOURCE
                for key in relative.evidence_ids
                if key in evidence
            ):
                detail["relative_strength_btc"] = validate_relative_strength_calculation(
                    relative,
                    evidence,
                    symbol=symbol,
                    as_of=context["as_of"],
                    policy=policy,
                )
        scored = score_factors(assessment.factor_scores, policy=policy, symbol=symbol,
                              critical_data_complete=assessment.critical_data_complete)
        if assessment.weighted_score is not None and not math.isclose(assessment.weighted_score, scored.score, abs_tol=1e-9, rel_tol=0):
            raise ValueError(f"CALCULATION_WEIGHTED_SCORE_MISMATCH: {symbol}")
        summaries[symbol] = {"trend": detail, "score": scored.score, "factors": {
            key: {"raw_score": assessment.factor_scores[key].score if key in assessment.factor_scores else None,
                  "reliability": (scored.factor_reliability or {}).get(key, 0.0),
                  "effective_score": (scored.effective_factor_scores or {}).get(key),
                  "weighted_contribution": (scored.factor_contributions or {}).get(key, 0.0)}
            for key in policy.scoring_profile(symbol)}}
    return summaries


def validate_packet_calculations(
    context: Mapping[str, Any] | None,
    actions: Any,
    scores: Mapping[str, Any],
    *,
    require_context: bool = False,
    expected_policy_hash: str | None = None,
    expected_as_of: str | None = None,
    expected_symbols: set[str] | None = None,
    expected_assessments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    executable = any((a.get("action") if isinstance(a, Mapping) else a.action) in {"INCREASE", "REDUCE", "EXIT"} for a in actions)
    if context is None:
        if require_context and executable:
            raise ValueError("CALCULATION_CONTEXT_REQUIRED_FOR_EXECUTABLE_ACTION")
        return {}
    summary = validate_calculation_context(
        context,
        require_trend=executable,
        expected_policy_hash=expected_policy_hash,
        expected_as_of=expected_as_of,
        expected_symbols=expected_symbols,
        expected_assessments=expected_assessments,
    )
    for symbol, value in scores.items():
        if isinstance(value, (int, float)):
            if symbol not in summary or not math.isclose(value, summary[symbol]["score"], abs_tol=1e-9, rel_tol=0):
                raise ValueError("CALCULATION_REPORT_SCORE_MISMATCH")
    return summary


def factor_change_attribution(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    now = validate_calculation_context(current)
    if previous is None:
        return {"availability": "UNAVAILABLE", "reason": "NO_PREVIOUS_CALCULATION_CONTEXT", "current": now}
    try:
        before = validate_calculation_context(previous)
    except (ValueError, TypeError, KeyError) as exc:
        return {"availability": "UNAVAILABLE", "reason": str(exc), "current": now}
    changes = {}
    for symbol in sorted(set(now) & set(before)):
        changes[symbol] = {factor: {name: {
            "previous": before[symbol]["factors"].get(factor, {}).get(name),
            "current": values.get(name),
            "change": values[name] - before[symbol]["factors"][factor][name]
            if values.get(name) is not None and before[symbol]["factors"].get(factor, {}).get(name) is not None else None}
            for name in ("raw_score", "reliability", "effective_score", "weighted_contribution")}
            for factor, values in now[symbol]["factors"].items()}
    return {"availability": "AVAILABLE", "current": now, "previous": before, "changes": changes}
