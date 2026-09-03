"""Typed, validated execution-plan records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from .time import normalize_timestamp
from .volume_profile import VolumeNode
from .factor_packet import freeze_packet_value, thaw_packet_value


_ACTIONS = {"INCREASE", "REDUCE", "EXIT", "HOLD", "WAIT", "NO_TRADE"}
_ENTRY_MODES = {"PULLBACK", "BREAKOUT", "WAIT"}
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
        unknown = set(value) - {"low", "high", "midpoint", "kind", "strength", "sources"}
        if unknown:
            raise ValueError(f"price zone contains unknown fields: {', '.join(sorted(unknown))}")
        missing = [field for field in ("low", "high") if field not in value]
        if missing:
            raise ValueError(f"price zone is missing fields: {', '.join(missing)}")
        return cls(
            low=value["low"],
            high=value["high"],
            midpoint=value.get("midpoint"),
            kind=value.get("kind", "SUPPORT"),
            strength=value.get("strength", 0.0),
            sources=value.get("sources", ()),
        )


@dataclass(frozen=True)
class Invalidation:
    kind: str
    trigger: str
    reference_price: float
    review_only: bool = True
    automatic_order: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "invalidation.kind").upper())
        object.__setattr__(self, "trigger", _text(self.trigger, "invalidation.trigger"))
        reference_price = _number(self.reference_price, "invalidation.reference_price", minimum=0.0)
        if reference_price <= 0:
            raise ValueError("invalidation.reference_price must be > 0")
        if self.review_only is not True:
            raise ValueError("invalidation.review_only must be true")
        if self.automatic_order is not False:
            raise ValueError("invalidation.automatic_order must be false")
        object.__setattr__(self, "reference_price", reference_price)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Invalidation":
        if not isinstance(value, Mapping):
            raise ValueError("invalidation must be an object")
        allowed = {"kind", "trigger", "reference_price", "review_only", "automatic_order"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"invalidation contains unknown fields: {', '.join(sorted(unknown))}")
        required = {"kind", "trigger", "reference_price", "review_only", "automatic_order"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"invalidation is missing fields: {', '.join(sorted(missing))}")
        return cls(**{field: value[field] for field in required})

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "reference_price": self.reference_price,
            "review_only": self.review_only,
            "automatic_order": self.automatic_order,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


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
        structural_sources = _sources(self.structural_sources, "tranche structural_sources")
        if not structural_sources:
            raise ValueError("tranche structural_sources must not be empty")
        object.__setattr__(self, "allocation_fraction", fraction)
        object.__setattr__(self, "amount_usd", amount)
        object.__setattr__(self, "price_low", low)
        object.__setattr__(self, "price_high", high)
        object.__setattr__(self, "reference_price", reference)
        object.__setattr__(self, "estimated_quantity", quantity)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "structural_sources", structural_sources)
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
        allowed = {
            "sequence", "allocation_fraction", "amount_usd", "price_low", "price_high",
            "reference_price", "estimated_quantity", "rationale", "structural_sources",
            "zone_quality",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"execution tranche contains unknown fields: {', '.join(sorted(unknown))}")
        required = (
            "sequence", "allocation_fraction", "amount_usd", "price_low", "price_high",
            "reference_price", "estimated_quantity", "rationale", "structural_sources",
            "zone_quality",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"execution tranche is missing fields: {', '.join(missing)}")
        return cls(**{field: value[field] for field in required})


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
    invalidation: Invalidation | Mapping[str, Any] | str | None = None
    rationale: str = ""
    ohlcv_hash: str | None = None
    ohlcv_metadata: Mapping[str, Any] | None = None
    technical_summary: Mapping[str, Any] | None = None
    volume_profile_hash: str | None = None
    volume_profile_metadata: Mapping[str, Any] | None = None
    positioning_summary: Mapping[str, Any] | None = None
    btc_cycle_summary: Mapping[str, Any] | None = None
    effective_deployment_factor: float | None = None
    overlay_warnings: tuple[str, ...] = ()

    @property
    def profile_hash(self) -> str | None:
        """Compatibility alias used by the execution/reporting vocabulary."""
        return self.volume_profile_hash

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
        if self.volume_profile_hash is not None:
            object.__setattr__(self, "volume_profile_hash", _hash(self.volume_profile_hash, "volume_profile_hash"))
        if self.ohlcv_metadata is not None:
            if not isinstance(self.ohlcv_metadata, Mapping):
                raise ValueError("ohlcv_metadata must be an object or null")
            metadata = dict(self.ohlcv_metadata)
            allowed_metadata = {
                "symbol", "source", "timeframe", "start_timestamp", "end_timestamp", "candle_count",
                "fetched_at", "ohlcv_hash", "calendar_span_days", "missing_day_count", "coverage_ratio",
                "max_gap_days", "observation_lag_days", "latest_completed_candle_date",
                "expected_latest_completed_date", "as_of", "venue", "market", "quote_currency",
            }
            unknown_metadata = set(metadata) - allowed_metadata
            if unknown_metadata:
                raise ValueError(
                    f"ohlcv_metadata contains unknown fields: {', '.join(sorted(unknown_metadata))}"
                )
            required_metadata = {
                "symbol", "source", "timeframe", "start_timestamp", "end_timestamp",
                "candle_count", "fetched_at", "ohlcv_hash",
            }
            missing_metadata = required_metadata - set(metadata)
            if missing_metadata:
                raise ValueError(
                    f"ohlcv_metadata is missing fields: {', '.join(sorted(missing_metadata))}"
                )
            if not isinstance(metadata["symbol"], str) or metadata["symbol"].strip().upper() != self.symbol:
                raise ValueError("ohlcv_metadata symbol does not match plan symbol")
            metadata["symbol"] = metadata["symbol"].strip().upper()
            if not isinstance(metadata["source"], str) or not metadata["source"].strip():
                raise ValueError("ohlcv_metadata source must be a non-empty string")
            metadata["source"] = metadata["source"].strip()
            if str(metadata["timeframe"]).strip().upper() not in {"1H", "4H", "1D"}:
                raise ValueError("ohlcv_metadata timeframe must be 1H, 4H, or 1D")
            metadata["timeframe"] = str(metadata["timeframe"]).strip().upper()
            for field in ("start_timestamp", "end_timestamp"):
                metadata[field] = normalize_timestamp(metadata[field], f"ohlcv_metadata.{field}")
            if metadata["fetched_at"] is not None:
                metadata["fetched_at"] = normalize_timestamp(metadata["fetched_at"], "ohlcv_metadata.fetched_at")
            for field in ("as_of",):
                if field in metadata:
                    metadata[field] = normalize_timestamp(metadata[field], f"ohlcv_metadata.{field}")
            for field in ("latest_completed_candle_date", "expected_latest_completed_date"):
                if field in metadata:
                    value = metadata[field]
                    if not isinstance(value, str):
                        raise ValueError(f"ohlcv_metadata {field} must be a date")
                    try:
                        metadata[field] = date.fromisoformat(value).isoformat()
                    except ValueError as exc:
                        raise ValueError(f"ohlcv_metadata {field} must be a date") from exc
            for field in ("venue", "market", "quote_currency"):
                if field in metadata and metadata[field] is not None:
                    metadata[field] = _text(metadata[field], f"ohlcv_metadata.{field}")
            count = metadata["candle_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("ohlcv_metadata candle_count must be a positive integer")
            for field in ("calendar_span_days", "missing_day_count", "max_gap_days", "observation_lag_days"):
                if field in metadata:
                    value = metadata[field]
                    minimum = 1 if field == "calendar_span_days" else 0
                    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                        raise ValueError(f"ohlcv_metadata {field} is invalid")
            if "coverage_ratio" in metadata:
                ratio = _number(metadata["coverage_ratio"], "ohlcv_metadata.coverage_ratio", minimum=0.0)
                if ratio > 1:
                    raise ValueError("ohlcv_metadata coverage_ratio must be <= 1")
                metadata["coverage_ratio"] = ratio
            metadata_hash = metadata.get("ohlcv_hash")
            if metadata_hash is not None:
                metadata_hash = _hash(metadata_hash, "ohlcv_metadata.ohlcv_hash")
                metadata["ohlcv_hash"] = metadata_hash
            if self.ohlcv_hash and metadata_hash != self.ohlcv_hash:
                raise ValueError("ohlcv_metadata ohlcv_hash does not match plan ohlcv_hash")
            object.__setattr__(self, "ohlcv_metadata", metadata)
        if self.volume_profile_metadata is not None:
            if not isinstance(self.volume_profile_metadata, Mapping):
                raise ValueError("volume_profile_metadata must be an object or null")
            object.__setattr__(self, "volume_profile_metadata", dict(self.volume_profile_metadata))
        if self.invalidation is not None and not isinstance(self.invalidation, (str, Mapping)):
            if not isinstance(self.invalidation, Invalidation):
                raise ValueError("invalidation must be a typed object, string, or null")
        if isinstance(self.invalidation, str):
            object.__setattr__(self, "invalidation", _text(self.invalidation, "invalidation"))
        elif isinstance(self.invalidation, Mapping):
            object.__setattr__(self, "invalidation", Invalidation.from_mapping(self.invalidation))
        for field_name in ("positioning_summary", "btc_cycle_summary"):
            value = getattr(self, field_name)
            if value is not None:
                if hasattr(value, "as_dict"):
                    value = value.as_dict()
                if not isinstance(value, Mapping):
                    raise ValueError(f"{field_name} must be an object or null")
                object.__setattr__(self, field_name, freeze_packet_value(value, path=field_name))
        if self.effective_deployment_factor is not None:
            factor = _number(self.effective_deployment_factor, "effective_deployment_factor", minimum=0.0)
            if factor > 1:
                raise ValueError("effective_deployment_factor must be <= 1")
            object.__setattr__(self, "effective_deployment_factor", factor)
        warnings = tuple(_text(item, "overlay_warnings") for item in self.overlay_warnings)
        if len(warnings) != len(set(warnings)):
            raise ValueError("overlay_warnings must contain unique values")
        object.__setattr__(self, "overlay_warnings", warnings)
        if self.execution_plan_version >= 2 and self.technical_summary is None:
            raise ValueError("execution plan version 2 requires technical_summary")
        if self.technical_summary is not None:
            if not isinstance(self.technical_summary, Mapping):
                raise ValueError("technical_summary must be an object or null")
            summary = dict(self.technical_summary)
            allowed_summary = {
                "summary_version", "symbol", "spot_price", "spot_observed_at", "spot_source",
                "spot_fetched_at", "spot_venue", "spot_market", "spot_quote_currency", "ma20",
                "ma50", "ma100", "ma200", "atr14", "atr_percent", "return_30d", "return_90d",
                "return_180d", "realized_vol_30d", "realized_vol_90d", "relative_volume",
                "trend_state", "data_confidence", "setup_quality", "data_quality",
                "data_quality_flags", "selected_zones", "ohlcv_hash", "volume_profile_confidence",
                "volume_profile_poc", "volume_profile_val", "volume_profile_vah", "volume_hvns",
                "volume_lvns", "volume_profile_summary", "volume_profile_hash", "volume_profile_metadata",
            }
            unknown_summary = set(summary) - allowed_summary
            if unknown_summary:
                raise ValueError(
                    f"technical_summary contains unknown fields: {', '.join(sorted(unknown_summary))}"
                )
            required_summary = {
                "summary_version", "symbol", "spot_price", "spot_observed_at",
                "spot_source", "data_confidence", "setup_quality", "selected_zones",
                "ohlcv_hash",
            }
            missing_summary = required_summary - set(summary)
            if missing_summary:
                raise ValueError(
                    f"technical_summary is missing fields: {', '.join(sorted(missing_summary))}"
                )
            if (
                isinstance(summary["summary_version"], bool)
                or not isinstance(summary["summary_version"], int)
                or summary["summary_version"] != 1
            ):
                raise ValueError("technical_summary summary_version must be 1")
            if not isinstance(summary["symbol"], str) or summary["symbol"].strip().upper() != self.symbol:
                raise ValueError("technical_summary symbol does not match plan symbol")
            summary["symbol"] = self.symbol
            spot = _number(summary["spot_price"], "technical_summary.spot_price", minimum=0.0)
            if spot <= 0:
                raise ValueError("technical_summary.spot_price must be > 0")
            summary["spot_price"] = spot
            summary["spot_observed_at"] = normalize_timestamp(
                summary["spot_observed_at"], "technical_summary.spot_observed_at"
            )
            if not isinstance(summary["spot_source"], str) or not summary["spot_source"].strip():
                raise ValueError("technical_summary spot_source must be a non-empty string")
            summary["spot_source"] = summary["spot_source"].strip()
            if str(summary["data_confidence"]).strip().upper() not in _CONFIDENCE:
                raise ValueError("technical_summary data_confidence is unsupported")
            summary["data_confidence"] = summary["data_confidence"].strip().upper()
            quality = _number(summary["setup_quality"], "technical_summary.setup_quality", minimum=0.0)
            if quality > 100:
                raise ValueError("technical_summary.setup_quality must be <= 100")
            summary["setup_quality"] = quality
            if not isinstance(summary["selected_zones"], list):
                raise ValueError("technical_summary selected_zones must be a list")
            summary["selected_zones"] = [
                (zone if isinstance(zone, PriceZone) else PriceZone.from_mapping(zone)).as_dict()
                for zone in summary["selected_zones"]
            ]
            positive_metrics = {"ma20", "ma50", "ma100", "ma200", "atr14"}
            for field in (
                "ma20", "ma50", "ma100", "ma200", "atr14", "atr_percent",
                "return_30d", "return_90d", "return_180d", "realized_vol_30d",
                "realized_vol_90d", "relative_volume",
            ):
                if field in summary and summary[field] is not None:
                    minimum = 0.0 if field not in {"return_30d", "return_90d", "return_180d"} else -1.0
                    summary[field] = _number(summary[field], f"technical_summary.{field}", minimum=minimum)
                    if field in positive_metrics and summary[field] <= 0:
                        raise ValueError(f"technical_summary.{field} must be > 0")
            for field in ("volume_profile_poc", "volume_profile_val", "volume_profile_vah"):
                if field in summary and summary[field] is not None:
                    summary[field] = _number(summary[field], f"technical_summary.{field}", minimum=0.0)
                    if summary[field] <= 0:
                        raise ValueError(f"technical_summary.{field} must be > 0")
            if summary.get("volume_profile_confidence") is not None:
                confidence = _text(
                    summary["volume_profile_confidence"],
                    "technical_summary.volume_profile_confidence",
                ).upper()
                if confidence not in {"HIGH", "MEDIUM", "LOW", "UNAVAILABLE"}:
                    raise ValueError("technical_summary volume_profile_confidence is unsupported")
                summary["volume_profile_confidence"] = confidence
            for field in ("volume_hvns", "volume_lvns"):
                if field in summary and summary[field] is not None:
                    if not isinstance(summary[field], list):
                        raise ValueError(f"technical_summary {field} must be a list")
                    summary[field] = [
                        (node if isinstance(node, VolumeNode) else VolumeNode.from_mapping(node)).as_dict()
                        for node in summary[field]
                    ]
            if summary.get("spot_fetched_at") is not None:
                summary["spot_fetched_at"] = normalize_timestamp(
                    summary["spot_fetched_at"], "technical_summary.spot_fetched_at"
                )
            if summary.get("data_quality_flags") is not None:
                if not isinstance(summary["data_quality_flags"], list):
                    raise ValueError("technical_summary data_quality_flags must be a list")
                flags = tuple(_text(item, "technical_summary.data_quality_flags") for item in summary["data_quality_flags"])
                if len(flags) != len(set(flags)):
                    raise ValueError("technical_summary data_quality_flags must be unique")
                summary["data_quality_flags"] = list(flags)
            for field in ("spot_venue", "spot_market", "spot_quote_currency", "trend_state", "data_quality"):
                if field in summary and summary[field] is not None:
                    summary[field] = _text(summary[field], f"technical_summary.{field}")
            if summary["ohlcv_hash"] is not None:
                summary["ohlcv_hash"] = _hash(summary["ohlcv_hash"], "technical_summary.ohlcv_hash")
            if self.ohlcv_hash and summary["ohlcv_hash"] != self.ohlcv_hash:
                raise ValueError("technical_summary ohlcv_hash does not match plan ohlcv_hash")
            if summary.get("volume_profile_hash") is not None:
                summary["volume_profile_hash"] = _hash(
                    summary["volume_profile_hash"],
                    "technical_summary.volume_profile_hash",
                )
            if self.volume_profile_hash and summary.get("volume_profile_hash") != self.volume_profile_hash:
                raise ValueError("technical_summary volume_profile_hash does not match plan volume_profile_hash")
            for field in ("volume_profile_summary", "volume_profile_metadata"):
                if field in summary and summary[field] is not None:
                    if not isinstance(summary[field], Mapping):
                        raise ValueError(f"technical_summary {field} must be an object or null")
                    summary[field] = dict(summary[field])
            object.__setattr__(self, "technical_summary", summary)

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
            "volume_profile_hash": self.volume_profile_hash,
            "profile_hash": self.volume_profile_hash,
        }
        if self.ohlcv_metadata is not None:
            result["ohlcv_metadata"] = dict(self.ohlcv_metadata)
        if self.volume_profile_metadata is not None:
            result["volume_profile_metadata"] = dict(self.volume_profile_metadata)
        if self.technical_summary is not None:
            result["technical_summary"] = dict(self.technical_summary)
        if self.positioning_summary is not None:
            result["positioning_summary"] = thaw_packet_value(self.positioning_summary)
        if self.btc_cycle_summary is not None:
            result["btc_cycle_summary"] = thaw_packet_value(self.btc_cycle_summary)
        if self.effective_deployment_factor is not None:
            result["effective_deployment_factor"] = self.effective_deployment_factor
        if self.overlay_warnings:
            result["overlay_warnings"] = list(self.overlay_warnings)
        if isinstance(self.invalidation, Invalidation):
            result["invalidation"] = self.invalidation.as_dict()
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionPlan":
        if not isinstance(value, Mapping):
            raise ValueError("execution plan must be an object")
        allowed = {
            "execution_plan_version", "symbol", "action", "approved_amount_usd",
            "planned_amount_usd", "unallocated_amount_usd", "current_price", "entry_mode",
            "technical_confidence", "tranches", "invalidation", "rationale", "ohlcv_hash",
            "volume_profile_hash", "profile_hash", "volume_profile_metadata", "ohlcv_metadata", "technical_summary",
            "positioning_summary", "btc_cycle_summary", "effective_deployment_factor", "overlay_warnings",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"execution plan contains unknown fields: {', '.join(sorted(unknown))}")
        required = (
            "execution_plan_version", "symbol", "action", "approved_amount_usd",
            "planned_amount_usd", "unallocated_amount_usd", "current_price", "entry_mode",
            "technical_confidence", "tranches", "invalidation", "rationale", "ohlcv_hash",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"execution plan is missing fields: {', '.join(missing)}")
        if (
            value.get("volume_profile_hash") is not None
            and value.get("profile_hash") is not None
            and value["volume_profile_hash"] != value["profile_hash"]
        ):
            raise ValueError("volume_profile_hash and profile_hash disagree")
        raw_tranches = value.get("tranches", ())
        if not isinstance(raw_tranches, (list, tuple)):
            raise ValueError("execution plan tranches must be a list")
        return cls(
            **{
                field: value[field]
                for field in required
                if field not in {"tranches", "invalidation", "rationale", "ohlcv_hash"}
            },
            tranches=tuple(
                item if isinstance(item, ExecutionTranche) else ExecutionTranche.from_mapping(item)
                for item in raw_tranches
            ),
            invalidation=value["invalidation"],
            rationale=value["rationale"],
            ohlcv_hash=value["ohlcv_hash"],
            volume_profile_hash=value.get("volume_profile_hash", value.get("profile_hash")),
            volume_profile_metadata=value.get("volume_profile_metadata"),
            ohlcv_metadata=value.get("ohlcv_metadata"),
            technical_summary=value.get("technical_summary"),
            positioning_summary=value.get("positioning_summary"),
            btc_cycle_summary=value.get("btc_cycle_summary"),
            effective_deployment_factor=value.get("effective_deployment_factor"),
            overlay_warnings=tuple(value.get("overlay_warnings", ())),
        )


__all__ = ["ExecutionPlan", "ExecutionTranche", "Invalidation", "PriceZone"]
