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
    "event_risk_multipliers",
    "confidence",
    "freshness_policy",
    "source_quality",
    "event_severity",
    "nav_history",
}
_UNIVERSE_FIELDS = {"core", "satellites", "stable", "excluded"}
_RISK_FIELDS = {"min_stablecoin_weight", "max_portfolio_drawdown"}
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
_REBALANCE_FIELDS = {"hold_below_pp", "watch_below_pp", "high_priority_above_pp"}
_ALLOCATION_FIELDS = {
    "satellite_entry_score",
    "satellite_exit_score",
    "satellite_full_score",
    "low_confidence_satellite_weight",
    "confidence_multipliers",
    "risk_multipliers",
}
_CORE_ALLOCATION_FIELDS = {"anchor", "eth", "confidence_multipliers", "relative_multipliers"}
_CORE_ANCHOR_FIELDS = {"BTC", "ETH"}
_CORE_ETH_FIELDS = {
    "increase_min_score",
    "hold_min_score",
    "relative_increase_min_score",
    "relative_reduce_below_score",
    "max_core_sleeve_share",
}
_CORE_CONFIDENCE_FIELDS = {"HIGH", "MEDIUM", "LOW"}
_CORE_RELATIVE_FIELDS = {"strong", "neutral", "weak", "materially_weak"}
_SCORING_FIELDS = {
    "high_confidence_min_coverage",
    "medium_confidence_min_coverage",
    "minimum_investable_coverage",
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
_FLOW_RULE_FIELDS = {"neutral_abs_max", "strong_abs"}
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
    "max_initial_tranche",
    "tranche_templates",
    "breakout",
}
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
    "deleveraging",
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
_DELEVERAGING_FIELDS = {"liquidation_to_open_interest", "normalized_funding_abs"}
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
_EVENT_CATEGORIES = ("security", "governance", "regulatory")
_CONFIDENCE_FIELDS = {
    "band_thresholds",
    "data_dimension_weights",
    "regime_domain_weights",
    "decision_component_weights",
    "caps",
}
_CONFIDENCE_BAND_FIELDS = {"medium_min", "high_min"}
_DATA_DIMENSIONS = {"coverage", "freshness", "source_quality", "redundancy", "signal_consistency"}
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

    relative = value["relative_multipliers"]
    if not isinstance(relative, dict) or set(relative) != _CORE_RELATIVE_FIELDS:
        raise PolicyError("core_allocation.relative_multipliers fields are incomplete")
    parsed_relative = {
        key: _number(relative[key], f"core_allocation.relative_multipliers.{key}", minimum=0.0, maximum=2.0)
        for key in _CORE_RELATIVE_FIELDS
    }
    return {
        "anchor": parsed_anchor,
        "eth": parsed_eth,
        "confidence_multipliers": parsed_confidence,
        "relative_multipliers": parsed_relative,
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
    benchmarks: Mapping[str, Mapping[str, float]]
    rebalance: Mapping[str, float]
    scoring_profiles: Mapping[str, Mapping[str, float]]
    asset_scoring_profiles: Mapping[str, str]
    scoring: Mapping[str, float]
    regimes: Mapping[str, RegimeLimits]
    allocation: Mapping[str, Any]
    event_risk_multipliers: Mapping[str, float]
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
            },
            "benchmarks": {name: dict(weights) for name, weights in self.benchmarks.items()},
            "rebalance": dict(self.rebalance),
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
        "max_initial_tranche": max_initial_parsed,
        "tranche_templates": parsed_templates,
        "breakout": parsed_breakout,
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
    if set(trend) != _TREND_RULE_FIELDS:
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
    parsed_flows = {"neutral_abs_max": neutral_abs_max, "strong_abs": strong_abs}
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

    deleveraging = value.get(
        "deleveraging",
        {"liquidation_to_open_interest": 0.10, "normalized_funding_abs": 0.0003},
    )
    if not isinstance(deleveraging, dict):
        raise PolicyError("positioning.deleveraging must be an object")
    _unknown_fields(deleveraging, _DELEVERAGING_FIELDS, "positioning.deleveraging")
    deleveraging_parsed = {
        "liquidation_to_open_interest": _number(deleveraging.get("liquidation_to_open_interest", 0.1), "positioning.deleveraging.liquidation_to_open_interest", minimum=0.0),
        "normalized_funding_abs": _number(deleveraging.get("normalized_funding_abs", 0.0003), "positioning.deleveraging.normalized_funding_abs", minimum=0.0),
    }
    if deleveraging_parsed["liquidation_to_open_interest"] <= 0:
        raise PolicyError("positioning.deleveraging.liquidation_to_open_interest must be > 0")
    return {
        "enabled": value["enabled"],
        "minimum_derivatives_confirmations_for_crowded": crowded,
        "minimum_derivatives_confirmations_for_extreme": extreme,
        "funding_rate": funding,
        "open_interest_change_7d": oi_parsed,
        "long_short_ratio": ratios_parsed,
        "futures_basis": basis,
        "social": social_parsed,
        "deleveraging": deleveraging_parsed,
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
    wait = value.get("wait", {"enabled": True, "minimum_extension_atr": 2.0})
    if not isinstance(wait, dict):
        raise PolicyError("execution_overlay.wait must be an object")
    _unknown_fields(wait, {"enabled", "minimum_extension_atr"}, "execution_overlay.wait")
    if set(wait) != {"enabled", "minimum_extension_atr"}:
        raise PolicyError("execution_overlay.wait fields are incomplete")
    if not isinstance(wait["enabled"], bool):
        raise PolicyError("execution_overlay.wait.enabled must be boolean")
    extension = _number(wait["minimum_extension_atr"], "execution_overlay.wait.minimum_extension_atr", minimum=0.0)
    if extension <= 0:
        raise PolicyError("execution_overlay.wait.minimum_extension_atr must be > 0")
    return {"positioning": positioning, "btc_cycle": cycle, "wait": {"enabled": wait["enabled"], "minimum_extension_atr": extension}}


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

    risk = data["risk"]
    if not isinstance(risk, dict):
        raise PolicyError("risk must be an object")
    _unknown_fields(risk, _RISK_FIELDS, "risk")
    if set(risk) != _RISK_FIELDS:
        raise PolicyError("risk must contain min_stablecoin_weight and max_portfolio_drawdown")

    benchmarks = data["benchmarks"]
    if not isinstance(benchmarks, dict) or not benchmarks:
        raise PolicyError("benchmarks must be a non-empty object")
    parsed_benchmarks: dict[str, dict[str, float]] = {}
    for name, weights in benchmarks.items():
        if not isinstance(name, str) or not name.strip():
            raise PolicyError("benchmark names must be non-empty strings")
        parsed_benchmarks[name] = _weighted_map(weights, "benchmarks")

    rebalance = data["rebalance"]
    if not isinstance(rebalance, dict):
        raise PolicyError("rebalance must be an object")
    _unknown_fields(rebalance, _REBALANCE_FIELDS, "rebalance")
    if set(rebalance) != _REBALANCE_FIELDS:
        raise PolicyError("rebalance fields are incomplete")
    parsed_rebalance = {
        key: _number(value, f"rebalance.{key}", minimum=0.0)
        for key, value in rebalance.items()
    }
    if not (
        parsed_rebalance["hold_below_pp"] < parsed_rebalance["watch_below_pp"]
        and parsed_rebalance["watch_below_pp"] < parsed_rebalance["high_priority_above_pp"]
    ):
        raise PolicyError("rebalance thresholds must be strictly ordered")

    parsed_profiles = _parse_scoring_profiles(data.get("scoring_profiles"))
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
        "satellite_full_score",
        "low_confidence_satellite_weight",
        "confidence_multipliers",
        "risk_multipliers",
    }
    score_fields = {"satellite_entry_score", "satellite_exit_score"}
    expected_allocation_fields = common_allocation_fields | score_fields
    if set(allocation) != expected_allocation_fields:
        raise PolicyError("allocation fields are incomplete")
    confidence_multipliers = allocation["confidence_multipliers"]
    if not isinstance(confidence_multipliers, dict):
        raise PolicyError("allocation.confidence_multipliers must be an object")
    if set(confidence_multipliers) != {"HIGH", "MEDIUM", "LOW"}:
        raise PolicyError("allocation.confidence_multipliers must contain HIGH, MEDIUM, and LOW")
    parsed_confidence_multipliers = {
        key: _fraction(value, f"allocation.confidence_multipliers.{key}")
        for key, value in confidence_multipliers.items()
    }
    risk_multipliers = allocation["risk_multipliers"]
    if not isinstance(risk_multipliers, dict):
        raise PolicyError("allocation.risk_multipliers must be an object")
    if set(risk_multipliers) != {"normal", "high_beta", "high"}:
        raise PolicyError("allocation.risk_multipliers must contain normal, high_beta, and high")
    parsed_risk_multipliers = {
        key: _fraction(value, f"allocation.risk_multipliers.{key}")
        for key, value in risk_multipliers.items()
    }
    parsed_allocation = {
        "satellite_full_score": _number(
            allocation["satellite_full_score"], "allocation.satellite_full_score", minimum=0, maximum=100
        ),
        "low_confidence_satellite_weight": _fraction(
            allocation["low_confidence_satellite_weight"],
            "allocation.low_confidence_satellite_weight",
        ),
        "confidence_multipliers": parsed_confidence_multipliers,
        "risk_multipliers": parsed_risk_multipliers,
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
    if not (
        parsed_allocation["satellite_exit_score"]
        < parsed_allocation["satellite_entry_score"]
        < parsed_allocation["satellite_full_score"]
    ):
        raise PolicyError(
            "allocation satellite scores must satisfy exit < entry < full"
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
