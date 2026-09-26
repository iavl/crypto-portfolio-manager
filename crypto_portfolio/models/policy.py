"""Canonical policy loading and validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Mapping


class PolicyError(ValueError):
    """Raised when canonical policy or an explicit override is invalid."""


_REGIMES = ("NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION")
SCORING_FACTORS = (
    "trend",
    "valuation",
    "fundamentals",
    "onchain",
    "capital_flows",
    "relative_strength_btc",
    "btc_valuation",
    "macro_liquidity",
)
_EVENT_RISK_STATES = ("NORMAL", "ELEVATED", "HIGH", "SEVERE", "CRITICAL")
_DEFAULT_EVENT_RISK_MULTIPLIERS = {
    "NORMAL": 1.0,
    "ELEVATED": 0.75,
    "HIGH": 0.5,
    "SEVERE": 0.0,
    "CRITICAL": 0.0,
}
_REGIME_FIELDS = (
    "stablecoin_target",
    "satellite_max",
    "core_risky_min",
    "single_asset_max",
)
_TOP_LEVEL_FIELDS = {
    "investment_horizon_months",
    "universe",
    "risk",
    "stress_scenarios",
    "risk_engine",
    "risk_tier_estimation",
    "dynamic_universe",
    "chain_liveness",
    "benchmarks",
    "rebalance",
    "scoring_profiles",
    "asset_scoring_profiles",
    "scoring",
    "factor_rules",
    "regimes",
    "allocation",
    "core_allocation",
    "execution",
    "volume_profile",
    "positioning",
    "btc_cycle",
    "execution_overlay",
    "events",
    "high_impact_review",
    "event_risk_multipliers",
    "confidence",
    "freshness_policy",
    "source_quality",
    "event_severity",
    "nav_history",
    "regime_transitions",
    "regime_model",
    "scoring_families",
}
_REGIME_TRANSITION_FIELDS = {"enabled", "max_notches_per_review"}
_REGIME_MODEL_FIELDS = {"mode", "normal_max", "defensive_max", "domain_weights", "severity"}
_REGIME_MODEL_MODES = {"vote_count", "weighted"}
_REGIME_MODEL_DOMAINS = ("trend", "volatility", "flows", "breadth")
_REGIME_SEVERITY_STATES = {
    "trend": {"BULLISH", "NEUTRAL", "BEARISH"},
    "volatility": {"LOW", "NORMAL", "ELEVATED", "HIGH", "EXTREME"},
    "flows": {"POSITIVE", "NEUTRAL", "NEGATIVE"},
    "breadth": {"HEALTHY", "NEUTRAL", "WEAK"},
}
_DEFAULT_REGIME_MODEL: dict[str, Any] = {
    "mode": "vote_count",
    "normal_max": 0.35,
    "defensive_max": 0.65,
    "domain_weights": {"trend": 0.30, "volatility": 0.25, "flows": 0.25, "breadth": 0.20},
    "severity": {
        "trend": {"BULLISH": 0.0, "NEUTRAL": 0.25, "BEARISH": 1.0},
        "volatility": {"LOW": 0.0, "NORMAL": 0.0, "ELEVATED": 0.7, "HIGH": 0.85, "EXTREME": 1.0},
        "flows": {"POSITIVE": 0.0, "NEUTRAL": 0.25, "NEGATIVE": 1.0},
        "breadth": {"HEALTHY": 0.0, "NEUTRAL": 0.25, "WEAK": 1.0},
    },
}
_UNIVERSE_FIELDS = {"core", "satellites", "stable", "excluded"}
_RISK_FIELDS = {"min_stablecoin_weight", "max_portfolio_drawdown", "drawdown_budget_overlay"}
# Canonical multi-scenario stress framework. Scenario returns are mechanism
# placeholders pending Strategy V2 Phase 6 walk-forward calibration; only
# ``moderate`` preserves the Strategy V1 single-scenario values exactly.
_STRESS_SCENARIO_NAMES = {
    "moderate",
    "severe_crypto_crash",
    "liquidity_shock",
    "correlation_one",
    "btc_gap_down",
    "eth_alt_crash",
    "stablecoin_depeg",
}
_RISK_ENGINE_MODES = {"legacy_drawdown", "volatility_budget"}
_RISK_ENGINE_FIELDS = {"mode", "portfolio_risk", "emergency_overlay", "regime_risk_scaling", "recovery"}
_REGIME_RISK_SCALING_REGIMES = {"NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
_RISK_ENGINE_RECOVERY_FIELDS = {
    "stage_1_reviews",
    "stage_1_risky_cap",
    "stage_2_reviews",
    "stage_2_risky_cap",
    "release_reviews",
    "max_portfolio_volatility",
}
_RISK_ENGINE_PORTFOLIO_FIELDS = {
    "target_volatility",
    "max_volatility",
    "volatility_window_weights",
    "correlation_window_days",
    "beta_window_days",
    "annualization_days",
    "minimum_history_days",
}
_DYNAMIC_UNIVERSE_FIELDS = {"enabled", "minimum_history_days", "minimum_median_volume_usd"}
_RISK_TIER_ESTIMATION_FIELDS = {
    "beta_enter",
    "beta_exit",
    "relative_vol_enter",
    "relative_vol_exit",
    "minimum_history_days",
}
_RISK_ENGINE_EMERGENCY_FIELDS = {
    "caution_fraction",
    "emergency_fraction",
    "breach_fraction",
    "caution_risky_cap",
    "emergency_risky_cap",
    "breach_risky_cap",
}
_OVERLAY_FIELDS = {"enabled", "recovery_reviews", "recovery_risky_floor"}
_CHAIN_LIVENESS_FIELDS = {"degraded_deployment_factor", "BTC", "ETH", "BNB", "SOL"}
_CHAIN_HEAD_FIELDS = {
    "healthy_head_age_seconds",
    "degraded_head_age_seconds",
    "halted_head_age_seconds",
    "halted_minimum_independent_sources",
}
_CHAIN_FINALIZED_FIELDS = {
    "healthy_finalized_age_seconds",
    "degraded_finalized_age_seconds",
    "halted_finalized_age_seconds",
}
_HORIZON_FIELDS = {"min", "max"}
_REBALANCE_FIELDS = {
    "hold_below_pp",
    "watch_below_pp",
    "high_priority_above_pp",
    "relative_target_floor",
    "relative_watch",
    "relative_high",
    "staging",
    "direction_flip_confirmation",
}
_REBALANCE_STAGING_FIELDS = {
    "enabled",
    "max_gap_close_fraction",
    "max_step_pp",
    "bypass_reasons",
}
_REBALANCE_DIRECTION_FLIP_FIELDS = {
    "enabled",
    "required_closes",
    "immediate_overshoot_pp",
    "bypass_reasons",
}
_ACTION_REASONS = (
    "THESIS_BROKEN",
    "EVENT_RISK",
    "HARD_EXIT_SCORE",
    "REGIME_DERISK",
    "ALLOCATION_OVERWEIGHT",
    "ALLOCATION_UNDERWEIGHT",
    "CONFIDENCE_LIMIT",
    "RISK_BUDGET_BREACH",
)
_HIGH_IMPACT_REVIEW_FIELDS = {"material_reduce_pp", "material_target_change_pp"}
_ALLOCATION_FIELDS = {
    "satellite_entry_score",
    "satellite_exit_score",
    "satellite_soft_exit_score",
    "satellite_full_score",
    "satellite_target_curve",
    "risk_tier_caps",
    "relative_strength",
}
_RELATIVE_STRENGTH_FIELDS = {"increase_min_score", "hard_block_below_score"}
_RISK_TIER_CAP_FIELDS = {
    "strategic_fraction_of_satellite_envelope",
    "hard_cap_buffer_pp",
}
_SATELLITE_CURVE_FIELDS = {
    "soft_exit_fraction",
    "exit_fraction",
    "entry_fraction",
    "full_fraction",
}
_CORE_ALLOCATION_FIELDS = {"anchor", "eth", "confidence_multipliers"}
_CORE_ANCHOR_FIELDS = {"BTC", "ETH"}
_CORE_ETH_FIELDS = {
    "increase_min_score",
    "hold_min_score",
    "relative_increase_min_score",
    "relative_reduce_below_score",
    "max_core_sleeve_share",
}
_CORE_CONFIDENCE_FIELDS = {"HIGH", "MEDIUM", "LOW"}
_SCORING_FIELDS = {
    "high_confidence_min_coverage",
    "medium_confidence_min_coverage",
    "minimum_investable_coverage",
    "minimum_normalization_coverage",
}
_FACTOR_RULE_FIELDS = {"trend", "relative_strength", "flows"}
_TREND_RULE_FIELDS = {
    "base_score",
    "ma_points",
    "alignment_windows",
    "alignment_points",
    "momentum",
    "support_points",
    "volume_points",
    "volume_weakness_confirmation_closes",
    "extension_threshold_atr",
    "extension_penalty",
}
_TREND_MA_FIELDS = {"20", "50", "100", "200"}
_TREND_MOMENTUM_FIELDS = {
    "horizon_weights",
    "neutral_abs",
    "saturation_abs",
    "max_points",
}
_TREND_HORIZONS = ("30d", "90d", "180d")
_RELATIVE_RULE_FIELDS = {
    "horizon_weights",
    "risk_adjusted_neutral_band",
    "risk_adjusted_saturation",
}
_FLOW_RULE_FIELDS = {"neutral_abs_max", "strong_abs", "supply_change_horizon_weights"}
_FLOW_SUPPLY_HORIZONS = ("7d", "30d", "90d")
_EXECUTION_FIELDS = {
    "timeframe",
    "preferred_history_days",
    "minimum_history_days",
    "max_fetched_age_days",
    "maximum_daily_candle_lag_days",
    "minimum_daily_coverage_ratio",
    "maximum_daily_gap_days",
    "moving_average_windows",
    "atr_period",
    "realized_volatility_windows",
    "volatility_annualization_days",
    "volume_average_window",
    "swing_window",
    "max_tranches",
    "zone_half_width_atr",
    "minimum_zone_separation_atr",
    "maximum_zone_span_atr",
    "maximum_spot_close_gap_atr",
    "zone_quality",
    "volatility_atr_percent",
    "confidence_deployment_factor",
    "deployment_factor_composition",
    "max_initial_tranche",
    "tranche_templates",
    "breakout",
    "resting_order_match_tolerance",
    "resting_order_full_fraction",
    "resting_order_partial_fraction",
}
_DEPLOYMENT_COMPOSITION_MODES = {"minimum_cap", "multiplicative"}
_VOLATILITY_FIELDS = {"low_max", "normal_max", "high_max"}
_BREAKOUT_FIELDS = {"minimum_relative_volume", "max_atr_extension", "max_initial_tranche"}
_ZONE_QUALITY_FIELDS = {"minimum_for_entry", "high_quality"}
_VOLUME_PROFILE_FIELDS = {
    "enabled",
    "preferred_timeframe",
    "fallback_timeframe",
    "lookback_days",
    "preferred_lookback_days",
    "price_bins",
    "value_area_fraction",
    "hvn_percentile",
    "max_hvn_nodes",
    "minimum_node_separation_atr",
    "zone_half_width_atr",
    "allow_daily_approximation",
    "daily_approximation_confidence_cap",
}
_POSITIONING_FIELDS = {
    "enabled",
    "minimum_derivatives_confirmations_for_crowded",
    "minimum_derivatives_confirmations_for_extreme",
    "funding_rate",
    "open_interest_change_7d",
    "long_short_ratio",
    "futures_basis",
    "social",
}
_FUNDING_FIELDS = {
    "elevated_positive",
    "extreme_positive",
    "elevated_negative",
    "extreme_negative",
}
_OI_FIELDS = {"building", "rapid"}
_LONG_SHORT_FIELDS = {"long_crowded", "short_crowded", "long_extreme", "short_extreme"}
_BASIS_FIELDS = {"elevated_positive", "extreme_positive", "elevated_negative", "extreme_negative"}
_SOCIAL_FIELDS = {
    "fearful_bullish_share",
    "optimistic_bullish_share",
    "euphoric_bullish_share",
    "attention_growth_extreme",
}
_BTC_CYCLE_FIELDS = {
    "enabled",
    "halving_context_days",
    "minimum_non_clock_confirmations_for_elevated_risk",
    "minimum_non_clock_confirmations_for_high_risk",
    "allow_halving_clock_as_trade_trigger",
    "allow_cycle_context_to_change_base_score",
    "allow_cycle_context_to_increase_exposure",
    "valuation",
    "price",
    "holder",
    "flows",
}
_HALVING_DAYS_FIELDS = {"early_post_halving_max", "mid_epoch_max", "late_epoch_min"}
_CYCLE_VALUATION_FIELDS = {
    "mvrv_zscore_elevated",
    "mvrv_zscore_extreme",
    "market_to_realized_price_elevated",
    "market_to_realized_price_extreme",
}
_CYCLE_PRICE_FIELDS = {"extension_atr", "drawdown_reset"}
_CYCLE_HOLDER_FIELDS = {"lth_distribution_threshold", "lth_accumulation_threshold", "sopr_distribution_threshold"}
_CYCLE_FLOW_FIELDS = {"weakening_threshold"}
_EXECUTION_OVERLAY_FIELDS = {"positioning", "btc_cycle", "wait"}
_EVENTS_FIELDS = {"lookback_days", "coverage"}
_EVENT_REVIEW_TYPES = ("SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW")
_EVENT_CATEGORIES = ("security", "regulatory")
_CONFIDENCE_FIELDS = {
    "band_thresholds",
    "data_dimension_weights",
    "regime_domain_weights",
    "decision_component_weights",
    "factor_sufficiency",
    "asset_evidence",
    "caps",
}
_CONFIDENCE_BAND_FIELDS = {"medium_min", "high_min"}
_DATA_DIMENSIONS = {"coverage", "freshness", "source_quality", "redundancy"}
_REGIME_DOMAINS = {"trend", "volatility", "breadth", "flows", "portfolio_drawdown", "systemic_risk"}
_DECISION_COMPONENTS = {
    "portfolio_data", "regime_confidence", "asset_evidence", "portfolio_accounting", "signal_agreement"
}
_FRESHNESS_DOMAINS = set(_REGIME_DOMAINS)
_FRESHNESS_FIELDS = {"domain_defaults", "metric_overrides", "dimension_weights"}
_FRESHNESS_AGE_FIELDS = {"max_age_seconds", "half_life_seconds"}
_SOURCE_QUALITY_FIELDS = {"tier_scores", "redundancy_scores"}
_EVENT_SEVERITY_FIELDS = {"states", "coverage_state"}
_NAV_HISTORY_FIELDS = {"unresolved_flow_cap", "provisional_cap"}
_OVERLAY_RISK_STATES = ("NORMAL", "ELEVATED", "HIGH", "EXTREME")
_POSITIONING_REQUIRED_FIELDS = {
    "enabled",
    "minimum_derivatives_confirmations_for_crowded",
    "minimum_derivatives_confirmations_for_extreme",
    "funding_rate",
    "open_interest_change_7d",
    "long_short_ratio",
}
_BTC_CYCLE_REQUIRED_FIELDS = {
    "enabled",
    "halving_context_days",
    "minimum_non_clock_confirmations_for_elevated_risk",
    "minimum_non_clock_confirmations_for_high_risk",
    "allow_halving_clock_as_trade_trigger",
    "allow_cycle_context_to_change_base_score",
    "allow_cycle_context_to_increase_exposure",
}
_OVERRIDE_FIELDS = {
    "core_symbols",
    "satellite_symbols",
    "stable_symbols",
    "excluded_symbols",
    "min_stablecoin_weight",
    "max_portfolio_drawdown",
}
_DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.json"


def _unknown_fields(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PolicyError(f"{name} contains unknown fields: {', '.join(unknown)}")


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _copy_mapping(item) if isinstance(item, Mapping) else list(item) if isinstance(item, tuple) else item
        for key, item in value.items()
    }


def _number(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise PolicyError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise PolicyError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise PolicyError(f"{name} must be <= {maximum}")
    return value


def _fraction(value: Any, name: str, *, exclusive_minimum: bool = False) -> float:
    minimum = 0.0 if not exclusive_minimum else math.nextafter(0.0, 1.0)
    return _number(value, name, minimum=minimum, maximum=1.0)


def _symbols(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PolicyError(f"{name} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{name}[{index}] must be a non-empty string")
        symbol = item.strip().upper()
        if symbol in result:
            raise PolicyError(f"{name} contains duplicate symbol {symbol}")
        result.append(symbol)
    return tuple(result)


def _weighted_map(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise PolicyError(f"{name} must be a non-empty object")
    result: dict[str, float] = {}
    for key, weight in value.items():
        if not isinstance(key, str) or not key.strip():
            raise PolicyError(f"{name} keys must be non-empty strings")
        normalized_key = key.strip().upper() if name == "benchmarks" else key.strip()
        if normalized_key in result:
            raise PolicyError(f"{name} contains duplicate key {normalized_key}")
        result[normalized_key] = _fraction(weight, f"{name}.{key}")
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise PolicyError(f"{name} weights must sum to 1")
    return result


def _parse_scoring_profiles(value: Any) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict) or not value:
        raise PolicyError("scoring_profiles must be a non-empty object")
    result: dict[str, dict[str, float]] = {}
    for raw_name, raw_weights in value.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise PolicyError("scoring profile names must be non-empty strings")
        name = raw_name.strip().lower()
        if name in result:
            raise PolicyError(f"scoring_profiles contains duplicate profile {name}")
        if not isinstance(raw_weights, dict):
            raise PolicyError(f"scoring_profiles.{name} must be an object")
        keys = {key.strip().lower() for key in raw_weights if isinstance(key, str)}
        if keys != set(SCORING_FACTORS) or len(raw_weights) != len(SCORING_FACTORS):
            raise PolicyError(
                f"scoring_profiles.{name} must contain exactly {', '.join(SCORING_FACTORS)}"
            )
        weights: dict[str, float] = {}
        for raw_factor, raw_weight in raw_weights.items():
            if not isinstance(raw_factor, str) or not raw_factor.strip():
                raise PolicyError(f"scoring_profiles.{name} keys must be non-empty strings")
            factor = raw_factor.strip().lower()
            if factor in weights:
                raise PolicyError(f"scoring_profiles.{name} contains duplicate key {factor}")
            weights[factor] = _number(
                raw_weight,
                f"scoring_profiles.{name}.{factor}",
                minimum=0.0,
                maximum=1.0,
            )
        if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
            raise PolicyError(f"scoring_profiles.{name} weights must sum to 1")
        result[name] = weights
    if "default" not in result:
        raise PolicyError("scoring_profiles must contain default")
    return result


def _parse_asset_scoring_profiles(value: Any, profiles: Mapping[str, Mapping[str, float]]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise PolicyError("asset_scoring_profiles must be an object")
    result: dict[str, str] = {}
    for raw_symbol, raw_name in value.items():
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise PolicyError("asset_scoring_profiles keys must be non-empty strings")
        symbol = raw_symbol.strip().upper()
        if symbol in result:
            raise PolicyError(f"asset_scoring_profiles contains duplicate asset {symbol}")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise PolicyError(f"asset_scoring_profiles.{symbol} must be a profile name")
        name = raw_name.strip().lower()
        if name not in profiles:
            raise PolicyError(f"asset_scoring_profiles.{symbol} references unknown profile {name}")
        result[symbol] = name
    btc_profile = result.get("BTC")
    if btc_profile is None:
        raise PolicyError("asset_scoring_profiles must explicitly map BTC")
    if profiles[btc_profile]["relative_strength_btc"] != 0.0:
        raise PolicyError("BTC scoring profile must assign zero relative_strength_btc weight")
    return result


def _parse_event_risk_multipliers(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise PolicyError("event_risk_multipliers must be an object")
    if set(value) != set(_EVENT_RISK_STATES):
        raise PolicyError(
            "event_risk_multipliers must contain NORMAL, ELEVATED, HIGH, SEVERE, and CRITICAL"
        )
    result = {
        state: _fraction(value[state], f"event_risk_multipliers.{state}")
        for state in _EVENT_RISK_STATES
    }
    if any(result[left] < result[right] for left, right in zip(_EVENT_RISK_STATES, _EVENT_RISK_STATES[1:])):
        raise PolicyError("event_risk_multipliers must be monotonically non-increasing")
    return result


def _parse_high_impact_review(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise PolicyError("high_impact_review must be an object")
    _unknown_fields(value, _HIGH_IMPACT_REVIEW_FIELDS, "high_impact_review")
    if set(value) != _HIGH_IMPACT_REVIEW_FIELDS:
        raise PolicyError("high_impact_review fields are incomplete")
    return {
        key: _number(value[key], f"high_impact_review.{key}", minimum=math.nextafter(0.0, 1.0))
        for key in _HIGH_IMPACT_REVIEW_FIELDS
    }


def _parse_regime_transitions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("regime_transitions must be an object")
    _unknown_fields(value, _REGIME_TRANSITION_FIELDS, "regime_transitions")
    if set(value) != _REGIME_TRANSITION_FIELDS:
        raise PolicyError("regime_transitions fields are incomplete")
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise PolicyError("regime_transitions.enabled must be boolean")
    max_notches = value["max_notches_per_review"]
    if (
        isinstance(max_notches, bool)
        or not isinstance(max_notches, int)
        or not 1 <= max_notches <= 2
    ):
        raise PolicyError("regime_transitions.max_notches_per_review must be 1 or 2")
    return {"enabled": enabled, "max_notches_per_review": max_notches}


def _parse_dynamic_universe(value: Any) -> dict[str, Any]:
    """Parse ``dynamic_universe`` (Strategy V2 Phase 6).

    Point-in-time universe membership: an asset joins the investable set
    only after ``minimum_history_days`` of daily observations and a median
    dollar volume at or above the floor, evaluated at every decision
    boundary from trailing data only (the anti-survivorship rule).
    """
    if not isinstance(value, dict):
        raise PolicyError("dynamic_universe must be an object")
    _unknown_fields(value, _DYNAMIC_UNIVERSE_FIELDS, "dynamic_universe")
    if set(value) != _DYNAMIC_UNIVERSE_FIELDS:
        raise PolicyError("dynamic_universe fields are incomplete")
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise PolicyError("dynamic_universe.enabled must be boolean")
    history = value["minimum_history_days"]
    if isinstance(history, bool) or not isinstance(history, int) or history < 30:
        raise PolicyError("dynamic_universe.minimum_history_days must be an integer >= 30")
    volume = _number(value["minimum_median_volume_usd"], "dynamic_universe.minimum_median_volume_usd", minimum=0.0)
    return {
        "enabled": enabled,
        "minimum_history_days": history,
        "minimum_median_volume_usd": volume,
    }


def _parse_risk_tier_estimation(value: Any) -> dict[str, Any]:
    """Parse ``risk_tier_estimation`` (Strategy V2 Phase 4).

    Entry/exit threshold pairs create the hysteresis band that keeps a
    threshold-crossing asset from flipping tiers daily. Values are
    structural placeholders pending Phase 6 walk-forward calibration.
    """
    if not isinstance(value, dict):
        raise PolicyError("risk_tier_estimation must be an object")
    _unknown_fields(value, _RISK_TIER_ESTIMATION_FIELDS, "risk_tier_estimation")
    if set(value) != _RISK_TIER_ESTIMATION_FIELDS:
        raise PolicyError("risk_tier_estimation fields are incomplete")
    parsed = {
        key: _number(value[key], f"risk_tier_estimation.{key}", minimum=0.0)
        for key in ("beta_enter", "beta_exit", "relative_vol_enter", "relative_vol_exit")
    }
    if not 0 < parsed["beta_exit"] < parsed["beta_enter"]:
        raise PolicyError("risk_tier_estimation beta thresholds must satisfy 0 < exit < enter")
    if not 0 < parsed["relative_vol_exit"] < parsed["relative_vol_enter"]:
        raise PolicyError("risk_tier_estimation volatility thresholds must satisfy 0 < exit < enter")
    history = value["minimum_history_days"]
    if isinstance(history, bool) or not isinstance(history, int) or history < 2:
        raise PolicyError("risk_tier_estimation.minimum_history_days must be an integer >= 2")
    return {**parsed, "minimum_history_days": history}


def _parse_risk_engine(value: Any) -> dict[str, Any]:
    """Parse the ``risk_engine`` block selecting the sizing mechanism.

    ``legacy_drawdown`` keeps the continuous ``risky_cap = 1 - |drawdown| /
    budget`` ladder as the normal sizing engine (Strategy V1 behavior, byte
    for byte). ``volatility_budget`` derives normal sizing from portfolio
    volatility and demotes drawdown to the staged emergency brake. The
    numeric targets are mechanism placeholders until walk-forward calibration
    (Strategy V2 Phase 6); nothing here is tuned against a backtest.
    """
    if not isinstance(value, dict):
        raise PolicyError("risk_engine must be an object")
    _unknown_fields(value, _RISK_ENGINE_FIELDS, "risk_engine")
    if set(value) != _RISK_ENGINE_FIELDS:
        raise PolicyError("risk_engine fields are incomplete")
    mode = value["mode"]
    if mode not in _RISK_ENGINE_MODES:
        raise PolicyError(
            "risk_engine.mode must be one of " + ", ".join(sorted(_RISK_ENGINE_MODES))
        )

    portfolio = value["portfolio_risk"]
    if not isinstance(portfolio, dict):
        raise PolicyError("risk_engine.portfolio_risk must be an object")
    _unknown_fields(portfolio, _RISK_ENGINE_PORTFOLIO_FIELDS, "risk_engine.portfolio_risk")
    if set(portfolio) != _RISK_ENGINE_PORTFOLIO_FIELDS:
        raise PolicyError("risk_engine.portfolio_risk fields are incomplete")
    target_volatility = _fraction(
        portfolio["target_volatility"], "risk_engine.portfolio_risk.target_volatility"
    )
    max_volatility = _fraction(
        portfolio["max_volatility"], "risk_engine.portfolio_risk.max_volatility"
    )
    if target_volatility > max_volatility:
        raise PolicyError(
            "risk_engine.portfolio_risk.target_volatility must not exceed max_volatility"
        )
    weights = portfolio["volatility_window_weights"]
    if not isinstance(weights, dict) or not weights:
        raise PolicyError("risk_engine.portfolio_risk.volatility_window_weights must be an object")
    parsed_weights: dict[str, float] = {}
    for label, weight in weights.items():
        text = str(label).strip().lower()
        if not text.endswith("d") or not text[:-1].isdigit() or int(text[:-1]) < 2:
            raise PolicyError(
                "risk_engine.portfolio_risk.volatility_window_weights keys must look like '30d'"
            )
        parsed_weights[text] = _fraction(weight, f"risk_engine.portfolio_risk.volatility_window_weights[{label}]")
    if not math.isclose(sum(parsed_weights.values()), 1.0, abs_tol=1e-9):
        raise PolicyError(
            "risk_engine.portfolio_risk.volatility_window_weights must sum to 1"
        )
    positive_ints = {}
    for field in ("correlation_window_days", "beta_window_days", "annualization_days", "minimum_history_days"):
        raw = portfolio[field]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 2:
            raise PolicyError(f"risk_engine.portfolio_risk.{field} must be an integer >= 2")
        positive_ints[field] = raw
    if positive_ints["minimum_history_days"] < max(int(days[:-1]) for days in parsed_weights):
        raise PolicyError(
            "risk_engine.portfolio_risk.minimum_history_days must cover the longest volatility window"
        )

    overlay = value["emergency_overlay"]
    if not isinstance(overlay, dict):
        raise PolicyError("risk_engine.emergency_overlay must be an object")
    _unknown_fields(overlay, _RISK_ENGINE_EMERGENCY_FIELDS, "risk_engine.emergency_overlay")
    if set(overlay) != _RISK_ENGINE_EMERGENCY_FIELDS:
        raise PolicyError("risk_engine.emergency_overlay fields are incomplete")
    caution_fraction = _fraction(overlay["caution_fraction"], "risk_engine.emergency_overlay.caution_fraction")
    emergency_fraction = _fraction(
        overlay["emergency_fraction"], "risk_engine.emergency_overlay.emergency_fraction"
    )
    breach_fraction = _fraction(overlay["breach_fraction"], "risk_engine.emergency_overlay.breach_fraction")
    if not 0 < caution_fraction < emergency_fraction < breach_fraction <= 1:
        raise PolicyError(
            "risk_engine.emergency_overlay fractions must satisfy 0 < caution < emergency < breach <= 1"
        )
    caution_cap = _fraction(overlay["caution_risky_cap"], "risk_engine.emergency_overlay.caution_risky_cap")
    emergency_cap = _fraction(
        overlay["emergency_risky_cap"], "risk_engine.emergency_overlay.emergency_risky_cap"
    )
    breach_cap = _fraction(overlay["breach_risky_cap"], "risk_engine.emergency_overlay.breach_risky_cap")
    if not caution_cap > emergency_cap > breach_cap:
        raise PolicyError(
            "risk_engine.emergency_overlay risky caps must satisfy caution > emergency > breach"
        )
    scaling = value["regime_risk_scaling"]
    if not isinstance(scaling, dict):
        raise PolicyError("risk_engine.regime_risk_scaling must be an object")
    if set(scaling) != _REGIME_RISK_SCALING_REGIMES:
        raise PolicyError(
            "risk_engine.regime_risk_scaling must define exactly "
            + ", ".join(sorted(_REGIME_RISK_SCALING_REGIMES))
        )
    multipliers: dict[str, float] = {}
    for name in sorted(scaling):
        entry = scaling[name]
        if not isinstance(entry, dict) or set(entry) != {"target_volatility_multiplier"}:
            raise PolicyError(
                f"risk_engine.regime_risk_scaling.{name} must be an object with "
                "exactly target_volatility_multiplier"
            )
        multipliers[name] = _fraction(
            entry["target_volatility_multiplier"],
            f"risk_engine.regime_risk_scaling.{name}.target_volatility_multiplier",
        )
    if not (
        multipliers["NORMAL"] >= multipliers["DEFENSIVE"] >= multipliers["CAPITAL_PRESERVATION"] > 0
    ):
        raise PolicyError(
            "risk_engine.regime_risk_scaling multipliers must satisfy "
            "NORMAL >= DEFENSIVE >= CAPITAL_PRESERVATION > 0"
        )
    recovery = value["recovery"]
    if not isinstance(recovery, dict):
        raise PolicyError("risk_engine.recovery must be an object")
    _unknown_fields(recovery, _RISK_ENGINE_RECOVERY_FIELDS, "risk_engine.recovery")
    if set(recovery) != _RISK_ENGINE_RECOVERY_FIELDS:
        raise PolicyError("risk_engine.recovery fields are incomplete")
    stage_1_reviews = recovery["stage_1_reviews"]
    stage_2_reviews = recovery["stage_2_reviews"]
    release_reviews = recovery["release_reviews"]
    for name, raw in (
        ("stage_1_reviews", stage_1_reviews),
        ("stage_2_reviews", stage_2_reviews),
        ("release_reviews", release_reviews),
    ):
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise PolicyError(f"risk_engine.recovery.{name} must be an integer >= 1")
    if not stage_1_reviews <= stage_2_reviews <= release_reviews:
        raise PolicyError(
            "risk_engine.recovery review thresholds must satisfy "
            "stage_1 <= stage_2 <= release"
        )
    stage_1_cap = _fraction(recovery["stage_1_risky_cap"], "risk_engine.recovery.stage_1_risky_cap")
    stage_2_cap = _fraction(recovery["stage_2_risky_cap"], "risk_engine.recovery.stage_2_risky_cap")
    breach_cap_parsed = breach_cap
    if not breach_cap_parsed < stage_1_cap < stage_2_cap < 1.0:
        raise PolicyError(
            "risk_engine.recovery risky caps must satisfy "
            "breach_risky_cap < stage_1_risky_cap < stage_2_risky_cap < 1"
        )
    recovery_vol_threshold = _fraction(
        recovery["max_portfolio_volatility"],
        "risk_engine.recovery.max_portfolio_volatility",
    )
    if recovery_vol_threshold > max_volatility:
        raise PolicyError(
            "risk_engine.recovery.max_portfolio_volatility must not exceed "
            "risk_engine.portfolio_risk.max_volatility"
        )
    return {
        "mode": mode,
        "portfolio_risk": {
            "target_volatility": target_volatility,
            "max_volatility": max_volatility,
            "volatility_window_weights": parsed_weights,
            **positive_ints,
        },
        "emergency_overlay": {
            "caution_fraction": caution_fraction,
            "emergency_fraction": emergency_fraction,
            "breach_fraction": breach_fraction,
            "caution_risky_cap": caution_cap,
            "emergency_risky_cap": emergency_cap,
            "breach_risky_cap": breach_cap,
        },
        "regime_risk_scaling": {
            name: {"target_volatility_multiplier": multipliers[name]}
            for name in sorted(multipliers)
        },
        "recovery": {
            "stage_1_reviews": stage_1_reviews,
            "stage_1_risky_cap": stage_1_cap,
            "stage_2_reviews": stage_2_reviews,
            "stage_2_risky_cap": stage_2_cap,
            "release_reviews": release_reviews,
            "max_portfolio_volatility": recovery_vol_threshold,
        },
    }


def _parse_drawdown_budget_overlay(value: Any) -> dict[str, Any]:
    """Parse the ``risk.drawdown_budget_overlay`` block.

    The overlay enforces the drawdown budget at position level: every unit of
    budget consumed removes one unit of risky-weight allowance, and a
    confirmed market recovery re-risks up to ``recovery_risky_floor`` while
    the ladder would otherwise pin the book in stable.
    """
    if not isinstance(value, dict):
        raise PolicyError("risk.drawdown_budget_overlay must be an object")
    _unknown_fields(value, _OVERLAY_FIELDS, "risk.drawdown_budget_overlay")
    if set(value) != _OVERLAY_FIELDS:
        raise PolicyError("risk.drawdown_budget_overlay fields are incomplete")
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise PolicyError("risk.drawdown_budget_overlay.enabled must be boolean")
    recovery_reviews = value["recovery_reviews"]
    if (
        isinstance(recovery_reviews, bool)
        or not isinstance(recovery_reviews, int)
        or recovery_reviews < 1
    ):
        raise PolicyError("risk.drawdown_budget_overlay.recovery_reviews must be an integer >= 1")
    recovery_risky_floor = _fraction(
        value["recovery_risky_floor"],
        "risk.drawdown_budget_overlay.recovery_risky_floor",
    )
    if recovery_risky_floor >= 1.0:
        raise PolicyError(
            "risk.drawdown_budget_overlay.recovery_risky_floor must be below 1"
        )
    return {
        "enabled": enabled,
        "recovery_reviews": recovery_reviews,
        "recovery_risky_floor": recovery_risky_floor,
    }


def _parse_scoring_families(
    value: Any,
    profiles: Mapping[str, Mapping[str, float]],
) -> dict[str, Mapping[str, Mapping[str, Any]]]:
    """Optional two-stage factor-family scoring trees, keyed by profile name.

    An empty object keeps every profile on the flat compatibility path.  A
    configured tree derives flat factor weights as
    ``sum(family_weight * in_family_weight)`` so MISSING/reliability/coverage
    semantics stay identical; the family grouping exists for attribution and
    for capping correlated signals inside a family.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PolicyError("scoring_families must be an object")
    parsed: dict[str, Mapping[str, Mapping[str, Any]]] = {}
    for raw_name, tree in value.items():
        name = str(raw_name).strip().lower()
        if not name:
            raise PolicyError("scoring_families profile names must be non-empty")
        if name in parsed:
            raise PolicyError(f"scoring_families contains duplicate profile {name}")
        if name not in profiles:
            raise PolicyError(f"scoring_families references unknown scoring profile {name}")
        if not isinstance(tree, dict) or not tree:
            raise PolicyError(f"scoring_families.{name} must be a non-empty object")
        family_weights: dict[str, float] = {}
        family_factors: dict[str, dict[str, float]] = {}
        seen_factors: set[str] = set()
        for raw_family, raw_spec in tree.items():
            family = str(raw_family).strip().lower()
            if not family or family in family_weights:
                raise PolicyError(f"scoring_families.{name} contains invalid or duplicate family {raw_family!r}")
            if not isinstance(raw_spec, dict) or set(raw_spec) != {"weight", "factors"}:
                raise PolicyError(f"scoring_families.{name}.{family} must contain exactly weight and factors")
            family_weights[family] = _fraction(raw_spec["weight"], f"scoring_families.{name}.{family}.weight")
            factors = raw_spec["factors"]
            if not isinstance(factors, dict) or not factors:
                raise PolicyError(f"scoring_families.{name}.{family}.factors must be a non-empty object")
            weights: dict[str, float] = {}
            for raw_factor, raw_weight in factors.items():
                factor = str(raw_factor).strip().lower()
                if factor not in SCORING_FACTORS:
                    raise PolicyError(f"scoring_families.{name}.{family} references unknown factor {factor}")
                if factor in seen_factors:
                    raise PolicyError(
                        f"scoring_families.{name} assigns factor {factor} to more than one family"
                    )
                seen_factors.add(factor)
                weights[factor] = _fraction(raw_weight, f"scoring_families.{name}.{family}.factors.{factor}")
            if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
                raise PolicyError(f"scoring_families.{name}.{family}.factors weights must sum to 1")
            family_factors[family] = weights
        if not math.isclose(sum(family_weights.values()), 1.0, abs_tol=1e-9):
            raise PolicyError(f"scoring_families.{name} family weights must sum to 1")
        profile_factors = {factor for factor, weight in profiles[name].items() if weight > 0}
        if seen_factors != profile_factors:
            raise PolicyError(
                f"scoring_families.{name} must cover exactly the positive-weight factors of "
                f"profile {name}"
            )
        parsed[name] = {
            family: {"weight": family_weights[family], "factors": dict(family_factors[family])}
            for family in family_weights
        }
    return parsed


def _parse_regime_model(value: Any) -> dict[str, Any]:
    if value is None:
        return {**_DEFAULT_REGIME_MODEL, "domain_weights": dict(_DEFAULT_REGIME_MODEL["domain_weights"]),
                "severity": {name: dict(states) for name, states in _DEFAULT_REGIME_MODEL["severity"].items()}}
    if not isinstance(value, dict):
        raise PolicyError("regime_model must be an object")
    _unknown_fields(value, _REGIME_MODEL_FIELDS, "regime_model")
    if set(value) != _REGIME_MODEL_FIELDS:
        raise PolicyError("regime_model fields are incomplete")
    mode = str(value["mode"]).strip().lower()
    if mode not in _REGIME_MODEL_MODES:
        raise PolicyError("regime_model.mode must be vote_count or weighted")
    parsed: dict[str, Any] = {"mode": mode}
    parsed["normal_max"] = _fraction(value["normal_max"], "regime_model.normal_max")
    parsed["defensive_max"] = _fraction(value["defensive_max"], "regime_model.defensive_max")
    if not 0 < parsed["normal_max"] < parsed["defensive_max"] <= 1:
        raise PolicyError("regime_model thresholds must satisfy 0 < normal_max < defensive_max <= 1")
    weights = value["domain_weights"]
    if not isinstance(weights, dict) or set(weights) != set(_REGIME_MODEL_DOMAINS):
        raise PolicyError("regime_model.domain_weights must contain exactly " + ", ".join(_REGIME_MODEL_DOMAINS))
    parsed_weights = {name: _fraction(weights[name], f"regime_model.domain_weights.{name}") for name in _REGIME_MODEL_DOMAINS}
    if not math.isclose(sum(parsed_weights.values()), 1.0, abs_tol=1e-9):
        raise PolicyError("regime_model.domain_weights must sum to 1")
    parsed["domain_weights"] = parsed_weights
    severity = value["severity"]
    if not isinstance(severity, dict) or set(severity) != set(_REGIME_MODEL_DOMAINS):
        raise PolicyError("regime_model.severity must contain exactly " + ", ".join(_REGIME_MODEL_DOMAINS))
    parsed_severity: dict[str, dict[str, float]] = {}
    for name in _REGIME_MODEL_DOMAINS:
        states = severity[name]
        expected = _REGIME_SEVERITY_STATES[name]
        if not isinstance(states, dict) or set(states) != expected:
            raise PolicyError(
                f"regime_model.severity.{name} must contain exactly " + ", ".join(sorted(expected))
            )
        parsed_severity[name] = {
            state: _fraction(states[state], f"regime_model.severity.{name}.{state}")
            for state in expected
        }
    parsed["severity"] = parsed_severity
    return parsed


def _parse_core_allocation(value: Any) -> dict[str, Any]:
    if value is None:
        raise PolicyError("core_allocation is required")
    if not isinstance(value, dict):
        raise PolicyError("core_allocation must be an object")
    _unknown_fields(value, _CORE_ALLOCATION_FIELDS, "core_allocation")
    if set(value) != _CORE_ALLOCATION_FIELDS:
        raise PolicyError("core_allocation fields are incomplete")

    anchor = value["anchor"]
    if not isinstance(anchor, dict) or set(anchor) != _CORE_ANCHOR_FIELDS:
        raise PolicyError("core_allocation.anchor must contain BTC and ETH")
    parsed_anchor = {
        symbol: _fraction(anchor[symbol], f"core_allocation.anchor.{symbol}")
        for symbol in _CORE_ANCHOR_FIELDS
    }
    if not math.isclose(sum(parsed_anchor.values()), 1.0, abs_tol=1e-9):
        raise PolicyError("core_allocation.anchor weights must sum to 1")

    eth = value["eth"]
    if not isinstance(eth, dict):
        raise PolicyError("core_allocation.eth must be an object")
    _unknown_fields(eth, _CORE_ETH_FIELDS, "core_allocation.eth")
    if set(eth) != _CORE_ETH_FIELDS:
        raise PolicyError("core_allocation.eth fields are incomplete")
    parsed_eth = {
        key: _number(eth[key], f"core_allocation.eth.{key}", minimum=0.0, maximum=100.0)
        for key in _CORE_ETH_FIELDS
    }
    if not (
        parsed_eth["relative_reduce_below_score"]
        < parsed_eth["relative_increase_min_score"]
        <= parsed_eth["hold_min_score"]
        <= parsed_eth["increase_min_score"]
    ):
        raise PolicyError("core_allocation.eth score thresholds are not ordered")
    parsed_eth["max_core_sleeve_share"] = _fraction(
        eth["max_core_sleeve_share"], "core_allocation.eth.max_core_sleeve_share"
    )

    confidence = value["confidence_multipliers"]
    if not isinstance(confidence, dict) or set(confidence) != _CORE_CONFIDENCE_FIELDS:
        raise PolicyError("core_allocation.confidence_multipliers must contain HIGH, MEDIUM, and LOW")
    parsed_confidence = {
        key: _fraction(confidence[key], f"core_allocation.confidence_multipliers.{key}")
        for key in _CORE_CONFIDENCE_FIELDS
    }
    if not (
        parsed_confidence["HIGH"]
        >= parsed_confidence["MEDIUM"]
        >= parsed_confidence["LOW"]
    ):
        raise PolicyError("core_allocation confidence multipliers must be monotonic")

    return {
        "anchor": parsed_anchor,
        "eth": parsed_eth,
        "confidence_multipliers": parsed_confidence,
    }


@dataclass(frozen=True)
class RegimeLimits:
    stablecoin_target: float
    satellite_max: float
    core_risky_min: float
    single_asset_max: float


@dataclass(frozen=True)
class Policy:
    investment_horizon_months: tuple[int, int]
    core_symbols: tuple[str, ...]
    satellite_symbols: tuple[str, ...]
    stable_symbols: tuple[str, ...]
    excluded_symbols: tuple[str, ...]
    min_stablecoin_weight: float
    max_portfolio_drawdown: float
    benchmarks: Mapping[str, Mapping[str, Any]]
    rebalance: Mapping[str, float]
    scoring_profiles: Mapping[str, Mapping[str, float]]
    asset_scoring_profiles: Mapping[str, str]
    scoring: Mapping[str, float]
    regimes: Mapping[str, RegimeLimits]
    allocation: Mapping[str, Any]
    event_risk_multipliers: Mapping[str, float]
    stress_scenarios: Mapping[str, Mapping[str, float]] = dataclass_field(default_factory=dict)
    risk_engine: Mapping[str, Any] = dataclass_field(default_factory=dict)
    risk_tier_estimation: Mapping[str, Any] = dataclass_field(default_factory=dict)
    dynamic_universe: Mapping[str, Any] = dataclass_field(default_factory=dict)
    core_allocation: Mapping[str, Any] = dataclass_field(default_factory=dict)
    execution: Mapping[str, Any] = dataclass_field(default_factory=dict)
    volume_profile: Mapping[str, Any] = dataclass_field(default_factory=dict)
    factor_rules: Mapping[str, Any] = dataclass_field(default_factory=dict)
    positioning: Mapping[str, Any] = dataclass_field(default_factory=dict)
    btc_cycle: Mapping[str, Any] = dataclass_field(default_factory=dict)
    execution_overlay: Mapping[str, Any] = dataclass_field(default_factory=dict)
    events: Mapping[str, Any] = dataclass_field(default_factory=dict)
    chain_liveness: Mapping[str, Any] = dataclass_field(default_factory=dict)
    confidence: Mapping[str, Any] = dataclass_field(default_factory=dict)
    freshness_policy: Mapping[str, Any] = dataclass_field(default_factory=dict)
    source_quality: Mapping[str, Any] = dataclass_field(default_factory=dict)
    event_severity: Mapping[str, Any] = dataclass_field(default_factory=dict)
    nav_history: Mapping[str, Any] = dataclass_field(default_factory=dict)
    high_impact_review: Mapping[str, float] = dataclass_field(default_factory=dict)
    regime_transitions: Mapping[str, Any] = dataclass_field(default_factory=dict)
    regime_model: Mapping[str, Any] = dataclass_field(default_factory=dict)
    drawdown_budget_overlay: Mapping[str, Any] = dataclass_field(default_factory=dict)
    scoring_families: Mapping[str, Mapping[str, Mapping[str, Any]]] = dataclass_field(default_factory=dict)

    def scoring_profile_name(self, symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise PolicyError("symbol must be a non-empty string")
        return self.asset_scoring_profiles.get(symbol.strip().upper(), "default")

    def scoring_profile(self, symbol: str) -> Mapping[str, float]:
        name = self.scoring_profile_name(symbol)
        try:
            return self.scoring_profiles[name]
        except KeyError as exc:
            raise PolicyError(f"unknown scoring profile: {name}") from exc

    @property
    def canonical_hash(self) -> str:
        return policy_hash(self)

    def is_excluded(self, symbol: str) -> bool:
        if not isinstance(symbol, str) or not symbol.strip():
            raise PolicyError("symbol must be a non-empty string")
        return symbol.strip().upper() in self.excluded_symbols

    def classify(self, symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise PolicyError("symbol must be a non-empty string")
        normalized = symbol.strip().upper()
        if normalized in self.stable_symbols:
            return "cash" if normalized in {"USD", "CASH"} else "stablecoin"
        if normalized in self.core_symbols:
            return "core"
        if normalized in self.satellite_symbols:
            return "satellite"
        return "other"

    def regime(self, name: str) -> RegimeLimits:
        name = name.upper()
        try:
            return self.regimes[name]
        except KeyError as exc:
            raise PolicyError(f"unknown market regime: {name}") from exc

    def as_dict(self) -> dict[str, Any]:
        result = {
            "investment_horizon_months": {
                "min": self.investment_horizon_months[0],
                "max": self.investment_horizon_months[1],
            },
            "universe": {
                "core": list(self.core_symbols),
                "satellites": list(self.satellite_symbols),
                "stable": list(self.stable_symbols),
                "excluded": list(self.excluded_symbols),
            },
            "risk": {
                "min_stablecoin_weight": self.min_stablecoin_weight,
                "max_portfolio_drawdown": self.max_portfolio_drawdown,
                "drawdown_budget_overlay": _copy_mapping(self.drawdown_budget_overlay),
            },
            "benchmarks": {name: dict(weights) for name, weights in self.benchmarks.items()},
            "rebalance": _copy_mapping(self.rebalance),
            "scoring": dict(self.scoring),
            "regimes": {
                name: {
                    "stablecoin_target": limits.stablecoin_target,
                    "satellite_max": limits.satellite_max,
                    "core_risky_min": limits.core_risky_min,
                    "single_asset_max": limits.single_asset_max,
                }
                for name, limits in self.regimes.items()
            },
            "allocation": dict(self.allocation),
        }
        result["stress_scenarios"] = {
            name: dict(returns) for name, returns in self.stress_scenarios.items()
        }
        if self.risk_engine:
            result["risk_engine"] = _copy_mapping(self.risk_engine)
        if self.risk_tier_estimation:
            result["risk_tier_estimation"] = _copy_mapping(self.risk_tier_estimation)
        if self.dynamic_universe:
            result["dynamic_universe"] = _copy_mapping(self.dynamic_universe)
        result["scoring_profiles"] = {
            name: dict(weights) for name, weights in self.scoring_profiles.items()
        }
        result["asset_scoring_profiles"] = dict(self.asset_scoring_profiles)
        result["event_risk_multipliers"] = dict(self.event_risk_multipliers)
        if self.volume_profile:
            result["volume_profile"] = dict(self.volume_profile)
        if self.factor_rules:
            result["factor_rules"] = {
                name: dict(value) if isinstance(value, Mapping) else value
                for name, value in self.factor_rules.items()
            }
        if self.execution:
            execution = dict(self.execution)
            result["execution"] = execution
        if self.positioning:
            result["positioning"] = _copy_mapping(self.positioning)
        if self.btc_cycle:
            result["btc_cycle"] = _copy_mapping(self.btc_cycle)
        if self.execution_overlay:
            result["execution_overlay"] = _copy_mapping(self.execution_overlay)
        if self.events:
            result["events"] = _copy_mapping(self.events)
        if self.chain_liveness:
            result["chain_liveness"] = _copy_mapping(self.chain_liveness)
        if self.core_allocation:
            result["core_allocation"] = _copy_mapping(self.core_allocation)
        if self.confidence:
            result["confidence"] = _copy_mapping(self.confidence)
        if self.freshness_policy:
            result["freshness_policy"] = _copy_mapping(self.freshness_policy)
        if self.source_quality:
            result["source_quality"] = _copy_mapping(self.source_quality)
        if self.event_severity:
            result["event_severity"] = _copy_mapping(self.event_severity)
        if self.nav_history:
            result["nav_history"] = _copy_mapping(self.nav_history)
        if self.high_impact_review:
            result["high_impact_review"] = dict(self.high_impact_review)
        if self.regime_transitions:
            result["regime_transitions"] = dict(self.regime_transitions)
        if self.regime_model:
            result["regime_model"] = _copy_mapping(self.regime_model)
        # Required top-level field: serialize even when empty so canonical
        # policy records round-trip through as_dict()/policy_from_mapping.
        result["scoring_families"] = _copy_mapping(self.scoring_families)
        return result

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> "Policy":
        if overrides is None:
            return self
        if not isinstance(overrides, Mapping):
            raise PolicyError("config must be an object")
        _unknown_fields(overrides, _OVERRIDE_FIELDS, "config")
        values = {
            "core_symbols": list(self.core_symbols),
            "satellite_symbols": list(self.satellite_symbols),
            "stable_symbols": list(self.stable_symbols),
            "excluded_symbols": list(self.excluded_symbols),
            "min_stablecoin_weight": self.min_stablecoin_weight,
            "max_portfolio_drawdown": self.max_portfolio_drawdown,
        }
        for field in _OVERRIDE_FIELDS:
            if field in overrides:
                values[field] = overrides[field]
        core = _symbols(values["core_symbols"], "config.core_symbols")
        satellites = _symbols(values["satellite_symbols"], "config.satellite_symbols")
        stable = _symbols(values["stable_symbols"], "config.stable_symbols")
        excluded = _symbols(values["excluded_symbols"], "config.excluded_symbols")
        _check_overlaps(core, satellites, stable, excluded)
        return _replace_policy(
            self,
            core_symbols=core,
            satellite_symbols=satellites,
            stable_symbols=stable,
            excluded_symbols=excluded,
            min_stablecoin_weight=_fraction(
                values["min_stablecoin_weight"], "config.min_stablecoin_weight"
            ),
            max_portfolio_drawdown=_fraction(
                values["max_portfolio_drawdown"],
                "config.max_portfolio_drawdown",
                exclusive_minimum=True,
            ),
        )


def _replace_policy(policy: Policy, **changes: Any) -> Policy:
    values = policy.__dict__ | changes
    return Policy(**values)


def _check_overlaps(
    core: tuple[str, ...],
    satellites: tuple[str, ...],
    stable: tuple[str, ...],
    excluded: tuple[str, ...] = (),
) -> None:
    owners: dict[str, str] = {}
    for name, symbols in (
        ("core", core),
        ("satellites", satellites),
        ("stable", stable),
        ("excluded", excluded),
    ):
        for symbol in symbols:
            if symbol in owners:
                raise PolicyError(f"symbol {symbol} appears in both {owners[symbol]} and {name}")
            owners[symbol] = name


def _integer_list(value: Any, name: str, *, exact: tuple[int, ...] | None = None) -> list[int]:
    if not isinstance(value, list) or not value:
        raise PolicyError(f"{name} must be a non-empty list of integers")
    result = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise PolicyError(f"{name}[{index}] must be a positive integer")
        result.append(item)
    if result != sorted(set(result)):
        raise PolicyError(f"{name} must be strictly increasing")
    if exact is not None and tuple(result) != exact:
        raise PolicyError(f"{name} must equal {list(exact)}")
    return result


def _parse_volume_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("volume_profile must be an object")
    _unknown_fields(value, _VOLUME_PROFILE_FIELDS, "volume_profile")
    if set(value) != _VOLUME_PROFILE_FIELDS:
        raise PolicyError("volume_profile fields are incomplete")
    if not isinstance(value["enabled"], bool):
        raise PolicyError("volume_profile.enabled must be boolean")
    preferred = str(value["preferred_timeframe"]).strip().upper()
    fallback = str(value["fallback_timeframe"]).strip().upper()
    if preferred not in {"1H", "4H"}:
        raise PolicyError("volume_profile.preferred_timeframe must be 1H or 4H")
    if fallback not in {"1H", "4H", "1D"}:
        raise PolicyError("volume_profile.fallback_timeframe must be 1H, 4H, or 1D")
    lookback_days = _integer_list(value["lookback_days"], "volume_profile.lookback_days")
    preferred_lookback = _number(
        value["preferred_lookback_days"],
        "volume_profile.preferred_lookback_days",
        minimum=1,
    )
    if not preferred_lookback.is_integer() or int(preferred_lookback) not in lookback_days:
        raise PolicyError("volume_profile.preferred_lookback_days must be one of lookback_days")
    price_bins = _number(value["price_bins"], "volume_profile.price_bins", minimum=2)
    if not price_bins.is_integer() or price_bins > 512:
        raise PolicyError("volume_profile.price_bins must be an integer from 2 to 512")
    value_area = _fraction(value["value_area_fraction"], "volume_profile.value_area_fraction", exclusive_minimum=True)
    hvn_percentile = _fraction(value["hvn_percentile"], "volume_profile.hvn_percentile", exclusive_minimum=True)
    max_hvn_nodes = _number(value["max_hvn_nodes"], "volume_profile.max_hvn_nodes", minimum=1)
    if not max_hvn_nodes.is_integer():
        raise PolicyError("volume_profile.max_hvn_nodes must be a positive integer")
    separation = _number(
        value["minimum_node_separation_atr"],
        "volume_profile.minimum_node_separation_atr",
        minimum=0.0,
    )
    width = _number(value["zone_half_width_atr"], "volume_profile.zone_half_width_atr", minimum=0.0)
    if separation <= 0 or width <= 0:
        raise PolicyError("volume_profile ATR settings must be > 0")
    if not isinstance(value["allow_daily_approximation"], bool):
        raise PolicyError("volume_profile.allow_daily_approximation must be boolean")
    confidence_cap = str(value["daily_approximation_confidence_cap"]).strip().upper()
    if confidence_cap not in {"LOW", "MEDIUM"}:
        raise PolicyError("volume_profile.daily_approximation_confidence_cap must be LOW or MEDIUM")
    return {
        "enabled": value["enabled"],
        "preferred_timeframe": preferred,
        "fallback_timeframe": fallback,
        "lookback_days": lookback_days,
        "preferred_lookback_days": int(preferred_lookback),
        "price_bins": int(price_bins),
        "value_area_fraction": value_area,
        "hvn_percentile": hvn_percentile,
        "max_hvn_nodes": int(max_hvn_nodes),
        "minimum_node_separation_atr": separation,
        "zone_half_width_atr": width,
        "allow_daily_approximation": value["allow_daily_approximation"],
        "daily_approximation_confidence_cap": confidence_cap,
    }


def _parse_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("execution must be an object")
    _unknown_fields(value, _EXECUTION_FIELDS, "execution")
    missing = _EXECUTION_FIELDS - set(value)
    if missing:
        raise PolicyError(f"execution fields are incomplete: {', '.join(sorted(missing))}")
    timeframe = value["timeframe"]
    if not isinstance(timeframe, str) or timeframe.strip().upper() != "1D":
        raise PolicyError("execution.timeframe must be 1D")
    preferred = _number(value["preferred_history_days"], "execution.preferred_history_days", minimum=1)
    minimum = _number(value["minimum_history_days"], "execution.minimum_history_days", minimum=1)
    if not preferred.is_integer() or not minimum.is_integer() or minimum > preferred:
        raise PolicyError("execution history days must be ordered positive integers")
    moving_average_windows = _integer_list(
        value["moving_average_windows"],
        "execution.moving_average_windows",
        exact=(20, 50, 100, 200),
    )
    realized_windows = _integer_list(
        value["realized_volatility_windows"],
        "execution.realized_volatility_windows",
        exact=(30, 90),
    )
    def positive_int(raw: Any, name: str) -> int:
        number = _number(raw, name, minimum=1)
        if not number.is_integer():
            raise PolicyError(f"{name} must be a positive integer")
        return int(number)

    max_tranches = positive_int(value["max_tranches"], "execution.max_tranches")
    if max_tranches > 3:
        raise PolicyError("execution.max_tranches must be <= 3")
    zone_half_width = _number(value["zone_half_width_atr"], "execution.zone_half_width_atr", minimum=0.0)
    separation = _number(
        value["minimum_zone_separation_atr"],
        "execution.minimum_zone_separation_atr",
        minimum=0.0,
    )
    if zone_half_width <= 0 or separation <= 0:
        raise PolicyError("execution ATR zone settings must be > 0")
    maximum_zone_span = _number(
        value["maximum_zone_span_atr"],
        "execution.maximum_zone_span_atr",
        minimum=0.0,
    )
    if maximum_zone_span <= 0:
        raise PolicyError("execution.maximum_zone_span_atr must be > 0")
    maximum_spot_gap = _number(
        value["maximum_spot_close_gap_atr"],
        "execution.maximum_spot_close_gap_atr",
        minimum=0.0,
    )
    if maximum_spot_gap <= 0:
        raise PolicyError("execution.maximum_spot_close_gap_atr must be > 0")

    maximum_lag_number = _number(
        value["maximum_daily_candle_lag_days"],
        "execution.maximum_daily_candle_lag_days",
        minimum=0.0,
    )
    if not maximum_lag_number.is_integer():
        raise PolicyError("execution.maximum_daily_candle_lag_days must be a non-negative integer")
    maximum_lag = int(maximum_lag_number)
    maximum_gap = _number(value["maximum_daily_gap_days"], "execution.maximum_daily_gap_days", minimum=0.0)
    if not maximum_gap.is_integer():
        raise PolicyError("execution.maximum_daily_gap_days must be a non-negative integer")
    coverage_ratio = _fraction(value["minimum_daily_coverage_ratio"], "execution.minimum_daily_coverage_ratio", exclusive_minimum=True)

    volatility = value["volatility_atr_percent"]
    if not isinstance(volatility, dict):
        raise PolicyError("execution.volatility_atr_percent must be an object")
    _unknown_fields(volatility, _VOLATILITY_FIELDS, "execution.volatility_atr_percent")
    if set(volatility) != _VOLATILITY_FIELDS:
        raise PolicyError("execution.volatility_atr_percent fields are incomplete")
    volatility_limits = {
        key: _number(item, f"execution.volatility_atr_percent.{key}", minimum=0.0, maximum=1.0)
        for key, item in volatility.items()
    }
    if not (
        0 < volatility_limits["low_max"] < volatility_limits["normal_max"] < volatility_limits["high_max"]
    ):
        raise PolicyError("execution volatility thresholds must be strictly ordered and > 0")

    max_initial = value["max_initial_tranche"]
    if not isinstance(max_initial, dict) or set(max_initial) != set(_REGIMES):
        raise PolicyError("execution.max_initial_tranche must contain all regimes")
    max_initial_parsed = {key: _fraction(item, f"execution.max_initial_tranche.{key}", exclusive_minimum=True) for key, item in max_initial.items()}
    if not (
        max_initial_parsed["NORMAL"] >= max_initial_parsed["DEFENSIVE"] >= max_initial_parsed["CAPITAL_PRESERVATION"]
    ):
        raise PolicyError("execution.max_initial_tranche must not increase in worse regimes")

    confidence_factor = value["confidence_deployment_factor"]
    if not isinstance(confidence_factor, dict) or set(confidence_factor) != {"HIGH", "MEDIUM", "LOW"}:
        raise PolicyError("execution.confidence_deployment_factor must contain HIGH, MEDIUM, and LOW")
    parsed_confidence_factor = {
        key: _fraction(item, f"execution.confidence_deployment_factor.{key}")
        for key, item in confidence_factor.items()
    }
    composition_mode = str(value["deployment_factor_composition"]).strip().lower()
    if composition_mode not in _DEPLOYMENT_COMPOSITION_MODES:
        raise PolicyError(
            "execution.deployment_factor_composition must be one of "
            + ", ".join(sorted(_DEPLOYMENT_COMPOSITION_MODES))
        )

    templates = value["tranche_templates"]
    template_names = {"NORMAL_LOW_VOL", "NORMAL_HIGH_VOL", "DEFENSIVE", "CAPITAL_PRESERVATION"}
    if not isinstance(templates, dict) or set(templates) != template_names:
        raise PolicyError("execution.tranche_templates must contain the configured template names")
    parsed_templates: dict[str, list[float]] = {}
    for name, raw_template in templates.items():
        if not isinstance(raw_template, list) or not 1 <= len(raw_template) <= max_tranches:
            raise PolicyError(f"execution.tranche_templates.{name} must contain 1 to max_tranches fractions")
        parsed_template = [_fraction(item, f"execution.tranche_templates.{name}[{index}]", exclusive_minimum=True) for index, item in enumerate(raw_template)]
        if not math.isclose(sum(parsed_template), 1.0, abs_tol=1e-9):
            raise PolicyError(f"execution.tranche_templates.{name} must sum to 1")
        parsed_templates[name] = parsed_template

    breakout = value["breakout"]
    if not isinstance(breakout, dict):
        raise PolicyError("execution.breakout must be an object")
    _unknown_fields(breakout, _BREAKOUT_FIELDS, "execution.breakout")
    if set(breakout) != _BREAKOUT_FIELDS:
        raise PolicyError("execution.breakout fields are incomplete")
    parsed_breakout = {
        "minimum_relative_volume": _number(
            breakout["minimum_relative_volume"],
            "execution.breakout.minimum_relative_volume",
            minimum=0.0,
        ),
        "max_atr_extension": _number(
            breakout["max_atr_extension"],
            "execution.breakout.max_atr_extension",
            minimum=0.0,
        ),
        "max_initial_tranche": _fraction(
            breakout["max_initial_tranche"],
            "execution.breakout.max_initial_tranche",
            exclusive_minimum=True,
        ),
    }
    if parsed_breakout["minimum_relative_volume"] <= 0 or parsed_breakout["max_atr_extension"] <= 0:
        raise PolicyError("execution breakout thresholds must be > 0")
    if parsed_breakout["max_initial_tranche"] > max_initial_parsed["NORMAL"]:
        raise PolicyError("execution.breakout.max_initial_tranche must not exceed NORMAL max_initial_tranche")
    resting_tolerance = _number(
        value["resting_order_match_tolerance"],
        "execution.resting_order_match_tolerance",
        minimum=0.0,
        maximum=0.1,
    )
    if resting_tolerance <= 0:
        raise PolicyError("execution.resting_order_match_tolerance must be > 0")
    resting_full = _fraction(
        value["resting_order_full_fraction"],
        "execution.resting_order_full_fraction",
        exclusive_minimum=True,
    )
    resting_partial = _fraction(
        value["resting_order_partial_fraction"],
        "execution.resting_order_partial_fraction",
        exclusive_minimum=True,
    )
    if resting_partial >= resting_full:
        raise PolicyError("execution resting-order fractions must satisfy partial < full")
    zone_quality = value["zone_quality"]
    if not isinstance(zone_quality, dict):
        raise PolicyError("execution.zone_quality must be an object")
    _unknown_fields(zone_quality, _ZONE_QUALITY_FIELDS, "execution.zone_quality")
    if set(zone_quality) != _ZONE_QUALITY_FIELDS:
        raise PolicyError("execution.zone_quality fields are incomplete")
    parsed_zone_quality = {
        key: _number(item, f"execution.zone_quality.{key}", minimum=0.0, maximum=100.0)
        for key, item in zone_quality.items()
    }
    if parsed_zone_quality["minimum_for_entry"] > parsed_zone_quality["high_quality"]:
        raise PolicyError("execution.zone_quality minimum_for_entry must not exceed high_quality")
    if not (
        parsed_confidence_factor["HIGH"]
        >= parsed_confidence_factor["MEDIUM"]
        >= parsed_confidence_factor["LOW"]
    ):
        raise PolicyError("execution.confidence_deployment_factor must be monotonic HIGH >= MEDIUM >= LOW")
    return {
        "timeframe": "1D",
        "preferred_history_days": int(preferred),
        "minimum_history_days": int(minimum),
        "max_fetched_age_days": positive_int(value["max_fetched_age_days"], "execution.max_fetched_age_days"),
        "maximum_daily_candle_lag_days": maximum_lag,
        "minimum_daily_coverage_ratio": coverage_ratio,
        "maximum_daily_gap_days": int(maximum_gap),
        "moving_average_windows": moving_average_windows,
        "atr_period": positive_int(value["atr_period"], "execution.atr_period"),
        "realized_volatility_windows": realized_windows,
        "volatility_annualization_days": positive_int(
            value["volatility_annualization_days"],
            "execution.volatility_annualization_days",
        ),
        "volume_average_window": positive_int(value["volume_average_window"], "execution.volume_average_window"),
        "swing_window": positive_int(value["swing_window"], "execution.swing_window"),
        "max_tranches": max_tranches,
        "zone_half_width_atr": zone_half_width,
        "minimum_zone_separation_atr": separation,
        "maximum_zone_span_atr": maximum_zone_span,
        "maximum_spot_close_gap_atr": maximum_spot_gap,
        "zone_quality": parsed_zone_quality,
        "volatility_atr_percent": volatility_limits,
        "confidence_deployment_factor": parsed_confidence_factor,
        "deployment_factor_composition": composition_mode,
        "max_initial_tranche": max_initial_parsed,
        "tranche_templates": parsed_templates,
        "breakout": parsed_breakout,
        "resting_order_match_tolerance": resting_tolerance,
        "resting_order_full_fraction": resting_full,
        "resting_order_partial_fraction": resting_partial,
    }


def _parse_factor_rules(
    value: Any
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("factor_rules must be an object")
    _unknown_fields(value, _FACTOR_RULE_FIELDS, "factor_rules")
    if set(value) != _FACTOR_RULE_FIELDS:
        raise PolicyError("factor_rules must contain trend, relative_strength, and flows")

    trend = value["trend"]
    if not isinstance(trend, dict):
        raise PolicyError("factor_rules.trend must be an object")
    _unknown_fields(trend, _TREND_RULE_FIELDS, "factor_rules.trend")
    if not (_TREND_RULE_FIELDS - {"volume_weakness_confirmation_closes"}) <= set(trend):
        raise PolicyError("factor_rules.trend fields are incomplete")
    base_score = _number(
        trend["base_score"], "factor_rules.trend.base_score", minimum=0.0, maximum=100.0
    )
    raw_ma_points = trend["ma_points"]
    if not isinstance(raw_ma_points, dict):
        raise PolicyError("factor_rules.trend.ma_points must be an object")
    _unknown_fields(raw_ma_points, _TREND_MA_FIELDS, "factor_rules.trend.ma_points")
    if set(raw_ma_points) != _TREND_MA_FIELDS:
        raise PolicyError("factor_rules.trend.ma_points must contain 20, 50, 100, and 200")
    ma_points = {
        key: _number(raw_ma_points[key], f"factor_rules.trend.ma_points.{key}", minimum=0.0)
        for key in _TREND_MA_FIELDS
    }
    if sum(ma_points[key] for key in ("20", "50", "100")) <= ma_points["200"]:
        raise PolicyError("factor_rules.trend short-term MA points must exceed MA200 authority")
    alignment_windows = _integer_list(
        trend["alignment_windows"],
        "factor_rules.trend.alignment_windows",
        exact=(20, 50, 100),
    )
    alignment_points = _number(
        trend["alignment_points"], "factor_rules.trend.alignment_points", minimum=0.0
    )
    momentum = trend["momentum"]
    if not isinstance(momentum, dict):
        raise PolicyError("factor_rules.trend.momentum must be an object")
    _unknown_fields(momentum, _TREND_MOMENTUM_FIELDS, "factor_rules.trend.momentum")
    if set(momentum) != _TREND_MOMENTUM_FIELDS:
        raise PolicyError("factor_rules.trend.momentum fields are incomplete")
    momentum_weights = _weighted_map(
        momentum["horizon_weights"], "factor_rules.trend.momentum.horizon_weights"
    )
    if set(momentum_weights) != set(_TREND_HORIZONS):
        raise PolicyError("factor_rules.trend.momentum.horizon_weights must contain 30d, 90d, and 180d")

    def horizon_map(raw: Any, name: str) -> dict[str, float]:
        if not isinstance(raw, dict) or set(raw) != set(_TREND_HORIZONS):
            raise PolicyError(f"{name} must contain 30d, 90d, and 180d")
        return {
            key: _number(raw[key], f"{name}.{key}", minimum=0.0)
            for key in _TREND_HORIZONS
        }

    neutral_abs = horizon_map(momentum["neutral_abs"], "factor_rules.trend.momentum.neutral_abs")
    saturation_abs = horizon_map(momentum["saturation_abs"], "factor_rules.trend.momentum.saturation_abs")
    for horizon in _TREND_HORIZONS:
        if saturation_abs[horizon] <= neutral_abs[horizon]:
            raise PolicyError(
                f"factor_rules.trend.momentum.saturation_abs.{horizon} must exceed neutral_abs"
            )
    max_points = _number(
        momentum["max_points"], "factor_rules.trend.momentum.max_points", minimum=0.0
    )
    if max_points <= 0:
        raise PolicyError("factor_rules.trend.momentum.max_points must be > 0")
    support_points = _number(
        trend["support_points"], "factor_rules.trend.support_points", minimum=0.0
    )
    volume_points = _number(
        trend["volume_points"], "factor_rules.trend.volume_points", minimum=0.0
    )
    # Optional since 2026-09: absence reproduces the legacy immediate
    # deduction, and embedded resolved policies that predate the field keep
    # their own shape (and hash). The engine reads it with a default of 1.
    volume_closes = trend.get("volume_weakness_confirmation_closes", 1)
    if isinstance(volume_closes, bool) or not isinstance(volume_closes, int) or volume_closes < 1:
        raise PolicyError("factor_rules.trend.volume_weakness_confirmation_closes must be an integer >= 1")
    extension_threshold = _number(
        trend["extension_threshold_atr"],
        "factor_rules.trend.extension_threshold_atr",
        minimum=0.0,
    )
    extension_penalty = _number(
        trend["extension_penalty"], "factor_rules.trend.extension_penalty", minimum=0.0
    )
    if extension_threshold <= 0:
        raise PolicyError("factor_rules.trend.extension_threshold_atr must be > 0")
    parsed_trend = {
        "base_score": base_score,
        "ma_points": ma_points,
        "alignment_windows": alignment_windows,
        "alignment_points": alignment_points,
        "momentum": {
            "horizon_weights": momentum_weights,
            "neutral_abs": neutral_abs,
            "saturation_abs": saturation_abs,
            "max_points": max_points,
        },
        "support_points": support_points,
        "volume_points": volume_points,
        "extension_threshold_atr": extension_threshold,
        "extension_penalty": extension_penalty,
    }
    if "volume_weakness_confirmation_closes" in trend:
        parsed_trend["volume_weakness_confirmation_closes"] = volume_closes

    relative = value["relative_strength"]
    if not isinstance(relative, dict):
        raise PolicyError("factor_rules.relative_strength must be an object")
    relative_fields = _RELATIVE_RULE_FIELDS
    _unknown_fields(relative, relative_fields, "factor_rules.relative_strength")
    if set(relative) != relative_fields:
        raise PolicyError("factor_rules.relative_strength fields are incomplete")
    horizon_weights = _weighted_map(relative["horizon_weights"], "factor_rules.relative_strength.horizon_weights")
    expected_horizons = {"30d", "90d", "180d"}
    if set(horizon_weights) != expected_horizons:
        names = ", ".join(sorted(expected_horizons))
        raise PolicyError(f"factor_rules.relative_strength.horizon_weights must contain {names}")
    neutral_band = _number(
        relative["risk_adjusted_neutral_band"],
        "factor_rules.relative_strength.risk_adjusted_neutral_band",
        minimum=0.0,
    )
    saturation = _number(
        relative["risk_adjusted_saturation"],
        "factor_rules.relative_strength.risk_adjusted_saturation",
        minimum=neutral_band,
    )
    if saturation <= neutral_band:
        raise PolicyError(
            "factor_rules.relative_strength.risk_adjusted_saturation must exceed neutral_band"
        )
    parsed_relative = {
        "horizon_weights": horizon_weights,
        "risk_adjusted_neutral_band": neutral_band,
        "risk_adjusted_saturation": saturation,
    }

    flows = value["flows"]
    if not isinstance(flows, dict):
        raise PolicyError("factor_rules.flows must be an object")
    flow_fields = _FLOW_RULE_FIELDS
    _unknown_fields(flows, flow_fields, "factor_rules.flows")
    if set(flows) != flow_fields:
        raise PolicyError("factor_rules.flows fields are incomplete")
    neutral_abs_max = _number(
        flows["neutral_abs_max"], "factor_rules.flows.neutral_abs_max", minimum=0.0
    )
    strong_abs = _number(flows["strong_abs"], "factor_rules.flows.strong_abs", minimum=neutral_abs_max)
    if strong_abs <= neutral_abs_max:
        raise PolicyError("factor_rules.flows.strong_abs must exceed neutral_abs_max")
    supply_weights = _weighted_map(
        flows["supply_change_horizon_weights"],
        "factor_rules.flows.supply_change_horizon_weights",
    )
    if set(supply_weights) != set(_FLOW_SUPPLY_HORIZONS):
        raise PolicyError(
            "factor_rules.flows.supply_change_horizon_weights must contain 7d, 30d, and 90d"
        )
    parsed_flows = {
        "neutral_abs_max": neutral_abs_max,
        "strong_abs": strong_abs,
        "supply_change_horizon_weights": {horizon: supply_weights[horizon] for horizon in _FLOW_SUPPLY_HORIZONS},
    }
    return {
        "trend": parsed_trend,
        "relative_strength": parsed_relative,
        "flows": parsed_flows,
    }


def _positive_integer(value: Any, name: str) -> int:
    number = _number(value, name, minimum=1)
    if not number.is_integer():
        raise PolicyError(f"{name} must be a positive integer")
    return int(number)


def _parse_positioning(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("positioning must be an object")
    _unknown_fields(value, _POSITIONING_FIELDS, "positioning")
    if not _POSITIONING_REQUIRED_FIELDS.issubset(value):
        raise PolicyError("positioning fields are incomplete")
    if not isinstance(value["enabled"], bool):
        raise PolicyError("positioning.enabled must be boolean")
    crowded = _positive_integer(
        value["minimum_derivatives_confirmations_for_crowded"],
        "positioning.minimum_derivatives_confirmations_for_crowded",
    )
    extreme = _positive_integer(
        value["minimum_derivatives_confirmations_for_extreme"],
        "positioning.minimum_derivatives_confirmations_for_extreme",
    )
    if extreme < crowded:
        raise PolicyError("positioning extreme confirmation count must be >= crowded count")

    def signed_thresholds(raw: Any, name: str, positive_keys: tuple[str, str], negative_keys: tuple[str, str]) -> dict[str, float]:
        if not isinstance(raw, dict):
            raise PolicyError(f"{name} must be an object")
        allowed = set(positive_keys + negative_keys)
        _unknown_fields(raw, allowed, name)
        if set(raw) != allowed:
            raise PolicyError(f"{name} fields are incomplete")
        parsed = {key: _number(raw[key], f"{name}.{key}") for key in allowed}
        positive, extreme_positive = positive_keys
        extreme_negative, negative = negative_keys
        if not (
            parsed[positive] > 0
            and parsed[extreme_positive] >= parsed[positive]
            and parsed[negative] < 0
            and parsed[extreme_negative] <= parsed[negative]
        ):
            raise PolicyError(f"{name} thresholds have invalid signs or ordering")
        return {key: parsed[key] for key in raw}

    funding = signed_thresholds(
        value["funding_rate"],
        "positioning.funding_rate",
        ("elevated_positive", "extreme_positive"),
        ("extreme_negative", "elevated_negative"),
    )
    oi = value["open_interest_change_7d"]
    if not isinstance(oi, dict):
        raise PolicyError("positioning.open_interest_change_7d must be an object")
    _unknown_fields(oi, _OI_FIELDS, "positioning.open_interest_change_7d")
    if set(oi) != _OI_FIELDS:
        raise PolicyError("positioning.open_interest_change_7d fields are incomplete")
    oi_parsed = {key: _number(oi[key], f"positioning.open_interest_change_7d.{key}", minimum=0.0) for key in oi}
    if oi_parsed["building"] <= 0 or oi_parsed["rapid"] < oi_parsed["building"]:
        raise PolicyError("positioning open-interest thresholds must be ordered and > 0")

    ratios = value["long_short_ratio"]
    if not isinstance(ratios, dict):
        raise PolicyError("positioning.long_short_ratio must be an object")
    _unknown_fields(ratios, _LONG_SHORT_FIELDS, "positioning.long_short_ratio")
    if not {"long_crowded", "short_crowded"}.issubset(ratios):
        raise PolicyError("positioning.long_short_ratio fields are incomplete")
    ratios_parsed = {
        "long_crowded": _number(ratios["long_crowded"], "positioning.long_short_ratio.long_crowded", minimum=0.0),
        "short_crowded": _number(ratios["short_crowded"], "positioning.long_short_ratio.short_crowded", minimum=0.0),
        "long_extreme": _number(ratios.get("long_extreme", 1.75), "positioning.long_short_ratio.long_extreme", minimum=0.0),
        "short_extreme": _number(ratios.get("short_extreme", 0.57), "positioning.long_short_ratio.short_extreme", minimum=0.0),
    }
    if not (
        ratios_parsed["long_crowded"] > 1
        and ratios_parsed["long_extreme"] >= ratios_parsed["long_crowded"]
        and 0 < ratios_parsed["short_extreme"] <= ratios_parsed["short_crowded"] < 1
    ):
        raise PolicyError("positioning long/short thresholds are invalid")

    basis = signed_thresholds(
        value.get(
            "futures_basis",
            {
                "elevated_positive": 0.10,
                "extreme_positive": 0.20,
                "elevated_negative": -0.10,
                "extreme_negative": -0.20,
            },
        ),
        "positioning.futures_basis",
        ("elevated_positive", "extreme_positive"),
        ("extreme_negative", "elevated_negative"),
    )
    social = value.get(
        "social",
        {
            "fearful_bullish_share": 0.20,
            "optimistic_bullish_share": 0.60,
            "euphoric_bullish_share": 0.80,
            "attention_growth_extreme": 2.0,
        },
    )
    if not isinstance(social, dict):
        raise PolicyError("positioning.social must be an object")
    _unknown_fields(social, _SOCIAL_FIELDS, "positioning.social")
    if not {"euphoric_bullish_share", "attention_growth_extreme"}.issubset(social):
        raise PolicyError("positioning.social fields are incomplete")
    social_parsed = {
        "fearful_bullish_share": _fraction(social.get("fearful_bullish_share", 0.2), "positioning.social.fearful_bullish_share"),
        "optimistic_bullish_share": _fraction(social.get("optimistic_bullish_share", 0.6), "positioning.social.optimistic_bullish_share"),
        "euphoric_bullish_share": _fraction(social["euphoric_bullish_share"], "positioning.social.euphoric_bullish_share"),
    }
    social_parsed["attention_growth_extreme"] = _number(
        social["attention_growth_extreme"],
        "positioning.social.attention_growth_extreme",
        minimum=0.0,
    )
    if not (
        social_parsed["fearful_bullish_share"]
        < social_parsed["optimistic_bullish_share"]
        < social_parsed["euphoric_bullish_share"]
    ):
        raise PolicyError("positioning social bullish-share thresholds must be ordered")

    return {
        "enabled": value["enabled"],
        "minimum_derivatives_confirmations_for_crowded": crowded,
        "minimum_derivatives_confirmations_for_extreme": extreme,
        "funding_rate": funding,
        "open_interest_change_7d": oi_parsed,
        "long_short_ratio": ratios_parsed,
        "futures_basis": basis,
        "social": social_parsed,
    }


def _parse_btc_cycle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("btc_cycle must be an object")
    _unknown_fields(value, _BTC_CYCLE_FIELDS, "btc_cycle")
    if not _BTC_CYCLE_REQUIRED_FIELDS.issubset(value):
        raise PolicyError("btc_cycle fields are incomplete")
    if not isinstance(value["enabled"], bool):
        raise PolicyError("btc_cycle.enabled must be boolean")
    halving = value["halving_context_days"]
    if not isinstance(halving, dict):
        raise PolicyError("btc_cycle.halving_context_days must be an object")
    _unknown_fields(halving, _HALVING_DAYS_FIELDS, "btc_cycle.halving_context_days")
    if set(halving) != _HALVING_DAYS_FIELDS:
        raise PolicyError("btc_cycle.halving_context_days fields are incomplete")
    halving_parsed = {key: _positive_integer(item, f"btc_cycle.halving_context_days.{key}") for key, item in halving.items()}
    if not (
        halving_parsed["early_post_halving_max"] < halving_parsed["mid_epoch_max"]
        and halving_parsed["late_epoch_min"] > halving_parsed["mid_epoch_max"]
    ):
        raise PolicyError("btc_cycle halving context ranges must be ordered")
    elevated = _positive_integer(
        value["minimum_non_clock_confirmations_for_elevated_risk"],
        "btc_cycle.minimum_non_clock_confirmations_for_elevated_risk",
    )
    high = _positive_integer(
        value["minimum_non_clock_confirmations_for_high_risk"],
        "btc_cycle.minimum_non_clock_confirmations_for_high_risk",
    )
    if high < elevated:
        raise PolicyError("btc_cycle high confirmation count must be >= elevated count")
    for name in (
        "allow_halving_clock_as_trade_trigger",
        "allow_cycle_context_to_change_base_score",
        "allow_cycle_context_to_increase_exposure",
    ):
        if value[name] is not False:
            raise PolicyError(f"btc_cycle.{name} must remain false")

    def optional_numbers(raw: Any, fields: set[str], name: str, defaults: Mapping[str, float]) -> dict[str, float]:
        if not isinstance(raw, dict):
            raise PolicyError(f"{name} must be an object")
        _unknown_fields(raw, fields, name)
        result = {key: float(defaults[key]) for key in fields}
        for key, item in raw.items():
            result[key] = _number(item, f"{name}.{key}", minimum=0.0)
        return result

    valuation = optional_numbers(
        value.get("valuation", {}),
        _CYCLE_VALUATION_FIELDS,
        "btc_cycle.valuation",
        {
            "mvrv_zscore_elevated": 3.5,
            "mvrv_zscore_extreme": 7.0,
            "market_to_realized_price_elevated": 1.5,
            "market_to_realized_price_extreme": 2.0,
        },
    )
    if not (
        valuation["mvrv_zscore_extreme"] >= valuation["mvrv_zscore_elevated"]
        and valuation["market_to_realized_price_extreme"] >= valuation["market_to_realized_price_elevated"]
    ):
        raise PolicyError("btc_cycle valuation thresholds must be ordered")
    price = optional_numbers(
        value.get("price", {}),
        _CYCLE_PRICE_FIELDS,
        "btc_cycle.price",
        {"extension_atr": 2.0, "drawdown_reset": 0.5},
    )
    holder = value.get("holder", {})
    if not isinstance(holder, dict):
        raise PolicyError("btc_cycle.holder must be an object")
    _unknown_fields(holder, _CYCLE_HOLDER_FIELDS, "btc_cycle.holder")
    holder_parsed = {
        "lth_distribution_threshold": _number(holder.get("lth_distribution_threshold", -0.05), "btc_cycle.holder.lth_distribution_threshold"),
        "lth_accumulation_threshold": _number(holder.get("lth_accumulation_threshold", 0.05), "btc_cycle.holder.lth_accumulation_threshold"),
        "sopr_distribution_threshold": _number(holder.get("sopr_distribution_threshold", 1.05), "btc_cycle.holder.sopr_distribution_threshold", minimum=0.0),
    }
    if holder_parsed["lth_distribution_threshold"] >= 0 or holder_parsed["lth_accumulation_threshold"] <= 0:
        raise PolicyError("btc_cycle holder LTH thresholds have invalid signs")
    if holder_parsed["sopr_distribution_threshold"] <= 1:
        raise PolicyError("btc_cycle.holder.sopr_distribution_threshold must be > 1")
    flows = value.get("flows", {})
    if not isinstance(flows, dict):
        raise PolicyError("btc_cycle.flows must be an object")
    _unknown_fields(flows, _CYCLE_FLOW_FIELDS, "btc_cycle.flows")
    flows_parsed = {"weakening_threshold": _number(flows.get("weakening_threshold", -0.05), "btc_cycle.flows.weakening_threshold")}
    if flows_parsed["weakening_threshold"] >= 0:
        raise PolicyError("btc_cycle.flows.weakening_threshold must be negative")
    return {
        "enabled": value["enabled"],
        "halving_context_days": halving_parsed,
        "minimum_non_clock_confirmations_for_elevated_risk": elevated,
        "minimum_non_clock_confirmations_for_high_risk": high,
        "allow_halving_clock_as_trade_trigger": False,
        "allow_cycle_context_to_change_base_score": False,
        "allow_cycle_context_to_increase_exposure": False,
        "valuation": valuation,
        "price": price,
        "holder": holder_parsed,
        "flows": flows_parsed,
    }


def _parse_execution_overlay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("execution_overlay must be an object")
    _unknown_fields(value, _EXECUTION_OVERLAY_FIELDS, "execution_overlay")
    if not {"positioning", "btc_cycle"}.issubset(value):
        raise PolicyError("execution_overlay fields are incomplete")

    def factors(raw: Any, name: str, states: tuple[str, ...]) -> dict[str, float]:
        if not isinstance(raw, dict):
            raise PolicyError(f"{name} must be an object")
        _unknown_fields(raw, set(states) | {"UNKNOWN"}, name)
        result = {state: _fraction(raw.get(state, 1.0), f"{name}.{state}") for state in states}
        result["UNKNOWN"] = _fraction(raw.get("UNKNOWN", 1.0), f"{name}.UNKNOWN")
        if any(result[left] < result[right] for left, right in zip(states, states[1:])):
            raise PolicyError(f"{name} deployment factors must not increase with risk")
        return result

    positioning = factors(value["positioning"], "execution_overlay.positioning", _OVERLAY_RISK_STATES)
    cycle = factors(value["btc_cycle"], "execution_overlay.btc_cycle", ("NORMAL", "ELEVATED", "HIGH"))
    wait = value.get("wait", {"enabled": True, "minimum_extension_atr": 2.0, "expiry_reviews": 5})
    if not isinstance(wait, dict):
        raise PolicyError("execution_overlay.wait must be an object")
    _unknown_fields(
        wait, {"enabled", "minimum_extension_atr", "expiry_reviews"}, "execution_overlay.wait"
    )
    if set(wait) != {"enabled", "minimum_extension_atr", "expiry_reviews"}:
        raise PolicyError("execution_overlay.wait fields are incomplete")
    if not isinstance(wait["enabled"], bool):
        raise PolicyError("execution_overlay.wait.enabled must be boolean")
    extension = _number(wait["minimum_extension_atr"], "execution_overlay.wait.minimum_extension_atr", minimum=0.0)
    if extension <= 0:
        raise PolicyError("execution_overlay.wait.minimum_extension_atr must be > 0")
    expiry = wait["expiry_reviews"]
    if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry < 1:
        raise PolicyError("execution_overlay.wait.expiry_reviews must be an integer >= 1")
    return {"positioning": positioning, "btc_cycle": cycle, "wait": {
        "enabled": wait["enabled"],
        "minimum_extension_atr": extension,
        # Bounded WAIT lifetime (Strategy V2 Phase 3): a technical veto may
        # not block a strategic approval forever. Placeholder pending Phase 6.
        "expiry_reviews": expiry,
    }}


def _parse_events(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("events must be an object")
    _unknown_fields(value, _EVENTS_FIELDS, "events")
    if set(value) != _EVENTS_FIELDS:
        raise PolicyError("events must contain lookback_days and coverage")
    raw_lookbacks = value["lookback_days"]
    if not isinstance(raw_lookbacks, dict) or set(raw_lookbacks) != set(_EVENT_REVIEW_TYPES):
        raise PolicyError("events.lookback_days must contain all review types")
    lookbacks: dict[str, dict[str, int]] = {}
    for review_type in _EVENT_REVIEW_TYPES:
        raw_categories = raw_lookbacks[review_type]
        if not isinstance(raw_categories, dict) or set(raw_categories) != set(_EVENT_CATEGORIES):
            raise PolicyError(f"events.lookback_days.{review_type} must contain all event categories")
        categories: dict[str, int] = {}
        for category in _EVENT_CATEGORIES:
            categories[category] = _positive_integer(
                raw_categories[category],
                f"events.lookback_days.{review_type}.{category}",
            )
        lookbacks[review_type] = categories
    coverage = value["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {"medium_minimum", "high_minimum"}:
        raise PolicyError("events.coverage must contain medium_minimum and high_minimum")
    medium = _fraction(coverage["medium_minimum"], "events.coverage.medium_minimum")
    high = _fraction(coverage["high_minimum"], "events.coverage.high_minimum")
    if medium <= 0 or high < medium:
        raise PolicyError("events coverage thresholds must be ordered and positive")
    return {
        "lookback_days": lookbacks,
        "coverage": {"medium_minimum": medium, "high_minimum": high},
    }


def _parse_confidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("confidence must be an object")
    _unknown_fields(value, _CONFIDENCE_FIELDS, "confidence")
    if set(value) != _CONFIDENCE_FIELDS:
        raise PolicyError("confidence fields are incomplete")
    bands = value["band_thresholds"]
    if not isinstance(bands, dict) or set(bands) != _CONFIDENCE_BAND_FIELDS:
        raise PolicyError("confidence.band_thresholds must contain medium_min and high_min")
    medium = _fraction(bands["medium_min"], "confidence.band_thresholds.medium_min", exclusive_minimum=True)
    high = _fraction(bands["high_min"], "confidence.band_thresholds.high_min", exclusive_minimum=True)
    if medium > high:
        raise PolicyError("confidence band thresholds must be ordered")

    def weights(raw: Any, name: str, expected: set[str]) -> dict[str, float]:
        if not isinstance(raw, dict) or set(raw) != expected:
            raise PolicyError(f"{name} must contain exactly {', '.join(sorted(expected))}")
        parsed = {key: _fraction(raw[key], f"{name}.{key}") for key in expected}
        if not math.isclose(sum(parsed.values()), 1.0, abs_tol=1e-9):
            raise PolicyError(f"{name} weights must sum to 1")
        return {key: parsed[key] for key in sorted(parsed)}

    factor_sufficiency = value["factor_sufficiency"]
    if not isinstance(factor_sufficiency, dict) or set(factor_sufficiency) != set(SCORING_FACTORS):
        raise PolicyError("confidence.factor_sufficiency must contain every scoring factor")
    parsed_factor_sufficiency: dict[str, dict[str, int]] = {}
    for factor in SCORING_FACTORS:
        entry = factor_sufficiency[factor]
        if not isinstance(entry, dict) or set(entry) != {"min_primary_available", "min_total_available"}:
            raise PolicyError(f"confidence.factor_sufficiency.{factor} must contain primary and total minima")
        primary = _positive_integer(
            entry["min_primary_available"],
            f"confidence.factor_sufficiency.{factor}.min_primary_available",
        )
        total = _positive_integer(
            entry["min_total_available"],
            f"confidence.factor_sufficiency.{factor}.min_total_available",
        )
        if primary > total:
            raise PolicyError(f"confidence.factor_sufficiency.{factor} primary minimum must not exceed total minimum")
        parsed_factor_sufficiency[factor] = {
            "min_primary_available": primary,
            "min_total_available": total,
        }

    asset_evidence = value["asset_evidence"]
    if not isinstance(asset_evidence, dict) or set(asset_evidence) != {
        "aggregation", "low_confidence_position_floor", "material_exposure_threshold"
    }:
        raise PolicyError("confidence.asset_evidence fields are incomplete")
    aggregation = str(asset_evidence["aggregation"]).strip().lower()
    if aggregation != "exposure_weighted":
        raise PolicyError("confidence.asset_evidence.aggregation must be exposure_weighted")
    low_floor = _fraction(
        asset_evidence["low_confidence_position_floor"],
        "confidence.asset_evidence.low_confidence_position_floor",
    )
    exposure_threshold = _fraction(
        asset_evidence["material_exposure_threshold"],
        "confidence.asset_evidence.material_exposure_threshold",
    )

    caps = value["caps"]
    if not isinstance(caps, dict) or not caps:
        raise PolicyError("confidence.caps must be a non-empty object")
    parsed_caps = {
        str(key).strip().lower(): _fraction(raw, f"confidence.caps.{key}")
        for key, raw in caps.items()
        if isinstance(key, str) and key.strip()
    }
    if len(parsed_caps) != len(caps):
        raise PolicyError("confidence.caps keys must be non-empty strings")
    return {
        "band_thresholds": {"medium_min": medium, "high_min": high},
        "data_dimension_weights": weights(value["data_dimension_weights"], "confidence.data_dimension_weights", _DATA_DIMENSIONS),
        "regime_domain_weights": weights(value["regime_domain_weights"], "confidence.regime_domain_weights", _REGIME_DOMAINS),
        "decision_component_weights": weights(value["decision_component_weights"], "confidence.decision_component_weights", _DECISION_COMPONENTS),
        "factor_sufficiency": parsed_factor_sufficiency,
        "asset_evidence": {
            "aggregation": aggregation,
            "low_confidence_position_floor": low_floor,
            "material_exposure_threshold": exposure_threshold,
        },
        "caps": parsed_caps,
    }


def _parse_freshness_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("freshness_policy must be an object")
    _unknown_fields(value, _FRESHNESS_FIELDS, "freshness_policy")
    if set(value) != _FRESHNESS_FIELDS:
        raise PolicyError("freshness_policy fields are incomplete")
    defaults = value["domain_defaults"]
    if not isinstance(defaults, dict) or set(defaults) != _FRESHNESS_DOMAINS:
        raise PolicyError("freshness_policy.domain_defaults must contain all regime domains")

    def age_entry(raw: Any, name: str) -> dict[str, float]:
        if not isinstance(raw, dict) or set(raw) != _FRESHNESS_AGE_FIELDS:
            raise PolicyError(f"{name} must contain max_age_seconds and half_life_seconds")
        max_age = _number(raw["max_age_seconds"], f"{name}.max_age_seconds", minimum=1.0)
        half_life = _number(raw["half_life_seconds"], f"{name}.half_life_seconds", minimum=1.0)
        if half_life > max_age:
            raise PolicyError(f"{name}.half_life_seconds must not exceed max_age_seconds")
        return {"max_age_seconds": max_age, "half_life_seconds": half_life}

    parsed_defaults = {
        name: age_entry(defaults[name], f"freshness_policy.domain_defaults.{name}")
        for name in sorted(defaults)
    }
    overrides = value["metric_overrides"]
    if not isinstance(overrides, dict):
        raise PolicyError("freshness_policy.metric_overrides must be an object")
    parsed_overrides = {
        str(key).strip().lower(): age_entry(raw, f"freshness_policy.metric_overrides.{key}")
        for key, raw in overrides.items()
        if isinstance(key, str) and key.strip()
    }
    if len(parsed_overrides) != len(overrides):
        raise PolicyError("freshness_policy.metric_overrides keys must be non-empty strings")
    dimensions = value["dimension_weights"]
    if not isinstance(dimensions, dict) or set(dimensions) != _DATA_DIMENSIONS:
        raise PolicyError("freshness_policy.dimension_weights must contain all data dimensions")
    parsed_dimensions = {
        key: _fraction(dimensions[key], f"freshness_policy.dimension_weights.{key}")
        for key in _DATA_DIMENSIONS
    }
    if not math.isclose(sum(parsed_dimensions.values()), 1.0, abs_tol=1e-9):
        raise PolicyError("freshness_policy.dimension_weights must sum to 1")
    return {
        "domain_defaults": parsed_defaults,
        "metric_overrides": parsed_overrides,
        "dimension_weights": {key: parsed_dimensions[key] for key in sorted(parsed_dimensions)},
    }


def _parse_source_quality(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("source_quality must be an object")
    _unknown_fields(value, _SOURCE_QUALITY_FIELDS, "source_quality")
    if set(value) != _SOURCE_QUALITY_FIELDS:
        raise PolicyError("source_quality fields are incomplete")
    tiers = value["tier_scores"]
    if not isinstance(tiers, dict) or set(tiers) != {"1", "2", "3", "unknown"}:
        raise PolicyError("source_quality.tier_scores must contain 1, 2, 3, and unknown")
    parsed_tiers = {key: _fraction(raw, f"source_quality.tier_scores.{key}") for key, raw in tiers.items()}
    redundancy = value["redundancy_scores"]
    if not isinstance(redundancy, dict) or set(redundancy) != {"0", "1", "2", "3_plus"}:
        raise PolicyError("source_quality.redundancy_scores must contain 0, 1, 2, and 3_plus")
    parsed_redundancy = {key: _fraction(raw, f"source_quality.redundancy_scores.{key}") for key, raw in redundancy.items()}
    if not parsed_redundancy["0"] <= parsed_redundancy["1"] <= parsed_redundancy["2"] <= parsed_redundancy["3_plus"]:
        raise PolicyError("source_quality.redundancy_scores must be ordered")
    return {"tier_scores": parsed_tiers, "redundancy_scores": parsed_redundancy}


def _parse_event_severity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("event_severity must be an object")
    _unknown_fields(value, _EVENT_SEVERITY_FIELDS, "event_severity")
    if set(value) != _EVENT_SEVERITY_FIELDS:
        raise PolicyError("event_severity fields are incomplete")
    states = value["states"]
    if not isinstance(states, list) or tuple(states) != ("CLEAR", "WATCH", "ELEVATED", "CRITICAL"):
        raise PolicyError("event_severity.states must be CLEAR, WATCH, ELEVATED, CRITICAL")
    coverage_state = value["coverage_state"]
    if not isinstance(coverage_state, dict) or set(coverage_state) != {"partial", "unreachable", "unknown"}:
        raise PolicyError("event_severity.coverage_state must contain partial, unreachable, and unknown")
    parsed = {key: str(coverage_state[key]).strip().upper() for key in coverage_state}
    if any(item not in {"WATCH", "ELEVATED", "CRITICAL"} for item in parsed.values()):
        raise PolicyError("event_severity coverage states must be WATCH, ELEVATED, or CRITICAL")
    return {"states": list(states), "coverage_state": parsed}


def _parse_nav_history(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise PolicyError("nav_history must be an object")
    _unknown_fields(value, _NAV_HISTORY_FIELDS, "nav_history")
    if set(value) != _NAV_HISTORY_FIELDS:
        raise PolicyError("nav_history fields are incomplete")
    return {
        key: _fraction(value[key], f"nav_history.{key}")
        for key in _NAV_HISTORY_FIELDS
    }


def _parse_chain_liveness(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("chain_liveness must be an object")
    _unknown_fields(value, _CHAIN_LIVENESS_FIELDS, "chain_liveness")
    if set(value) != _CHAIN_LIVENESS_FIELDS:
        raise PolicyError("chain_liveness must contain its deployment factor and all native assets")
    deployment_factor = _fraction(
        value["degraded_deployment_factor"],
        "chain_liveness.degraded_deployment_factor",
        exclusive_minimum=True,
    )
    parsed: dict[str, Any] = {"degraded_deployment_factor": deployment_factor}
    for asset in ("BTC", "ETH", "BNB", "SOL"):
        raw = value[asset]
        if not isinstance(raw, dict):
            raise PolicyError(f"chain_liveness.{asset} must be an object")
        allowed = _CHAIN_HEAD_FIELDS | _CHAIN_FINALIZED_FIELDS
        _unknown_fields(raw, allowed, f"chain_liveness.{asset}")
        required = (
            _CHAIN_FINALIZED_FIELDS | {"halted_minimum_independent_sources"}
            if asset == "SOL" else _CHAIN_HEAD_FIELDS
        )
        if asset == "ETH":
            required = _CHAIN_HEAD_FIELDS | _CHAIN_FINALIZED_FIELDS
        if set(raw) != required:
            raise PolicyError(f"chain_liveness.{asset} fields are incomplete")
        parsed_asset: dict[str, Any] = {}
        for field_name in required:
            if field_name == "halted_minimum_independent_sources":
                parsed_asset[field_name] = _positive_integer(
                    raw[field_name], f"chain_liveness.{asset}.{field_name}"
                )
                if parsed_asset[field_name] < 2:
                    raise PolicyError(
                        f"chain_liveness.{asset}.{field_name} must be at least 2"
                    )
            else:
                parsed_asset[field_name] = _number(
                    raw[field_name], f"chain_liveness.{asset}.{field_name}", minimum=0.0
                )
                if parsed_asset[field_name] <= 0:
                    raise PolicyError(f"chain_liveness.{asset}.{field_name} must be > 0")
        for prefix in ("head", "finalized"):
            fields = (
                f"healthy_{prefix}_age_seconds",
                f"degraded_{prefix}_age_seconds",
                f"halted_{prefix}_age_seconds",
            )
            if all(field_name in parsed_asset for field_name in fields):
                if not (
                    parsed_asset[fields[0]]
                    <= parsed_asset[fields[1]]
                    <= parsed_asset[fields[2]]
                ):
                    raise PolicyError(
                        f"chain_liveness.{asset} {prefix} age thresholds must be ordered"
                    )
        parsed[asset] = parsed_asset
    return parsed


def _parse_policy(
    data: Any,
) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("policy must be an object")
    _unknown_fields(data, _TOP_LEVEL_FIELDS, "policy")
    missing = set(_TOP_LEVEL_FIELDS - set(data))
    if missing:
        raise PolicyError(f"policy is missing fields: {', '.join(sorted(missing))}")

    horizon = data["investment_horizon_months"]
    if not isinstance(horizon, dict):
        raise PolicyError("investment_horizon_months must be an object")
    _unknown_fields(horizon, _HORIZON_FIELDS, "investment_horizon_months")
    if set(horizon) != _HORIZON_FIELDS:
        raise PolicyError("investment_horizon_months must contain min and max")
    horizon_min = _number(horizon["min"], "investment_horizon_months.min", minimum=1)
    horizon_max = _number(horizon["max"], "investment_horizon_months.max", minimum=1)
    if not horizon_min.is_integer() or not horizon_max.is_integer() or horizon_min > horizon_max:
        raise PolicyError("investment_horizon_months must be ordered positive integers")

    universe = data["universe"]
    if not isinstance(universe, dict):
        raise PolicyError("universe must be an object")
    _unknown_fields(universe, _UNIVERSE_FIELDS, "universe")
    if set(universe) != _UNIVERSE_FIELDS:
        raise PolicyError("universe must contain core, satellites, stable, and excluded")
    core = _symbols(universe["core"], "universe.core")
    satellites = _symbols(universe["satellites"], "universe.satellites")
    stable = _symbols(universe["stable"], "universe.stable")
    excluded = _symbols(universe["excluded"], "universe.excluded")
    _check_overlaps(core, satellites, stable, excluded)

    stress_scenarios = data["stress_scenarios"]
    if not isinstance(stress_scenarios, dict):
        raise PolicyError("stress_scenarios must be an object")
    if set(stress_scenarios) != _STRESS_SCENARIO_NAMES:
        missing = sorted(_STRESS_SCENARIO_NAMES - set(stress_scenarios))
        unknown = sorted(set(stress_scenarios) - _STRESS_SCENARIO_NAMES)
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unknown:
            detail.append("unknown: " + ", ".join(unknown))
        raise PolicyError(
            "stress_scenarios must define exactly the canonical scenario set ("
            + ", ".join(sorted(_STRESS_SCENARIO_NAMES)) + "); " + "; ".join(detail)
        )
    parsed_scenarios: dict[str, dict[str, float]] = {}
    for name, scenario in stress_scenarios.items():
        if not isinstance(scenario, dict):
            raise PolicyError(f"stress_scenarios.{name} must be an object")
        if any(not isinstance(k, str) or not k or k != k.strip().upper() for k in scenario):
            raise PolicyError(f"stress_scenarios.{name} requires uppercase asset symbols")
        parsed_scenarios[name] = {
            k: _number(v, f"stress_scenarios.{name}.{k}", minimum=-1.0, maximum=0.0)
            for k, v in scenario.items()
        }
        # The drawdown-budget stress math treats stables as the safe sleeve,
        # so a stable return is only meaningful in the depeg scenario, which
        # exists precisely to price that sleeve's tail.
        if name != "stablecoin_depeg":
            unstable = {
                k: v for k, v in parsed_scenarios[name].items()
                if k in stable and v != 0
            }
            if unstable:
                raise PolicyError(
                    f"stress_scenarios.{name} stable returns must be zero "
                    "(only stablecoin_depeg may stress the stable sleeve)"
                )
        # A managed risky asset with no scenario return makes the whole stress
        # diagnostic unavailable, so the universe and every scenario are one
        # contract: adding a satellite without its scenario return is a policy
        # error rather than a silently skipped exposure.
        missing_scenario = sorted((set(core) | set(satellites)) - set(parsed_scenarios[name]))
        if missing_scenario:
            raise PolicyError(
                f"stress_scenarios.{name} must cover every core and satellite asset: "
                + ", ".join(missing_scenario)
            )
    # correlation_one models diversification failure: every risky asset takes
    # the same loss, so no pairwise structure can soften the portfolio hit.
    correlation_one = parsed_scenarios["correlation_one"]
    risky_symbols = sorted(set(core) | set(satellites))
    if len({correlation_one[symbol] for symbol in risky_symbols}) != 1:
        raise PolicyError(
            "stress_scenarios.correlation_one must assign every core and satellite "
            "asset the same return"
        )

    risk_engine = _parse_risk_engine(data["risk_engine"])
    risk_tier_estimation = _parse_risk_tier_estimation(data["risk_tier_estimation"])
    dynamic_universe = _parse_dynamic_universe(data["dynamic_universe"])

    risk = data["risk"]
    if not isinstance(risk, dict):
        raise PolicyError("risk must be an object")
    _unknown_fields(risk, _RISK_FIELDS, "risk")
    if set(risk) != _RISK_FIELDS:
        raise PolicyError(
            "risk must contain min_stablecoin_weight, max_portfolio_drawdown, "
            "and drawdown_budget_overlay"
        )
    parsed_overlay = _parse_drawdown_budget_overlay(risk["drawdown_budget_overlay"])

    benchmarks = data["benchmarks"]
    if not isinstance(benchmarks, dict) or not benchmarks:
        raise PolicyError("benchmarks must be a non-empty object")
    # Strategy V2 Phase 5: the primary comparison is the vol-matched
    # BTC/cash benchmark (a strategy-dependent weight solved in closed
    # form, declared by type marker); 100% BTC is the opportunity-cost
    # reference and 70/30 the secondary static benchmark.
    if set(benchmarks) != {"risk_matched_primary", "opportunity_cost_btc", "secondary_static"}:
        raise PolicyError(
            "benchmarks must define exactly risk_matched_primary, "
            "opportunity_cost_btc, and secondary_static"
        )
    primary = benchmarks["risk_matched_primary"]
    if not isinstance(primary, dict) or set(primary) != {"type"} or primary["type"] != "VOL_MATCHED_BTC_CASH":
        raise PolicyError(
            "benchmarks.risk_matched_primary must be the type marker "
            "VOL_MATCHED_BTC_CASH"
        )
    parsed_benchmarks: dict[str, dict[str, Any]] = {
        # Keep the marker so canonical records round-trip; weight-map
        # consumers reject it explicitly instead of misreading it.
        "risk_matched_primary": {"type": "VOL_MATCHED_BTC_CASH"},
    }
    for name in ("opportunity_cost_btc", "secondary_static"):
        parsed_benchmarks[name] = _weighted_map(benchmarks[name], f"benchmarks.{name}")

    rebalance = data["rebalance"]
    if not isinstance(rebalance, dict):
        raise PolicyError("rebalance must be an object")
    _unknown_fields(rebalance, _REBALANCE_FIELDS, "rebalance")
    if not (_REBALANCE_FIELDS - {"direction_flip_confirmation"}) <= set(rebalance):
        raise PolicyError("rebalance fields are incomplete")
    parsed_rebalance: dict[str, Any] = {
        key: _number(value, f"rebalance.{key}", minimum=0.0)
        for key, value in rebalance.items()
        if key not in {"staging", "direction_flip_confirmation"}
    }
    if not (
        parsed_rebalance["hold_below_pp"] < parsed_rebalance["watch_below_pp"]
        and parsed_rebalance["watch_below_pp"] < parsed_rebalance["high_priority_above_pp"]
    ):
        raise PolicyError("rebalance thresholds must be strictly ordered")
    if parsed_rebalance["relative_target_floor"] <= 0:
        raise PolicyError("rebalance.relative_target_floor must be > 0")
    if parsed_rebalance["relative_watch"] <= 0:
        raise PolicyError("rebalance.relative_watch must be > 0")
    if parsed_rebalance["relative_high"] < parsed_rebalance["relative_watch"]:
        raise PolicyError("rebalance.relative_high must be >= relative_watch")
    staging = rebalance["staging"]
    if not isinstance(staging, dict):
        raise PolicyError("rebalance.staging must be an object")
    _unknown_fields(staging, _REBALANCE_STAGING_FIELDS, "rebalance.staging")
    if set(staging) != _REBALANCE_STAGING_FIELDS:
        raise PolicyError("rebalance.staging fields are incomplete")
    if not isinstance(staging["enabled"], bool):
        raise PolicyError("rebalance.staging.enabled must be boolean")
    max_gap_close_fraction = _fraction(
        staging["max_gap_close_fraction"], "rebalance.staging.max_gap_close_fraction"
    )
    if not 0 < max_gap_close_fraction <= 1:
        raise PolicyError("rebalance.staging.max_gap_close_fraction must be in (0, 1]")
    max_step_pp = _number(staging["max_step_pp"], "rebalance.staging.max_step_pp", minimum=0.0)
    if max_step_pp <= 0:
        raise PolicyError("rebalance.staging.max_step_pp must be > 0")
    bypass_reasons = staging["bypass_reasons"]
    if not isinstance(bypass_reasons, list) or not bypass_reasons:
        raise PolicyError("rebalance.staging.bypass_reasons must be a non-empty list")
    normalized_reasons = []
    for item in bypass_reasons:
        reason = str(item).strip().upper()
        if reason not in _ACTION_REASONS:
            raise PolicyError(f"rebalance.staging.bypass_reasons contains unknown action reason {reason!r}")
        normalized_reasons.append(reason)
    if len(normalized_reasons) != len(set(normalized_reasons)):
        raise PolicyError("rebalance.staging.bypass_reasons must not contain duplicates")
    parsed_rebalance["staging"] = {
        "enabled": staging["enabled"],
        "max_gap_close_fraction": max_gap_close_fraction,
        "max_step_pp": max_step_pp,
        "bypass_reasons": tuple(normalized_reasons),
    }

    # Optional since 2026-09: absence means the gate is disabled, which is
    # exactly how records that predate it were computed; embedded resolved
    # policies keep their own shape and hash.
    flip = rebalance.get("direction_flip_confirmation")
    if flip is not None:
        if not isinstance(flip, dict):
            raise PolicyError("rebalance.direction_flip_confirmation must be an object")
        _unknown_fields(flip, _REBALANCE_DIRECTION_FLIP_FIELDS, "rebalance.direction_flip_confirmation")
        if set(flip) != _REBALANCE_DIRECTION_FLIP_FIELDS:
            raise PolicyError("rebalance.direction_flip_confirmation fields are incomplete")
        if not isinstance(flip["enabled"], bool):
            raise PolicyError("rebalance.direction_flip_confirmation.enabled must be boolean")
        required_closes = flip["required_closes"]
        if isinstance(required_closes, bool) or not isinstance(required_closes, int) or required_closes < 1:
            raise PolicyError("rebalance.direction_flip_confirmation.required_closes must be an integer >= 1")
        immediate_overshoot_pp = _number(
            flip["immediate_overshoot_pp"],
            "rebalance.direction_flip_confirmation.immediate_overshoot_pp",
            minimum=0.0,
        )
        flip_bypass = flip["bypass_reasons"]
        if not isinstance(flip_bypass, list) or not flip_bypass:
            raise PolicyError("rebalance.direction_flip_confirmation.bypass_reasons must be a non-empty list")
        normalized_flip_bypass = []
        for item in flip_bypass:
            reason = str(item).strip().upper()
            if reason not in _ACTION_REASONS:
                raise PolicyError(
                    "rebalance.direction_flip_confirmation.bypass_reasons contains "
                    f"unknown action reason {reason!r}"
                )
            normalized_flip_bypass.append(reason)
        if len(normalized_flip_bypass) != len(set(normalized_flip_bypass)):
            raise PolicyError("rebalance.direction_flip_confirmation.bypass_reasons must not contain duplicates")
        parsed_rebalance["direction_flip_confirmation"] = {
            "enabled": flip["enabled"],
            "required_closes": required_closes,
            "immediate_overshoot_pp": immediate_overshoot_pp,
            "bypass_reasons": tuple(normalized_flip_bypass),
        }

    parsed_profiles = _parse_scoring_profiles(data.get("scoring_profiles"))
    parsed_scoring_families = _parse_scoring_families(data.get("scoring_families"), parsed_profiles)
    parsed_asset_profiles = _parse_asset_scoring_profiles(
        data.get("asset_scoring_profiles"), parsed_profiles
    )

    scoring = data["scoring"]
    if not isinstance(scoring, dict):
        raise PolicyError("scoring must be an object")
    _unknown_fields(scoring, _SCORING_FIELDS, "scoring")
    if set(scoring) != _SCORING_FIELDS:
        raise PolicyError("scoring fields are incomplete")
    parsed_scoring = {
        key: _fraction(value, f"scoring.{key}") for key, value in scoring.items()
    }
    if not (
        parsed_scoring["minimum_investable_coverage"]
        <= parsed_scoring["medium_confidence_min_coverage"]
        <= parsed_scoring["high_confidence_min_coverage"]
    ):
        raise PolicyError("scoring coverage thresholds must be ordered")
    if not (
        0 < parsed_scoring["minimum_normalization_coverage"]
        <= parsed_scoring["medium_confidence_min_coverage"]
    ):
        raise PolicyError(
            "scoring.minimum_normalization_coverage must be in (0, medium_confidence_min_coverage]"
        )

    parsed_factor_rules = _parse_factor_rules(
        data.get("factor_rules"),
    )
    parsed_positioning = _parse_positioning(data.get("positioning"))
    parsed_btc_cycle = _parse_btc_cycle(data.get("btc_cycle"))
    parsed_execution_overlay = _parse_execution_overlay(
        data.get("execution_overlay")
    )
    parsed_events = _parse_events(data.get("events"))
    parsed_chain_liveness = _parse_chain_liveness(
        data.get("chain_liveness")
    )
    parsed_confidence = _parse_confidence(data.get("confidence"))
    parsed_freshness_policy = _parse_freshness_policy(data.get("freshness_policy"))
    parsed_source_quality = _parse_source_quality(data.get("source_quality"))
    parsed_event_severity = _parse_event_severity(data.get("event_severity"))
    parsed_nav_history = _parse_nav_history(data.get("nav_history"))
    parsed_high_impact_review = _parse_high_impact_review(data.get("high_impact_review"))
    parsed_regime_transitions = _parse_regime_transitions(data.get("regime_transitions"))
    parsed_regime_model = _parse_regime_model(data.get("regime_model"))

    regimes = data["regimes"]
    if not isinstance(regimes, dict):
        raise PolicyError("regimes must be an object")
    if set(regimes) != set(_REGIMES):
        raise PolicyError("regimes must contain NORMAL, DEFENSIVE, and CAPITAL_PRESERVATION")
    parsed_regimes: dict[str, RegimeLimits] = {}
    for name in _REGIMES:
        value = regimes[name]
        if not isinstance(value, dict):
            raise PolicyError(f"regimes.{name} must be an object")
        _unknown_fields(value, set(_REGIME_FIELDS), f"regimes.{name}")
        if set(value) != set(_REGIME_FIELDS):
            raise PolicyError(f"regimes.{name} fields are incomplete")
        parsed = {
            key: _fraction(item, f"regimes.{name}.{key}") for key, item in value.items()
        }
        if parsed["core_risky_min"] + parsed["satellite_max"] > 1.0:
            raise PolicyError(f"regimes.{name} risky envelopes exceed 1")
        parsed_regimes[name] = RegimeLimits(**parsed)

    allocation = data["allocation"]
    if not isinstance(allocation, dict):
        raise PolicyError("allocation must be an object")
    _unknown_fields(allocation, _ALLOCATION_FIELDS, "allocation")
    common_allocation_fields = {
        "satellite_full_score", "risk_tier_caps", "satellite_target_curve", "relative_strength",
    }
    score_fields = {
        "satellite_entry_score",
        "satellite_exit_score",
        "satellite_soft_exit_score",
    }
    expected_allocation_fields = common_allocation_fields | score_fields
    if set(allocation) != expected_allocation_fields:
        raise PolicyError("allocation fields are incomplete")
    risk_tier_caps = allocation["risk_tier_caps"]
    if not isinstance(risk_tier_caps, dict):
        raise PolicyError("allocation.risk_tier_caps must be an object")
    if set(risk_tier_caps) != {"normal", "high_beta", "high"}:
        raise PolicyError("allocation.risk_tier_caps must contain normal, high_beta, and high")
    parsed_risk_tier_caps: dict[str, dict[str, float]] = {}
    for tier, raw_caps in risk_tier_caps.items():
        if not isinstance(raw_caps, dict):
            raise PolicyError(f"allocation.risk_tier_caps.{tier} must be an object")
        _unknown_fields(raw_caps, _RISK_TIER_CAP_FIELDS, f"allocation.risk_tier_caps.{tier}")
        if set(raw_caps) != _RISK_TIER_CAP_FIELDS:
            raise PolicyError(f"allocation.risk_tier_caps.{tier} fields are incomplete")
        strategic_fraction = _fraction(
            raw_caps["strategic_fraction_of_satellite_envelope"],
            f"allocation.risk_tier_caps.{tier}.strategic_fraction_of_satellite_envelope",
            exclusive_minimum=True,
        )
        buffer_pp = _number(
            raw_caps["hard_cap_buffer_pp"],
            f"allocation.risk_tier_caps.{tier}.hard_cap_buffer_pp",
            minimum=0.0,
        )
        parsed_risk_tier_caps[tier] = {
            "strategic_fraction_of_satellite_envelope": strategic_fraction,
            "hard_cap_buffer_pp": buffer_pp,
        }
    parsed_allocation = {
        "satellite_full_score": _number(
            allocation["satellite_full_score"], "allocation.satellite_full_score", minimum=0, maximum=100
        ),
        "risk_tier_caps": parsed_risk_tier_caps,
    }
    parsed_allocation["satellite_entry_score"] = _number(
        allocation["satellite_entry_score"],
        "allocation.satellite_entry_score",
        minimum=0,
        maximum=100,
    )
    parsed_allocation["satellite_exit_score"] = _number(
        allocation["satellite_exit_score"],
        "allocation.satellite_exit_score",
        minimum=0,
        maximum=100,
    )
    parsed_allocation["satellite_soft_exit_score"] = _number(
        allocation["satellite_soft_exit_score"],
        "allocation.satellite_soft_exit_score",
        minimum=0,
        maximum=100,
    )
    curve = allocation["satellite_target_curve"]
    if not isinstance(curve, dict):
        raise PolicyError("allocation.satellite_target_curve must be an object")
    _unknown_fields(curve, _SATELLITE_CURVE_FIELDS, "allocation.satellite_target_curve")
    if set(curve) != _SATELLITE_CURVE_FIELDS:
        raise PolicyError("allocation.satellite_target_curve fields are incomplete")
    parsed_curve = {
        key: _fraction(curve[key], f"allocation.satellite_target_curve.{key}")
        for key in _SATELLITE_CURVE_FIELDS
    }
    if not (
        parsed_curve["soft_exit_fraction"]
        <= parsed_curve["exit_fraction"]
        <= parsed_curve["entry_fraction"]
        <= parsed_curve["full_fraction"]
    ):
        raise PolicyError(
            "allocation.satellite_target_curve fractions must be non-decreasing "
            "from soft_exit to full"
        )
    if parsed_curve["full_fraction"] != 1.0:
        # The curve scales the satellite envelope; a full score must be able
        # to use all of it or the envelope itself is the real cap.
        raise PolicyError("allocation.satellite_target_curve.full_fraction must be 1.0")
    parsed_allocation["satellite_target_curve"] = parsed_curve
    relative_strength = allocation["relative_strength"]
    if not isinstance(relative_strength, dict):
        raise PolicyError("allocation.relative_strength must be an object")
    _unknown_fields(relative_strength, _RELATIVE_STRENGTH_FIELDS, "allocation.relative_strength")
    if set(relative_strength) != _RELATIVE_STRENGTH_FIELDS:
        raise PolicyError("allocation.relative_strength fields are incomplete")
    parsed_relative_strength = {
        key: _number(
            relative_strength[key], f"allocation.relative_strength.{key}", minimum=0, maximum=100
        )
        for key in _RELATIVE_STRENGTH_FIELDS
    }
    if not (
        0 <= parsed_relative_strength["hard_block_below_score"]
        < parsed_relative_strength["increase_min_score"] <= 100
    ):
        raise PolicyError(
            "allocation.relative_strength must satisfy "
            "0 <= hard_block_below_score < increase_min_score <= 100"
        )
    parsed_allocation["relative_strength"] = parsed_relative_strength
    if not (
        parsed_allocation["satellite_soft_exit_score"]
        < parsed_allocation["satellite_exit_score"]
        < parsed_allocation["satellite_entry_score"]
        < parsed_allocation["satellite_full_score"]
    ):
        raise PolicyError(
            "allocation satellite scores must satisfy soft_exit < exit < entry < full"
        )

    parsed_event_risk_multipliers = _parse_event_risk_multipliers(
        data.get("event_risk_multipliers")
    )
    parsed_core_allocation = _parse_core_allocation(
        data.get("core_allocation")
    )

    parsed_execution = _parse_execution(data.get("execution"))
    parsed_volume_profile = _parse_volume_profile(
        data.get("volume_profile")
    )
    policy = Policy(
        investment_horizon_months=(int(horizon_min), int(horizon_max)),
        core_symbols=core,
        satellite_symbols=satellites,
        stable_symbols=stable,
        excluded_symbols=excluded,
        min_stablecoin_weight=_fraction(
            risk["min_stablecoin_weight"], "risk.min_stablecoin_weight"
        ),
        max_portfolio_drawdown=_fraction(
            risk["max_portfolio_drawdown"],
            "risk.max_portfolio_drawdown",
            exclusive_minimum=True,
        ),
        stress_scenarios=parsed_scenarios,
        risk_engine=risk_engine,
        risk_tier_estimation=risk_tier_estimation,
        dynamic_universe=dynamic_universe,
        benchmarks=parsed_benchmarks,
        rebalance=parsed_rebalance,
        scoring_profiles=parsed_profiles,
        asset_scoring_profiles=parsed_asset_profiles,
        scoring=parsed_scoring,
        regimes=parsed_regimes,
        allocation=parsed_allocation,
        event_risk_multipliers=parsed_event_risk_multipliers,
        core_allocation=parsed_core_allocation,
        volume_profile=parsed_volume_profile,
        execution=parsed_execution,
        factor_rules=parsed_factor_rules,
        positioning=parsed_positioning,
        btc_cycle=parsed_btc_cycle,
        execution_overlay=parsed_execution_overlay,
        events=parsed_events,
        chain_liveness=parsed_chain_liveness,
        confidence=parsed_confidence,
        freshness_policy=parsed_freshness_policy,
        source_quality=parsed_source_quality,
        event_severity=parsed_event_severity,
        nav_history=parsed_nav_history,
        high_impact_review=parsed_high_impact_review,
        regime_transitions=parsed_regime_transitions,
        regime_model=parsed_regime_model,
        drawdown_budget_overlay=parsed_overlay,
        scoring_families=parsed_scoring_families,
    )
    return policy


def load_policy(
    path: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Policy:
    policy_path = Path(path) if path is not None else _DEFAULT_POLICY_PATH
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"unable to load policy from {policy_path}: {exc}") from exc
    return _parse_policy(data).with_overrides(overrides)


def policy_from_mapping(data: Mapping[str, Any]) -> Policy:
    """Parse an embedded resolved policy record."""
    return _parse_policy(dict(data))


def resolve_policy(
    overrides: Mapping[str, Any] | None = None, *, path: str | Path | None = None
) -> Policy:
    return load_policy(path, overrides)


def policy_hash(policy: Policy | Mapping[str, Any]) -> str:
    """Return the SHA-256 digest of a policy's canonical JSON representation."""
    value = policy.as_dict() if isinstance(policy, Policy) else dict(policy)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "Policy",
    "PolicyError",
    "RegimeLimits",
    "SCORING_FACTORS",
    "load_policy",
    "policy_hash",
    "policy_from_mapping",
    "resolve_policy",
]
