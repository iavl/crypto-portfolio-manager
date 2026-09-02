"""Validated volume-at-price profile records."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .time import normalize_timestamp


_TIMEFRAMES = {"1H", "4H", "1D"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNAVAILABLE"}
_NODE_KINDS = {"HVN", "LVN"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return result


def _hash(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return result


@dataclass(frozen=True)
class VolumeProfileBin:
    price_low: float
    price_high: float
    midpoint: float
    volume: float
    volume_fraction: float

    def __post_init__(self) -> None:
        low = _number(self.price_low, "profile bin price_low", minimum=0.0)
        high = _number(self.price_high, "profile bin price_high", minimum=0.0)
        midpoint = _number(self.midpoint, "profile bin midpoint", minimum=0.0)
        if low <= 0 or high <= 0 or low > high or not low <= midpoint <= high:
            raise ValueError("profile bin prices must satisfy 0 < low <= midpoint <= high")
        object.__setattr__(self, "price_low", low)
        object.__setattr__(self, "price_high", high)
        object.__setattr__(self, "midpoint", midpoint)
        object.__setattr__(self, "volume", _number(self.volume, "profile bin volume", minimum=0.0))
        object.__setattr__(self, "volume_fraction", _number(self.volume_fraction, "profile bin volume_fraction", minimum=0.0, maximum=1.0))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VolumeProfileBin":
        if not isinstance(value, Mapping):
            raise ValueError("profile bin must be an object")
        allowed = {"price_low", "price_high", "midpoint", "volume", "volume_fraction"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"profile bin contains unknown fields: {', '.join(sorted(unknown))}")
        missing = allowed - set(value)
        if missing:
            raise ValueError(f"profile bin is missing fields: {', '.join(sorted(missing))}")
        return cls(**{field: value[field] for field in allowed})

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_low": self.price_low,
            "price_high": self.price_high,
            "midpoint": self.midpoint,
            "volume": self.volume,
            "volume_fraction": self.volume_fraction,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True)
class VolumeNode:
    price_low: float
    price_high: float
    midpoint: float
    kind: str
    strength: float
    volume_fraction: float

    def __post_init__(self) -> None:
        low = _number(self.price_low, "volume node price_low", minimum=0.0)
        high = _number(self.price_high, "volume node price_high", minimum=0.0)
        midpoint = _number(self.midpoint, "volume node midpoint", minimum=0.0)
        if low <= 0 or high <= 0 or low > high or not low <= midpoint <= high:
            raise ValueError("volume node prices must satisfy 0 < low <= midpoint <= high")
        kind = _text(self.kind, "volume node kind").upper()
        if kind not in _NODE_KINDS:
            raise ValueError(f"volume node kind must be one of {sorted(_NODE_KINDS)}")
        object.__setattr__(self, "price_low", low)
        object.__setattr__(self, "price_high", high)
        object.__setattr__(self, "midpoint", midpoint)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "strength", _number(self.strength, "volume node strength", minimum=0.0, maximum=100.0))
        object.__setattr__(self, "volume_fraction", _number(self.volume_fraction, "volume node volume_fraction", minimum=0.0, maximum=1.0))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VolumeNode":
        if not isinstance(value, Mapping):
            raise ValueError("volume node must be an object")
        allowed = {"price_low", "price_high", "midpoint", "kind", "strength", "volume_fraction"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"volume node contains unknown fields: {', '.join(sorted(unknown))}")
        missing = allowed - set(value)
        if missing:
            raise ValueError(f"volume node is missing fields: {', '.join(sorted(missing))}")
        return cls(**{field: value[field] for field in allowed})

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_low": self.price_low,
            "price_high": self.price_high,
            "midpoint": self.midpoint,
            "kind": self.kind,
            "strength": self.strength,
            "volume_fraction": self.volume_fraction,
        }


def _profile_payload(profile: "VolumeProfile") -> dict[str, Any]:
    return {
        "symbol": profile.symbol,
        "as_of": profile.as_of,
        "timeframe": profile.timeframe,
        "lookback_days": profile.lookback_days,
        "total_volume": profile.total_volume,
        "bins": [item.as_dict() for item in profile.bins],
        "poc": profile.poc,
        "value_area_low": profile.value_area_low,
        "value_area_high": profile.value_area_high,
        "high_volume_nodes": [item.as_dict() for item in profile.high_volume_nodes],
        "low_volume_nodes": [item.as_dict() for item in profile.low_volume_nodes],
        "data_confidence": profile.data_confidence,
        "source": profile.source,
        "ohlcv_hash": profile.ohlcv_hash,
        "value_area_fraction": profile.value_area_fraction,
        "metadata": profile.metadata,
    }


@dataclass(frozen=True)
class VolumeProfile:
    symbol: str
    as_of: str
    timeframe: str
    lookback_days: int
    total_volume: float
    bins: tuple[VolumeProfileBin, ...]
    poc: float
    value_area_low: float
    value_area_high: float
    high_volume_nodes: tuple[VolumeNode, ...] = ()
    low_volume_nodes: tuple[VolumeNode, ...] = ()
    data_confidence: str = "LOW"
    source: str = "unknown"
    ohlcv_hash: str | None = None
    value_area_fraction: float = 0.7
    metadata: Mapping[str, Any] | None = None
    profile_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "profile.symbol").upper())
        object.__setattr__(self, "as_of", normalize_timestamp(self.as_of, "profile.as_of"))
        timeframe = _text(self.timeframe, "profile.timeframe").upper()
        if timeframe not in _TIMEFRAMES:
            raise ValueError(f"profile.timeframe must be one of {sorted(_TIMEFRAMES)}")
        object.__setattr__(self, "timeframe", timeframe)
        if isinstance(self.lookback_days, bool) or not isinstance(self.lookback_days, int) or self.lookback_days < 1:
            raise ValueError("profile.lookback_days must be a positive integer")
        object.__setattr__(self, "total_volume", _number(self.total_volume, "profile.total_volume", minimum=0.0))
        bins = tuple(self.bins)
        if not bins or any(not isinstance(item, VolumeProfileBin) for item in bins):
            raise ValueError("profile.bins must contain VolumeProfileBin objects")
        if any(left.price_low >= right.price_low for left, right in zip(bins, bins[1:])):
            raise ValueError("profile bins must be ordered by price")
        if not math.isclose(sum(item.volume for item in bins), self.total_volume, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError("profile bin volumes must sum to total_volume")
        if self.total_volume > 0 and not math.isclose(sum(item.volume_fraction for item in bins), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("profile bin volume fractions must sum to 1")
        object.__setattr__(self, "bins", bins)
        value_area_fraction = _number(self.value_area_fraction, "profile.value_area_fraction", minimum=0.0, maximum=1.0)
        if value_area_fraction <= 0:
            raise ValueError("profile.value_area_fraction must be > 0")
        object.__setattr__(self, "value_area_fraction", value_area_fraction)
        prices = [item.midpoint for item in bins]
        poc = _number(self.poc, "profile.poc", minimum=0.0)
        val = _number(self.value_area_low, "profile.value_area_low", minimum=0.0)
        vah = _number(self.value_area_high, "profile.value_area_high", minimum=0.0)
        if poc <= 0 or val <= 0 or vah <= 0 or val > poc or poc > vah:
            raise ValueError("profile value-area levels must satisfy 0 < VAL <= POC <= VAH")
        if poc < prices[0] or poc > prices[-1] or val < bins[0].price_low or vah > bins[-1].price_high:
            raise ValueError("profile value-area levels must lie within the profile range")
        object.__setattr__(self, "poc", poc)
        object.__setattr__(self, "value_area_low", val)
        object.__setattr__(self, "value_area_high", vah)
        for field in ("high_volume_nodes", "low_volume_nodes"):
            nodes = tuple(getattr(self, field))
            if any(not isinstance(item, VolumeNode) for item in nodes):
                raise ValueError(f"profile.{field} must contain VolumeNode objects")
            object.__setattr__(self, field, nodes)
        confidence = _text(self.data_confidence, "profile.data_confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError(f"profile.data_confidence must be one of {sorted(_CONFIDENCE)}")
        object.__setattr__(self, "data_confidence", confidence)
        object.__setattr__(self, "source", _text(self.source, "profile.source"))
        if self.ohlcv_hash is not None:
            object.__setattr__(self, "ohlcv_hash", _hash(self.ohlcv_hash, "profile.ohlcv_hash"))
        if self.metadata is not None:
            if not isinstance(self.metadata, Mapping):
                raise ValueError("profile.metadata must be an object or null")
            metadata = dict(self.metadata)
            try:
                json.dumps(metadata, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("profile.metadata must be JSON serializable and finite") from exc
            object.__setattr__(self, "metadata", metadata)
        hash_payload = _profile_payload(self)
        hash_payload.pop("source", None)
        expected_hash = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.profile_hash is not None and _hash(self.profile_hash, "profile.profile_hash") != expected_hash:
            raise ValueError("profile_hash does not match profile content")
        object.__setattr__(self, "profile_hash", expected_hash)

    @property
    def volume_profile_hash(self) -> str:
        return self.profile_hash

    @property
    def val(self) -> float:
        return self.value_area_low

    @property
    def vah(self) -> float:
        return self.value_area_high

    @property
    def confidence(self) -> str:
        return self.data_confidence

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "VolumeProfile":
        if not isinstance(value, Mapping):
            raise ValueError("volume profile must be an object")
        allowed = {
            "symbol", "as_of", "timeframe", "lookback_days", "total_volume", "bins", "poc",
            "value_area_low", "value_area_high", "high_volume_nodes", "low_volume_nodes",
            "data_confidence", "source", "ohlcv_hash", "value_area_fraction", "metadata", "profile_hash", "volume_profile_hash",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"volume profile contains unknown fields: {', '.join(sorted(unknown))}")
        if (
            value.get("profile_hash") is not None
            and value.get("volume_profile_hash") is not None
            and value["profile_hash"] != value["volume_profile_hash"]
        ):
            raise ValueError("profile_hash and volume_profile_hash disagree")
        required = {"symbol", "as_of", "timeframe", "lookback_days", "total_volume", "bins", "poc", "value_area_low", "value_area_high", "data_confidence", "source", "ohlcv_hash"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"volume profile is missing fields: {', '.join(sorted(missing))}")
        bins = value["bins"]
        if not isinstance(bins, (list, tuple)):
            raise ValueError("volume profile bins must be a list")
        return cls(
            symbol=value["symbol"],
            as_of=value["as_of"],
            timeframe=value["timeframe"],
            lookback_days=value["lookback_days"],
            total_volume=value["total_volume"],
            bins=tuple(item if isinstance(item, VolumeProfileBin) else VolumeProfileBin.from_mapping(item) for item in bins),
            poc=value["poc"],
            value_area_low=value["value_area_low"],
            value_area_high=value["value_area_high"],
            high_volume_nodes=tuple(VolumeNode.from_mapping(item) for item in value.get("high_volume_nodes", ())),
            low_volume_nodes=tuple(VolumeNode.from_mapping(item) for item in value.get("low_volume_nodes", ())),
            data_confidence=value["data_confidence"],
            source=value["source"],
            ohlcv_hash=value["ohlcv_hash"],
            value_area_fraction=value.get("value_area_fraction", 0.7),
            metadata=value.get("metadata"),
            profile_hash=value.get("profile_hash", value.get("volume_profile_hash")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **_profile_payload(self),
            "profile_hash": self.profile_hash,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


__all__ = ["VolumeNode", "VolumeProfile", "VolumeProfileBin"]
