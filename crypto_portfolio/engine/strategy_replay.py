"""Deterministic full-pipeline replay harness over frozen review records.

Research tooling (phase 10 of the structural refactor plan): it replays the
complete strategy mapping

    frozen evidence -> regime -> strategic target -> staged action
        -> costs -> realized next-period returns

over frozen per-review records without lookahead.  The next-period realized
returns are labels attached to each record; the decision path only ever sees
a :class:`FrozenReviewView`, which structurally cannot carry them.  This
module never touches live providers and never optimizes policy parameters.

Simulation is closed-loop: review ``t+1`` starts from the portfolio the
replayed actions and realized returns actually produced, not from the
historical record's weights (only the first review is seeded from it).  The
benchmark paths (BTC buy-and-hold, 70/30 BTC/ETH) consume the same labels
over the same periods, so excess return comparisons share boundaries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..models.policy import Policy, resolve_policy
from .allocation import build_target_allocation
from .evaluation import buy_and_hold_path
from .regime import RegimeInputs, determine_regime, market_only_regime
from .rebalance import recommend_rebalance


def _normalized_weights(value: Mapping[str, float], name: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError(f"{name} contains an empty symbol")
        if symbol in result:
            raise ValueError(f"{name} contains duplicate symbol {symbol}")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise ValueError(f"{name}.{symbol} must be a number")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"{name}.{symbol} must be a finite fraction >= 0")
        result[symbol] = weight
    if result and sum(result.values()) > 1.0 + 1e-9:
        raise ValueError(f"{name} must sum to no more than 1")
    return result


def _normalized_returns(value: Mapping[str, float], name: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_symbol, raw_return in value.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError(f"{name} contains an empty symbol")
        if symbol in result:
            raise ValueError(f"{name} contains duplicate symbol {symbol}")
        if isinstance(raw_return, bool) or not isinstance(raw_return, (int, float)) or not math.isfinite(float(raw_return)):
            raise ValueError(f"{name}.{symbol} must be a finite decimal fraction")
        if float(raw_return) < -1.0:
            raise ValueError(f"{name}.{symbol} cannot be below -100%")
        result[symbol] = float(raw_return)
    return result


def _parse_as_of(value: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("as_of must be a timezone-aware RFC3339 string")
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("as_of must be a timezone-aware RFC3339 string") from exc
    if moment.tzinfo is None:
        raise ValueError("as_of must be timezone-aware; naive timestamps are rejected")
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class FrozenReviewView:
    """Everything the decision path may see for one replayed review.

    Structurally excludes the realized-return label: constructing this view
    is the only way replay code reaches the deterministic pipeline, so
    future returns cannot leak into score/regime/target/action decisions.
    """

    as_of: str
    current_weights: Mapping[str, float]
    portfolio_value: float
    assessments: Mapping[str, Any]
    regime_inputs: Mapping[str, Any]
    new_cash: float
    thesis_broken: tuple[str, ...]
    hard_action_reasons: Mapping[str, str] | None
    technical_inputs: Mapping[str, Any] = field(default_factory=dict)
    overlays: Mapping[str, Any] | None = None
    chain_liveness: Mapping[str, Any] | None = None
    eth_alpha_state: str | None = None
    satellite_alpha_states: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ReplayReview:
    """One frozen review record plus its next-period realized-return label.

    The label (``next_returns``) is evaluation ground truth only.  The
    decision view is exposed via :meth:`decision_view` so callers and tests
    can prove the pipeline never receives it.
    """

    as_of: str
    period_end: str
    current_weights: Mapping[str, float]
    portfolio_value: float
    assessments: Mapping[str, Any] = field(default_factory=dict)
    regime_inputs: Mapping[str, Any] = field(default_factory=dict)
    new_cash: float = 0.0
    thesis_broken: tuple[str, ...] = ()
    hard_action_reasons: Mapping[str, str] | None = None
    next_returns: Mapping[str, float] = field(default_factory=dict)
    technical_inputs: Mapping[str, Any] = field(default_factory=dict)
    execution_plans: Mapping[str, Any] = field(default_factory=dict)
    execution_bars: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    overlays: Mapping[str, Any] | None = None
    chain_liveness: Mapping[str, Any] | None = None
    current_prices: Mapping[str, float] = field(default_factory=dict)
    eth_alpha_state: str | None = None
    satellite_alpha_states: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        moment = _parse_as_of(self.as_of)
        period_end = _parse_as_of(self.period_end)
        if period_end <= moment:
            raise ValueError("period_end must be after as_of")
        weights = _normalized_weights(self.current_weights, "current_weights")
        if isinstance(self.portfolio_value, bool) or not isinstance(self.portfolio_value, (int, float)) \
                or not math.isfinite(float(self.portfolio_value)) or float(self.portfolio_value) <= 0:
            raise ValueError("portfolio_value must be a finite positive number")
        if isinstance(self.new_cash, bool) or not isinstance(self.new_cash, (int, float)) \
                or not math.isfinite(float(self.new_cash)) or float(self.new_cash) < 0:
            raise ValueError("new_cash must be a finite number >= 0")
        if not isinstance(self.assessments, Mapping):
            raise ValueError("assessments must be an object")
        if not isinstance(self.regime_inputs, Mapping):
            raise ValueError("regime_inputs must be an object")
        if not isinstance(self.execution_plans, Mapping):
            raise ValueError("execution_plans must be an object")
        normalized_plans = {}
        for raw_symbol, raw_plan in self.execution_plans.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or not isinstance(raw_plan, Mapping):
                raise ValueError("execution_plans must map symbols to objects")
            action = str(raw_plan.get("action", "")).strip().upper()
            if action not in {"INCREASE", "WAIT"}:
                raise ValueError("replay execution plan action must be INCREASE or WAIT")
            planned = raw_plan.get("planned_amount_usd", 0.0)
            approved = raw_plan.get("approved_amount_usd", planned)
            for name, value in (("planned_amount_usd", planned), ("approved_amount_usd", approved)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
                    raise ValueError(f"execution_plans.{symbol}.{name} must be finite and >= 0")
            normalized_plans[symbol] = dict(raw_plan)
        object.__setattr__(self, "execution_plans", normalized_plans)
        if not isinstance(self.execution_bars, Mapping):
            raise ValueError("execution_bars must be an object")
        normalized_bars = {}
        for raw_symbol, raw_bars in self.execution_bars.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or isinstance(raw_bars, (str, bytes)) or not isinstance(raw_bars, (list, tuple)):
                raise ValueError("execution_bars must map symbols to bar sequences")
            normalized_bars[symbol] = tuple(dict(item) for item in raw_bars)
        object.__setattr__(self, "execution_bars", normalized_bars)
        if self.overlays is not None and not isinstance(self.overlays, Mapping):
            raise ValueError("overlays must be an object or null")
        if self.chain_liveness is not None and not isinstance(self.chain_liveness, Mapping):
            raise ValueError("chain_liveness must be an object or null")
        normalized_prices: dict[str, float] = {}
        for raw_symbol, raw_price in self.current_prices.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or symbol in normalized_prices:
                raise ValueError("current_prices contains an empty or duplicate symbol")
            price = float(raw_price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("current_prices values must be finite and positive")
            normalized_prices[symbol] = price
        object.__setattr__(self, "current_prices", normalized_prices)
        if self.eth_alpha_state is not None:
            from .eth_relative_alpha import (
                ETH_ALPHA_NEGATIVE,
                ETH_ALPHA_NEUTRAL,
                ETH_ALPHA_POSITIVE,
            )
            state = str(self.eth_alpha_state).strip().upper()
            if state not in {ETH_ALPHA_POSITIVE, ETH_ALPHA_NEUTRAL, ETH_ALPHA_NEGATIVE}:
                raise ValueError("eth_alpha_state is unsupported")
            object.__setattr__(self, "eth_alpha_state", state)
        if self.satellite_alpha_states is not None:
            from .relative_alpha_core import asset_alpha_state_names
            normalized_states: dict[str, str] = {}
            for raw_symbol, raw_state in self.satellite_alpha_states.items():
                symbol = str(raw_symbol).strip().upper()
                if not symbol or symbol in normalized_states:
                    raise ValueError("satellite_alpha_states must use unique non-empty symbols")
                positive, neutral, negative = asset_alpha_state_names(symbol)
                state_text = str(raw_state).strip().upper()
                if state_text not in {positive, neutral, negative}:
                    raise ValueError(
                        f"satellite_alpha_states.{symbol} must be {positive}, "
                        f"{neutral}, or {negative}"
                    )
                normalized_states[symbol] = state_text
            object.__setattr__(self, "satellite_alpha_states", normalized_states)
        if isinstance(self.thesis_broken, str):
            raise ValueError("thesis_broken must be a sequence of symbols")
        broken = tuple(str(item).strip().upper() for item in self.thesis_broken if str(item).strip())
        if self.hard_action_reasons is not None and not isinstance(self.hard_action_reasons, Mapping):
            raise ValueError("hard_action_reasons must be an object or null")
        returns = _normalized_returns(self.next_returns, "next_returns")
        object.__setattr__(self, "current_weights", weights)
        object.__setattr__(self, "thesis_broken", broken)
        object.__setattr__(self, "next_returns", returns)

    @property
    def moment(self) -> datetime:
        return _parse_as_of(self.as_of)

    @property
    def end_moment(self) -> datetime:
        return _parse_as_of(self.period_end)

    def decision_view(self, *, current_weights: Mapping[str, float] | None = None,
                      portfolio_value: float | None = None) -> FrozenReviewView:
        """Frozen view for the decision path; never includes next_returns."""
        return FrozenReviewView(
            as_of=self.as_of,
            current_weights=dict(self.current_weights if current_weights is None else current_weights),
            portfolio_value=float(self.portfolio_value if portfolio_value is None else portfolio_value),
            technical_inputs=dict(self.technical_inputs),
            assessments=dict(self.assessments),
            regime_inputs=dict(self.regime_inputs),
            new_cash=float(self.new_cash),
            thesis_broken=tuple(self.thesis_broken),
            hard_action_reasons=dict(self.hard_action_reasons) if self.hard_action_reasons else None,
            overlays=dict(self.overlays) if self.overlays is not None else None,
            chain_liveness=dict(self.chain_liveness) if self.chain_liveness is not None else None,
            eth_alpha_state=self.eth_alpha_state,
            satellite_alpha_states=(
                dict(self.satellite_alpha_states) if self.satellite_alpha_states is not None else None
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplayReview":
        known = {
            "as_of", "period_end", "current_weights", "portfolio_value", "assessments",
            "regime_inputs", "new_cash", "thesis_broken", "hard_action_reasons",
            "next_returns", "technical_inputs",
            "execution_plans",
            "execution_bars",
            "overlays", "chain_liveness",
            "current_prices", "eth_alpha_state", "satellite_alpha_states",
        }
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError("replay review contains unknown fields: " + ", ".join(unknown))
        for required in ("as_of", "period_end", "current_weights", "portfolio_value"):
            if required not in value:
                raise ValueError(f"replay review is missing {required}")
        return cls(
            as_of=value["as_of"],
            period_end=value["period_end"],
            current_weights=value["current_weights"],
            portfolio_value=value["portfolio_value"],
            assessments=value.get("assessments", {}),
            regime_inputs=value.get("regime_inputs", {}),
            new_cash=value.get("new_cash", 0.0),
            thesis_broken=tuple(value.get("thesis_broken", ())),
            hard_action_reasons=value.get("hard_action_reasons"),
            next_returns=value.get("next_returns", {}),
            technical_inputs=value.get("technical_inputs", {}),
            execution_plans=value.get("execution_plans", {}),
            execution_bars=value.get("execution_bars", {}),
            overlays=value.get("overlays"),
            chain_liveness=value.get("chain_liveness"),
            current_prices=value.get("current_prices", {}),
            eth_alpha_state=value.get("eth_alpha_state"),
            satellite_alpha_states=value.get("satellite_alpha_states"),
        )


def _max_drawdown(navs: Sequence[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for nav in navs:
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1.0)
    return worst


def research_readiness(reviews: Sequence[ReplayReview], *, regimes: Sequence[str]) -> dict[str, Any]:
    """State whether the frozen sample can support a strategy policy choice."""

    days = {review.moment.date() for review in reviews}
    # Frozen regime inputs carry per-domain states, never the determined
    # regime, so diversity is judged on the labels the replay itself produced.
    observed = {str(item).strip().upper() for item in regimes if str(item).strip()}
    planned = sum(bool(review.execution_plans) for review in reviews)
    fill_capable = sum(bool(review.execution_bars) for review in reviews)
    reasons = []
    if len(days) < 90:
        reasons.append("FEWER_THAN_90_DISTINCT_REVIEW_DAYS")
    if len(observed - {"UNKNOWN"}) < 2:
        reasons.append("INSUFFICIENT_REGIME_DIVERSITY")
    if planned < 30:
        reasons.append("FEWER_THAN_30_FROZEN_EXECUTION_PLANS")
    if fill_capable < 30:
        reasons.append("FEWER_THAN_30_FILL_CAPABLE_REVIEWS")
    return {
        "status": "INSUFFICIENT_EVIDENCE" if reasons else "READY_FOR_PREREGISTERED_COMPARISON",
        "distinct_review_days": len(days),
        "observed_regime_labels": sorted(observed),
        "reviews_with_execution_plans": planned,
        "reviews_with_execution_bars": fill_capable,
        "reasons": reasons,
        "decision_effect": "NO_POLICY_CHANGE" if reasons else "RESEARCH_ONLY",
    }


def replay_strategy(
    reviews: Sequence[ReplayReview],
    *,
    policy: Policy | None = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    research_variant: str = "baseline",
) -> dict[str, Any]:
    """Replay the full deterministic pipeline over frozen review records.

    Closed-loop ordering per review ``t``: consume the frozen view, classify
    the regime (with the previous replayed regime for the transition cap),
    build strategic targets, recommend staged actions, charge costs on the
    traded notional, apply the record's realized returns over ``(t, t+1]``,
    and carry the resulting dollars into the next review.  The last review's
    label is applied for measurement symmetry but no decision follows it.
    """
    resolved = policy or resolve_policy()
    for name, value in (("fee_bps", fee_bps), ("slippage_bps", slippage_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
    if not reviews:
        raise ValueError("at least one replay review is required")
    moments = [review.moment for review in reviews]
    if moments != sorted(moments) or len(set(moments)) != len(moments):
        raise ValueError("replay reviews must be unique and ordered by as_of")
    if any(review.end_moment > following.moment for review, following in zip(reviews, reviews[1:])):
        raise ValueError("replay label periods must not overlap the next decision boundary")

    from .research_variants import ResearchSignals
    from .review_diagnostics import build_review_diagnostics
    signals = ResearchSignals(research_variant, resolved)
    stables = set(resolved.stable_symbols)
    previous_regime: Any = None
    market_recovery_streak = 0
    # Bounded WAIT lifetime tracking (Strategy V2 Phase 3).
    entry_wait_streaks: dict[str, int] = {}
    # Dollar positions, seeded from the first frozen record.
    dollars: dict[str, float] = {
        symbol: weight * reviews[0].portfolio_value
        for symbol, weight in reviews[0].current_weights.items()
    }
    value = reviews[0].portfolio_value
    nav_path: list[float] = [1.0]
    period_returns: list[float] = []
    turnover_total = 0.0
    cost_total = 0.0
    regime_counts: dict[str, int] = {}
    rebalances = 0
    staged_increases = 0
    staged_reductions = 0
    stable_weight_sum = 0.0
    review_rows: list[dict[str, Any]] = []
    replayed_decisions: list[dict[str, Any]] = []

    for index, review in enumerate(reviews):
        if index > 0:
            view = review.decision_view(
                current_weights={
                    symbol: dollars.get(symbol, 0.0) / value for symbol in dollars
                },
                portfolio_value=value,
            )
        else:
            view = review.decision_view()
        # The replayed portfolio owns its drawdown path.  Frozen external
        # drawdown values may describe another account or another candidate
        # policy and therefore cannot drive the simulated risk floor.
        peak_nav = max(nav_path)
        replay_drawdown = nav_path[-1] / peak_nav - 1.0
        regime_values = dict(view.regime_inputs)
        regime_values["portfolio_drawdown_band"] = replay_drawdown
        # Market-anchored recovery streak feeding the drawdown-budget
        # overlay's re-risk floor; the regime label itself is untouched.
        market_recovery_streak = (
            market_recovery_streak + 1
            if market_only_regime(RegimeInputs(**regime_values), policy=resolved) == "NORMAL"
            else 0
        )
        # Volatility-budget mode: the portfolio's own drawdown never shapes
        # the regime label; it acts only through the emergency overlay.
        regime = determine_regime(
            RegimeInputs(**regime_values), policy=resolved, previous=previous_regime,
            include_portfolio_drawdown=(
                (resolved.risk_engine or {}).get("mode", "legacy_drawdown") != "volatility_budget"
            ),
        )
        previous_regime = regime
        regime_counts[regime.regime] = regime_counts.get(regime.regime, 0) + 1
        allocation = build_target_allocation(
            policy=resolved,
            regime=regime.regime,
            assessments=signals.assessments(view),
            current_weights=view.current_weights,
            portfolio_drawdown=replay_drawdown,
            market_recovery_streak=market_recovery_streak,
            eth_alpha_state=view.eth_alpha_state,
            satellite_alpha_states=view.satellite_alpha_states,
        )
        from .risk import run_risk_gate
        risk = run_risk_gate(
            allocation, policy=resolved, regime=regime.regime,
            assessments=signals.assessments(view), current_drawdown=replay_drawdown,
            overlays=view.overlays, chain_liveness=view.chain_liveness,
            market_recovery_streak=market_recovery_streak,
            current_weights=view.current_weights,
        )
        if not risk.ok:
            raise ValueError("REPLAY_RISK_GATE_FAILED: " + "; ".join(item.code for item in risk.errors))
        from .rebalance import direction_history_from_decisions
        rebalance = recommend_rebalance(
            view.current_weights,
            dict(allocation.target_weights),
            view.portfolio_value,
            new_cash_available=view.new_cash,
            thesis_broken=view.thesis_broken or None,
            policy=resolved,
            regime=regime.regime,
            deployment_caps=dict(allocation.deployment_factors),
            hard_action_reasons=dict(view.hard_action_reasons) if view.hard_action_reasons else None,
            hard_exposure_caps={
                symbol: float(value["hard_exposure_cap"])
                for symbol, value in allocation.deployment_allowances.items()
                if value.get("hard_exposure_cap") is not None
            },
            direction_history=direction_history_from_decisions(replayed_decisions),
            portfolio_drawdown=replay_drawdown,
            market_recovery_streak=market_recovery_streak,
        )
        executable = [a for a in signals.confirmed(view, rebalance.actions)
                      if a.action in {"INCREASE", "REDUCE", "EXIT"} and a.symbol not in stables]
        entry_waits: list[dict[str, Any]] = []
        entry_outcomes: list[dict[str, Any]] = []
        effective_plans = dict(review.execution_plans)
        if view.technical_inputs:
            from .entry import build_entry_plan
            from ..models.market import TechnicalSnapshot
            assessment_values = signals.assessments(view)
            for action in executable:
                if action.action != "INCREASE" or action.symbol in effective_plans:
                    continue
                candidates = view.technical_inputs.get(action.symbol, ())
                if not candidates:
                    continue
                raw_snapshot = list(candidates)[-1]
                snapshot = raw_snapshot if isinstance(raw_snapshot, TechnicalSnapshot) else TechnicalSnapshot.from_mapping(raw_snapshot)
                assessment = assessment_values.get(action.symbol, {})
                confidence = getattr(assessment, "confidence", None) or (
                    assessment.get("confidence", "LOW") if isinstance(assessment, Mapping) else "LOW"
                )
                effective_plans[action.symbol] = build_entry_plan(
                    action.symbol, action.amount_usd, snapshot, regime.regime, confidence,
                    policy=resolved,
                    wait_streak=entry_wait_streaks.get(action.symbol, 0),
                ).as_dict()
                plan_action = str(effective_plans[action.symbol].get("action", "")).strip().upper()
                entry_wait_streaks[action.symbol] = (
                    entry_wait_streaks.get(action.symbol, 0) + 1
                    if plan_action == "WAIT"
                    else 0
                )
        if effective_plans:
            from dataclasses import replace as replace_action
            from .execution_replay import simulate_execution_plan
            from ..models.policy import policy_hash

            gated: list[Any] = []
            for action in executable:
                raw_plan = effective_plans.get(action.symbol)
                if raw_plan is None or action.action != "INCREASE":
                    gated.append(action)
                    continue
                plan_action = str(raw_plan.get("action", "")).strip().upper()
                planned = float(raw_plan.get("planned_amount_usd", 0.0) or 0.0)
                planning_context = raw_plan.get("planning_context")
                if planning_context and planning_context.get("policy_hash") != policy_hash(resolved):
                    raise ValueError("replay execution plan policy does not match the candidate policy")
                if plan_action == "WAIT" or planned <= 0:
                    entry_waits.append({
                        "symbol": action.symbol,
                        "approved_amount_usd": action.amount_usd,
                        "planned_amount_usd": planned,
                        "reason": raw_plan.get("rationale", "entry plan returned WAIT"),
                    })
                    continue
                bars = review.execution_bars.get(action.symbol)
                if bars is None:
                    entry_waits.append({
                        "symbol": action.symbol,
                        "approved_amount_usd": action.amount_usd,
                        "planned_amount_usd": planned,
                        "reason": "EXECUTION_BARS_REQUIRED_FOR_FILL_SIMULATION",
                    })
                    continue
                outcome = simulate_execution_plan(raw_plan, bars, decision_as_of=review.as_of)
                entry_outcomes.append({"symbol": action.symbol, **outcome})
                filled = min(action.amount_usd, outcome["filled_amount_usd"])
                if filled <= 1e-9:
                    entry_waits.append({
                        "symbol": action.symbol,
                        "approved_amount_usd": action.amount_usd,
                        "planned_amount_usd": planned,
                        "reason": outcome["status"],
                    })
                    continue
                gated.append(replace_action(action, amount_usd=filled))
            executable = gated
        candidate_projection = None
        if research_variant == "confirm_2" or effective_plans:
            def projection(selected):
                return build_review_diagnostics(current_weights=view.current_weights,
                    target_weights=allocation.target_weights, portfolio_value=view.portfolio_value,
                    new_cash=view.new_cash, actions=selected, regime=regime.regime,
                    policy=resolved)["scenarios"]["APPROVED_FULL"]
            candidate_projection = projection(executable)
            if not candidate_projection["feasible"]:
                # A postponed sale cannot fund a purchase. Preserve hard risk
                # reductions; postpone whole ordinary buys rather than resize.
                executable = [a for a in executable if a.action != "INCREASE"]
                candidate_projection = projection(executable)
            if candidate_projection["weights"] is None:
                raise ValueError("RESEARCH_PROJECTION_INFEASIBLE")
        if executable:
            rebalances += 1
        staged_increases += sum(1 for a in executable if a.staging_applied and a.action == "INCREASE")
        staged_reductions += sum(1 for a in executable if a.staging_applied and a.action in {"REDUCE", "EXIT"})

        traded = sum(a.amount_usd for a in executable)
        turnover = traded / view.portfolio_value if view.portfolio_value > 0 else 0.0
        cost = traded * (float(fee_bps) + float(slippage_bps)) / 10000.0
        turnover_total += turnover
        cost_total += cost

        # Post-action dollars come from the rebalance projection, which
        # already folds funding constraints and undeployed cash into the
        # stable sleeve and conserves the total.
        projected = rebalance.post_action_projection or {}
        post_weights = dict(candidate_projection["weights"] if candidate_projection else projected.get("projected_weights", {}))
        if not post_weights:
            raise ValueError(f"replay review {review.as_of} produced no post-action projection")
        before_cost = view.portfolio_value + view.new_cash
        post_dollars = {symbol: weight * before_cost for symbol, weight in post_weights.items()}
        stable_dollars = sum(v for s, v in post_dollars.items() if s in stables)
        if cost > stable_dollars + 1e-9:
            raise ValueError("replay costs exceed available stable balance")
        for symbol in post_dollars:
            if symbol in stables and stable_dollars:
                post_dollars[symbol] *= (stable_dollars - cost) / stable_dollars
        post_total = before_cost - cost
        if post_total <= 0:
            raise ValueError("replay costs exhaust portfolio")
        post_weights = {s: v / post_total for s, v in post_dollars.items()}
        stable_weight = sum(weight for symbol, weight in post_weights.items() if symbol in stables)
        stable_weight_sum += stable_weight
        label = review.next_returns
        missing = sorted(symbol for symbol, weight in post_weights.items()
                         if weight > 1e-12 and symbol not in label)
        if missing:
            raise ValueError(f"replay review {review.as_of} is missing realized returns for "
                             "post-action exposure(s): " + ", ".join(missing))
        dollars = {symbol: amount * (1.0 + label.get(symbol, 0.0))
                   for symbol, amount in post_dollars.items()}
        period_return = sum(dollars.values()) / before_cost - 1.0
        period_returns.append(period_return)
        nav_path.append(nav_path[-1] * (1.0 + period_return))
        value = sum(dollars.values())
        replayed_decisions.append({
            "timestamp": review.as_of,
            "actions": [action.as_dict() for action in rebalance.actions],
        })
        from .operation import build_final_operation
        try:
            operation: Any = build_final_operation(executable, effective_plans, stable_symbols=stables).as_dict()
        except ValueError as exc:
            # Frozen configuration-level fixtures may predate technical plans.
            # Keep that limitation explicit instead of inventing an executable
            # operation or preventing return/risk diagnostics.
            operation = {"status": "UNAVAILABLE", "reason": str(exc)}
        review_rows.append({
            "as_of": review.as_of,
            "period_end": review.period_end,
            "portfolio_drawdown_input": replay_drawdown,
            "regime": regime.regime,
            "decision": rebalance.decision,
            "executable_actions": len(executable),
            "entry_plan_waits": entry_waits,
            "entry_outcomes": entry_outcomes,
            "risk_gate": risk.as_dict(),
            "final_operation": operation,
            "execution_plans_used": bool(effective_plans),
            "staged_actions": sum(1 for a in executable if a.staging_applied),
            "actions": [a.as_dict() for a in executable],
            "turnover": turnover,
            "cost": cost,
            "period_return": period_return,
            "nav": nav_path[-1],
            "stable_weight": stable_weight,
        })

    final_nav = nav_path[-1]
    count = len(reviews)
    total_return = final_nav - 1.0
    elapsed_days = max((reviews[-1].end_moment - moments[0]).total_seconds() / 86400.0, 1.0)
    period_days = [(review.end_moment - review.moment).total_seconds() / 86400.0 for review in reviews]
    periods_per_year = 365.25 / max(sum(period_days) / count, 1e-9)
    average_return = sum(period_returns) / count
    variance = sum((item - average_return) ** 2 for item in period_returns) / max(count - 1, 1)
    volatility = math.sqrt(variance) * math.sqrt(periods_per_year)
    annualized = (final_nav ** (365.25 / elapsed_days)) - 1.0 if final_nav > 0 else -1.0
    sharpe_like = (annualized / volatility) if volatility > 0 else None
    return {
        "research_variant": research_variant,
        "research_readiness": research_readiness(
            reviews, regimes=[row["regime"] for row in review_rows]
        ),
        "reviews": count,
        "final_nav": final_nav,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": _max_drawdown(nav_path),
        "annualized_volatility": volatility,
        "sharpe_like_rf_zero": sharpe_like,
        "assumptions": {
            "risk_free_rate": 0.0,
            "annualization": "365.25-day year over explicit as_of-to-period_end label intervals",
            "entry_execution": (
                "future bars confirm at most one tranche per bar; wick-only touches do not fill; "
                "explicit fill_fraction controls partial liquidity"
                if any(review.execution_bars for review in reviews)
                else "frozen WAIT plans are honored; non-WAIT plans require future execution bars"
                if any(review.execution_plans for review in reviews)
                else "configuration-level approved actions; no frozen execution plans supplied"
            ),
        },
        "total_turnover": turnover_total,
        "total_cost": cost_total,
        "average_stable_weight": stable_weight_sum / count,
        "regime_counts": dict(sorted(regime_counts.items())),
        "rebalances": rebalances,
        "staged_increases": staged_increases,
        "staged_reductions": staged_reductions,
        "review_detail": review_rows,
    }


def replay_benchmarks(
    reviews: Sequence[ReplayReview],
    *,
    weights: Mapping[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    """BTC and 70/30 BTC/ETH buy-and-hold paths over the same labels."""
    if not reviews:
        raise ValueError("at least one replay review is required")
    labels = [dict(review.next_returns) for review in reviews]
    primary = {"BTC": 1.0}
    secondary = weights or {"BTC": 0.7, "ETH": 0.3}
    return {
        "btc_buy_and_hold": buy_and_hold_path(labels, weights=primary),
        "btc_eth_buy_and_hold": buy_and_hold_path(labels, weights=secondary),
    }


def compare_policies(
    reviews: Sequence[ReplayReview],
    *,
    baseline_policy: Policy | None = None,
    candidate_policy: Policy | None = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    research_variant: str = "baseline",
) -> dict[str, Any]:
    """Replay two policies over identical frozen periods and benchmarks.

    Purely deterministic comparison; calibration decisions remain human.
    The candidate is never preferred merely for holdout outperformance -
    that judgement is explicitly left out of the code.
    """
    baseline = replay_strategy(
        reviews, policy=baseline_policy, fee_bps=fee_bps, slippage_bps=slippage_bps,
        research_variant=research_variant
    )
    candidate = replay_strategy(
        reviews, policy=candidate_policy, fee_bps=fee_bps, slippage_bps=slippage_bps,
        research_variant=research_variant
    )
    benchmarks = replay_benchmarks(reviews)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "benchmarks": benchmarks,
        "excess_return": {
            "baseline_vs_btc": baseline["total_return"] - benchmarks["btc_buy_and_hold"]["total_return"],
            "candidate_vs_btc": candidate["total_return"] - benchmarks["btc_buy_and_hold"]["total_return"],
            "candidate_vs_baseline": candidate["total_return"] - baseline["total_return"],
        },
    }


def load_replay_reviews(value: Any) -> tuple[ReplayReview, ...]:
    """Parse a sequence of frozen review records (mappings or instances)."""
    if isinstance(value, Mapping) and "reviews" in value:
        value = value["reviews"]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("replay reviews must be a sequence")
    return tuple(
        item if isinstance(item, ReplayReview) else ReplayReview.from_mapping(item)
        for item in value
    )


__all__ = [
    "FrozenReviewView",
    "ReplayReview",
    "compare_policies",
    "load_replay_reviews",
    "replay_benchmarks",
    "replay_strategy",
    "research_readiness",
]
