"""Validated portfolio snapshot and position models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .policy import Policy, policy_from_mapping, policy_hash, resolve_policy
from .time import normalize_timestamp


ASSET_TYPES = {"core", "satellite", "stablecoin", "cash", "other"}
EXTERNAL_CASH_FLOW_TYPES = {"NONE", "DEPOSIT", "WITHDRAWAL", "UNRESOLVED"}
_LEGACY_TIMESTAMP = "UNSPECIFIED"


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return value


def _optional_number(value: Any, field: str, *, minimum: float | None = None) -> float | None:
    return None if value is None else _number(value, field, minimum=minimum)


def _asset_type(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in ASSET_TYPES:
        raise ValueError(f"{field} must be one of {sorted(ASSET_TYPES)}")
    return value


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float | None = None
    value_usd: float = 0.0
    cost_basis_usd: float | None = None
    resolved_asset_type: str = "other"
    asset_type_hint: str | None = None
    current_price_usd: float | None = None
    average_cost_price_usd: float | None = None
    exchange_unrealized_pnl_usd: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("position.symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(
            self,
            "value_usd",
            _number(self.value_usd, f"position {self.symbol}.value_usd", minimum=0),
        )
        object.__setattr__(
            self,
            "quantity",
            _optional_number(self.quantity, f"position {self.symbol}.quantity", minimum=0),
        )
        object.__setattr__(
            self,
            "cost_basis_usd",
            _optional_number(
                self.cost_basis_usd,
                f"position {self.symbol}.cost_basis_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "current_price_usd",
            _optional_number(
                self.current_price_usd,
                f"position {self.symbol}.current_price_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "average_cost_price_usd",
            _optional_number(
                self.average_cost_price_usd,
                f"position {self.symbol}.average_cost_price_usd",
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "exchange_unrealized_pnl_usd",
            _optional_number(
                self.exchange_unrealized_pnl_usd,
                f"position {self.symbol}.exchange_unrealized_pnl_usd",
            ),
        )
        object.__setattr__(
            self,
            "resolved_asset_type",
            _asset_type(self.resolved_asset_type, f"position {self.symbol}.resolved_asset_type"),
        )
        if self.asset_type_hint is not None:
            object.__setattr__(
                self,
                "asset_type_hint",
                _asset_type(self.asset_type_hint, f"position {self.symbol}.asset_type_hint"),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "value_usd": self.value_usd,
            "cost_basis_usd": self.cost_basis_usd,
            "current_price_usd": self.current_price_usd,
            "average_cost_price_usd": self.average_cost_price_usd,
            "exchange_unrealized_pnl_usd": self.exchange_unrealized_pnl_usd,
            "asset_type": self.resolved_asset_type,
            **({"asset_type_hint": self.asset_type_hint} if self.asset_type_hint else {}),
        }


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: str
    base_currency: str = "USD"
    positions: tuple[Position, ...] = ()
    external_cash_flow: float = 0.0
    external_cash_flow_type: str | None = None
    total_value: float | None = None
    policy_version: int | None = None
    source: str | None = None
    portfolio_peak_value: float | None = None
    policy_hash: str | None = None
    resolved_policy: Mapping[str, Any] | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp == _LEGACY_TIMESTAMP:
            object.__setattr__(self, "timestamp", _LEGACY_TIMESTAMP)
        else:
            object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        positions = tuple(self.positions)
        if not positions:
            raise ValueError("positions must be a non-empty sequence")
        if any(not isinstance(position, Position) for position in positions):
            raise ValueError("positions must contain Position objects")
        symbols = [position.symbol for position in positions]
        if len(symbols) != len(set(symbols)):
            duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
            raise ValueError(f"duplicate position symbol(s): {', '.join(duplicates)}")
        object.__setattr__(self, "positions", positions)
        if not isinstance(self.base_currency, str) or not self.base_currency.strip():
            raise ValueError("base_currency must be a non-empty string")
        object.__setattr__(self, "base_currency", self.base_currency.strip().upper())
        object.__setattr__(
            self,
            "external_cash_flow",
            _number(self.external_cash_flow, "external_cash_flow"),
        )
        flow_type = self.external_cash_flow_type
        if flow_type is None:
            flow_type = (
                "DEPOSIT" if self.external_cash_flow > 0 else
                "WITHDRAWAL" if self.external_cash_flow < 0 else
                "UNRESOLVED"
            )
        if not isinstance(flow_type, str) or flow_type.strip().upper() not in EXTERNAL_CASH_FLOW_TYPES:
            raise ValueError(f"external_cash_flow_type must be one of {sorted(EXTERNAL_CASH_FLOW_TYPES)}")
        flow_type = flow_type.strip().upper()
        if flow_type == "NONE" and self.external_cash_flow != 0:
            raise ValueError("external_cash_flow_type NONE requires external_cash_flow 0")
        if flow_type == "DEPOSIT" and self.external_cash_flow <= 0:
            raise ValueError("external_cash_flow_type DEPOSIT requires a positive external_cash_flow")
        if flow_type == "WITHDRAWAL" and self.external_cash_flow >= 0:
            raise ValueError("external_cash_flow_type WITHDRAWAL requires a negative external_cash_flow")
        object.__setattr__(self, "external_cash_flow_type", flow_type)
        object.__setattr__(
            self,
            "total_value",
            _optional_number(self.total_value, "total_value", minimum=0),
        )
        if self.policy_version is not None and (
            isinstance(self.policy_version, bool)
            or not isinstance(self.policy_version, int)
            or self.policy_version < 1
        ):
            raise ValueError("policy_version must be a positive integer")
        if self.source is not None and not isinstance(self.source, str):
            raise ValueError("source must be a string or null")
        object.__setattr__(
            self,
            "portfolio_peak_value",
            _optional_number(self.portfolio_peak_value, "portfolio_peak_value", minimum=0),
        )
        if self.policy_hash is not None:
            if not isinstance(self.policy_hash, str) or len(self.policy_hash) != 64:
                raise ValueError("policy_hash must be a SHA-256 hex digest")
            try:
                int(self.policy_hash, 16)
            except ValueError as exc:
                raise ValueError("policy_hash must be a SHA-256 hex digest") from exc
            object.__setattr__(self, "policy_hash", self.policy_hash.lower())
        if self.resolved_policy is not None:
            if not isinstance(self.resolved_policy, Mapping):
                raise ValueError("resolved_policy must be an object or null")
            object.__setattr__(self, "resolved_policy", dict(self.resolved_policy))
            if self.resolved_policy.get("policy_version") != self.policy_version:
                raise ValueError("resolved_policy policy_version must match snapshot policy_version")
            if self.policy_hash is not None and policy_hash(self.resolved_policy) != self.policy_hash:
                raise ValueError("policy_hash does not match resolved_policy")
        if self.snapshot_id is not None:
            if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
                raise ValueError("snapshot_id must be a non-empty string or null")
            object.__setattr__(self, "snapshot_id", self.snapshot_id.strip())

    @property
    def total_value_usd(self) -> float:
        return sum(position.value_usd for position in self.positions)

    def weights(self) -> dict[str, float]:
        total = self.total_value_usd
        if total <= 0:
            raise ValueError("portfolio total must be > 0")
        return {position.symbol: position.value_usd / total for position in self.positions}

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "base_currency": self.base_currency,
            "policy_version": self.policy_version,
            "external_cash_flow": self.external_cash_flow,
            "external_cash_flow_type": self.external_cash_flow_type,
            "total_value": self.total_value,
            "policy_hash": self.policy_hash,
            "resolved_policy": self.resolved_policy,
            "snapshot_id": self.snapshot_id,
            "positions": [position.as_dict() for position in self.positions],
        }


def _position_from_mapping(raw: Mapping[str, Any], policy: Policy, index: int) -> Position:
    if not isinstance(raw, Mapping):
        raise ValueError(f"position {index} must be an object")
    raw_symbol = raw.get("symbol")
    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        raise ValueError(f"position {index} is missing symbol")
    symbol = raw_symbol.strip().upper()
    if "value_usd" not in raw:
        raise ValueError(f"position {symbol} is missing value_usd")

    hints = [raw.get(field) for field in ("asset_type_hint", "asset_type", "resolved_asset_type")]
    supplied_hints = [hint for hint in hints if hint is not None]
    if any(not isinstance(hint, str) or hint not in ASSET_TYPES for hint in supplied_hints):
        raise ValueError(f"position {symbol} contains an invalid asset type hint")
    if supplied_hints and any(hint != supplied_hints[0] for hint in supplied_hints[1:]):
        raise ValueError(f"position {symbol} contains conflicting asset type hints")
    hint = supplied_hints[0] if supplied_hints else None
    resolved = policy.classify(symbol)
    if hint is not None and hint != resolved:
        raise ValueError(
            f"position {symbol} asset_type_hint {hint!r} conflicts with resolved type {resolved!r}"
        )

    return Position(
        symbol=symbol,
        quantity=raw.get("quantity"),
        value_usd=raw["value_usd"],
        cost_basis_usd=raw.get("cost_basis_usd"),
        resolved_asset_type=resolved,
        asset_type_hint=hint,
        current_price_usd=raw.get("current_price_usd"),
        average_cost_price_usd=raw.get("average_cost_price_usd"),
        exchange_unrealized_pnl_usd=raw.get("exchange_unrealized_pnl_usd"),
    )


def snapshot_from_mapping(
    data: Mapping[str, Any], *, policy: Policy | None = None
) -> tuple[PortfolioSnapshot, Policy, list[str]]:
    if not isinstance(data, Mapping):
        raise ValueError("snapshot must be an object")
    if policy is not None:
        resolved_policy = policy
    elif data.get("resolved_policy") is not None:
        resolved_policy = policy_from_mapping(data["resolved_policy"])
    else:
        resolved_policy = resolve_policy(data.get("config"))
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise ValueError("positions must be a non-empty list")
    positions = tuple(
        _position_from_mapping(raw, resolved_policy, index)
        for index, raw in enumerate(raw_positions)
    )
    timestamp = data.get("timestamp")
    legacy_timestamp = timestamp is None
    expected_policy_version = resolved_policy.policy_version
    supplied_policy_version = data.get("policy_version", expected_policy_version)
    if supplied_policy_version != expected_policy_version:
        raise ValueError(
            f"snapshot policy_version {supplied_policy_version!r} does not match resolved policy "
            f"version {expected_policy_version}"
        )
    expected_policy_hash = policy_hash(resolved_policy)
    supplied_policy_hash = data.get("policy_hash", expected_policy_hash)
    if supplied_policy_hash != expected_policy_hash:
        raise ValueError("snapshot policy_hash does not match resolved policy")
    reported_total_value = data.get("total_value")
    if reported_total_value is None:
        reported_total_value = data.get("reported_total_value")
    flow_value = data.get("external_cash_flow", data.get("external_cash_flow_usd", 0.0))
    if "external_cash_flow" in data and "external_cash_flow_usd" in data and data["external_cash_flow"] != data["external_cash_flow_usd"]:
        raise ValueError("external_cash_flow and external_cash_flow_usd disagree")
    flow_type = data.get("external_cash_flow_type")
    if flow_type is None and ("external_cash_flow" in data or "external_cash_flow_usd" in data) and isinstance(flow_value, (int, float)) and not isinstance(flow_value, bool):
        flow_type = "DEPOSIT" if flow_value > 0 else "WITHDRAWAL" if flow_value < 0 else "NONE"
    snapshot = PortfolioSnapshot(
        timestamp=_LEGACY_TIMESTAMP if legacy_timestamp else timestamp,
        positions=positions,
        base_currency=data.get("base_currency", "USD"),
        external_cash_flow=flow_value,
        external_cash_flow_type=flow_type,
        total_value=reported_total_value,
        policy_version=supplied_policy_version,
        source=data.get("source"),
        portfolio_peak_value=data.get("portfolio_peak_value"),
        policy_hash=expected_policy_hash,
        resolved_policy=resolved_policy.as_dict(),
        snapshot_id=data.get("snapshot_id"),
    )
    total = snapshot.total_value_usd
    if total <= 0:
        raise ValueError("portfolio total must be > 0")

    warnings: list[str] = []
    if legacy_timestamp:
        warnings.append("timestamp is missing; legacy normalization cannot be used for ordered history")
    reported_total = data.get("reported_total_value")
    if reported_total is None:
        reported_total = data.get("total_value")
    if reported_total is not None:
        reported_total = _number(reported_total, "reported_total_value", minimum=0)
        if reported_total > 0:
            gap = abs(total - reported_total) / reported_total
            if gap > 0.01:
                warnings.append(f"sum of position values differs from reported total by {gap:.2%}")

    for index, raw in enumerate(raw_positions):
        if not isinstance(raw, Mapping):
            continue
        displayed = raw.get("displayed_weight")
        if displayed is None:
            continue
        displayed = _number(displayed, f"position {index}.displayed_weight", minimum=0)
        if displayed > 1:
            raise ValueError(f"position {index}.displayed_weight must be <= 1")
        position = positions[index]
        difference = abs(displayed - position.value_usd / total)
        if difference > 0.01:
            warnings.append(
                f"{position.symbol} displayed weight differs from computed weight by {difference:.2%}"
            )

    stable_value = sum(
        position.value_usd
        for position in positions
        if position.resolved_asset_type in {"stablecoin", "cash"}
    )
    stable_weight = stable_value / total
    if stable_weight < resolved_policy.min_stablecoin_weight:
        warnings.append(
            f"stablecoin weight {stable_weight:.2%} is below configured minimum "
            f"{resolved_policy.min_stablecoin_weight:.2%}"
        )

    if snapshot.portfolio_peak_value is not None:
        if snapshot.external_cash_flow != 0:
            warnings.append("portfolio_peak_value ignored because external cash flow requires NAV history")
        elif snapshot.portfolio_peak_value >= total and snapshot.portfolio_peak_value > 0:
            drawdown = total / snapshot.portfolio_peak_value - 1.0
            warnings.append("portfolio_peak_value is legacy; use cash-flow-aware NAV history for drawdown")
            if drawdown < -resolved_policy.max_portfolio_drawdown:
                warnings.append(
                    f"portfolio drawdown {drawdown:.2%} exceeds configured maximum "
                    f"{resolved_policy.max_portfolio_drawdown:.2%}"
                )
        elif snapshot.portfolio_peak_value > 0:
            warnings.append("portfolio_peak_value is below current total; drawdown omitted")

    return snapshot, resolved_policy, warnings


def normalize_snapshot(data: Mapping[str, Any], *, policy: Policy | None = None) -> dict[str, Any]:
    snapshot, resolved_policy, warnings = snapshot_from_mapping(data, policy=policy)
    total = snapshot.total_value_usd
    from ..engine.position_pnl import (
        calculate_portfolio_position_performance,
        position_performance_record,
    )

    performance = calculate_portfolio_position_performance(snapshot)
    positions = [
        position_performance_record(position, position_result)
        for position, position_result in zip(snapshot.positions, performance.positions)
    ]
    warnings.extend(performance.validation_notes)
    values = {
        "stablecoin": {"stablecoin", "cash"},
        "core": {"core"},
        "satellite": {"satellite"},
    }
    weights = {
        name: sum(
            position.value_usd
            for position in snapshot.positions
            if position.resolved_asset_type in types
        )
        / total
        for name, types in values.items()
    }
    drawdown = None
    if (
        snapshot.portfolio_peak_value
        and snapshot.external_cash_flow == 0
        and snapshot.portfolio_peak_value >= total
    ):
        drawdown = total / snapshot.portfolio_peak_value - 1.0

    reported_total = snapshot.total_value
    visible_coverage = None
    if reported_total is not None and reported_total > 0:
        visible_coverage = total / reported_total
        if visible_coverage < 0.99:
            warnings.append(
                f"visible position value covers only {visible_coverage:.2%} of reported total"
            )
        elif visible_coverage > 1.01:
            warnings.append(
                f"visible position value exceeds reported total by {visible_coverage - 1:.2%}"
            )

    return {
        "config": resolved_policy.legacy_config(),
        "policy_version": resolved_policy.policy_version,
        "timestamp": None if snapshot.timestamp == _LEGACY_TIMESTAMP else snapshot.timestamp,
        "source": snapshot.source,
        "base_currency": snapshot.base_currency,
        "total_value_usd": total,
        "reported_total_value_usd": reported_total,
        "visible_positions_value_usd": total,
        "visible_value_coverage_ratio": visible_coverage,
        "stablecoin_weight": weights["stablecoin"],
        "core_weight": weights["core"],
        "satellite_weight": weights["satellite"],
        "portfolio_drawdown": drawdown,
        "external_cash_flow": snapshot.external_cash_flow,
        "external_cash_flow_type": snapshot.external_cash_flow_type,
        "cost_known_current_value_usd": performance.cost_known_current_value_usd,
        "cost_known_cost_basis_usd": performance.cost_known_cost_basis_usd,
        "total_unrealized_pnl_known_usd": performance.total_unrealized_pnl_known_usd,
        "aggregate_unrealized_return_pct": performance.aggregate_unrealized_return_pct,
        "pnl_value_coverage_ratio": performance.pnl_value_coverage_ratio,
        "position_performance": performance.as_dict(),
        "positions": positions,
        "warnings": warnings,
    }


def classify_symbol(symbol: str, policy: Policy | None = None) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return (policy or resolve_policy()).classify(symbol)


__all__ = [
    "ASSET_TYPES",
    "EXTERNAL_CASH_FLOW_TYPES",
    "Position",
    "PortfolioSnapshot",
    "classify_symbol",
    "normalize_snapshot",
    "snapshot_from_mapping",
]
