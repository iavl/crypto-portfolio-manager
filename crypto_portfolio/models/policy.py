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
_REGIME_FIELDS = (
    "stablecoin_target",
    "satellite_max",
    "core_risky_min",
    "single_asset_max",
)
_TOP_LEVEL_FIELDS = {
    "policy_version",
    "investment_horizon_months",
    "universe",
    "risk",
    "benchmarks",
    "rebalance",
    "scoring_weights",
    "scoring",
    "factor_rules",
    "regimes",
    "allocation",
    "execution",
    "volume_profile",
    "positioning",
    "btc_cycle",
    "execution_overlay",
    "events",
}
_UNIVERSE_FIELDS = {"core", "satellites", "stable"}
_RISK_FIELDS = {"min_stablecoin_weight", "max_portfolio_drawdown"}
_HORIZON_FIELDS = {"min", "max"}
_REBALANCE_FIELDS = {"hold_below_pp", "watch_below_pp", "high_priority_above_pp"}
_ALLOCATION_FIELDS = {
    "satellite_min_score",
    "satellite_full_score",
    "core_min_score",
    "low_confidence_satellite_weight",
    "confidence_multipliers",
    "risk_multipliers",
}
_SCORING_FIELDS = {
    "high_confidence_min_coverage",
    "medium_confidence_min_coverage",
    "minimum_investable_coverage",
}
_FACTOR_RULE_FIELDS = {"trend", "relative_strength", "flows"}
_TREND_RULE_FIELDS = {
    "base_score",
    "price_ma_points",
    "alignment_points",
    "return_points",
    "drawdown_points",
    "support_points",
    "volume_points",
    "drawdown_tolerance",
    "extension_threshold_atr",
    "extension_penalty",
}
_RELATIVE_RULE_FIELDS = {"positive_threshold", "negative_threshold", "horizon_weights"}
_FLOW_RULE_FIELDS = {"positive_threshold", "negative_threshold"}
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
_EXECUTION_COMPAT_DEFAULTS = {
    "maximum_daily_candle_lag_days": 1,
    "minimum_daily_coverage_ratio": 0.90,
    "maximum_daily_gap_days": 3,
    "maximum_zone_span_atr": 1.0,
    "maximum_spot_close_gap_atr": 4.0,
    "zone_quality": {"minimum_for_entry": 55.0, "high_quality": 75.0},
}
_OVERRIDE_FIELDS = {
    "core_symbols",
    "satellite_symbols",
    "stable_symbols",
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


@dataclass(frozen=True)
class RegimeLimits:
    stablecoin_target: float
    satellite_max: float
    core_risky_min: float
    single_asset_max: float


@dataclass(frozen=True)
class Policy:
    policy_version: int
    investment_horizon_months: tuple[int, int]
    core_symbols: tuple[str, ...]
    satellite_symbols: tuple[str, ...]
    stable_symbols: tuple[str, ...]
    min_stablecoin_weight: float
    max_portfolio_drawdown: float
    benchmarks: Mapping[str, Mapping[str, float]]
    rebalance: Mapping[str, float]
    scoring_weights: Mapping[str, float]
    scoring: Mapping[str, float]
    regimes: Mapping[str, RegimeLimits]
    allocation: Mapping[str, Any]
    execution: Mapping[str, Any] = dataclass_field(default_factory=dict)
    _execution_omitted_fields: frozenset[str] = dataclass_field(
        default_factory=frozenset, repr=False, compare=False
    )
    volume_profile: Mapping[str, Any] = dataclass_field(default_factory=dict)
    factor_rules: Mapping[str, Any] = dataclass_field(default_factory=dict)
    positioning: Mapping[str, Any] = dataclass_field(default_factory=dict)
    btc_cycle: Mapping[str, Any] = dataclass_field(default_factory=dict)
    execution_overlay: Mapping[str, Any] = dataclass_field(default_factory=dict)
    events: Mapping[str, Any] = dataclass_field(default_factory=dict)

    @property
    def core(self) -> tuple[str, ...]:
        return self.core_symbols

    @property
    def satellites(self) -> tuple[str, ...]:
        return self.satellite_symbols

    @property
    def stable(self) -> tuple[str, ...]:
        return self.stable_symbols

    @property
    def canonical_hash(self) -> str:
        return policy_hash(self)

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
            "policy_version": self.policy_version,
            "investment_horizon_months": {
                "min": self.investment_horizon_months[0],
                "max": self.investment_horizon_months[1],
            },
            "universe": {
                "core": list(self.core_symbols),
                "satellites": list(self.satellite_symbols),
                "stable": list(self.stable_symbols),
            },
            "risk": {
                "min_stablecoin_weight": self.min_stablecoin_weight,
                "max_portfolio_drawdown": self.max_portfolio_drawdown,
            },
            "benchmarks": {name: dict(weights) for name, weights in self.benchmarks.items()},
            "rebalance": dict(self.rebalance),
            "scoring_weights": dict(self.scoring_weights),
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
        if self.volume_profile:
            result["volume_profile"] = dict(self.volume_profile)
        if self.factor_rules:
            result["factor_rules"] = {
                name: dict(value) if isinstance(value, Mapping) else value
                for name, value in self.factor_rules.items()
            }
        if self.execution:
            execution = dict(self.execution)
            for field in self._execution_omitted_fields:
                execution.pop(field, None)
            result["execution"] = execution
        if self.positioning:
            result["positioning"] = _copy_mapping(self.positioning)
        if self.btc_cycle:
            result["btc_cycle"] = _copy_mapping(self.btc_cycle)
        if self.execution_overlay:
            result["execution_overlay"] = _copy_mapping(self.execution_overlay)
        if self.events:
            result["events"] = _copy_mapping(self.events)
        return result

    def legacy_config(self) -> dict[str, Any]:
        """Return the old snapshot config shape for compatibility output."""
        return {
            "core_symbols": list(self.core_symbols),
            "satellite_symbols": list(self.satellite_symbols),
            "stable_symbols": list(self.stable_symbols),
            "min_stablecoin_weight": self.min_stablecoin_weight,
            "max_portfolio_drawdown": self.max_portfolio_drawdown,
        }

    as_config = legacy_config

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> "Policy":
        if overrides is None:
            return self
        if not isinstance(overrides, Mapping):
            raise PolicyError("config must be an object")
        _unknown_fields(overrides, _OVERRIDE_FIELDS, "config")
        values = self.legacy_config()
        for field in _OVERRIDE_FIELDS:
            if field in overrides:
                values[field] = overrides[field]
        core = _symbols(values["core_symbols"], "config.core_symbols")
        satellites = _symbols(values["satellite_symbols"], "config.satellite_symbols")
        stable = _symbols(values["stable_symbols"], "config.stable_symbols")
        _check_overlaps(core, satellites, stable)
        return _replace_policy(
            self,
            core_symbols=core,
            satellite_symbols=satellites,
            stable_symbols=stable,
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


def _check_overlaps(core: tuple[str, ...], satellites: tuple[str, ...], stable: tuple[str, ...]) -> None:
    owners: dict[str, str] = {}
    for name, symbols in (("core", core), ("satellites", satellites), ("stable", stable)):
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


def _parse_volume_profile(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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


def _parse_execution(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
    if not isinstance(value, dict):
        raise PolicyError("execution must be an object")
    _unknown_fields(value, _EXECUTION_FIELDS, "execution")
    missing = _EXECUTION_FIELDS - set(value)
    unsupported_missing = missing - set(_EXECUTION_COMPAT_DEFAULTS)
    if unsupported_missing:
        raise PolicyError(f"execution fields are incomplete: {', '.join(sorted(unsupported_missing))}")
    value = {**_EXECUTION_COMPAT_DEFAULTS, **value}
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


def _parse_factor_rules(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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
    parsed_trend = {
        key: _number(item, f"factor_rules.trend.{key}", minimum=0.0)
        for key, item in trend.items()
    }
    parsed_trend["base_score"] = _number(
        trend["base_score"], "factor_rules.trend.base_score", minimum=0.0, maximum=100.0
    )
    parsed_trend["drawdown_tolerance"] = _number(
        trend["drawdown_tolerance"],
        "factor_rules.trend.drawdown_tolerance",
        minimum=0.0,
        maximum=1.0,
    )
    if parsed_trend["extension_threshold_atr"] <= 0:
        raise PolicyError("factor_rules.trend.extension_threshold_atr must be > 0")

    relative = value["relative_strength"]
    if not isinstance(relative, dict):
        raise PolicyError("factor_rules.relative_strength must be an object")
    _unknown_fields(relative, _RELATIVE_RULE_FIELDS, "factor_rules.relative_strength")
    if set(relative) != _RELATIVE_RULE_FIELDS:
        raise PolicyError("factor_rules.relative_strength fields are incomplete")
    positive = _number(relative["positive_threshold"], "factor_rules.relative_strength.positive_threshold")
    negative = _number(relative["negative_threshold"], "factor_rules.relative_strength.negative_threshold")
    if negative > positive or negative > 0 or positive < 0:
        raise PolicyError("relative-strength thresholds must satisfy negative <= 0 <= positive")
    horizon_weights = _weighted_map(relative["horizon_weights"], "factor_rules.relative_strength.horizon_weights")
    if set(horizon_weights) != {"30d", "90d", "180d"}:
        raise PolicyError("factor_rules.relative_strength.horizon_weights must contain 30d, 90d, and 180d")

    flows = value["flows"]
    if not isinstance(flows, dict):
        raise PolicyError("factor_rules.flows must be an object")
    _unknown_fields(flows, _FLOW_RULE_FIELDS, "factor_rules.flows")
    if set(flows) != _FLOW_RULE_FIELDS:
        raise PolicyError("factor_rules.flows fields are incomplete")
    flow_positive = _number(flows["positive_threshold"], "factor_rules.flows.positive_threshold")
    flow_negative = _number(flows["negative_threshold"], "factor_rules.flows.negative_threshold")
    if flow_negative > flow_positive or flow_negative > 0 or flow_positive < 0:
        raise PolicyError("flow thresholds must satisfy negative <= 0 <= positive")
    return {
        "trend": parsed_trend,
        "relative_strength": {
            "positive_threshold": positive,
            "negative_threshold": negative,
            "horizon_weights": horizon_weights,
        },
        "flows": {
            "positive_threshold": flow_positive,
            "negative_threshold": flow_negative,
        },
    }


def _positive_integer(value: Any, name: str) -> int:
    number = _number(value, name, minimum=1)
    if not number.is_integer():
        raise PolicyError(f"{name} must be a positive integer")
    return int(number)


def _parse_positioning(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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


def _parse_btc_cycle(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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


def _parse_execution_overlay(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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


def _parse_events(value: Any, *, allow_missing: bool = False) -> dict[str, Any]:
    if value is None and allow_missing:
        return {}
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


def _parse_policy(
    data: Any,
    *,
    allow_missing_execution: bool = False,
    allow_missing_volume_profile: bool = False,
    allow_missing_factor_rules: bool = False,
    allow_missing_overlays: bool = False,
    allow_missing_events: bool = False,
) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("policy must be an object")
    _unknown_fields(data, _TOP_LEVEL_FIELDS, "policy")
    missing = set(_TOP_LEVEL_FIELDS - set(data))
    if allow_missing_execution:
        missing.discard("execution")
    if allow_missing_volume_profile:
        missing.discard("volume_profile")
    if allow_missing_factor_rules:
        missing.discard("factor_rules")
    if allow_missing_overlays:
        missing.difference_update({"positioning", "btc_cycle", "execution_overlay"})
    if allow_missing_events:
        missing.discard("events")
    if missing:
        raise PolicyError(f"policy is missing fields: {', '.join(sorted(missing))}")

    version = data["policy_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PolicyError("policy_version must be a positive integer")

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
        raise PolicyError("universe must contain core, satellites, and stable")
    core = _symbols(universe["core"], "universe.core")
    satellites = _symbols(universe["satellites"], "universe.satellites")
    stable = _symbols(universe["stable"], "universe.stable")
    _check_overlaps(core, satellites, stable)

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

    scoring_weights = data["scoring_weights"]
    if not isinstance(scoring_weights, dict) or not scoring_weights:
        raise PolicyError("scoring_weights must be a non-empty object")
    parsed_scores: dict[str, float] = {}
    for key, value in scoring_weights.items():
        if not isinstance(key, str) or not key.strip():
            raise PolicyError("scoring_weights keys must be non-empty strings")
        key = key.strip()
        if key in parsed_scores:
            raise PolicyError(f"scoring_weights contains duplicate key {key}")
        parsed_scores[key] = _fraction(value, f"scoring_weights.{key}")
    if not math.isclose(sum(parsed_scores.values()), 1.0, abs_tol=1e-9):
        raise PolicyError("scoring_weights must sum to 1")

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
        data.get("factor_rules"), allow_missing=allow_missing_factor_rules
    )
    parsed_positioning = _parse_positioning(data.get("positioning"), allow_missing=allow_missing_overlays)
    parsed_btc_cycle = _parse_btc_cycle(data.get("btc_cycle"), allow_missing=allow_missing_overlays)
    parsed_execution_overlay = _parse_execution_overlay(
        data.get("execution_overlay"), allow_missing=allow_missing_overlays
    )
    parsed_events = _parse_events(data.get("events"), allow_missing=allow_missing_events)

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
    if set(allocation) != _ALLOCATION_FIELDS:
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
        "satellite_min_score": _number(
            allocation["satellite_min_score"], "allocation.satellite_min_score", minimum=0, maximum=100
        ),
        "satellite_full_score": _number(
            allocation["satellite_full_score"], "allocation.satellite_full_score", minimum=0, maximum=100
        ),
        "core_min_score": _number(
            allocation["core_min_score"], "allocation.core_min_score", minimum=0, maximum=100
        ),
        "low_confidence_satellite_weight": _fraction(
            allocation["low_confidence_satellite_weight"],
            "allocation.low_confidence_satellite_weight",
        ),
        "confidence_multipliers": parsed_confidence_multipliers,
        "risk_multipliers": parsed_risk_multipliers,
    }
    if parsed_allocation["satellite_full_score"] <= parsed_allocation["satellite_min_score"]:
        raise PolicyError("allocation.satellite_full_score must exceed satellite_min_score")

    parsed_execution = _parse_execution(data.get("execution"), allow_missing=allow_missing_execution)
    parsed_volume_profile = _parse_volume_profile(
        data.get("volume_profile"), allow_missing=allow_missing_volume_profile
    )
    policy = Policy(
        policy_version=version,
        investment_horizon_months=(int(horizon_min), int(horizon_max)),
        core_symbols=core,
        satellite_symbols=satellites,
        stable_symbols=stable,
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
        scoring_weights=parsed_scores,
        scoring=parsed_scoring,
        regimes=parsed_regimes,
        allocation=parsed_allocation,
        volume_profile=parsed_volume_profile,
        execution=parsed_execution,
        factor_rules=parsed_factor_rules,
        positioning=parsed_positioning,
        btc_cycle=parsed_btc_cycle,
        execution_overlay=parsed_execution_overlay,
        events=parsed_events,
    )
    raw_execution = data.get("execution")
    omitted = (
        frozenset(_EXECUTION_FIELDS - set(raw_execution))
        if isinstance(raw_execution, dict)
        else frozenset(_EXECUTION_FIELDS)
    )
    object.__setattr__(policy, "_execution_omitted_fields", omitted)
    if "volume_profile" not in data:
        object.__setattr__(policy, "volume_profile", {})
    if "factor_rules" not in data:
        object.__setattr__(policy, "factor_rules", {})
    if "positioning" not in data:
        object.__setattr__(policy, "positioning", {})
    if "btc_cycle" not in data:
        object.__setattr__(policy, "btc_cycle", {})
    if "execution_overlay" not in data:
        object.__setattr__(policy, "execution_overlay", {})
    if "events" not in data:
        object.__setattr__(policy, "events", {})
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
    """Parse an embedded resolved policy for historical-state replay."""
    return _parse_policy(
        dict(data),
        allow_missing_execution=True,
        allow_missing_volume_profile=True,
        allow_missing_factor_rules=True,
        allow_missing_overlays=True,
        allow_missing_events=True,
    )


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
    "load_policy",
    "policy_hash",
    "policy_from_mapping",
    "resolve_policy",
]
