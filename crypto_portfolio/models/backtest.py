"""Strict research-only contracts for historical strategy validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .time import normalize_timestamp, parse_timestamp


_POINT_IN_TIME_QUALITY = {"PUBLISHED_AT_TIME", "HISTORICAL_APPROXIMATION", "UNRECONSTRUCTABLE"}
_CADENCES = {"DAILY_WITH_14D_FULL", "WEEKLY_WITH_DAILY_RISK"}
_EXECUTION_TIMEFRAMES = {"1D", "1H"}
_VALIDATION_MODES = {"STRICT_POINT_IN_TIME", "SYNTHETIC_ASSUMPTIONS"}


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, field_name: str) -> str:
    return normalize_timestamp(_text(value, field_name), field_name)


def _number(value: Any, field_name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        suffix = " and non-negative" if minimum == 0 else ""
        raise ValueError(f"{field_name} must be finite{suffix}")
    return result


def _weights(value: Mapping[str, Any], field_name: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field_name} must be a non-empty object")
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = _text(raw_symbol, f"{field_name} symbol").upper()
        if symbol in result:
            raise ValueError(f"{field_name} contains duplicate symbol {symbol}")
        result[symbol] = _number(raw_weight, f"{field_name}.{symbol}", minimum=0)
    if not math.isclose(sum(result.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{field_name} must sum to 1")
    return result


@dataclass(frozen=True)
class BacktestSpec:
    """Frozen, policy-neutral experiment specification.

    The contract deliberately contains no optimization target.  A run can
    compare fixed scenarios, but it cannot search policy parameters.
    """

    run_id: str
    start_at: str
    end_at: str
    warmup_start_at: str
    initial_value_usd: float
    initial_portfolios: Mapping[str, Mapping[str, float]]
    asset_scopes: Mapping[str, Sequence[str]]
    decision_cadence: str
    full_review_interval_days: int
    fee_bps: float
    slippage_bps: float
    cost_sensitivity_bps: tuple[float, ...]
    semantic_scenarios: tuple[int, ...]
    policy_hash: str
    git_sha: str
    valuation_currency: str = "USD"
    execution_timeframe: str = "1D"
    stablecoin_peg_assumption: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id"))
        for name in ("start_at", "end_at", "warmup_start_at"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        start, end, warmup = (
            parse_timestamp(self.start_at), parse_timestamp(self.end_at), parse_timestamp(self.warmup_start_at)
        )
        if not warmup < start < end:
            raise ValueError("warmup_start_at must precede start_at, which must precede end_at")
        object.__setattr__(self, "initial_value_usd", _number(self.initial_value_usd, "initial_value_usd", minimum=0))
        if self.initial_value_usd <= 0:
            raise ValueError("initial_value_usd must be > 0")
        portfolios = {
            _text(name, "initial portfolio name"): _weights(weights, f"initial_portfolios.{name}")
            for name, weights in self.initial_portfolios.items()
        }
        if not portfolios:
            raise ValueError("initial_portfolios must be non-empty")
        object.__setattr__(self, "initial_portfolios", portfolios)
        scopes: dict[str, tuple[str, ...]] = {}
        for raw_name, raw_symbols in self.asset_scopes.items():
            name = _text(raw_name, "asset scope name")
            if isinstance(raw_symbols, (str, bytes)) or not isinstance(raw_symbols, Sequence):
                raise ValueError(f"asset_scopes.{name} must be a sequence")
            symbols = tuple(dict.fromkeys(_text(item, f"asset_scopes.{name}").upper() for item in raw_symbols))
            if not symbols:
                raise ValueError(f"asset_scopes.{name} must be non-empty")
            scopes[name] = symbols
        if not scopes:
            raise ValueError("asset_scopes must be non-empty")
        object.__setattr__(self, "asset_scopes", scopes)
        cadence = _text(self.decision_cadence, "decision_cadence").upper()
        if cadence not in _CADENCES:
            raise ValueError(f"decision_cadence must be one of {sorted(_CADENCES)}")
        object.__setattr__(self, "decision_cadence", cadence)
        if isinstance(self.full_review_interval_days, bool) or not isinstance(self.full_review_interval_days, int) \
                or self.full_review_interval_days <= 0:
            raise ValueError("full_review_interval_days must be a positive integer")
        for name in ("fee_bps", "slippage_bps"):
            object.__setattr__(self, name, _number(getattr(self, name), name, minimum=0))
        costs = tuple(_number(item, "cost_sensitivity_bps", minimum=0) for item in self.cost_sensitivity_bps)
        if not costs or len(costs) != len(set(costs)):
            raise ValueError("cost_sensitivity_bps must be non-empty and unique")
        object.__setattr__(self, "cost_sensitivity_bps", costs)
        scenarios = tuple(self.semantic_scenarios)
        if scenarios != (30, 50, 70):
            raise ValueError("semantic_scenarios must be exactly [30, 50, 70]")
        object.__setattr__(self, "semantic_scenarios", scenarios)
        if len(_text(self.policy_hash, "policy_hash")) != 64:
            raise ValueError("policy_hash must be a SHA-256 hex digest")
        if len(_text(self.git_sha, "git_sha")) < 7:
            raise ValueError("git_sha must identify a commit")
        currency = _text(self.valuation_currency, "valuation_currency").upper()
        if currency != "USD":
            raise ValueError("formal backtests require valuation_currency=USD")
        object.__setattr__(self, "valuation_currency", currency)
        execution_timeframe = _text(self.execution_timeframe, "execution_timeframe").upper()
        if execution_timeframe not in _EXECUTION_TIMEFRAMES:
            raise ValueError(f"execution_timeframe must be one of {sorted(_EXECUTION_TIMEFRAMES)}")
        object.__setattr__(self, "execution_timeframe", execution_timeframe)
        if not isinstance(self.stablecoin_peg_assumption, bool):
            raise ValueError("stablecoin_peg_assumption must be boolean")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BacktestSpec":
        if not isinstance(value, Mapping):
            raise ValueError("backtest spec must be an object")
        fields = set(cls.__dataclass_fields__)
        unknown = set(value) - fields
        if unknown:
            raise ValueError("backtest spec contains unknown fields: " + ", ".join(sorted(unknown)))
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid backtest spec: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "warmup_start_at": self.warmup_start_at,
            "initial_value_usd": self.initial_value_usd,
            "initial_portfolios": {name: dict(weights) for name, weights in self.initial_portfolios.items()},
            "asset_scopes": {name: list(symbols) for name, symbols in self.asset_scopes.items()},
            "decision_cadence": self.decision_cadence,
            "full_review_interval_days": self.full_review_interval_days,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "cost_sensitivity_bps": list(self.cost_sensitivity_bps),
            "semantic_scenarios": list(self.semantic_scenarios),
            "policy_hash": self.policy_hash,
            "git_sha": self.git_sha,
            "valuation_currency": self.valuation_currency,
            "execution_timeframe": self.execution_timeframe,
            "stablecoin_peg_assumption": self.stablecoin_peg_assumption,
        }

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class HistoricalSeriesManifest:
    series_id: str
    symbol: str
    metric: str
    timeframe: str
    source: str
    quote_currency: str
    observed_start_at: str | None
    observed_end_at: str | None
    fetched_at: str
    available_at_field: str | None
    point_in_time_quality: str
    content_sha256: str | None
    row_count: int
    missing_intervals: int
    consumer: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    status: str = "AVAILABLE"

    def __post_init__(self) -> None:
        for name in ("series_id", "symbol", "metric", "timeframe", "source", "quote_currency"):
            value = _text(getattr(self, name), name)
            object.__setattr__(self, name, value.upper() if name in {"symbol", "timeframe", "quote_currency"} else value)
        for name in ("observed_start_at", "observed_end_at"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        object.__setattr__(self, "fetched_at", _timestamp(self.fetched_at, "fetched_at"))
        quality = _text(self.point_in_time_quality, "point_in_time_quality").upper()
        if quality not in _POINT_IN_TIME_QUALITY:
            raise ValueError(f"point_in_time_quality must be one of {sorted(_POINT_IN_TIME_QUALITY)}")
        object.__setattr__(self, "point_in_time_quality", quality)
        for name in ("row_count", "missing_intervals"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.content_sha256 is not None and len(_text(self.content_sha256, "content_sha256")) != 64:
            raise ValueError("content_sha256 must be a SHA-256 hex digest or null")
        object.__setattr__(self, "consumer", tuple(dict.fromkeys(_text(item, "consumer") for item in self.consumer)))
        object.__setattr__(self, "limitations", tuple(dict.fromkeys(_text(item, "limitation") for item in self.limitations)))
        status = _text(self.status, "status").upper()
        if status not in {"AVAILABLE", "PARTIAL", "BLOCKED", "UNAVAILABLE"}:
            raise ValueError("status is unsupported")
        object.__setattr__(self, "status", status)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HistoricalSeriesManifest":
        if not isinstance(value, Mapping):
            raise ValueError("historical series manifest must be an object")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid historical series manifest: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id, "symbol": self.symbol, "metric": self.metric,
            "timeframe": self.timeframe, "source": self.source,
            "quote_currency": self.quote_currency, "observed_start_at": self.observed_start_at,
            "observed_end_at": self.observed_end_at, "fetched_at": self.fetched_at,
            "available_at_field": self.available_at_field,
            "point_in_time_quality": self.point_in_time_quality,
            "content_sha256": self.content_sha256, "row_count": self.row_count,
            "missing_intervals": self.missing_intervals, "consumer": list(self.consumer),
            "limitations": list(self.limitations), "status": self.status,
        }


@dataclass(frozen=True)
class HistoricalDataManifest:
    manifest_id: str
    created_at: str
    spec_hash: str
    series: tuple[HistoricalSeriesManifest, ...]
    strict_ready: bool
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        if len(_text(self.spec_hash, "spec_hash")) != 64:
            raise ValueError("spec_hash must be a SHA-256 hex digest")
        parsed = tuple(item if isinstance(item, HistoricalSeriesManifest) else HistoricalSeriesManifest.from_mapping(item)
                       for item in self.series)
        identifiers = [item.series_id for item in parsed]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("series IDs must be unique")
        object.__setattr__(self, "series", parsed)
        if not isinstance(self.strict_ready, bool):
            raise ValueError("strict_ready must be boolean")
        object.__setattr__(self, "blockers", tuple(dict.fromkeys(_text(item, "blocker") for item in self.blockers)))
        if self.strict_ready and self.blockers:
            raise ValueError("strict_ready manifest cannot contain blockers")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HistoricalDataManifest":
        if not isinstance(value, Mapping):
            raise ValueError("historical data manifest must be an object")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise ValueError(f"invalid historical data manifest: {exc}") from exc

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id, "created_at": self.created_at,
            "spec_hash": self.spec_hash, "series": [item.as_dict() for item in self.series],
            "strict_ready": self.strict_ready, "blockers": list(self.blockers),
        }


def default_backtest_spec(*, run_id: str, end_at: str, policy_hash: str, git_sha: str) -> BacktestSpec:
    return BacktestSpec(
        run_id=run_id,
        start_at="2024-01-01T00:00:00Z",
        end_at=end_at,
        warmup_start_at="2022-01-01T00:00:00Z",
        initial_value_usd=100_000.0,
        initial_portfolios={
            "all_cash": {"USD": 1.0},
            "core_existing": {"BTC": 0.595, "ETH": 0.255, "USD": 0.15},
        },
        asset_scopes={
            "core": ("BTC", "ETH", "USD"),
            "full": ("BTC", "ETH", "BNB", "SOL", "AAVE", "USD"),
        },
        decision_cadence="DAILY_WITH_14D_FULL",
        full_review_interval_days=14,
        fee_bps=10.0,
        slippage_bps=5.0,
        cost_sensitivity_bps=(0.0, 10.0, 25.0),
        semantic_scenarios=(30, 50, 70),
        policy_hash=policy_hash,
        git_sha=git_sha,
    )


__all__ = [
    "BacktestSpec", "HistoricalDataManifest", "HistoricalSeriesManifest", "default_backtest_spec",
]
