"""Immutable derivatives-positioning and social-context records."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .time import normalize_timestamp


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class PositioningLeverageState(_ValueEnum):
    DELEVERAGED = "DELEVERAGED"
    NORMAL = "NORMAL"
    BUILDING = "BUILDING"
    CROWDED = "CROWDED"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class PositioningBias(_ValueEnum):
    SHORT_CROWDED = "SHORT_CROWDED"
    SHORT_BIASED = "SHORT_BIASED"
    BALANCED = "BALANCED"
    LONG_BIASED = "LONG_BIASED"
    LONG_CROWDED = "LONG_CROWDED"
    UNKNOWN = "UNKNOWN"


class PositioningRisk(_ValueEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class SocialSentimentState(_ValueEnum):
    FEARFUL = "FEARFUL"
    NEUTRAL = "NEUTRAL"
    OPTIMISTIC = "OPTIMISTIC"
    EUPHORIC = "EUPHORIC"
    UNKNOWN = "UNKNOWN"


_LEVERAGE = {item.value for item in PositioningLeverageState}
_BIAS = {item.value for item in PositioningBias}
_RISK = {item.value for item in PositioningRisk}
_SOCIAL = {item.value for item in SocialSentimentState}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
_METRIC_FIELDS = (
    "funding_rate",
    "funding_rate_24h_avg",
    "funding_rate_7d_avg",
    "funding_rate_percentile",
    "open_interest_usd",
    "open_interest_change_1d",
    "open_interest_change_7d",
    "open_interest_to_market_cap",
    "long_short_account_ratio",
    "top_trader_long_short_ratio",
    "long_liquidations_24h_usd",
    "short_liquidations_24h_usd",
    "total_liquidations_24h_usd",
    "long_liquidations_7d_usd",
    "short_liquidations_7d_usd",
    "futures_basis_annualized",
    "social_bullish_share",
    "social_mentions_24h",
    "social_mentions_change_7d",
    "social_sentiment_percentile",
    "social_attention_percentile",
    "market_fear_greed",
)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _state(value: Any, allowed: set[str], field_name: str) -> str:
    raw = value.value if isinstance(value, Enum) else value
    result = _text(raw, field_name).upper()
    if result not in allowed:
        raise ValueError(f"{field_name} is unsupported")
    return result


def _ids(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a sequence of strings")
    result = tuple(_text(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must contain unique values")
    return result


def _freeze(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{field_name} contains an invalid key")
            normalized = key.strip().lower()
            if normalized in {"raw", "raw_data", "raw_posts", "full_history", "history", "dense_series", "candles"} or normalized.startswith("raw_"):
                raise ValueError(f"{field_name} must not contain raw or dense-history fields")
            frozen[key] = _freeze(item, f"{field_name}.{key}")
        try:
            json.dumps(_thaw(frozen), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be JSON serializable and finite") from exc
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, f"{field_name}[]") for item in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        raise ValueError(f"{field_name} must contain finite values")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class PositioningFacts:
    symbol: str
    as_of: str

    funding_rate: float | None = None
    funding_rate_24h_avg: float | None = None
    funding_rate_7d_avg: float | None = None
    funding_rate_percentile: float | None = None

    open_interest_usd: float | None = None
    open_interest_change_1d: float | None = None
    open_interest_change_7d: float | None = None
    open_interest_to_market_cap: float | None = None

    long_short_account_ratio: float | None = None
    top_trader_long_short_ratio: float | None = None

    long_liquidations_24h_usd: float | None = None
    short_liquidations_24h_usd: float | None = None
    total_liquidations_24h_usd: float | None = None
    long_liquidations_7d_usd: float | None = None
    short_liquidations_7d_usd: float | None = None

    futures_basis_annualized: float | None = None

    social_bullish_share: float | None = None
    social_mentions_24h: float | None = None
    social_mentions_change_7d: float | None = None
    social_sentiment_percentile: float | None = None
    social_attention_percentile: float | None = None
    market_fear_greed: float | None = None

    leverage_state: str = PositioningLeverageState.UNKNOWN.value
    bias: str = PositioningBias.UNKNOWN.value
    risk: str = PositioningRisk.UNKNOWN.value
    social_state: str = SocialSentimentState.UNKNOWN.value
    confidence: str = "LOW"
    evidence_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    data_quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "positioning symbol").upper())
        object.__setattr__(self, "as_of", normalize_timestamp(self.as_of, "positioning as_of"))
        for field_name in _METRIC_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"positioning {field_name} must be numeric or null")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"positioning {field_name} must be finite")
            if field_name in {
                "open_interest_usd", "long_liquidations_24h_usd", "short_liquidations_24h_usd",
                "total_liquidations_24h_usd", "long_liquidations_7d_usd", "short_liquidations_7d_usd",
                "social_mentions_24h",
            } and number < 0:
                raise ValueError(f"positioning {field_name} must be non-negative")
            if field_name in {
                "open_interest_to_market_cap", "long_short_account_ratio", "top_trader_long_short_ratio",
            } and number <= 0:
                raise ValueError(f"positioning {field_name} must be > 0")
            if field_name in {"funding_rate_percentile", "social_bullish_share", "social_sentiment_percentile", "social_attention_percentile"} and not 0 <= number <= 1:
                raise ValueError(f"positioning {field_name} must be in [0, 1]")
            if field_name == "market_fear_greed" and not 0 <= number <= 100:
                raise ValueError("positioning market_fear_greed must be in [0, 100]")
            if field_name in {"open_interest_change_1d", "open_interest_change_7d", "social_mentions_change_7d"} and number < -1:
                raise ValueError(f"positioning {field_name} must be >= -1")
            object.__setattr__(self, field_name, number)
        object.__setattr__(self, "leverage_state", _state(self.leverage_state, _LEVERAGE, "leverage_state"))
        object.__setattr__(self, "bias", _state(self.bias, _BIAS, "bias"))
        object.__setattr__(self, "risk", _state(self.risk, _RISK, "positioning risk"))
        object.__setattr__(self, "social_state", _state(self.social_state, _SOCIAL, "social_state"))
        confidence = _text(self.confidence, "positioning confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError("positioning confidence must be HIGH, MEDIUM, or LOW")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "evidence_ids", _ids(self.evidence_ids, "evidence_ids"))
        object.__setattr__(self, "notes", _ids(self.notes, "notes"))
        object.__setattr__(self, "data_quality_flags", _ids(self.data_quality_flags, "data_quality_flags"))
        if not isinstance(self.source_metadata, Mapping):
            raise ValueError("source_metadata must be an object")
        object.__setattr__(self, "source_metadata", _freeze(self.source_metadata, "source_metadata"))

    @property
    def is_long_crowded(self) -> bool:
        return self.bias == PositioningBias.LONG_CROWDED.value

    @property
    def is_extreme(self) -> bool:
        return self.leverage_state == PositioningLeverageState.EXTREME.value or self.risk == PositioningRisk.EXTREME.value

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "symbol": self.symbol,
            "as_of": self.as_of,
            **{field_name: getattr(self, field_name) for field_name in _METRIC_FIELDS},
            "leverage_state": self.leverage_state,
            "bias": self.bias,
            "risk": self.risk,
            "social_state": self.social_state,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "notes": list(self.notes),
            "source_metadata": _thaw(self.source_metadata),
            "data_quality_flags": list(self.data_quality_flags),
        }
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PositioningFacts":
        if not isinstance(value, Mapping):
            raise ValueError("positioning facts must be an object")
        allowed = {"symbol", "as_of", *_METRIC_FIELDS, "leverage_state", "bias", "risk", "social_state", "confidence", "evidence_ids", "notes", "source_metadata", "data_quality_flags"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"positioning facts contain unknown fields: {', '.join(sorted(unknown))}")
        return cls(**{key: value[key] for key in value if key in allowed})


PositioningOverlay = PositioningFacts


__all__ = [
    "PositioningBias",
    "PositioningFacts",
    "PositioningOverlay",
    "PositioningLeverageState",
    "PositioningRisk",
    "SocialSentimentState",
]
