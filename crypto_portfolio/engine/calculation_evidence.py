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
from ..models.time import parse_timestamp

TREND_SOURCE = "crypto_portfolio.engine.factors.trend"


def calculation_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(thaw_packet_value(value), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def trend_calculation_evidence(snapshot: Any, policy: Policy) -> Evidence:
    """Bind the actual normalized technical input, not unrelated cached scalars.

    This is a calculation receipt, not independent corroboration of a provider.
    Dataset hashes remain provenance; they are never masqueraded as Evidence IDs.
    """
    value = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(vars(snapshot))
    metadata = {"policy_hash": policy_hash(policy), "ohlcv_hash": snapshot.ohlcv_hash,
                "volume_profile_hash": snapshot.volume_profile_hash,
                "calculation_input_hash": calculation_hash(value)}
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
    result = calculate_trend_factor(snapshot, policy=policy)
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


def validate_calculation_context(
    context: Mapping[str, Any],
    *,
    require_trend: bool = False,
    expected_policy_hash: str | None = None,
    expected_as_of: str | None = None,
    expected_symbols: set[str] | None = None,
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
        assessment = AssetAssessment.from_mapping(symbol, thaw_packet_value(raw))
        validate_factor_evidence_binding(assessment.factor_scores, evidence, symbol=symbol)
        for factor in assessment.factor_scores.values():
            for key in factor.evidence_ids:
                item = evidence[key]
                if parse_timestamp(item.observed_at) > moment or parse_timestamp(item.fetched_at) > moment:
                    raise ValueError("CALCULATION_FUTURE_EVIDENCE")
        trend = assessment.factor_scores.get("trend")
        detail = {}
        if trend is not None and trend.availability == "AVAILABLE":
            if require_trend or any(evidence[key].source == TREND_SOURCE for key in trend.evidence_ids):
                detail = validate_trend_calculation(trend, evidence, symbol=symbol,
                                                    as_of=context["as_of"], policy=policy)
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
