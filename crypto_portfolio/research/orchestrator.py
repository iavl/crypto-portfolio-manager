"""Closed-loop historical strategy orchestration over frozen review inputs."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from ..engine.allocation import build_target_allocation
from ..engine.backtest import QuantityLedger, buy_and_hold_benchmark, performance_metrics
from ..engine.entry import build_entry_plan
from ..engine.execution_replay import simulate_execution_plan
from ..engine.operation import build_final_operation
from ..engine.rebalance import direction_history_from_decisions, recommend_rebalance
from ..engine.regime import RegimeInputs, determine_regime
from ..engine.risk import run_risk_gate
from ..engine.strategy_replay import ReplayReview
from ..models.evidence import AssetAssessment
from ..models.market import TechnicalSnapshot
from ..models.policy import Policy, resolve_policy
from ..models.time import parse_timestamp


def _assessments(value: Mapping[str, Any]) -> dict[str, AssetAssessment]:
    return {
        str(symbol).strip().upper(): (
            assessment if isinstance(assessment, AssetAssessment)
            else AssetAssessment.from_mapping(symbol, assessment)
        )
        for symbol, assessment in value.items()
    }


def _period_end_prices(review: ReplayReview) -> dict[str, float]:
    result = {}
    for symbol, price in review.current_prices.items():
        if symbol not in review.next_returns:
            raise ValueError(f"period {review.as_of} is missing return for {symbol}")
        result[symbol] = price * (1.0 + review.next_returns[symbol])
        if result[symbol] <= 0:
            raise ValueError("period return produces a non-positive price")
    return result


def run_historical_backtest(
    reviews: Sequence[ReplayReview],
    *,
    policy: Policy | None = None,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    ordinary_review_weekday: int | None = None,
) -> dict[str, Any]:
    """Run the shared decision engines with an exact quantity/cash ledger."""
    if not reviews:
        raise ValueError("at least one historical review is required")
    resolved = policy or resolve_policy()
    if ordinary_review_weekday is not None and ordinary_review_weekday not in range(7):
        raise ValueError("ordinary_review_weekday must be 0..6 or null")
    if any(not review.current_prices for review in reviews):
        raise ValueError("historical quantity replay requires current_prices on every review")
    moments = [review.moment for review in reviews]
    if moments != sorted(moments) or len(moments) != len(set(moments)):
        raise ValueError("historical reviews must be unique and ordered")
    if any(review.end_moment > following.moment for review, following in zip(reviews, reviews[1:])):
        raise ValueError("historical review periods overlap")

    first = reviews[0]
    ledger = QuantityLedger.from_weights(first.portfolio_value, first.current_weights, first.current_prices)
    current_prices = dict(first.current_prices)
    ledger.mark(first.as_of, current_prices)
    previous_regime = None
    replayed_decisions: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    regime_counts: Counter[str] = Counter()
    constraint_violations: Counter[str] = Counter()
    plan_counts: Counter[str] = Counter()

    for index, review in enumerate(reviews):
        if index > 0:
            # The prior period end is the current decision boundary. Require
            # the independently frozen close to agree with the carried mark.
            for symbol, price in review.current_prices.items():
                prior = current_prices.get(symbol)
                if prior is None or abs(prior / price - 1.0) > 1e-8:
                    raise ValueError(f"PRICE_BOUNDARY_MISMATCH: {symbol} at {review.as_of}")
        point = ledger.valuations[-1]
        assessments = _assessments(review.assessments)
        regime_values = dict(review.regime_inputs)
        regime_values["portfolio_drawdown_band"] = point.drawdown
        regime = determine_regime(RegimeInputs(**regime_values), policy=resolved, previous=previous_regime)
        previous_regime = regime
        regime_counts[regime.regime] += 1
        allocation = build_target_allocation(
            policy=resolved, regime=regime.regime, assessments=assessments,
            current_weights=point.weights,
        )
        risk = run_risk_gate(
            allocation, policy=resolved, regime=regime.regime, assessments=assessments,
            current_drawdown=point.drawdown, overlays=review.overlays,
            chain_liveness=review.chain_liveness, current_weights=point.weights,
        )
        for violation in risk.violations:
            constraint_violations[violation.code] += 1
        rebalance = recommend_rebalance(
            point.weights, allocation.target_weights, point.total_value_usd,
            policy=resolved, regime=regime.regime,
            deployment_caps=allocation.deployment_factors,
            hard_exposure_caps={
                symbol: float(value["hard_exposure_cap"])
                for symbol, value in allocation.deployment_allowances.items()
                if value.get("hard_exposure_cap") is not None
            },
            thesis_broken=review.thesis_broken or None,
            hard_action_reasons=review.hard_action_reasons,
            direction_history=direction_history_from_decisions(replayed_decisions),
        )
        actions = [action for action in rebalance.actions
                   if action.symbol not in resolved.stable_symbols
                   and action.action in {"INCREASE", "REDUCE", "EXIT"}]
        if ordinary_review_weekday is not None and review.moment.weekday() != ordinary_review_weekday:
            daily_risk_active = regime.regime != "NORMAL" or point.drawdown <= -0.4 * resolved.max_portfolio_drawdown
            actions = [
                action for action in actions
                if action.action in {"REDUCE", "EXIT"}
                and (daily_risk_active or action.action_reason in resolved.rebalance["staging"]["bypass_reasons"])
            ]
        if risk.errors:
            # Invalid target/risk states must not add exposure. Existing risk
            # reductions remain executable so a breach cannot trap the book.
            actions = [action for action in actions if action.action in {"REDUCE", "EXIT"}]

        effective_plans: dict[str, Mapping[str, Any]] = {}
        entry_outcomes: dict[str, Mapping[str, Any]] = {}
        scheduled: dict[Any, list[tuple[int, str, str, float, float, str]]] = {}
        for action in actions:
            bars = review.execution_bars.get(action.symbol, ())
            if not bars:
                plan_counts["BLOCKED_NO_HOURLY_BARS"] += 1
                continue
            if action.action in {"REDUCE", "EXIT"}:
                first_bar = bars[0]
                moment = parse_timestamp(first_bar["timestamp"])
                scheduled.setdefault(moment, []).append(
                    (0, action.symbol, "SELL", action.amount_usd, float(first_bar["open"]), action.action_reason)
                )
                continue
            snapshots = review.technical_inputs.get(action.symbol, ())
            if not snapshots:
                plan_counts["BLOCKED_NO_TECHNICAL_SNAPSHOT"] += 1
                continue
            snapshot = TechnicalSnapshot.from_mapping(list(snapshots)[-1])
            assessment = assessments[action.symbol]
            plan = build_entry_plan(
                action.symbol, action.amount_usd, snapshot, regime.regime,
                assessment.confidence, policy=resolved,
            )
            effective_plans[action.symbol] = plan.as_dict()
            outcome = simulate_execution_plan(plan.as_dict(), bars, decision_as_of=review.as_of)
            entry_outcomes[action.symbol] = outcome
            plan_counts[outcome["status"]] += 1
            for event in outcome["events"]:
                if event["status"] not in {"FILLED", "PARTIAL_FILL"}:
                    continue
                moment = parse_timestamp(event["timestamp"])
                scheduled.setdefault(moment, []).append(
                    (1, action.symbol, "BUY", float(event["amount_usd"]), float(event["fill_price"]), "CONDITIONAL_ENTRY")
                )

        bars_by_time: dict[Any, dict[str, Mapping[str, Any]]] = {}
        for symbol, bars in review.execution_bars.items():
            for bar in bars:
                bars_by_time.setdefault(parse_timestamp(bar["timestamp"]), {})[symbol] = bar
        period_trade_start = len(ledger.trades)
        for moment in sorted(bars_by_time):
            for _, symbol, side, amount, reference, reason in sorted(scheduled.get(moment, ()), key=lambda item: item[0]):
                ledger.execute(
                    timestamp=moment.isoformat().replace("+00:00", "Z"), symbol=symbol, side=side,
                    amount_usd=amount, reference_price=reference,
                    fee_bps=fee_bps, slippage_bps=slippage_bps, reason=reason,
                )
            mark_moments = []
            for symbol, bar in bars_by_time[moment].items():
                current_prices[symbol] = float(bar["close"])
                mark_moments.append(parse_timestamp(bar.get("mark_timestamp", bar["timestamp"])))
            # A bar's close is only known at its mark_timestamp.  This keeps
            # daily execution from leaking the next day's close into the
            # opening fill and preserves the same rule for optional 1H bars.
            mark_moment = max(mark_moments)
            period_end_moment = parse_timestamp(review.period_end)
            if mark_moment < period_end_moment:
                ledger.mark(mark_moment.isoformat().replace("+00:00", "Z"), current_prices)
        current_prices = _period_end_prices(review)
        period_end_moment = parse_timestamp(review.period_end)
        if parse_timestamp(ledger.valuations[-1].timestamp) < period_end_moment:
            ledger.mark(review.period_end, current_prices)
        period_trades = ledger.trades[period_trade_start:]
        turnover = sum(item.gross_notional_usd for item in period_trades) / point.total_value_usd
        cost = sum(
            item.fee_usd + abs(item.execution_price - item.reference_price) * item.quantity
            for item in period_trades
        )
        try:
            operation: Any = build_final_operation(
                actions, effective_plans, stable_symbols=resolved.stable_symbols,
            ).as_dict()
        except ValueError as exc:
            operation = {"status": "UNAVAILABLE", "reason": str(exc)}
        replayed_decisions.append({
            "timestamp": review.as_of, "actions": [action.as_dict() for action in rebalance.actions],
        })
        review_rows.append({
            "as_of": review.as_of, "period_end": review.period_end,
            "regime": regime.as_dict(), "drawdown_input": point.drawdown,
            "assessments": {symbol: value.as_dict() for symbol, value in assessments.items()},
            "allocation": allocation.as_dict(), "risk_gate": risk.as_dict(),
            "rebalance": rebalance.as_dict(), "execution_plans": effective_plans,
            "entry_outcomes": entry_outcomes, "operation": operation,
            "trades": [item.as_dict() for item in period_trades],
            "turnover": turnover, "cost_usd": cost,
            "end_value_usd": ledger.valuations[-1].total_value_usd,
            "end_drawdown": ledger.valuations[-1].drawdown,
        })

    metrics = performance_metrics(ledger.valuations)
    aligned_prices = [(reviews[0].as_of, dict(reviews[0].current_prices))]
    for review in reviews:
        aligned_prices.append((review.period_end, _period_end_prices(review)))
    benchmarks = {
        "btc_buy_and_hold_zero_cost": buy_and_hold_benchmark(
            prices_by_time=aligned_prices, weights={"BTC": 1.0},
            initial_value_usd=first.portfolio_value,
        ),
        "btc_eth_70_30_zero_cost": buy_and_hold_benchmark(
            prices_by_time=aligned_prices, weights={"BTC": 0.7, "ETH": 0.3},
            initial_value_usd=first.portfolio_value,
        ),
        "btc_buy_and_hold_investable": buy_and_hold_benchmark(
            prices_by_time=aligned_prices, weights={"BTC": 1.0},
            initial_value_usd=first.portfolio_value, fee_bps=fee_bps, slippage_bps=slippage_bps,
        ),
        "btc_eth_70_30_investable": buy_and_hold_benchmark(
            prices_by_time=aligned_prices, weights={"BTC": 0.7, "ETH": 0.3},
            initial_value_usd=first.portfolio_value, fee_bps=fee_bps, slippage_bps=slippage_bps,
        ),
    }
    strategy_return = metrics["total_return"]
    benchmark_comparison = {
        name: {
            "total_return": value["metrics"]["total_return"],
            "excess_return": strategy_return - value["metrics"]["total_return"],
            "maximum_drawdown": value["metrics"]["maximum_drawdown"],
        }
        for name, value in benchmarks.items()
    }
    return {
        "engine": "quantity_cash_closed_loop", "metrics": metrics,
        "cadence": (
            "DAILY_WITH_14D_FULL" if ordinary_review_weekday is None
            else "WEEKLY_ORDINARY_WITH_DAILY_RISK_CHECKS"
        ),
        "total_turnover": sum(row["turnover"] for row in review_rows),
        "total_cost_usd": sum(row["cost_usd"] for row in review_rows),
        "average_cash_weight": sum(item.weights.get("USD", 0.0) for item in ledger.valuations) / len(ledger.valuations),
        "regime_counts": dict(regime_counts), "plan_status_counts": dict(plan_counts),
        "constraint_violation_counts": dict(constraint_violations),
        "benchmarks": benchmarks, "benchmark_comparison": benchmark_comparison, "reviews": review_rows,
        "trades": [item.as_dict() for item in ledger.trades],
        "valuations": [item.as_dict() for item in ledger.valuations],
    }


__all__ = ["run_historical_backtest"]
