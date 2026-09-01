"""Typed, validated execution-plan records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_ENTRY_MODES = {"PULLBACK", "BREAKOUT", "MIXED", "WAIT"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return result


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _hash(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(result, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return result


def _sources(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a sequence of strings")
    result = tuple(_text(item, f"{field}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicate sources")
    return result


@dataclass(frozen=True)
class PriceZone:
    """An ATR-aware structural price range."""

    low: float
    high: float
    midpoint: float | None = None
    kind: str = "SUPPORT"
    strength: float = 0.0
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        low = _number(self.low, "price zone low", minimum=0.0)
        high = _number(self.high, "price zone high", minimum=0.0)
        if low <= 0 or high <= 0:
            raise ValueError("price zone prices must be > 0")
        if low > high:
            raise ValueError("price zone low must be <= high")
        midpoint = (low + high) / 2 if self.midpoint is None else _number(self.midpoint, "price zone midpoint", minimum=0.0)
        if not low <= midpoint <= high:
            raise ValueError("price zone midpoint must be within low and high")
        kind = _text(self.kind, "price zone kind").upper()
        strength = _number(self.strength, "price zone strength", minimum=0.0)
        if strength > 100:
            raise ValueError("price zone strength must be <= 100")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)
        object.__setattr__(self, "midpoint", midpoint)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "sources", _sources(self.sources, "price zone sources"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "midpoint": self.midpoint,
            "kind": self.kind,
            "strength": self.strength,
            "sources": list(self.sources),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PriceZone":
        if not isinstance(value, Mapping):
            raise ValueError("price zone must be an object")
        return cls(
            low=value["low"],
            high=value["high"],
            midpoint=value.get("midpoint"),
            kind=value.get("kind", "SUPPORT"),
            strength=value.get("strength", 0.0),
            sources=value.get("sources", ()),
        )


@dataclass(frozen=True)
class ExecutionTranche:
    sequence: int
    allocation_fraction: float
    amount_usd: float
    price_low: float
    price_high: float
    reference_price: float
    estimated_quantity: float
    rationale: str = ""
    structural_sources: tuple[str, ...] = ()
    zone_quality: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("tranche sequence must be a positive integer")
        fraction = _number(self.allocation_fraction, "tranche allocation_fraction")
        if not 0 < fraction <= 1:
            raise ValueError("tranche allocation_fraction must be in (0, 1]")
        amount = _number(self.amount_usd, "tranche amount_usd", minimum=0.0)
        if amount <= 0:
            raise ValueError("tranche amount_usd must be > 0")
        low = _number(self.price_low, "tranche price_low", minimum=0.0)
        high = _number(self.price_high, "tranche price_high", minimum=0.0)
        reference = _number(self.reference_price, "tranche reference_price", minimum=0.0)
        quantity = _number(self.estimated_quantity, "tranche estimated_quantity", minimum=0.0)
        if min(low, high, reference) <= 0 or quantity <= 0:
            raise ValueError("tranche prices and estimated_quantity must be > 0")
        if low > high or not low <= reference <= high:
            raise ValueError("tranche prices must satisfy low <= reference <= high")
        expected = amount / reference
        if not math.isclose(quantity, expected, rel_tol=1e-2, abs_tol=1e-12):
            raise ValueError("tranche estimated_quantity must equal amount_usd / reference_price")
        rationale = _text(self.rationale, "tranche rationale", allow_empty=True)
        quality = _number(self.zone_quality, "tranche zone_quality", minimum=0.0)
        if quality > 100:
            raise ValueError("tranche zone_quality must be <= 100")
        object.__setattr__(self, "allocation_fraction", fraction)
        object.__setattr__(self, "amount_usd", amount)
        object.__setattr__(self, "price_low", low)
        object.__setattr__(self, "price_high", high)
        object.__setattr__(self, "reference_price", reference)
        object.__setattr__(self, "estimated_quantity", quantity)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "structural_sources", _sources(self.structural_sources, "tranche structural_sources"))
        object.__setattr__(self, "zone_quality", quality)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "allocation_fraction": self.allocation_fraction,
            "amount_usd": self.amount_usd,
            "price_low": self.price_low,
            "price_high": self.price_high,
            "reference_price": self.reference_price,
            "estimated_quantity": self.estimated_quantity,
            "rationale": self.rationale,
            "structural_sources": list(self.structural_sources),
            "zone_quality": self.zone_quality,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionTranche":
        if not isinstance(value, Mapping):
            raise ValueError("execution tranche must be an object")
        required = (
            "sequence", "allocation_fraction", "amount_usd", "price_low", "price_high",
            "reference_price", "estimated_quantity",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"execution tranche is missing fields: {', '.join(missing)}")
        return cls(
            **{field: value[field] for field in required},
            rationale=value.get("rationale", ""),
            structural_sources=value.get("structural_sources", ()),
            zone_quality=value.get("zone_quality", 0.0),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    execution_plan_version: int
    symbol: str
    action: str
    approved_amount_usd: float
    planned_amount_usd: float
    unallocated_amount_usd: float
    current_price: float
    entry_mode: str
    technical_confidence: str
    tranches: tuple[ExecutionTranche, ...] = ()
    invalidation: Mapping[str, Any] | str | None = None
    rationale: str = ""
    ohlcv_hash: str | None = None
    ohlcv_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.execution_plan_version, bool) or not isinstance(self.execution_plan_version, int) or self.execution_plan_version < 1:
            raise ValueError("execution_plan_version must be a positive integer")
        object.__setattr__(self, "symbol", _text(self.symbol, "execution symbol").upper())
        action = _text(self.action, "execution action").upper()
        if action not in _ACTIONS:
            raise ValueError(f"execution action must be one of {sorted(_ACTIONS)}")
        object.__setattr__(self, "action", action)
        amounts = {
            field: _number(getattr(self, field), field, minimum=0.0)
            for field in ("approved_amount_usd", "planned_amount_usd", "unallocated_amount_usd")
        }
        if amounts["planned_amount_usd"] > amounts["approved_amount_usd"] + 1e-9:
            raise ValueError("planned_amount_usd must not exceed approved_amount_usd")
        if not math.isclose(
            amounts["planned_amount_usd"] + amounts["unallocated_amount_usd"],
            amounts["approved_amount_usd"],
            rel_tol=1e-9,
            abs_tol=1e-7,
        ):
            raise ValueError("planned_amount_usd plus unallocated_amount_usd must equal approved_amount_usd")
        object.__setattr__(self, "approved_amount_usd", amounts["approved_amount_usd"])
        object.__setattr__(self, "planned_amount_usd", amounts["planned_amount_usd"])
        object.__setattr__(self, "unallocated_amount_usd", amounts["unallocated_amount_usd"])
        object.__setattr__(self, "current_price", _number(self.current_price, "current_price", minimum=0.0))
        if self.current_price <= 0:
            raise ValueError("current_price must be > 0")
        entry_mode = _text(self.entry_mode, "entry_mode").upper()
        if entry_mode not in _ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of {sorted(_ENTRY_MODES)}")
        object.__setattr__(self, "entry_mode", entry_mode)
        confidence = _text(self.technical_confidence, "technical_confidence").upper()
        if confidence not in _CONFIDENCE:
            raise ValueError(f"technical_confidence must be one of {sorted(_CONFIDENCE)}")
        object.__setattr__(self, "technical_confidence", confidence)
        tranches = tuple(self.tranches)
        if any(not isinstance(item, ExecutionTranche) for item in tranches):
            raise ValueError("tranches must contain ExecutionTranche objects")
        sequences = [item.sequence for item in tranches]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("tranche sequences must be contiguous and start at 1")
        if tranches and not math.isclose(sum(item.amount_usd for item in tranches), self.planned_amount_usd, rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError("tranche amounts must sum to planned_amount_usd")
        if tranches and not math.isclose(sum(item.allocation_fraction for item in tranches), 1.0, abs_tol=1e-9):
            raise ValueError("tranche allocation fractions must sum to 1")
        if not tranches and amounts["planned_amount_usd"] > 1e-7:
            raise ValueError("a plan with deployed dollars must contain tranches")
        if action in {"WAIT", "HOLD", "NO_TRADE"} and amounts["planned_amount_usd"] > 1e-7:
            raise ValueError(f"{action} plans cannot deploy dollars")
        object.__setattr__(self, "tranches", tranches)
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale", allow_empty=True))
        if self.ohlcv_hash is not None:
            object.__setattr__(self, "ohlcv_hash", _hash(self.ohlcv_hash, "ohlcv_hash"))
        if self.ohlcv_metadata is not None:
            if not isinstance(self.ohlcv_metadata, Mapping):
                raise ValueError("ohlcv_metadata must be an object or null")
            object.__setattr__(self, "ohlcv_metadata", dict(self.ohlcv_metadata))
            metadata_hash = self.ohlcv_metadata.get("ohlcv_hash")
            if metadata_hash is not None and self.ohlcv_hash and metadata_hash != self.ohlcv_hash:
                raise ValueError("ohlcv_metadata ohlcv_hash does not match plan ohlcv_hash")
            metadata_symbol = self.ohlcv_metadata.get("symbol")
            if metadata_symbol is not None and str(metadata_symbol).strip().upper() != self.symbol:
                raise ValueError("ohlcv_metadata symbol does not match plan symbol")
        if self.invalidation is not None and not isinstance(self.invalidation, (str, Mapping)):
            raise ValueError("invalidation must be a string, object, or null")
        if isinstance(self.invalidation, str):
            object.__setattr__(self, "invalidation", _text(self.invalidation, "invalidation"))
        elif isinstance(self.invalidation, Mapping):
            object.__setattr__(self, "invalidation", dict(self.invalidation))

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "execution_plan_version": self.execution_plan_version,
            "symbol": self.symbol,
            "action": self.action,
            "approved_amount_usd": self.approved_amount_usd,
            "planned_amount_usd": self.planned_amount_usd,
            "unallocated_amount_usd": self.unallocated_amount_usd,
            "current_price": self.current_price,
            "entry_mode": self.entry_mode,
            "technical_confidence": self.technical_confidence,
            "tranches": [item.as_dict() for item in self.tranches],
            "invalidation": self.invalidation,
            "rationale": self.rationale,
            "ohlcv_hash": self.ohlcv_hash,
        }
        if self.ohlcv_metadata is not None:
            result["ohlcv_metadata"] = dict(self.ohlcv_metadata)
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionPlan":
        if not isinstance(value, Mapping):
            raise ValueError("execution plan must be an object")
        required = (
            "execution_plan_version", "symbol", "action", "approved_amount_usd",
            "planned_amount_usd", "unallocated_amount_usd", "current_price", "entry_mode",
            "technical_confidence",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"execution plan is missing fields: {', '.join(missing)}")
        raw_tranches = value.get("tranches", ())
        if not isinstance(raw_tranches, (list, tuple)):
            raise ValueError("execution plan tranches must be a list")
        return cls(
            **{field: value[field] for field in required},
            tranches=tuple(
                item if isinstance(item, ExecutionTranche) else ExecutionTranche.from_mapping(item)
                for item in raw_tranches
            ),
            invalidation=value.get("invalidation"),
            rationale=value.get("rationale", ""),
            ohlcv_hash=value.get("ohlcv_hash"),
            ohlcv_metadata=value.get("ohlcv_metadata"),
        )


__all__ = ["ExecutionPlan", "ExecutionTranche", "PriceZone"]
