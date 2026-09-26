"""Closed-loop historical strategy orchestration over frozen review inputs."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from ..engine.allocation import build_target_allocation
from ..engine.backtest import (
    QuantityLedger,
    buy_and_hold_benchmark,
    constant_weight_rebalanced_benchmark,
    performance_metrics,
)
from ..engine.benchmark import exposure_timing_contribution, vol_matched_cash_weight
from ..engine.entry import build_entry_plan
from ..engine.execution_replay import simulate_execution_plan
from ..engine.operation import build_final_operation
from ..engine.portfolio_risk import PortfolioRiskInputs, build_portfolio_risk_inputs_from_closes
from ..engine.rebalance import direction_history_from_decisions, recommend_rebalance
from ..engine.regime import RegimeInputs, determine_regime, market_only_regime
from ..engine.risk import risk_overlay_floor, run_risk_gate
from ..engine.risk_recovery import RecoveryState, advance_emergency_recovery
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


def _metric_delta(strategy: Mapping[str, Any], benchmark: Mapping[str, Any], field: str) -> float | None:
    """Difference of one performance metric, or None when either side lacks it."""
    left = strategy.get(field)
    right = benchmark.get(field)
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _benchmark_comparison(strategy: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    """Pair a strategy with one benchmark on cumulative and annualized terms."""
    tracking_difference = strategy["total_return"] - benchmark["total_return"]
    return {
        "total_return": benchmark["total_return"],
        "excess_return": tracking_difference,
        # Strategy V2 Phase 5: tracking difference is the named cumulative
        # gap against the comparison benchmark (primary reading against the
        # vol-matched BTC/cash mix).
        "tracking_difference": tracking_difference,
        "maximum_drawdown": benchmark["maximum_drawdown"],
        "cagr": benchmark.get("cagr"),
        "annualized_volatility": benchmark.get("annualized_volatility"),
        "sharpe_rf_zero": benchmark.get("sharpe_rf_zero"),
        "excess_return_annualized": _metric_delta(strategy, benchmark, "cagr"),
        "volatility_delta": _metric_delta(strategy, benchmark, "annualized_volatility"),
        "sharpe_delta": _metric_delta(strategy, benchmark, "sharpe_rf_zero"),
        "drawdown_delta": _metric_delta(strategy, benchmark, "maximum_drawdown"),
        "sortino_delta": _metric_delta(strategy, benchmark, "sortino_target_zero"),
    }


def _vol_matched_benchmark(
    prices_by_time: Sequence[tuple[str, Mapping[str, float]]],
    *,
    metrics: Mapping[str, Any],
    initial_value_usd: float,
    fee_bps: float,
    slippage_bps: float,
) -> dict[str, Any] | None:
    """Size a static BTC/cash mix to the strategy's own volatility.

    This answers "was the risk that was taken worth it" instead of "did the
    strategy beat the riskiest available single holding".  It returns None when
    the strategy has no measurable volatility or would be matched by cash
    alone, because the all-cash experiments already cover that case.
    """
    target = metrics.get("annualized_volatility")
    mean_period_days = metrics.get("mean_period_days")
    if not target or not mean_period_days or target <= 0 or mean_period_days <= 0:
        return None
    btc_prices = [prices.get("BTC") for _, prices in prices_by_time]
    if any(price is None for price in btc_prices):
        return None
    weight = vol_matched_cash_weight(
        btc_returns=[right / left - 1.0 for left, right in zip(btc_prices, btc_prices[1:])],
        target_volatility=float(target),
        periods_per_year=365.25 / float(mean_period_days),
    )
    if weight <= 0:
        return None
    result = constant_weight_rebalanced_benchmark(
        prices_by_time=prices_by_time, weights={"BTC": weight, "USD": 1.0 - weight},
        initial_value_usd=initial_value_usd, fee_bps=fee_bps, slippage_bps=slippage_bps,
    )
    result["methodology"] = (
        f"{result['methodology']}; BTC weight {weight:.4f} solved to match the strategy's "
        f"{float(target):.4%} annualized volatility"
    )
    return result


def _vol_matched_btc_eth_benchmark(
    prices_by_time: Sequence[tuple[str, Mapping[str, float]]],
    *,
    metrics: Mapping[str, Any],
    initial_value_usd: float,
    fee_bps: float,
    slippage_bps: float,
    btc_weight: float = 0.7,
    eth_weight: float = 0.3,
) -> dict[str, Any] | None:
    """Size a static 70/30 BTC/ETH sleeve-plus-cash mix to the strategy's vol.

    The ablation question this answers (Strategy V2 Phase 6 benchmark B):
    would a two-asset volatility-targeting mix have used the same risk
    budget better than the full strategy? The sleeve weight is solved in
    closed form over the sleeve's own return series; the sleeve keeps the
    static 70/30 internal split. Returns None when either leg's prices are
    missing or the solve degenerates (the single-asset benchmark covers
    those cases).
    """
    target = metrics.get("annualized_volatility")
    mean_period_days = metrics.get("mean_period_days")
    if not target or not mean_period_days or target <= 0 or mean_period_days <= 0:
        return None
    btc_prices = [prices.get("BTC") for _, prices in prices_by_time]
    eth_prices = [prices.get("ETH") for _, prices in prices_by_time]
    if any(price is None for price in btc_prices) or any(price is None for price in eth_prices):
        return None
    if btc_prices[0] <= 0 or eth_prices[0] <= 0:
        return None
    sleeve_returns = [
        btc_weight * (b / a - 1.0) + eth_weight * (f / e - 1.0)
        for a, b, e, f in zip(btc_prices, btc_prices[1:], eth_prices, eth_prices[1:])
    ]
    try:
        sleeve_weight = vol_matched_cash_weight(
            btc_returns=sleeve_returns,
            target_volatility=float(target),
            periods_per_year=365.25 / float(mean_period_days),
        )
    except ValueError:
        return None
    if sleeve_weight <= 0:
        return None
    weights = {
        "BTC": btc_weight * sleeve_weight,
        "ETH": eth_weight * sleeve_weight,
        "USD": 1.0 - sleeve_weight,
    }
    result = constant_weight_rebalanced_benchmark(
        prices_by_time=prices_by_time, weights=weights,
        initial_value_usd=initial_value_usd, fee_bps=fee_bps, slippage_bps=slippage_bps,
    )
    result["methodology"] = (
        f"{result['methodology']}; 70/30 BTC/ETH sleeve weight {sleeve_weight:.4f} "
        f"solved to match the strategy's {float(target):.4%} annualized volatility"
    )
    return result


def _cash_yield_sensitivity(
    valuations: Sequence[Any], *, stable_symbols: Sequence[str], yields: tuple[float, ...] = (0.04, 0.05),
) -> dict[str, Any]:
    """What the realized path would have earned if the stable sleeve yielded.

    The replay books the stable leg at exactly zero, which understates a
    mandate that spends most of its life in cash. This diagnostic re-credits
    each valuation period with the stable share times an assumed annual
    yield, compounded over the period's actual length. It changes no engine
    accounting, and the zero-yield benchmarks stay the comparison basis.
    """
    from ..models.time import parse_timestamp
    if len(valuations) < 2:
        return {"status": "UNAVAILABLE", "reason": "at least two valuations are required"}
    total_seconds = (
        parse_timestamp(valuations[-1].timestamp) - parse_timestamp(valuations[0].timestamp)
    ).total_seconds()
    years = total_seconds / (365.25 * 86400.0)
    if years <= 0:
        return {"status": "UNAVAILABLE", "reason": "valuation span is empty"}
    scenarios: dict[str, dict[str, float]] = {}
    for annual_yield in yields:
        growth = 1.0
        for left, right in zip(valuations, valuations[1:]):
            period_return = right.total_value_usd / left.total_value_usd - 1.0
            stable_share = sum(left.weights.get(symbol, 0.0) for symbol in stable_symbols)
            span_years = (
                parse_timestamp(right.timestamp) - parse_timestamp(left.timestamp)
            ).total_seconds() / (365.25 * 86400.0)
            growth *= 1.0 + period_return + stable_share * ((1.0 + annual_yield) ** span_years - 1.0)
        total = growth - 1.0
        scenarios[f"yield_{annual_yield:.2%}"] = {
            "annual_yield": annual_yield,
            "total_return": total,
            "cagr": (1.0 + total) ** (1.0 / years) - 1.0,
        }
    return {"status": "AVAILABLE", "window_years": years, "scenarios": scenarios}


def build_risk_inputs_for_reviews(
    reviews: Sequence[ReplayReview],
    *,
    daily_by_symbol: Mapping[str, Any],
    policy: Policy,
) -> list[PortfolioRiskInputs]:
    """Point-in-time portfolio risk inputs for every review boundary.

    Mirrors the historical builder's convention exactly: only candles
    completed strictly before the review's as-of day enter the trailing
    window, so the volatility/correlation estimates are as knowable at the
    boundary as the assessments that consume them.
    """
    from bisect import bisect_right
    from datetime import timedelta

    from ..models.market import OHLCVSeries

    engine = policy.risk_engine or {}
    config = engine.get("portfolio_risk") or {}
    if not config:
        raise ValueError("risk engine portfolio_risk configuration is required")
    usable = {
        symbol: series
        for symbol, series in daily_by_symbol.items()
        if isinstance(series, OHLCVSeries)
    }
    symbols = sorted(
        {symbol for review in reviews for symbol in review.current_prices}
        - {symbol for symbol in policy.stable_symbols}
    )
    missing = [symbol for symbol in symbols if symbol not in usable]
    if missing:
        raise ValueError("portfolio risk inputs are missing daily series for: " + ", ".join(missing))
    caches = {
        symbol: tuple(series.completed_candles()) for symbol, series in usable.items() if symbol in symbols
    }
    times = {
        symbol: tuple(parse_timestamp(item.timestamp) for item in candles)
        for symbol, candles in caches.items()
    }
    result: list[PortfolioRiskInputs] = []
    for review in reviews:
        moment = parse_timestamp(review.as_of)
        boundary = moment - timedelta(days=1)
        closes_by_symbol: dict[str, list[float]] = {}
        for symbol in symbols:
            candles = caches[symbol]
            available = bisect_right(times[symbol], boundary)
            closes_by_symbol[symbol] = [float(item.close) for item in candles[:available]]
        result.append(
            build_portfolio_risk_inputs_from_closes(
                closes_by_symbol,
                window_weights=config["volatility_window_weights"],
                correlation_window_days=int(config["correlation_window_days"]),
                annualization_days=int(config["annualization_days"]),
                minimum_history_days=int(config["minimum_history_days"]),
            )
        )
    return result


def _emergency_recovery_diagnostics(
    rows: Sequence[Mapping[str, Any]], policy: Policy
) -> dict[str, Any] | None:
    """Phase B recovery diagnostics over the per-review emergency rows.

    Durations are consecutive-review run lengths; trough-to-reentry and the
    +30/60/90-day exposure snapshots answer whether the FSM neither bottoms
    out too early nor stays small for too long; recovery efficiency counts
    reviews where the market had already normalized while the book remained
    emergency-capped.
    """
    if not rows:
        return None
    transitions: Counter[str] = Counter()
    durations: dict[str, list[int]] = {"BREACH": [], "RECOVERY_1": [], "RECOVERY_2": []}
    run_state = str(rows[0]["state"])
    run_length = 1
    for row in rows[1:]:
        state = str(row["state"])
        if state != run_state:
            transitions[f"{run_state}->{state}"] += 1
            if run_state in durations:
                durations[run_state].append(run_length)
            run_state = state
            run_length = 1
        else:
            run_length += 1
    if run_state in durations:
        durations[run_state].append(run_length)

    breach_cap = float((policy.risk_engine or {})["emergency_overlay"]["breach_risky_cap"])
    troughs: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index == 0:
            continue
        if float(row["last_portfolio_low"]) < float(rows[index - 1]["last_portfolio_low"]) - 1e-12:
            moment = row["moment"]
            reentry_days: float | None = None
            for later in rows[index:]:
                if float(later["risky_weight"]) > breach_cap + 1e-12:
                    reentry_days = (later["moment"] - moment).total_seconds() / 86400.0
                    break
            snapshots = {}
            for offset in (30, 60, 90):
                target = None
                for later in rows[index:]:
                    if (later["moment"] - moment).total_seconds() / 86400.0 >= offset:
                        target = later
                        break
                snapshots[f"risky_weight_plus_{offset}d"] = (
                    float(target["risky_weight"]) if target is not None else None
                )
            troughs.append({
                "moment": moment.isoformat().replace("+00:00", "Z"),
                "drawdown_low": float(row["last_portfolio_low"]),
                "time_to_reentry_days": reentry_days,
                **snapshots,
            })
    inefficient = [
        row for row in rows
        if row["market_only_regime"] == "NORMAL" and row["state"] in {"BREACH", "EMERGENCY"}
    ]
    longest_inefficient = 0
    current_inefficient = 0
    for row in rows:
        if row["market_only_regime"] == "NORMAL" and row["state"] in {"BREACH", "EMERGENCY"}:
            current_inefficient += 1
            longest_inefficient = max(longest_inefficient, current_inefficient)
        else:
            current_inefficient = 0

    def _runs(values: list[int]) -> dict[str, Any]:
        return {
            "runs": len(values),
            "average_reviews": sum(values) / len(values) if values else None,
            "max_reviews": max(values) if values else None,
        }

    return {
        "reviews": len(rows),
        "state_transition_counts": dict(sorted(transitions.items())),
        "breach_duration": _runs(durations["BREACH"]),
        "recovery_1_duration": _runs(durations["RECOVERY_1"]),
        "recovery_2_duration": _runs(durations["RECOVERY_2"]),
        "trough_reentry": troughs,
        "recovery_efficiency": {
            "definition": "reviews where market-only regime is NORMAL while the "
                          "emergency state is still BREACH/EMERGENCY",
            "reviews": len(inefficient),
            "share": len(inefficient) / len(rows),
            "longest_consecutive_reviews": longest_inefficient,
        },
    }


def run_historical_backtest(
    reviews: Sequence[ReplayReview],
    *,
    policy: Policy | None = None,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    ordinary_review_weekday: int | None = None,
    risk_inputs_by_review: Sequence[PortfolioRiskInputs | None] | None = None,
) -> dict[str, Any]:
    """Run the shared decision engines with an exact quantity/cash ledger."""
    if not reviews:
        raise ValueError("at least one historical review is required")
    resolved = policy or resolve_policy()
    if ordinary_review_weekday is not None and ordinary_review_weekday not in range(7):
        raise ValueError("ordinary_review_weekday must be 0..6 or null")
    if risk_inputs_by_review is not None and len(risk_inputs_by_review) != len(reviews):
        raise ValueError("risk_inputs_by_review must align one-to-one with reviews")
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
    market_regime_counts: Counter[str] = Counter()
    effective_target_volatilities: list[float] = []
    constraint_violations: Counter[str] = Counter()
    plan_counts: Counter[str] = Counter()
    market_recovery_streak = 0
    # Consecutive reviews a strategic INCREASE waited on its entry plan
    # (Strategy V2 Phase 3): bounded WAIT lifetime, deterministic from the
    # replayed decision sequence alone.
    entry_wait_streaks: dict[str, int] = {}
    risky_weights: list[float] = []
    floor_pin = Counter()
    overlay_binding_reviews = 0
    risk_engine_volatilities: list[float] = []
    risk_engine_states: Counter[str] = Counter()
    risk_engine_bindings: Counter[str] = Counter()
    risk_engine_mode: str | None = None
    # Emergency-recovery FSM (Strategy V2.1 Phase B), volatility-budget mode
    # only: the state advances once per review from replayed observations and
    # the same block feeds allocation and the risk gate.
    recovery_state: RecoveryState | None = RecoveryState()
    previous_estimated_volatility: float | None = None
    emergency_rows: list[dict[str, Any]] = []

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
        # Market-anchored recovery streak: consecutive reviews whose ordinary
        # market domains alone read NORMAL. It feeds the drawdown-budget
        # overlay's re-risk floor; it never touches the regime label itself,
        # so the mandatory drawdown floors stay immediate in both directions.
        market_streak_inputs = RegimeInputs(**regime_values)
        market_label = market_only_regime(market_streak_inputs, policy=resolved)
        market_recovery_streak = (
            market_recovery_streak + 1
            if market_label == "NORMAL"
            else 0
        )
        # Volatility-budget mode removes the portfolio's own drawdown from the
        # regime label; drawdown risk acts only through the emergency overlay.
        risk_mode = (resolved.risk_engine or {}).get("mode", "legacy_drawdown")
        regime = determine_regime(
            RegimeInputs(**regime_values), policy=resolved, previous=previous_regime,
            include_portfolio_drawdown=risk_mode != "volatility_budget",
        )
        previous_regime = regime
        regime_counts[regime.regime] += 1
        market_regime_counts[market_label] += 1
        emergency_block: Mapping[str, Any] | None = None
        if risk_mode == "volatility_budget":
            # Emergency-recovery FSM (Strategy V2.1 Phase B): advance once per
            # review from replayed observations; the returned block feeds both
            # allocation and the risk gate, and the persisted state carries
            # over to the next review.
            systemic = regime_values.get("systemic_event_risk")
            recovery_state, emergency_block = advance_emergency_recovery(
                recovery_state,
                portfolio_drawdown=point.drawdown,
                market_only_regime=market_label,
                systemic_event_severe=(
                    systemic is True
                    or str(systemic).strip().upper() in {"SEVERE", "CRITICAL"}
                ),
                chain_liveness=review.chain_liveness,
                portfolio_volatility=previous_estimated_volatility,
                policy=resolved,
            )
        # Deterministic diagnostics: the exposure the strategy carried into
        # the period, whether the regime label sat on its own-drawdown floor,
        # and whether the drawdown budget overlay raised the stable floor
        # beyond the regime's own target.
        risky_weights.append(max(0.0, 1.0 - sum(
            point.weights.get(symbol, 0.0) for symbol in resolved.stable_symbols
        )))
        defensive_floor = -0.6 * resolved.max_portfolio_drawdown
        cp_floor = -0.8 * resolved.max_portfolio_drawdown
        label_level = {"NORMAL": 0, "DEFENSIVE": 1, "CAPITAL_PRESERVATION": 2}[regime.regime]
        floor_level = 0 if point.drawdown > defensive_floor else 1 if point.drawdown > cp_floor else 2
        if label_level >= 1:
            floor_pin["label_defensive_or_worse"] += 1
        if floor_level >= 1:
            floor_pin["drawdown_at_or_below_defensive_floor"] += 1
        if floor_level >= 2:
            floor_pin["drawdown_at_or_below_cp_floor"] += 1
        if label_level > floor_level:
            floor_pin["market_driven_defensive_reviews"] += 1
        overlay_floor, _, _ = risk_overlay_floor(
            resolved, point.drawdown, market_recovery_streak
        )
        if overlay_floor > max(
            resolved.min_stablecoin_weight,
            resolved.regime(regime.regime).stablecoin_target,
        ) + 1e-12:
            overlay_binding_reviews += 1
        allocation = build_target_allocation(
            policy=resolved, regime=regime.regime, assessments=assessments,
            current_weights=point.weights,
            portfolio_drawdown=point.drawdown,
            market_recovery_streak=market_recovery_streak,
            risk_inputs=(
                risk_inputs_by_review[index]
                if risk_inputs_by_review is not None and risk_inputs_by_review[index] is not None
                else None
            ),
            recovery_state=emergency_block,
        )
        risk = run_risk_gate(
            allocation, policy=resolved, regime=regime.regime, assessments=assessments,
            current_drawdown=point.drawdown, overlays=review.overlays,
            chain_liveness=review.chain_liveness, current_weights=point.weights,
            market_recovery_streak=market_recovery_streak,
            recovery_state=emergency_block,
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
            portfolio_drawdown=point.drawdown,
            market_recovery_streak=market_recovery_streak,
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
                wait_streak=entry_wait_streaks.get(action.symbol, 0),
            )
            effective_plans[action.symbol] = plan.as_dict()
            entry_wait_streaks[action.symbol] = (
                entry_wait_streaks.get(action.symbol, 0) + 1 if plan.action == "WAIT" else 0
            )
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
        engine_block = allocation.risk_engine or {}
        if engine_block:
            risk_engine_mode = str(engine_block.get("mode"))
            volatility = engine_block.get("portfolio_volatility")
            if isinstance(volatility, (int, float)) and volatility > 0:
                risk_engine_volatilities.append(float(volatility))
            effective_target = engine_block.get("effective_target_volatility")
            if isinstance(effective_target, (int, float)):
                effective_target_volatilities.append(float(effective_target))
            emergency = engine_block.get("emergency_overlay_state") or {}
            if isinstance(emergency, Mapping) and emergency.get("state") is not None:
                risk_engine_states[str(emergency["state"])] += 1
            binding = engine_block.get("binding_risk_constraint")
            if binding is not None:
                risk_engine_bindings[str(binding)] += 1
        if emergency_block is not None:
            sigma = engine_block.get("portfolio_volatility") if engine_block else None
            previous_estimated_volatility = (
                float(sigma) if isinstance(sigma, (int, float)) else None
            )
            emergency_rows.append({
                "moment": review.moment,
                "state": str(emergency_block["state"]),
                "transition": str(emergency_block.get("transition", "HOLD")),
                "risky_weight": risky_weights[-1],
                "market_only_regime": market_label,
                "last_portfolio_low": float(emergency_block["last_portfolio_low"]),
                "reviews_since_new_low": int(emergency_block["reviews_since_new_low"]),
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
        # Fair comparisons.  The four BTC benchmarks above can only answer "did
        # the strategy beat the riskiest available holding"; these answer "did
        # the active decisions add anything over the starting allocation" and
        # "was the risk that was taken worth it".  A risk-reducing strategy can
        # lose the first comparison by design, so judging it on that alone
        # misreads the strategy instead of testing it.
        "static_initial_weights_investable": buy_and_hold_benchmark(
            prices_by_time=aligned_prices, weights=dict(first.current_weights),
            initial_value_usd=first.portfolio_value, fee_bps=fee_bps, slippage_bps=slippage_bps,
        ),
    }
    vol_matched = _vol_matched_benchmark(
        aligned_prices, metrics=metrics, initial_value_usd=first.portfolio_value,
        fee_bps=fee_bps, slippage_bps=slippage_bps,
    )
    if vol_matched is not None:
        benchmarks["vol_matched_btc_cash_investable"] = vol_matched
    # Ablation benchmark B (Strategy V2 Phase 6): the same risk budget in a
    # static two-asset volatility-targeting mix, joining the comparison set.
    vol_matched_btc_eth = _vol_matched_btc_eth_benchmark(
        aligned_prices, metrics=metrics, initial_value_usd=first.portfolio_value,
        fee_bps=fee_bps, slippage_bps=slippage_bps,
    )
    if vol_matched_btc_eth is not None:
        benchmarks["vol_matched_btc_eth_70_30_cash_investable"] = vol_matched_btc_eth
    # Exposure-matched fair comparison: the same constant average exposure the
    # strategy realized, held passively in BTC/cash.  Together with the signed
    # timing contribution below it separates "what did the exposure path earn"
    # from "what did the average exposure alone earn".
    exposure_timing = exposure_timing_contribution(risky_weights, aligned_prices)
    average_risky = exposure_timing["realized_average_risky_weight"]
    if average_risky > 0:
        benchmarks["exposure_matched_btc_cash_investable"] = constant_weight_rebalanced_benchmark(
            prices_by_time=aligned_prices,
            weights={"BTC": average_risky, "USD": 1.0 - average_risky},
            initial_value_usd=first.portfolio_value, fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
    benchmark_comparison = {
        name: _benchmark_comparison(metrics, value["metrics"])
        for name, value in benchmarks.items()
    }
    # Strategy attribution (Strategy V2 Phase 5): holding the strategy's own
    # time-average weights constantly is the counterfactual that isolates the
    # value of active re-weighting (risk scaling + regime response) from the
    # asset selection embedded in those average weights. The signed exposure
    # timing contribution and the cash-yield sensitivity below complete the
    # decomposition; they are diagnostics, never engine inputs.
    average_weights: dict[str, float] = {}
    valuation_count = len(ledger.valuations)
    for item in ledger.valuations:
        for symbol, weight in item.weights.items():
            average_weights[symbol] = average_weights.get(symbol, 0.0) + weight
    average_weights = {
        symbol: weight / valuation_count
        for symbol, weight in average_weights.items()
        if weight / valuation_count > 1e-12
    }
    attribution: dict[str, Any] = {
        "methodology": (
            "risk_scaling_effect = strategy total return minus the total "
            "return of the same average weights held constantly (with costs); "
            "exposure timing and cash-yield sensitivity are separate blocks"
        ),
        "average_weights": dict(sorted(average_weights.items())),
    }
    if average_weights:
        average_hold = constant_weight_rebalanced_benchmark(
            prices_by_time=aligned_prices, weights=average_weights,
            initial_value_usd=first.portfolio_value,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
        )
        hold_metrics = average_hold["metrics"]
        attribution["average_weights_hold"] = {
            "total_return": hold_metrics["total_return"],
            "maximum_drawdown": hold_metrics["maximum_drawdown"],
            "annualized_volatility": hold_metrics["annualized_volatility"],
        }
        attribution["risk_scaling_effect"] = (
            metrics["total_return"] - hold_metrics["total_return"]
        )
    vol_matched_comparison = benchmark_comparison.get("vol_matched_btc_cash_investable") or {}
    attribution["vol_matched_excess_return_annualized"] = (
        vol_matched_comparison.get("excess_return_annualized")
    )
    return {
        "strategy_attribution": attribution,
        "engine": "quantity_cash_closed_loop", "metrics": metrics,
        "cadence": (
            "DAILY_WITH_14D_FULL" if ordinary_review_weekday is None
            else "WEEKLY_ORDINARY_WITH_DAILY_RISK_CHECKS"
        ),
        "total_turnover": sum(row["turnover"] for row in review_rows),
        "total_cost_usd": sum(row["cost_usd"] for row in review_rows),
        "average_cash_weight": sum(item.weights.get("USD", 0.0) for item in ledger.valuations) / len(ledger.valuations),
        "regime_counts": dict(regime_counts), "plan_status_counts": dict(plan_counts),
        "risk_authority_separation": {
            # In volatility-budget mode the regime label must never move with
            # the portfolio's own drawdown; the market-only distribution and
            # the YES/NO flag make the separation auditable per report.
            "drawdown_influenced_regime": "NO" if risk_engine_mode == "volatility_budget" else "YES",
            "market_only_regime_counts": dict(sorted(market_regime_counts.items())),
            "effective_target_volatility": {
                "reviews": len(effective_target_volatilities),
                "average": (
                    sum(effective_target_volatilities) / len(effective_target_volatilities)
                    if effective_target_volatilities else None
                ),
                "min": min(effective_target_volatilities) if effective_target_volatilities else None,
                "max": max(effective_target_volatilities) if effective_target_volatilities else None,
            },
        },
        "risk_engine_diagnostics": {
            "mode": risk_engine_mode,
            "average_estimated_portfolio_volatility": (
                sum(risk_engine_volatilities) / len(risk_engine_volatilities)
                if risk_engine_volatilities else None
            ),
            "max_estimated_portfolio_volatility": (
                max(risk_engine_volatilities) if risk_engine_volatilities else None
            ),
            "estimated_volatility_reviews": len(risk_engine_volatilities),
            "emergency_overlay_states": dict(risk_engine_states),
            "binding_constraint_counts": dict(risk_engine_bindings),
        },
        "emergency_recovery_diagnostics": _emergency_recovery_diagnostics(
            emergency_rows, resolved
        ),
        "constraint_violation_counts": dict(constraint_violations),
        "regime_floor_diagnostics": {
            "reviews": len(review_rows),
            "label_defensive_or_worse_share": floor_pin["label_defensive_or_worse"] / len(review_rows),
            "drawdown_at_or_below_defensive_floor_share": (
                floor_pin["drawdown_at_or_below_defensive_floor"] / len(review_rows)
            ),
            "drawdown_at_or_below_cp_floor_share": (
                floor_pin["drawdown_at_or_below_cp_floor"] / len(review_rows)
            ),
            "market_driven_defensive_reviews": floor_pin["market_driven_defensive_reviews"],
            "overlay_binding_share": overlay_binding_reviews / len(review_rows),
        },
        "exposure_timing": exposure_timing,
        "cash_yield_sensitivity": _cash_yield_sensitivity(
            ledger.valuations, stable_symbols=tuple(resolved.stable_symbols),
        ),
        "benchmarks": benchmarks, "benchmark_comparison": benchmark_comparison, "reviews": review_rows,
        "trades": [item.as_dict() for item in ledger.trades],
        "valuations": [item.as_dict() for item in ledger.valuations],
    }


__all__ = ["run_historical_backtest"]
