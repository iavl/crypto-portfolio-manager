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
    "regimes",
    "allocation",
    "execution",
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
        if self.execution:
            execution = dict(self.execution)
            for field in self._execution_omitted_fields:
                execution.pop(field, None)
            result["execution"] = execution
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


def _parse_policy(data: Any, *, allow_missing_execution: bool = False) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("policy must be an object")
    _unknown_fields(data, _TOP_LEVEL_FIELDS, "policy")
    missing = sorted(_TOP_LEVEL_FIELDS - set(data))
    if allow_missing_execution and missing == ["execution"]:
        missing = []
    if missing:
        raise PolicyError(f"policy is missing fields: {', '.join(missing)}")

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
        execution=parsed_execution,
    )
    raw_execution = data.get("execution")
    omitted = (
        frozenset(_EXECUTION_FIELDS - set(raw_execution))
        if isinstance(raw_execution, dict)
        else frozenset(_EXECUTION_FIELDS)
    )
    object.__setattr__(policy, "_execution_omitted_fields", omitted)
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
    return _parse_policy(dict(data), allow_missing_execution=True)


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
