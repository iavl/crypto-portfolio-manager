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
from .regime import RegimeInputs, determine_regime
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


@dataclass(frozen=True)
class ReplayReview:
    """One frozen review record plus its next-period realized-return label.

    The label (``next_returns``) is evaluation ground truth only.  The
    decision view is exposed via :meth:`decision_view` so callers and tests
    can prove the pipeline never receives it.
    """

    as_of: str
    current_weights: Mapping[str, float]
    portfolio_value: float
    assessments: Mapping[str, Any] = field(default_factory=dict)
    regime_inputs: Mapping[str, Any] = field(default_factory=dict)
    new_cash: float = 0.0
    thesis_broken: tuple[str, ...] = ()
    hard_action_reasons: Mapping[str, str] | None = None
    next_returns: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _parse_as_of(self.as_of)
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

    def decision_view(self, *, current_weights: Mapping[str, float] | None = None,
                      portfolio_value: float | None = None) -> FrozenReviewView:
        """Frozen view for the decision path; never includes next_returns."""
        return FrozenReviewView(
            as_of=self.as_of,
            current_weights=dict(self.current_weights if current_weights is None else current_weights),
            portfolio_value=float(self.portfolio_value if portfolio_value is None else portfolio_value),
            assessments=dict(self.assessments),
            regime_inputs=dict(self.regime_inputs),
            new_cash=float(self.new_cash),
            thesis_broken=tuple(self.thesis_broken),
            hard_action_reasons=dict(self.hard_action_reasons) if self.hard_action_reasons else None,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplayReview":
        known = {
            "as_of", "current_weights", "portfolio_value", "assessments",
            "regime_inputs", "new_cash", "thesis_broken", "hard_action_reasons",
            "next_returns",
        }
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError("replay review contains unknown fields: " + ", ".join(unknown))
        for required in ("as_of", "current_weights", "portfolio_value"):
            if required not in value:
                raise ValueError(f"replay review is missing {required}")
        return cls(
            as_of=value["as_of"],
            current_weights=value["current_weights"],
            portfolio_value=value["portfolio_value"],
            assessments=value.get("assessments", {}),
            regime_inputs=value.get("regime_inputs", {}),
            new_cash=value.get("new_cash", 0.0),
            thesis_broken=tuple(value.get("thesis_broken", ())),
            hard_action_reasons=value.get("hard_action_reasons"),
            next_returns=value.get("next_returns", {}),
        )


def _max_drawdown(navs: Sequence[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for nav in navs:
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1.0)
    return worst


def replay_strategy(
    reviews: Sequence[ReplayReview],
    *,
    policy: Policy | None = None,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
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

    stables = set(resolved.stable_symbols)
    previous_regime: Any = None
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
        regime = determine_regime(
            RegimeInputs(**view.regime_inputs), policy=resolved, previous=previous_regime
        )
        previous_regime = regime
        regime_counts[regime.regime] = regime_counts.get(regime.regime, 0) + 1
        allocation = build_target_allocation(
            policy=resolved,
            regime=regime.regime,
            assessments=view.assessments,
            current_weights=view.current_weights,
        )
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
        )
        executable = [a for a in rebalance.actions if a.action in {"INCREASE", "REDUCE", "EXIT"}]
        if executable:
            rebalances += 1
        staged_increases += sum(1 for a in rebalance.actions if a.staging_applied and a.action == "INCREASE")
        staged_reductions += sum(1 for a in rebalance.actions if a.staging_applied and a.action in {"REDUCE", "EXIT"})

        traded = sum(a.amount_usd for a in executable)
        turnover = traded / view.portfolio_value if view.portfolio_value > 0 else 0.0
        cost = traded * (float(fee_bps) + float(slippage_bps)) / 10000.0
        turnover_total += turnover
        cost_total += cost

        # Post-action dollars come from the rebalance projection, which
        # already folds funding constraints and undeployed cash into the
        # stable sleeve and conserves the total.
        projected = rebalance.post_action_projection or {}
        post_weights = dict(projected.get("projected_weights", {}))
        if not post_weights:
            raise ValueError(f"replay review {review.as_of} produced no post-action projection")
        post_total = view.portfolio_value + view.new_cash - cost
        stable_weight = sum(weight for symbol, weight in post_weights.items() if symbol in stables)
        stable_weight_sum += stable_weight

        label = review.next_returns
        missing = sorted(
            symbol for symbol, weight in post_weights.items()
            if weight > 1e-12 and symbol not in stables and symbol not in label
        )
        if missing:
            raise ValueError(
                f"replay review {review.as_of} is missing realized returns for "
                "post-action exposure(s): " + ", ".join(missing)
            )
        period_return = sum(
            weight * label.get(symbol, 0.0) for symbol, weight in post_weights.items()
        ) - (cost / post_total if post_total > 0 else 0.0)
        period_returns.append(period_return)
        nav_path.append(nav_path[-1] * (1.0 + period_return))
        dollars = {
            symbol: weight * post_total * (1.0 + label.get(symbol, 0.0))
            for symbol, weight in post_weights.items()
        }
        value = sum(dollars.values())
        review_rows.append({
            "as_of": review.as_of,
            "regime": regime.regime,
            "decision": rebalance.decision,
            "executable_actions": len(executable),
            "staged_actions": sum(1 for a in rebalance.actions if a.staging_applied),
            "turnover": turnover,
            "cost": cost,
            "period_return": period_return,
            "nav": nav_path[-1],
            "stable_weight": stable_weight,
        })

    final_nav = nav_path[-1]
    count = len(reviews)
    total_return = final_nav - 1.0
    elapsed_days = max((moments[-1] - moments[0]).total_seconds() / 86400.0, 1.0)
    periods_per_year = 365.25 / max(elapsed_days / max(count - 1, 1), 1e-9)
    average_return = sum(period_returns) / count
    variance = sum((item - average_return) ** 2 for item in period_returns) / max(count - 1, 1)
    volatility = math.sqrt(variance) * math.sqrt(periods_per_year)
    annualized = (final_nav ** (365.25 / elapsed_days)) - 1.0 if final_nav > 0 else -1.0
    sharpe_like = (annualized / volatility) if volatility > 0 else None
    return {
        "reviews": count,
        "final_nav": final_nav,
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": _max_drawdown(nav_path),
        "annualized_volatility": volatility,
        "sharpe_like_rf_zero": sharpe_like,
        "assumptions": {
            "risk_free_rate": 0.0,
            "annualization": "365.25-day year scaled by mean review cadence",
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
) -> dict[str, Any]:
    """Replay two policies over identical frozen periods and benchmarks.

    Purely deterministic comparison; calibration decisions remain human.
    The candidate is never preferred merely for holdout outperformance -
    that judgement is explicitly left out of the code.
    """
    baseline = replay_strategy(
        reviews, policy=baseline_policy, fee_bps=fee_bps, slippage_bps=slippage_bps
    )
    candidate = replay_strategy(
        reviews, policy=candidate_policy, fee_bps=fee_bps, slippage_bps=slippage_bps
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
]
