"""One publication boundary for finalized local reviews. No network or trading."""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
from typing import Mapping

from ..engine.decision_packet import build_decision_review_packet
from ..engine.report_packet import build_final_review_output, build_report_packet
from ..models.confidence import DecisionConfidence
from ..models.decision import Decision
from ..models.policy import policy_from_mapping, resolve_policy
from ..models.portfolio import PortfolioSnapshot, snapshot_from_mapping
from ..models.time import parse_timestamp
from .decisions import _validated_decision, append_decision


def wait_history(decision, history):
    """Explain consecutive gate holds without adding or reserving past budgets."""
    result = {}
    ordered = sorted(history, key=lambda d: parse_timestamp(d['timestamp']))
    if len({d['decision_id'] for d in ordered}) != len(ordered):
        raise ValueError('duplicate historical decision ID')
    for symbol, plan in (decision.execution_plans or {}).items():
        if plan.action != 'WAIT':
            continue
        code = (plan.gate_details or {}).get('code', plan.rationale)
        first = decision.timestamp
        previous_id, count = None, 1
        for prior in reversed(ordered):
            if parse_timestamp(prior['timestamp']) >= parse_timestamp(decision.timestamp):
                raise ValueError('review history must precede the decision')
            old = (prior.get('execution_plans') or {}).get(symbol)
            if not old or old['action'] != 'WAIT':
                break
            old_code = (old.get('gate_details') or {}).get('code', old.get('rationale'))
            if old_code != code:
                break
            first = prior['timestamp']
            previous_id = previous_id or prior['decision_id']
            count += 1
        result[symbol] = {'first_wait_at': first, 'same_reason_reviews': count,
            'previous_decision_id': previous_id, 'reason_code': code,
            'budget_semantics': 'current approval only; previous approvals are not additive'}
    return result


def superseded_status_events(decision, history):
    """Propose append-only terminal events; never write them implicitly."""

    events = []
    current_symbols = set((decision.execution_plans or {}).keys())
    for prior in history:
        if str(prior.get("status", "PENDING")).upper() != "PENDING":
            continue
        if parse_timestamp(prior["timestamp"]) >= parse_timestamp(decision.timestamp):
            raise ValueError("review history must precede the decision")
        plans = prior.get("execution_plans") or {}
        if not plans or not (set(plans) & current_symbols):
            continue
        events.append(
            {
                "decision_id": prior["decision_id"],
                "timestamp": decision.timestamp,
                "status": "NOT_EXECUTED",
                "reason": (
                    "SUPERSEDED_BY_REVIEW: prior conditional plan was not confirmed; "
                    "its approved budget is not additive to the current review"
                ),
            }
        )
    return events


def finalize_review(decision, snapshot, *, acquisition, artifact_root=None, history=(),
                    new_cash=0.0, persist=False, decision_path=None):
    """Validate frozen inputs, calculate diagnostics, publish a single operation view.

    The caller supplies the already authorized allocation/rebalance and confidence.
    This boundary never repairs a contradictory supplied confidence or reruns trades.
    """
    finalized = acquisition.get('finalized') if isinstance(acquisition, Mapping) else acquisition.finalized
    if finalized is not True:
        raise ValueError('acquisition must be finalized before review publication')
    model = decision if isinstance(decision, Decision) else Decision.from_mapping(decision)
    policy = policy_from_mapping(model.resolved_policy) if model.resolved_policy else resolve_policy()
    current = snapshot if isinstance(snapshot, PortfolioSnapshot) else snapshot_from_mapping(snapshot, policy=policy)[0]
    if not model.based_on_snapshot_id or current.snapshot_id != model.based_on_snapshot_id:
        raise ValueError('review snapshot identity mismatch')
    if parse_timestamp(current.timestamp) > parse_timestamp(model.timestamp):
        raise ValueError('review snapshot is from the future')
    expected = {p.symbol: p.value_usd/current.total_value_usd for p in current.positions}
    if any(not math.isclose(expected.get(s, 0), model.current_weights.get(s, 0), abs_tol=1e-9)
           for s in set(expected) | set(model.current_weights)):
        raise ValueError('review weights do not match the referenced snapshot')
    from ..engine.rebalance import RebalanceAction
    symbols = list(dict.fromkeys([*model.current_weights, *model.target_weights, *model.factor_scores]))
    known = {a.symbol for a in model.actions}
    actions = (*model.actions, *(RebalanceAction(s, "HOLD", model.current_weights.get(s, 0),
                model.current_weights.get(s, 0), 0, "LOW") for s in symbols if s not in known))
    model = replace(model, actions=actions, operation=None)
    nav = model.nav_performance or {}
    packet = build_decision_review_packet(
        review_type=model.review_type, market_regime=model.market_regime,
        current_weights=model.current_weights, target_weights=model.target_weights,
        assessments=model.factor_scores, actions=model.actions,
        execution_plans=model.execution_plans or {}, calculation_context=model.calculation_context,
        regime_confidence=model.regime_confidence, decision_confidence=model.decision_confidence,
        nav_performance=model.nav_performance, benchmark_performance=model.benchmark_performance,
        portfolio_drawdown=nav.get('current_drawdown'), portfolio_value=current.total_value_usd,
        new_cash=new_cash, policy=policy, overlays=model.market_overlays,
        event_scan_summary=model.event_scan_summary, manual_asset_contexts=model.manual_asset_contexts,
        risk_flags=tuple(str(item) for item in model.risk_checks),
        target_attribution=model.target_attribution)
    confidence = packet.decision_confidence
    confidence = confidence if isinstance(confidence, DecisionConfidence) else DecisionConfidence.from_mapping(confidence)
    if confidence.band == 'LOW' and any(a.action == 'INCREASE' and a.symbol not in policy.stable_symbols for a in model.actions):
        raise ValueError('LOW confidence cannot authorize a new increase')
    model = replace(model, decision_confidence=confidence, regime_confidence=packet.regime_confidence,
                    review_diagnostics=packet.review_diagnostics)
    from ..engine.funding import funding_readiness
    readiness = funding_readiness(current, model.as_dict()['operation'], settlement_asset=policy.stable_symbols[0])
    model = replace(model, funding_readiness=readiness)
    # Same validation runs for dry-run, report generation and append.
    record, _ = _validated_decision(model, policy, artifact_root=artifact_root)
    report = build_report_packet(packet, acquisition=acquisition)
    output = build_final_review_output(report, acquisition=acquisition)
    output['funding_readiness'] = readiness
    output['current_instruction'] = "WAIT_FOR_FUNDING_CONDITIONS" if readiness['status'] == "CONDITIONAL" else output['operation']['decision']
    output['decision_record'] = record
    output['wait_history'] = wait_history(model, history)
    output['superseded_status_events_to_append'] = superseded_status_events(model, history)
    if output['operation'] != record['operation']:
        raise ValueError('report and persisted operation differ')
    if persist:
        append_decision(model, Path(decision_path) if decision_path else None, policy=policy, artifact_root=artifact_root)
    return output
