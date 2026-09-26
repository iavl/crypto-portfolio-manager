"""Staged emergency-recovery state machine (Strategy V2.1 Phase B).

The emergency brake de-risks in steps (CAUTION / EMERGENCY / BREACH) and the
recovery ladder re-risks in steps (RECOVERY_1 / RECOVERY_2 / release) driven
by market state, never by the portfolio's own sunk P&L: a book whose NAV is
far below its old high may re-enter risk as soon as the market regime,
realized portfolio volatility, and the absence of new lows say it may, and any
new low, market deterioration, severe systemic event, or critical chain
liveness knocks it straight back down to the drawdown ladder.

The machine is a pure function of an explicit persisted state plus the
review's observations, so replay and production advance the identical
contract (``RecoveryState`` round-trips through its mapping form).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..models.policy import Policy, resolve_policy
from .portfolio_risk import emergency_drawdown_state

_EMERGENCY_STATES = {"NORMAL", "CAUTION", "EMERGENCY", "BREACH"}
_RECOVERY_STATES = {"RECOVERY_1", "RECOVERY_2"}
_ALL_STATES = _EMERGENCY_STATES | _RECOVERY_STATES
_CRITICAL_LIVENESS = {"HALTED", "UNKNOWN", "FAILED", "CONFLICT"}


def recovery_config(policy: Policy | None = None) -> dict[str, Any]:
    """Validated ``risk_engine.recovery`` block (preregistered placeholders)."""
    resolved = policy or resolve_policy()
    config = (resolved.risk_engine or {}).get("recovery") or {}
    required = {
        "stage_1_reviews", "stage_1_risky_cap", "stage_2_reviews",
        "stage_2_risky_cap", "release_reviews", "max_portfolio_volatility",
    }
    if set(config) != required:
        raise ValueError("risk_engine.recovery fields are incomplete")
    return dict(config)


@dataclass(frozen=True)
class RecoveryState:
    """Persisted emergency-recovery state advanced once per review."""

    state: str = "NORMAL"
    reviews_in_state: int = 0
    market_normal_streak: int = 0
    reviews_since_new_low: int = 0
    last_portfolio_low: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in _ALL_STATES:
            raise ValueError(f"unknown emergency recovery state {self.state!r}")
        for name in ("reviews_in_state", "market_normal_streak", "reviews_since_new_low"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        drawdown = self.last_portfolio_low
        if isinstance(drawdown, bool) or not isinstance(drawdown, (int, float)):
            raise ValueError("last_portfolio_low must be numeric")
        if not math.isfinite(float(drawdown)) or drawdown > 0:
            raise ValueError("last_portfolio_low must be finite and <= 0")
        object.__setattr__(self, "last_portfolio_low", float(drawdown))

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reviews_in_state": self.reviews_in_state,
            "market_normal_streak": self.market_normal_streak,
            "reviews_since_new_low": self.reviews_since_new_low,
            "last_portfolio_low": self.last_portfolio_low,
        }

    @classmethod
    def from_mapping(cls, value: RecoveryState | Mapping[str, Any] | None) -> "RecoveryState":
        if value is None or isinstance(value, RecoveryState):
            return value or cls()
        if not isinstance(value, Mapping):
            raise ValueError("recovery state must be an object or null")
        allowed = {"state", "reviews_in_state", "market_normal_streak",
                   "reviews_since_new_low", "last_portfolio_low"}
        if not set(value) <= allowed:
            raise ValueError("recovery state contains unknown fields")
        return cls(
            state=str(value.get("state", "NORMAL")).upper(),
            reviews_in_state=int(value.get("reviews_in_state", 0)),
            market_normal_streak=int(value.get("market_normal_streak", 0)),
            reviews_since_new_low=int(value.get("reviews_since_new_low", 0)),
            last_portfolio_low=float(value.get("last_portfolio_low", 0.0)),
        )


def _ladder(drawdown: float, policy: Policy) -> tuple[str, float]:
    engine = policy.risk_engine or {}
    bands = engine["emergency_overlay"]
    result = emergency_drawdown_state(
        drawdown,
        budget=float(policy.max_portfolio_drawdown),
        caution_fraction=float(bands["caution_fraction"]),
        emergency_fraction=float(bands["emergency_fraction"]),
        breach_fraction=float(bands["breach_fraction"]),
        caution_risky_cap=float(bands["caution_risky_cap"]),
        emergency_risky_cap=float(bands["emergency_risky_cap"]),
        breach_risky_cap=float(bands["breach_risky_cap"]),
    )
    return str(result["state"]), float(result["risky_cap"])


def _liveness_critical(liveness: Mapping[str, Any] | None) -> bool:
    for raw_status in (liveness or {}).values():
        status = raw_status.get("status") if isinstance(raw_status, Mapping) else raw_status
        if str(status).strip().upper() in _CRITICAL_LIVENESS:
            return True
    return False


def advance_emergency_recovery(
    state: RecoveryState | Mapping[str, Any] | None,
    *,
    portfolio_drawdown: float,
    market_only_regime: str,
    systemic_event_severe: bool = False,
    chain_liveness: Mapping[str, Any] | None = None,
    portfolio_volatility: float | None = None,
    policy: Policy | None = None,
) -> tuple[RecoveryState, dict[str, Any]]:
    """Advance the emergency-recovery FSM by one review.

    ``portfolio_volatility`` is the prior allocation's estimated annualized
    portfolio volatility; when unavailable the volatility recovery condition
    fails closed (no re-risk without evidence). Returns the new state and a
    descriptive block carrying the effective risky cap, the transition taken,
    and the recovery conditions observed this review.
    """
    resolved = policy or resolve_policy()
    current = RecoveryState.from_mapping(state)
    if isinstance(portfolio_drawdown, bool) or not isinstance(portfolio_drawdown, (int, float)):
        raise ValueError("portfolio_drawdown must be numeric")
    drawdown = float(portfolio_drawdown)
    if not math.isfinite(drawdown) or drawdown > 0:
        raise ValueError("portfolio_drawdown must be finite and <= 0")
    regime_name = str(market_only_regime).strip().upper()
    if regime_name not in {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}:
        raise ValueError("market_only_regime must be a regime label")
    config = recovery_config(resolved)
    threshold = float(config["max_portfolio_volatility"])
    volatility_ok = (
        portfolio_volatility is not None
        and float(portfolio_volatility) <= threshold + 1e-12
    )
    liveness_critical = _liveness_critical(chain_liveness)
    severe = bool(systemic_event_severe) or liveness_critical

    # Clocks: a strictly lower drawdown is a new portfolio low and restarts
    # the since-trough clock; the market streak counts consecutive NORMAL
    # market-only regimes regardless of the emergency state.
    new_low = drawdown < current.last_portfolio_low - 1e-12
    last_low = min(current.last_portfolio_low, drawdown)
    reviews_since_new_low = 0 if new_low else current.reviews_since_new_low + 1
    market_normal_streak = (
        current.market_normal_streak + 1 if regime_name == "NORMAL" else 0
    )

    ladder_state, ladder_cap = _ladder(drawdown, resolved)
    conditions = {
        "market_normal": regime_name == "NORMAL",
        "volatility_within_threshold": volatility_ok,
        "no_recent_new_low": reviews_since_new_low >= int(config["stage_1_reviews"]),
        "no_severe_systemic_or_liveness_event": not severe,
    }
    conditions_ok = all(conditions.values())

    # Reset: any of the plan's hard resets knocks the book back onto the
    # drawdown ladder, however far up the recovery ladder it had climbed.
    market_deteriorated = regime_name != "NORMAL"
    reset = (
        new_low
        or severe
        or (market_deteriorated and current.state in _RECOVERY_STATES)
        or (market_deteriorated and current.state == "NORMAL" and ladder_state in {"EMERGENCY", "BREACH"})
    )
    # A ladder more severe than the current CAUTION/EMERGENCY/BREACH state
    # re-asserts itself: worsening drawdown can only ever tighten risk. The
    # recovery stages and a released book (state NORMAL while the ladder is
    # still deep) are exempt by construction — they deliberately sit above
    # the ladder while conditions hold, and any genuinely worsening drawdown
    # is a new low and resets through that rule instead, so no re-risk loop
    # can form.
    ladder_severity = {"NORMAL": 0, "CAUTION": 1, "EMERGENCY": 2, "BREACH": 3}
    if (
        current.state in {"CAUTION", "EMERGENCY", "BREACH"}
        and ladder_severity[ladder_state] > ladder_severity[current.state]
    ):
        reset = True

    state_name = current.state
    transition = "HOLD"
    # Once the ladder itself relaxes to CAUTION/NORMAL the emergency is over:
    # the FSM returns to the ladder state and the ladder cap governs. This
    # takes precedence over staged progression so a healed drawdown cannot
    # keep the book labeled as recovering.
    if ladder_state in {"NORMAL", "CAUTION"} and (
        current.state in _RECOVERY_STATES or current.state in {"EMERGENCY", "BREACH"}
    ):
        state_name = ladder_state
        transition = f"LADDER_RELAXED_TO_{ladder_state}"
    elif reset and current.state != ladder_state:
        state_name = ladder_state
        transition = f"RESET_TO_{ladder_state}"
    elif reset:
        transition = f"RESET_HOLD_{ladder_state}"
    elif ladder_state in {"EMERGENCY", "BREACH"} or current.state in _RECOVERY_STATES:
        stage_1 = int(config["stage_1_reviews"])
        stage_2 = int(config["stage_2_reviews"])
        release = int(config["release_reviews"])
        if current.state in {"EMERGENCY", "BREACH"} and conditions_ok and reviews_since_new_low >= stage_1:
            state_name = "RECOVERY_1"
            transition = "ENTER_RECOVERY_1"
        elif current.state == "RECOVERY_1" and conditions_ok and reviews_since_new_low >= stage_2:
            state_name = "RECOVERY_2"
            transition = "ENTER_RECOVERY_2"
        elif current.state == "RECOVERY_2" and conditions_ok and reviews_since_new_low >= release:
            state_name = "NORMAL"
            transition = "RELEASE"

    stage_caps = {
        "RECOVERY_1": float(config["stage_1_risky_cap"]),
        "RECOVERY_2": float(config["stage_2_risky_cap"]),
    }
    if state_name in stage_caps:
        # Recovery stages sit between the breach and emergency caps; the
        # effective cap never falls below the ladder's own while recovering.
        risky_cap = max(stage_caps[state_name], ladder_cap)
    elif state_name == "NORMAL":
        risky_cap = 1.0 if ladder_state in {"EMERGENCY", "BREACH"} else ladder_cap
    else:
        risky_cap = ladder_cap
    reviews_in_state = 0 if state_name != current.state else current.reviews_in_state + 1
    result = RecoveryState(
        state=state_name,
        reviews_in_state=reviews_in_state,
        market_normal_streak=market_normal_streak,
        reviews_since_new_low=reviews_since_new_low,
        last_portfolio_low=last_low,
    )
    block = {
        "state": result.state,
        "risky_cap": risky_cap,
        "ladder_state": ladder_state,
        "transition": transition,
        "budget_consumed": min(1.0, max(0.0, -drawdown / float(resolved.max_portfolio_drawdown))),
        "reviews_in_state": result.reviews_in_state,
        "market_normal_streak": result.market_normal_streak,
        "reviews_since_new_low": result.reviews_since_new_low,
        "last_portfolio_low": result.last_portfolio_low,
        "recovery_conditions": conditions,
    }
    return result, block


__all__ = ["RecoveryState", "advance_emergency_recovery", "recovery_config"]
