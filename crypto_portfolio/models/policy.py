"""Canonical policy loading and validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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
        return {
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


def _parse_policy(data: Any) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("policy must be an object")
    _unknown_fields(data, _TOP_LEVEL_FIELDS, "policy")
    missing = sorted(_TOP_LEVEL_FIELDS - set(data))
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

    return Policy(
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
    )


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
    "load_policy",
    "policy_hash",
    "policy_from_mapping",
    "resolve_policy",
]
