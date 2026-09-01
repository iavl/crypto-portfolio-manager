"""Validated portfolio snapshot and position models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from .policy import Policy, resolve_policy


ASSET_TYPES = {"core", "satellite", "stablecoin", "cash", "other"}
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
            "asset_type": self.resolved_asset_type,
            **({"asset_type_hint": self.asset_type_hint} if self.asset_type_hint else {}),
        }


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: str
    base_currency: str = "USD"
    positions: tuple[Position, ...] = ()
    external_cash_flow: float = 0.0
    total_value: float | None = None
    policy_version: int | None = None
    source: str | None = None
    portfolio_peak_value: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str) or not self.timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        object.__setattr__(self, "timestamp", self.timestamp.strip())
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
            "total_value": self.total_value,
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
    )


def snapshot_from_mapping(
    data: Mapping[str, Any], *, policy: Policy | None = None
) -> tuple[PortfolioSnapshot, Policy, list[str]]:
    if not isinstance(data, Mapping):
        raise ValueError("snapshot must be an object")
    resolved_policy = policy or resolve_policy(data.get("config"))
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise ValueError("positions must be a non-empty list")
    positions = tuple(
        _position_from_mapping(raw, resolved_policy, index)
        for index, raw in enumerate(raw_positions)
    )
    timestamp = data.get("timestamp")
    legacy_timestamp = timestamp is None
    snapshot = PortfolioSnapshot(
        timestamp=_LEGACY_TIMESTAMP if legacy_timestamp else timestamp,
        positions=positions,
        base_currency=data.get("base_currency", "USD"),
        external_cash_flow=data.get("external_cash_flow", 0.0),
        total_value=data.get("total_value", data.get("reported_total_value")),
        policy_version=(
            resolved_policy.policy_version
            if "policy_version" not in data
            else data["policy_version"]
        ),
        source=data.get("source"),
        portfolio_peak_value=data.get("portfolio_peak_value"),
    )
    total = snapshot.total_value_usd
    if total <= 0:
        raise ValueError("portfolio total must be > 0")

    warnings: list[str] = []
    if legacy_timestamp:
        warnings.append("timestamp is missing; legacy normalization cannot be used for ordered history")
    reported_total = data.get("reported_total_value", data.get("total_value"))
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
    positions = [
        {
            **position.as_dict(),
            "computed_weight": position.value_usd / total,
        }
        for position in snapshot.positions
    ]
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

    return {
        "config": resolved_policy.legacy_config(),
        "policy_version": resolved_policy.policy_version,
        "timestamp": None if snapshot.timestamp == _LEGACY_TIMESTAMP else snapshot.timestamp,
        "base_currency": snapshot.base_currency,
        "total_value_usd": total,
        "stablecoin_weight": weights["stablecoin"],
        "core_weight": weights["core"],
        "satellite_weight": weights["satellite"],
        "portfolio_drawdown": drawdown,
        "external_cash_flow": snapshot.external_cash_flow,
        "positions": positions,
        "warnings": warnings,
    }


def classify_symbol(symbol: str, policy: Policy | None = None) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return (policy or resolve_policy()).classify(symbol)


__all__ = [
    "ASSET_TYPES",
    "Position",
    "PortfolioSnapshot",
    "classify_symbol",
    "normalize_snapshot",
    "snapshot_from_mapping",
]
