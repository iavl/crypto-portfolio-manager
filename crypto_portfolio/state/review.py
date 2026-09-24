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


_EXECUTABLE_PLAN_ACTIONS = {"INCREASE", "REDUCE", "EXIT"}
_TRANCHE_FULL_FRACTION = 0.995
_TRANCHE_PARTIAL_FRACTION = 0.01
_ZONE_REL_TOL = 1e-4


def _field(record, name, default=None):
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _plan_tranches(plan):
    tranches = _field(plan, "tranches") or ()
    normalized = tuple(t.as_dict() if hasattr(t, "as_dict") else t for t in tranches)
    return tuple(sorted(normalized, key=lambda t: _field(t, "sequence", 0)))


def _tranche_number(tranche, name):
    value = _field(tranche, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"execution tranche {name} must be a finite number")
    return float(value)


def _snapshot_quantity(snapshots, snapshot_id, symbol):
    for snapshot in snapshots:
        if _field(snapshot, "snapshot_id") != snapshot_id:
            continue
        for position in snapshot.get("positions", ()) if isinstance(snapshot, Mapping) else getattr(snapshot, "positions", ()):
            if str(_field(position, "symbol", "")).strip().upper() == symbol:
                quantity = _field(position, "quantity")
                if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) \
                        or not math.isfinite(float(quantity)) or quantity < 0:
                    return None
                return float(quantity)
        return None
    return None


def _external_flow_between(snapshots, first_id, second_id):
    """True when any snapshot at or after the second one's window carries an external flow.

    A non-zero external flow between the two referenced snapshots makes a raw
    quantity delta unusable for fill attribution (the delta may contain a
    deposit or withdrawal of the same asset instead of a plan fill).
    """
    indexes = {}
    for index, snapshot in enumerate(snapshots):
        indexes.setdefault(_field(snapshot, "snapshot_id"), index)
    if first_id not in indexes or second_id not in indexes:
        return False
    for snapshot in snapshots[indexes[first_id] + 1:indexes[second_id] + 1]:
        flow = _field(snapshot, "external_cash_flow", 0.0)
        if isinstance(flow, (int, float)) and not isinstance(flow, bool) and math.isfinite(float(flow)) and float(flow) != 0.0:
            return True
    return False


def _effective_plan_status(record, events_by_decision):
    events = events_by_decision.get(record["decision_id"], ())
    status = str(_field(record, "status", "PENDING") or "PENDING").strip().upper()
    if events:
        status = events[-1]["status"]
    return status


def _zones_match(prior_tranches, current_tranches):
    if len(prior_tranches) != len(current_tranches):
        return False
    for prior, current in zip(prior_tranches, current_tranches):
        for name in ("price_low", "price_high"):
            if not math.isclose(_tranche_number(prior, name), _tranche_number(current, name), rel_tol=_ZONE_REL_TOL):
                return False
    return True


def _snapshot_timestamp(snapshots, snapshot_id):
    for snapshot in snapshots:
        if _field(snapshot, "snapshot_id") == snapshot_id:
            return parse_timestamp(_field(snapshot, "timestamp"))
    return None


def _attribution_window(snapshots, prior, decision):
    """(start, end] datetimes bounding the fills/delta attribution interval."""
    prior_ts = _snapshot_timestamp(snapshots, _field(prior, "based_on_snapshot_id"))
    current_ts = _snapshot_timestamp(snapshots, _field(decision, "based_on_snapshot_id"))
    if prior_ts is None or current_ts is None:
        prior_ts = parse_timestamp(prior["timestamp"])
        current_ts = parse_timestamp(_field(decision, "timestamp"))
    return prior_ts, current_ts


def _supplied_fills(fills, symbol):
    """Normalized trade records for a symbol, or None when not fetched.

    Key presence in the supplied mapping means the exchange trade history was
    fetched for that symbol — an empty sequence is a confident zero, not a gap.
    """
    if not isinstance(fills, Mapping):
        return None
    supplied = fills.get(symbol)
    if supplied is None:
        return None
    from ..models.fill import TradeFill

    return tuple(
        fill if isinstance(fill, TradeFill) else TradeFill.from_mapping(fill)
        for fill in supplied
    )


def _fills_attribution(records, tranches, plan_action, effective_status, window):
    """Attribute exchange trade records to tranche zones; records are truth."""
    start, end = window
    required_side = "BUY" if plan_action == "INCREASE" else "SELL"
    in_window = [
        fill for fill in records
        if start < parse_timestamp(fill.executed_at) <= end
    ]
    attributed_qty = [0.0] * len(tranches)
    attributed_notional = [0.0] * len(tranches)
    out_of_zone = {"count": 0, "quantity": 0.0, "notional": 0.0}
    opposite = {"count": 0, "quantity": 0.0, "notional": 0.0}
    for fill in in_window:
        if fill.side != required_side:
            opposite["count"] += 1
            opposite["quantity"] += fill.quantity
            opposite["notional"] += fill.notional
            continue
        for index, tranche in enumerate(tranches):
            if _tranche_number(tranche, "price_low") <= fill.price <= _tranche_number(tranche, "price_high"):
                attributed_qty[index] += fill.quantity
                attributed_notional[index] += fill.notional
                break
        else:
            out_of_zone["count"] += 1
            out_of_zone["quantity"] += fill.quantity
            out_of_zone["notional"] += fill.notional

    tranche_states = []
    remaining_planned_usd = 0.0
    for index, tranche in enumerate(tranches):
        est = _tranche_number(tranche, "estimated_quantity")
        amount = _tranche_number(tranche, "amount_usd")
        fraction = min(1.0, attributed_qty[index] / est) if est > 0 else 0.0
        if fraction >= _TRANCHE_FULL_FRACTION:
            state = "FULL"
        elif fraction >= _TRANCHE_PARTIAL_FRACTION:
            state = "PARTIAL"
        else:
            state = "UNFILLED"
        if state != "FULL":
            remaining_planned_usd += max(0.0, amount - attributed_notional[index])
        tranche_states.append({
            "sequence": _field(tranche, "sequence"),
            "zone_low": _tranche_number(tranche, "price_low"),
            "zone_high": _tranche_number(tranche, "price_high"),
            "fill_state": state,
            "fill_fraction": fraction,
            "fill_quantity": attributed_qty[index],
            "fill_notional_usd": attributed_notional[index],
        })

    attribution = "EXCHANGE_TRADE_RECORDS"
    matched_qty = sum(attributed_qty)
    total_est = sum(_tranche_number(t, "estimated_quantity") for t in tranches)
    caveat = None
    if effective_status == "NOT_EXECUTED" and total_est > 0 \
            and matched_qty / total_est >= _TRANCHE_PARTIAL_FRACTION:
        attribution = "STATUS_EVENT_CONFLICT"
    elif effective_status == "CONFIRMED" and matched_qty <= 0.0:
        caveat = ("terminal CONFIRMED but no in-window trade records matched — verify the "
                  "trade-history fetch window covers the attribution interval")
    return {
        "attribution": attribution,
        "tranche_states": tranche_states,
        "remaining_planned_usd": remaining_planned_usd,
        "unmatched_out_of_zone": out_of_zone,
        "unmatched_opposite_side": opposite,
        "caveat": caveat,
    }


def prior_plan_disposition(decision, history, snapshots=(), status_events=(), fills=None):
    """State what the current review does with resting orders from prior plans.

    For every asset whose most recent prior decision planned executable
    tranches, attribute fills and derive one advisory instruction for the
    unfilled remainder: ``CANCEL_RESTING`` (superseded — budgets are not
    additive), ``REPLACE_WITH_NEW_PLAN`` / ``KEEP_EQUIVALENT_ORDERS`` (the
    current decision re-plans the asset), or ``NOTHING_RESTING``.

    Attribution prefers exchange trade records: pass ``fills`` as a mapping of
    symbol -> trade records; key presence means the history was fetched (an
    empty sequence is a confident zero). Records are matched to tranche zones
    by executed price inside the attribution window. Without records for a
    symbol, fills are inferred from snapshot quantity deltas in tranche order,
    guarded against external flows and status-event contradictions. The system
    never places or cancels real orders; these are deterministic instructions
    for the human's manually rested exchange orders.
    """
    ordered = sorted(history, key=lambda d: parse_timestamp(d['timestamp']))
    for prior in ordered:
        if parse_timestamp(prior['timestamp']) >= parse_timestamp(_field(decision, 'timestamp')):
            raise ValueError('review history must precede the decision')

    events_by_decision = {}
    for event in status_events:
        normalized = event if isinstance(event, Mapping) else event.as_dict()
        events_by_decision.setdefault(normalized["decision_id"], []).append(normalized)
    for events in events_by_decision.values():
        events.sort(key=lambda e: parse_timestamp(e["timestamp"]))

    current_plans = _field(decision, 'execution_plans') or {}
    if not isinstance(current_plans, Mapping):
        current_plans = {p.symbol: p for p in current_plans}

    latest_plan_by_symbol = {}
    for prior in reversed(ordered):
        plans = prior.get("execution_plans") or {}
        for symbol, plan in plans.items():
            action = str(_field(plan, "action", "")).strip().upper()
            planned = _field(plan, "planned_amount_usd", 0.0) or 0.0
            if action not in _EXECUTABLE_PLAN_ACTIONS or planned <= 0:
                continue
            if not _plan_tranches(plan):
                continue
            latest_plan_by_symbol.setdefault(str(symbol).strip().upper(), (prior, plan))

    disposition = {}
    for symbol, (prior, plan) in sorted(latest_plan_by_symbol.items()):
        tranches = _plan_tranches(plan)
        plan_action = str(_field(plan, "action", "")).strip().upper()
        effective_status = _effective_plan_status(prior, events_by_decision)
        prior_snapshot_id = _field(prior, "based_on_snapshot_id")
        current_snapshot_id = _field(decision, "based_on_snapshot_id")

        fills_for_symbol = _supplied_fills(fills, symbol)
        if fills_for_symbol is not None:
            derived = _fills_attribution(
                fills_for_symbol, tranches, plan_action, effective_status,
                _attribution_window(snapshots, prior, decision),
            )
            attribution = derived["attribution"]
            tranche_states = derived["tranche_states"]
            remaining_planned_usd = derived["remaining_planned_usd"]
            quantity_delta = None
            unmatched = {
                "out_of_zone": derived["unmatched_out_of_zone"],
                "opposite_side": derived["unmatched_opposite_side"],
            }
            caveat = derived["caveat"]
        else:
            unmatched = None
            caveat = None
            first = _snapshot_quantity(snapshots, prior_snapshot_id, symbol)
            second = _snapshot_quantity(snapshots, current_snapshot_id, symbol)
            quantity_delta = None if first is None or second is None else second - first
            attribution = "STATUS_EVENT_ONLY"
            if quantity_delta is not None:
                attribution = (
                    "UNRESOLVED_EXTERNAL_FLOW"
                    if _external_flow_between(snapshots, prior_snapshot_id, current_snapshot_id)
                    else "EXCHANGE_QUANTITY_DELTA"
                )
                total_est = sum(_tranche_number(t, "estimated_quantity") for t in tranches)
                delta_shows_fill = total_est > 0 and abs(quantity_delta) / total_est >= _TRANCHE_PARTIAL_FRACTION
                if effective_status == "NOT_EXECUTED" and delta_shows_fill:
                    attribution = "STATUS_EVENT_CONFLICT"
                elif effective_status == "CONFIRMED" and not delta_shows_fill:
                    attribution = "STATUS_EVENT_CONFLICT"

            remaining = None
            if attribution == "EXCHANGE_QUANTITY_DELTA":
                remaining = quantity_delta if plan_action == "INCREASE" else -quantity_delta
            tranche_states = []
            remaining_planned_usd = 0.0
            for tranche in tranches:
                est = _tranche_number(tranche, "estimated_quantity")
                amount = _tranche_number(tranche, "amount_usd")
                fraction = 0.0
                if remaining is not None:
                    filled = min(max(remaining, 0.0), est)
                    fraction = filled / est if est > 0 else 0.0
                    remaining -= filled
                elif effective_status == "CONFIRMED":
                    fraction = None
                if fraction is None:
                    state = "UNKNOWN"
                    remaining_planned_usd += amount
                elif fraction >= _TRANCHE_FULL_FRACTION:
                    state = "FULL"
                elif fraction >= _TRANCHE_PARTIAL_FRACTION:
                    state = "PARTIAL"
                    remaining_planned_usd += (1.0 - fraction) * amount
                else:
                    state = "UNFILLED"
                    remaining_planned_usd += amount
                tranche_states.append({
                    "sequence": _field(tranche, "sequence"),
                    "zone_low": _tranche_number(tranche, "price_low"),
                    "zone_high": _tranche_number(tranche, "price_high"),
                    "fill_state": state,
                    "fill_fraction": fraction,
                    "fill_quantity": None,
                    "fill_notional_usd": None,
                })

        resting = any(t["fill_state"] in ("UNFILLED", "PARTIAL", "UNKNOWN") for t in tranche_states)
        current_plan = current_plans.get(symbol)
        current_action = str(_field(current_plan, "action", "")).strip().upper() if current_plan is not None else ""
        if not resting:
            instruction = "NOTHING_RESTING"
            reason = "every planned tranche is filled; no resting order remains from this plan"
        elif effective_status == "NOT_EXECUTED":
            instruction = "CANCEL_RESTING"
            reason = ("terminal NOT_EXECUTED with an unfilled remainder: no order should rest "
                      "against this plan — cancel any manually placed order")
        elif current_plan is None or current_action not in _EXECUTABLE_PLAN_ACTIONS:
            instruction = "CANCEL_RESTING"
            reason = ("superseded by the current review, which funds a different priority: cancel the "
                      "unfilled resting orders; the remaining strategic gap is re-evaluated and prior "
                      "budgets are not additive")
        elif current_action != plan_action or not _zones_match(
                tranches, _plan_tranches(current_plan)):
            instruction = "REPLACE_WITH_NEW_PLAN"
            reason = ("the current review re-plans this asset at different zones: cancel the unfilled "
                      "resting orders and rest the current plan's zones instead")
        else:
            instruction = "KEEP_EQUIVALENT_ORDERS"
            reason = ("the current review re-issues identical zones: equivalent resting orders may remain, "
                      "with the current decision as the authoritative plan record")
        if attribution == "STATUS_EVENT_CONFLICT":
            reason += ("; the attribution evidence and the terminal status event disagree — verify "
                       "exchange history before cancelling or resting orders")
        elif attribution == "UNRESOLVED_EXTERNAL_FLOW":
            reason += ("; an external cash flow between the reference snapshots prevents quantity-based "
                       "fill attribution — verify fills against exchange history before cancelling")
        elif attribution == "STATUS_EVENT_ONLY":
            reason += "; referenced snapshots were not supplied, so per-tranche fills rely on the status event only"
        if unmatched is not None:
            for label, bucket in (("outside every planned zone", unmatched["out_of_zone"]),
                                  ("on the opposite side of the plan", unmatched["opposite_side"])):
                if bucket["count"]:
                    reason += (f"; {bucket['count']} in-window trade(s) {label} totalling "
                               f"{bucket['quantity']:.8f} / ${bucket['notional']:.2f} were not attributed")
        if caveat:
            reason += f"; {caveat}"

        record = {
            "decision_id": prior["decision_id"],
            "decision_timestamp": prior["timestamp"],
            "action": plan_action,
            "effective_status": effective_status,
            "attribution": attribution,
            "quantity_delta": quantity_delta,
            "remaining_planned_usd": round(remaining_planned_usd, 6),
            "order_instruction": instruction,
            "instruction_reason": reason,
            "tranches": tranche_states,
        }
        if unmatched is not None:
            record["unmatched_trades"] = unmatched
        disposition[symbol] = record
    return disposition


def finalize_review(decision, snapshot, *, acquisition, artifact_root=None, history=(),
                    new_cash=0.0, persist=False, decision_path=None,
                    status_events=(), snapshots=(), fills=None):
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
    output['prior_plan_disposition'] = prior_plan_disposition(
        model, history, snapshots=snapshots, status_events=status_events, fills=fills)
    if output['operation'] != record['operation']:
        raise ValueError('report and persisted operation differ')
    if persist:
        append_decision(model, Path(decision_path) if decision_path else None, policy=policy, artifact_root=artifact_root)
    return output
