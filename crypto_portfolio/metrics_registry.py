"""Canonical decision-relevant metric definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Mapping


_EXPECTED_TYPES = {"number", "string"}
_DIRECTIONS = {"HIGHER_IS_BETTER", "LOWER_IS_BETTER", "CONTEXTUAL"}
_FALLBACK_MODES = {"STRUCTURED_ONLY", "WEB_ALLOWED"}
_DECISION_ROLES = {
    "SCORING_FACTOR",
    "EVENT_RISK",
    "POSITIONING_OVERLAY",
    "CYCLE_CONTEXT",
    "EXECUTION_CONTEXT",
    "STRUCTURAL_RISK",
}
DECISION_ROLES = tuple(sorted(_DECISION_ROLES))
REVIEW_TYPES = ("SNAPSHOT_REVIEW", "FULL_REVIEW", "EVENT_REVIEW")
_FRESHNESS = {"CURRENT", "STALE", "UNKNOWN"}
_FRESHNESS_WINDOW = re.compile(r"^[1-9][0-9]*d$")
_CHAIN_NATIVE_ASSETS = ("BTC", "ETH", "SOL", "BNB")
CHAIN_NATIVE_ASSETS = _CHAIN_NATIVE_ASSETS
_ACTIVE_ADDRESS_ASSETS = ("BTC", "ETH", "SOL")
_TRANSFER_VOLUME_ASSETS = ("BTC", "ETH", "SOL")
_PROTOCOL_ASSETS = ("BTC", "ETH", "SOL", "BNB", "AAVE")
_APPLICATION_ASSETS = ("ETH", "SOL", "BNB", "AAVE")
# DeFiLlama defines an application's dailyRevenue as a fixed share of its
# dailyFees.  Scoring both as independent fundamentals growth evidence counts
# one source twice, so BNB uses only its dedicated market-cap-to-network-fees
# valuation scale, its own on-chain fee windows, and TVL/stablecoin scale.
_FEE_REVENUE_EVIDENCE_ASSETS = tuple(asset for asset in _APPLICATION_ASSETS if asset != "BNB")
_BNB_ASSETS = ("BNB",)
_ETH_ASSETS = ("ETH",)
_FDV_ASSETS = tuple(asset for asset in _PROTOCOL_ASSETS if asset != "ETH")
_FDV_RATIO_ASSETS = tuple(asset for asset in _PROTOCOL_ASSETS if asset != "ETH")
_DEVELOPER_ACTIVITY_ASSETS = ("ETH", "AAVE")
_UNLOCK_ASSETS: tuple[str, ...] = ()
_DERIVATIVES_ASSETS = _PROTOCOL_ASSETS
_TRANSACTION_COUNT_ASSETS = ("BTC", "ETH")
_TOKENOMICS_ASSETS = ("BTC", "ETH")
_DELIVERY_BASIS_ASSETS = ("BTC", "ETH")


def _freshness(value: Any, field: str) -> str | int | float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a freshness state or day window")
    if isinstance(value, (int, float)):
        if value <= 0 or value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{field} must be a positive finite day window")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a freshness state or day window")
    result = value.strip().upper()
    if result not in _FRESHNESS and not _FRESHNESS_WINDOW.fullmatch(result.lower()):
        raise ValueError(f"{field} must be CURRENT, STALE, UNKNOWN, or Nd")
    return result.lower() if result.lower().endswith("d") else result


def _scope(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ValueError("metric definition asset_scope must be a sequence of symbols or null")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("metric definition asset_scope must contain strings")
    result = tuple(item.strip().upper() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError("metric definition asset_scope must contain unique non-empty symbols")
    return result


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    factor: str
    expected_type: str | None = None
    unit: str | None = None
    direction: str = "CONTEXTUAL"
    default_freshness: str | int | float = "CURRENT"
    critical: bool = False
    trend_comparison_enabled: bool = True
    asset_scope: tuple[str, ...] | None = None
    value_type: str | None = None
    freshness: str | int | float | None = None
    trend_enabled: bool | None = None
    decision_role: str = "SCORING_FACTOR"
    context_group: str | None = None
    critical_review_types: tuple[str, ...] | None = None
    fallback_mode: str = "STRUCTURED_ONLY"

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("metric definition key must be a non-empty string")
        object.__setattr__(self, "key", self.key.strip().lower())
        if not isinstance(self.factor, str) or not self.factor.strip():
            raise ValueError("metric definition factor must be a non-empty string")
        object.__setattr__(self, "factor", self.factor.strip().lower())
        expected_type = self.expected_type if self.value_type is None else self.value_type
        expected_type = expected_type.strip().lower() if isinstance(expected_type, str) else expected_type
        supplied_expected = self.expected_type.strip().lower() if isinstance(self.expected_type, str) else self.expected_type
        supplied_value = self.value_type.strip().lower() if isinstance(self.value_type, str) else self.value_type
        if self.expected_type is not None and self.value_type is not None and supplied_expected != supplied_value:
            raise ValueError("metric definition expected_type and value_type disagree")
        if expected_type not in _EXPECTED_TYPES:
            raise ValueError("metric definition expected_type is unsupported")
        object.__setattr__(self, "expected_type", expected_type)
        object.__setattr__(self, "value_type", expected_type)
        if self.unit is not None and (not isinstance(self.unit, str) or not self.unit.strip()):
            raise ValueError("metric definition unit must be a non-empty string or null")
        direction = str(self.direction).strip().upper()
        if direction not in _DIRECTIONS:
            raise ValueError("metric definition direction is unsupported")
        object.__setattr__(self, "direction", direction)
        freshness = self.default_freshness if self.freshness is None else self.freshness
        freshness = _freshness(freshness, "metric definition freshness")
        object.__setattr__(self, "default_freshness", freshness)
        object.__setattr__(self, "freshness", freshness)
        if not isinstance(self.critical, bool):
            raise ValueError("metric definition critical must be boolean")
        critical_review_types = self.critical_review_types
        if critical_review_types is not None:
            if isinstance(critical_review_types, str) or not isinstance(critical_review_types, (tuple, list)):
                raise ValueError("metric definition critical_review_types must be a sequence or null")
            normalized_review_types = tuple(str(item).strip().upper() for item in critical_review_types)
            if any(item not in REVIEW_TYPES for item in normalized_review_types):
                raise ValueError("metric definition critical_review_types contains an unknown review type")
            if len(normalized_review_types) != len(set(normalized_review_types)):
                raise ValueError("metric definition critical_review_types must be unique")
            critical_review_types = normalized_review_types
        object.__setattr__(self, "critical_review_types", critical_review_types)
        trend_enabled = self.trend_comparison_enabled if self.trend_enabled is None else self.trend_enabled
        if (
            self.trend_enabled is not None
            and self.trend_comparison_enabled is not True
            and self.trend_comparison_enabled != self.trend_enabled
        ):
            raise ValueError("metric definition trend_comparison_enabled and trend_enabled disagree")
        if not isinstance(trend_enabled, bool):
            raise ValueError("metric definition trend_comparison_enabled must be boolean")
        object.__setattr__(self, "trend_comparison_enabled", trend_enabled)
        object.__setattr__(self, "trend_enabled", trend_enabled)
        object.__setattr__(self, "asset_scope", _scope(self.asset_scope))
        role = str(self.decision_role).strip().upper()
        if role not in _DECISION_ROLES:
            raise ValueError("metric definition decision_role is unsupported")
        context_group = self.context_group
        if context_group is not None:
            if not isinstance(context_group, str) or not context_group.strip():
                raise ValueError("metric definition context_group must be a non-empty string or null")
            context_group = context_group.strip().lower()
        if role != "SCORING_FACTOR" and context_group is None:
            raise ValueError("overlay metric definitions require context_group")
        if role == "SCORING_FACTOR" and context_group is not None:
            raise ValueError("scoring metric definitions must not set context_group")
        object.__setattr__(self, "decision_role", role)
        object.__setattr__(self, "context_group", context_group)
        fallback_mode = str(self.fallback_mode).strip().upper()
        if fallback_mode not in _FALLBACK_MODES:
            raise ValueError("metric definition fallback_mode is unsupported")
        object.__setattr__(self, "fallback_mode", fallback_mode)

    def applies_to(self, asset: str) -> bool:
        """Return whether this definition explicitly covers ``asset``."""
        if not isinstance(asset, str) or not asset.strip():
            raise ValueError("asset must be a non-empty string")
        return self.asset_scope is None or asset.strip().upper() in self.asset_scope

    def is_critical_for(self, review_type: str) -> bool:
        """Return whether a failed value is hard-critical for this review."""
        if not isinstance(review_type, str) or review_type.strip().upper() not in REVIEW_TYPES:
            raise ValueError(f"review_type must be one of {list(REVIEW_TYPES)}")
        if self.critical_review_types is None:
            return self.critical
        return review_type.strip().upper() in self.critical_review_types

    @property
    def is_scoring_factor(self) -> bool:
        return self.decision_role == "SCORING_FACTOR"

    @property
    def is_overlay(self) -> bool:
        return self.decision_role in {
            "POSITIONING_OVERLAY", "CYCLE_CONTEXT", "EXECUTION_CONTEXT", "STRUCTURAL_RISK"
        }

    @property
    def is_event_risk(self) -> bool:
        return self.decision_role == "EVENT_RISK"

    @property
    def freshness_days(self) -> int | float | None:
        if isinstance(self.freshness, str) and self.freshness.endswith("d"):
            return int(self.freshness[:-1])
        return self.freshness if isinstance(self.freshness, (int, float)) else None

    @property
    def applicable_assets(self) -> tuple[str, ...] | None:
        return self.asset_scope

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.key,
            "factor": self.factor,
            "expected_type": self.expected_type,
            "value_type": self.value_type,
            "unit": self.unit,
            "direction": self.direction,
            "default_freshness": self.default_freshness,
            "freshness": self.freshness,
            "critical": self.critical,
            "critical_review_types": list(self.critical_review_types) if self.critical_review_types is not None else None,
            "trend_comparison_enabled": self.trend_comparison_enabled,
            "trend_enabled": self.trend_enabled,
            "asset_scope": list(self.asset_scope) if self.asset_scope is not None else None,
            "decision_role": self.decision_role,
            "context_group": self.context_group,
            "fallback_mode": self.fallback_mode,
        }


def _definition(
    key: str,
    factor: str,
    expected_type: str,
    unit: str | None = None,
    direction: str = "CONTEXTUAL",
    *,
    critical: bool = False,
    asset_scope: tuple[str, ...] | None = None,
    freshness: str | int | float = "CURRENT",
    trend_enabled: bool = True,
    decision_role: str = "SCORING_FACTOR",
    context_group: str | None = None,
    critical_review_types: tuple[str, ...] | None = None,
    fallback_mode: str = "STRUCTURED_ONLY",
) -> MetricDefinition:
    return MetricDefinition(
        key,
        factor,
        expected_type,
        unit,
        direction,
        freshness,
        critical,
        trend_enabled,
        asset_scope,
        expected_type,
        freshness,
        trend_enabled,
        decision_role=decision_role,
        context_group=context_group,
        critical_review_types=critical_review_types,
        fallback_mode=fallback_mode,
    )


METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "market.spot_price": _definition("market.spot_price", "trend", "number", "USD", "CONTEXTUAL", critical=True, freshness="1d"),
    "market.return_30d": _definition("market.return_30d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.return_90d": _definition("market.return_90d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.return_180d": _definition("market.return_180d", "trend", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.ma20": _definition("market.ma20", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma50": _definition("market.ma50", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma100": _definition("market.ma100", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.ma200": _definition("market.ma200", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.atr14": _definition("market.atr14", "trend", "number", "USD", "CONTEXTUAL", freshness="7d"),
    "market.realized_vol_30d": _definition("market.realized_vol_30d", "trend", "number", "fraction", "LOWER_IS_BETTER", freshness="7d"),
    "market.realized_vol_90d": _definition("market.realized_vol_90d", "trend", "number", "fraction", "LOWER_IS_BETTER", freshness="7d"),
    "market.relative_volume": _definition("market.relative_volume", "trend", "number", "ratio", "CONTEXTUAL", freshness="1d"),
    "market.drawdown": _definition("market.drawdown", "valuation", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d"),
    "market.btc_dominance": _definition("market.btc_dominance", "relative_strength_btc", "number", "fraction", "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "market.total_crypto_market_cap": _definition("market.total_crypto_market_cap", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "market.stablecoin_supply": _definition("market.stablecoin_supply", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=("MARKET",)),
    "market.breadth": _definition("market.breadth", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=("MARKET",)),
    "market.btc_trend": _definition("market.btc_trend", "trend", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("BTC",)),
    "market.volatility_state": _definition("market.volatility_state", "trend", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("BTC",)),
    "market.breadth_state": _definition("market.breadth_state", "relative_strength_btc", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "market.flow_state": _definition("market.flow_state", "capital_flows", "string", None, "CONTEXTUAL", freshness="2d", asset_scope=("MARKET",)),
    "flows.etf_net_1d": _definition("flows.etf_net_1d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="2d", asset_scope=("ETH", "MARKET")),
    "flows.etf_net_7d": _definition("flows.etf_net_7d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=("ETH", "MARKET")),
    "flows.etf_net_30d": _definition("flows.etf_net_30d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=("ETH", "MARKET")),
    "flows.btc_etf_net_to_aum_7d": _definition(
        "flows.btc_etf_net_to_aum_7d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "flows.btc_etf_net_to_aum_30d": _definition(
        "flows.btc_etf_net_to_aum_30d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "flows.btc_etf_net_1d": _definition(
        "flows.btc_etf_net_1d", "capital_flows", "number", "USD", "HIGHER_IS_BETTER",
        freshness="2d", asset_scope=("BTC",),
    ),
    "flows.btc_etf_aum_usd": _definition(
        "flows.btc_etf_aum_usd", "capital_flows", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="btc_etf",
    ),
    "flows.eth_etf_net_to_aum_7d": _definition(
        "flows.eth_etf_net_to_aum_7d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="7d", asset_scope=_ETH_ASSETS,
    ),
    "flows.eth_etf_net_to_aum_30d": _definition(
        "flows.eth_etf_net_to_aum_30d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="7d", asset_scope=_ETH_ASSETS,
    ),
    "flows.eth_etf_aum_usd": _definition(
        "flows.eth_etf_aum_usd", "capital_flows", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=_ETH_ASSETS, decision_role="EXECUTION_CONTEXT", context_group="eth_etf",
    ),
    "flows.eth_active_stake_change_to_supply_30d": _definition(
        "flows.eth_active_stake_change_to_supply_30d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="2d", asset_scope=_ETH_ASSETS,
    ),
    # No BNB ETF exists, so BNB's capital_flows factor uses the free BSC
    # USD-pegged stablecoin supply series as an expansion/contraction proxy.
    # The scored signal is the *absolute* supply change ranked against its own
    # 365-day history (see the BNB supply-proxy policy), not a ratio against a
    # denominator, so these metrics carry the raw fractional change only.
    "flows.bnb_stablecoin_supply_change_7d": _definition(
        "flows.bnb_stablecoin_supply_change_7d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "flows.bnb_stablecoin_supply_change_30d": _definition(
        "flows.bnb_stablecoin_supply_change_30d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "flows.bnb_stablecoin_supply_change_90d": _definition(
        "flows.bnb_stablecoin_supply_change_90d", "capital_flows", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "fundamentals.tvl": _definition("fundamentals.tvl", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "fundamentals.fees_30d": _definition("fundamentals.fees_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_FEE_REVENUE_EVIDENCE_ASSETS),
    "fundamentals.revenue_30d": _definition("fundamentals.revenue_30d", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_FEE_REVENUE_EVIDENCE_ASSETS),
    "fundamentals.stablecoin_liquidity": _definition("fundamentals.stablecoin_liquidity", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=("ETH", "SOL", "BNB")),
    "fundamentals.developer_activity": _definition("fundamentals.developer_activity", "fundamentals", "number", "count", "HIGHER_IS_BETTER", freshness="30d", asset_scope=_DEVELOPER_ACTIVITY_ASSETS),
    "onchain.active_addresses": _definition("onchain.active_addresses", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_ACTIVE_ADDRESS_ASSETS),
    "onchain.transfer_volume": _definition("onchain.transfer_volume", "onchain", "number", "USD", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_TRANSFER_VOLUME_ASSETS),
    "onchain.blockspace_fees": _definition("onchain.blockspace_fees", "onchain", "number", "USD", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_CHAIN_NATIVE_ASSETS),
    # BNB network fees come from DeFiLlama's dailyFees series for BSC only:
    # `/overview/fees/BSC` also sums on-chain application protocol fees and is
    # therefore not network gas demand.  Window totals and window-over-window
    # changes share the one daily-fee history request with blockspace fees.
    "onchain.bnb_network_fees_30d_usd": _definition(
        "onchain.bnb_network_fees_30d_usd", "onchain", "number", "USD", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "onchain.bnb_network_fees_90d_usd": _definition(
        "onchain.bnb_network_fees_90d_usd", "onchain", "number", "USD", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "onchain.bnb_network_fees_30d_change": _definition(
        "onchain.bnb_network_fees_30d_change", "onchain", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "onchain.bnb_network_fees_90d_change": _definition(
        "onchain.bnb_network_fees_90d_change", "onchain", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "onchain.transaction_count": _definition("onchain.transaction_count", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="3d", asset_scope=_TRANSACTION_COUNT_ASSETS),
    "valuation.market_cap": _definition("valuation.market_cap", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d", asset_scope=_PROTOCOL_ASSETS),
    "valuation.fdv": _definition("valuation.fdv", "valuation", "number", "USD", "CONTEXTUAL", freshness="7d", asset_scope=_FDV_ASSETS),
    "valuation.fdv_market_cap_ratio": _definition("valuation.fdv_market_cap_ratio", "valuation", "number", "ratio", "LOWER_IS_BETTER", freshness="7d", asset_scope=_FDV_RATIO_ASSETS),
    # Market capitalisation divided by annualized network fees (a scale, not a
    # price-to-earnings, cash-yield, or holder-revenue multiple).  Population is
    # Python-owned; zero fees or a mismatched window return unavailable.
    "valuation.bnb_market_cap_to_annualized_network_fees_90d": _definition(
        "valuation.bnb_market_cap_to_annualized_network_fees_90d", "valuation", "number", "ratio", "LOWER_IS_BETTER",
        freshness="3d", asset_scope=_BNB_ASSETS,
    ),
    "btc_valuation.mvrv": _definition(
        "btc_valuation.mvrv", "btc_valuation", "number", "ratio", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "btc_valuation.mvrv_zscore": _definition(
        "btc_valuation.mvrv_zscore", "btc_valuation", "number", "zscore", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "btc_valuation.realized_price": _definition(
        "btc_valuation.realized_price", "btc_valuation", "number", "USD", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "btc_valuation.price_to_realized_price": _definition(
        "btc_valuation.price_to_realized_price", "btc_valuation", "number", "ratio", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "btc_valuation.realized_cap_usd": _definition(
        "btc_valuation.realized_cap_usd", "btc_valuation", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="btc_valuation",
    ),
    "macro.dff": _definition(
        "macro.dff", "macro_liquidity", "number", "percent", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="macro_liquidity",
    ),
    "macro.dfii10": _definition(
        "macro.dfii10", "macro_liquidity", "number", "percent", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="macro_liquidity",
    ),
    "macro.dtwexbgs": _definition(
        "macro.dtwexbgs", "macro_liquidity", "number", "index", "CONTEXTUAL",
        freshness="14d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="macro_liquidity",
    ),
    "macro.walcl": _definition(
        "macro.walcl", "macro_liquidity", "number", "USD_millions", "CONTEXTUAL",
        freshness="14d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="macro_liquidity",
    ),
    "macro.m2sl": _definition(
        "macro.m2sl", "macro_liquidity", "number", "USD_billions", "CONTEXTUAL",
        freshness="75d", asset_scope=("BTC",), decision_role="EXECUTION_CONTEXT", context_group="macro_liquidity",
    ),
    "macro.fed_funds_change_90d": _definition(
        "macro.fed_funds_change_90d", "macro_liquidity", "number", "percentage_points", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "macro.real_yield_change_90d": _definition(
        "macro.real_yield_change_90d", "macro_liquidity", "number", "percentage_points", "LOWER_IS_BETTER",
        freshness="7d", asset_scope=("BTC",),
    ),
    "macro.broad_dollar_change_90d": _definition(
        "macro.broad_dollar_change_90d", "macro_liquidity", "number", "fraction", "LOWER_IS_BETTER",
        freshness="14d", asset_scope=("BTC",),
    ),
    "macro.fed_balance_sheet_change_13w": _definition(
        "macro.fed_balance_sheet_change_13w", "macro_liquidity", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="14d", asset_scope=("BTC",),
    ),
    "macro.m2_change_6m": _definition(
        "macro.m2_change_6m", "macro_liquidity", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="75d", asset_scope=("BTC",),
    ),
    "macro.m2_change_12m": _definition(
        "macro.m2_change_12m", "macro_liquidity", "number", "fraction", "HIGHER_IS_BETTER",
        freshness="75d", asset_scope=("BTC",),
    ),
    "relative.return_vs_btc_30d": _definition("relative.return_vs_btc_30d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "relative.return_vs_btc_90d": _definition("relative.return_vs_btc_90d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "relative.return_vs_btc_180d": _definition("relative.return_vs_btc_180d", "relative_strength_btc", "number", "fraction", "HIGHER_IS_BETTER", freshness="7d", asset_scope=_APPLICATION_ASSETS),
    "eth.monetary.current_supply_eth": _definition("eth.monetary.current_supply_eth", "fundamentals", "number", "ETH", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.monetary.issuance_30d_eth": _definition("eth.monetary.issuance_30d_eth", "fundamentals", "number", "ETH", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.monetary.issuance_365d_eth": _definition("eth.monetary.issuance_365d_eth", "fundamentals", "number", "ETH", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.monetary.net_supply_growth_30d": _definition("eth.monetary.net_supply_growth_30d", "fundamentals", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.monetary.net_supply_growth_90d": _definition("eth.monetary.net_supply_growth_90d", "fundamentals", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.monetary.net_supply_growth_365d": _definition("eth.monetary.net_supply_growth_365d", "fundamentals", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.staking.active_effective_stake_eth": _definition("eth.staking.active_effective_stake_eth", "fundamentals", "number", "ETH", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.staking.active_effective_stake_change_30d": _definition("eth.staking.active_effective_stake_change_30d", "fundamentals", "number", "ETH", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.l2.rent_paid_30d_usd": _definition("eth.l2.rent_paid_30d_usd", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.l2.rent_paid_90d_usd": _definition("eth.l2.rent_paid_90d_usd", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.l2.tvs_usd": _definition("eth.l2.tvs_usd", "fundamentals", "number", "USD", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.l2.activity_30d": _definition("eth.l2.activity_30d", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.da.ethereum_blob_data_30d_mb": _definition("eth.da.ethereum_blob_data_30d_mb", "onchain", "number", "MB", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.da.ethereum_blob_fees_30d_usd": _definition("eth.da.ethereum_blob_fees_30d_usd", "onchain", "number", "USD", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.da.ethereum_share_of_tracked_da_bytes_30d": _definition("eth.da.ethereum_share_of_tracked_da_bytes_30d", "fundamentals", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.da.ethereum_share_of_tracked_da_fees_30d": _definition("eth.da.ethereum_share_of_tracked_da_fees_30d", "fundamentals", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.blobs.count_1d": _definition("eth.blobs.count_1d", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.blobs.count_30d": _definition("eth.blobs.count_30d", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.blobs.data_bytes_30d": _definition("eth.blobs.data_bytes_30d", "onchain", "number", "bytes", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.blobs.blob_transactions_30d": _definition("eth.blobs.blob_transactions_30d", "onchain", "number", "count", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth.blobs.utilization_30d": _definition("eth.blobs.utilization_30d", "onchain", "number", "fraction", "HIGHER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    "eth_valuation.mvrv": _definition("eth_valuation.mvrv", "valuation", "number", "ratio", "LOWER_IS_BETTER", freshness="2d", asset_scope=_ETH_ASSETS),
    # MVRV already encodes price/realized-price, so realized-price values stay
    # context-only: their failure must never penalize ETH valuation scoring.
    "eth_valuation.realized_price": _definition("eth_valuation.realized_price", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS, decision_role="EXECUTION_CONTEXT", context_group="eth_valuation"),
    "eth_valuation.realized_cap_usd": _definition("eth_valuation.realized_cap_usd", "valuation", "number", "USD", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS, decision_role="EXECUTION_CONTEXT", context_group="eth_valuation"),
    "eth_valuation.price_to_realized_price": _definition("eth_valuation.price_to_realized_price", "valuation", "number", "ratio", "CONTEXTUAL", freshness="2d", asset_scope=_ETH_ASSETS, decision_role="EXECUTION_CONTEXT", context_group="eth_valuation"),
    "tokenomics.next_unlock_pct": _definition("tokenomics.next_unlock_pct", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_UNLOCK_ASSETS, decision_role="EVENT_RISK", context_group="event_risk"),
    "tokenomics.annualized_emissions": _definition("tokenomics.annualized_emissions", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_TOKENOMICS_ASSETS, decision_role="EVENT_RISK", context_group="event_risk"),
    "tokenomics.supply_growth": _definition("tokenomics.supply_growth", "event_risk", "number", "fraction", "LOWER_IS_BETTER", freshness="30d", asset_scope=_TOKENOMICS_ASSETS, decision_role="EVENT_RISK", context_group="event_risk"),
    "risk.security_event_status": _definition("risk.security_event_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_PROTOCOL_ASSETS, decision_role="EVENT_RISK", context_group="event_risk", fallback_mode="WEB_ALLOWED"),
    "risk.chain_liveness_status": _definition("risk.chain_liveness_status", "event_risk", "string", None, "CONTEXTUAL", critical=True, freshness="1d", asset_scope=_CHAIN_NATIVE_ASSETS, decision_role="EVENT_RISK", context_group="event_risk", fallback_mode="STRUCTURED_ONLY"),
    "risk.regulatory_event_status": _definition(
        "risk.regulatory_event_status", "event_risk", "string", None, "CONTEXTUAL",
        critical=True, critical_review_types=("EVENT_REVIEW",), freshness="1d", asset_scope=_PROTOCOL_ASSETS,
        decision_role="EVENT_RISK", context_group="event_risk", fallback_mode="WEB_ALLOWED",
    ),
    # Positioning and social context are deliberately separate from scoring.
    "derivatives.funding_rate": _definition(
        "derivatives.funding_rate", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_24h_avg": _definition(
        "derivatives.funding_rate_24h_avg", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_7d_avg": _definition(
        "derivatives.funding_rate_7d_avg", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.funding_rate_percentile": _definition(
        "derivatives.funding_rate_percentile", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_usd": _definition(
        "derivatives.open_interest_usd", "positioning", "number", "USD", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_change_1d": _definition(
        "derivatives.open_interest_change_1d", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_change_7d": _definition(
        "derivatives.open_interest_change_7d", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.open_interest_to_market_cap": _definition(
        "derivatives.open_interest_to_market_cap", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.long_short_account_ratio": _definition(
        "derivatives.long_short_account_ratio", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.top_trader_long_short_ratio": _definition(
        "derivatives.top_trader_long_short_ratio", "positioning", "number", "ratio", "CONTEXTUAL",
        freshness="1d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "derivatives.futures_basis_annualized": _definition(
        "derivatives.futures_basis_annualized", "positioning", "number", "fraction", "CONTEXTUAL",
        freshness="1d", asset_scope=_DELIVERY_BASIS_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_bullish_share": _definition(
        "sentiment.social_bullish_share", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_mentions_change_7d": _definition(
        "sentiment.social_mentions_change_7d", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.social_attention_percentile": _definition(
        "sentiment.social_attention_percentile", "sentiment", "number", "fraction", "CONTEXTUAL",
        freshness="2d", asset_scope=_DERIVATIVES_ASSETS,
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "sentiment.market_fear_greed": _definition(
        "sentiment.market_fear_greed", "sentiment", "number", "score", "CONTEXTUAL",
        freshness="2d", asset_scope=("MARKET",),
        decision_role="POSITIONING_OVERLAY", context_group="positioning",
    ),
    "onchain.btc.mvrv": _definition(
        "onchain.btc.mvrv", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.mvrv_zscore": _definition(
        "onchain.btc.mvrv_zscore", "cycle_context", "number", "zscore", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.realized_price": _definition(
        "onchain.btc.realized_price", "cycle_context", "number", "USD", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.market_to_realized_price": _definition(
        "onchain.btc.market_to_realized_price", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.sopr": _definition(
        "onchain.btc.sopr", "cycle_context", "number", "ratio", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
    "onchain.btc.lth_net_position_change": _definition(
        "onchain.btc.lth_net_position_change", "cycle_context", "number", "fraction", "CONTEXTUAL",
        freshness="7d", asset_scope=("BTC",),
        decision_role="CYCLE_CONTEXT", context_group="btc_cycle",
    ),
}

_CONSUMER_BY_ROLE = {
    "SCORING_FACTOR": "factor_engine",
    "EVENT_RISK": "event_risk_gate",
    "POSITIONING_OVERLAY": "positioning_engine",
    "CYCLE_CONTEXT": "btc_cycle_engine",
    "EXECUTION_CONTEXT": "execution_overlay",
    "STRUCTURAL_RISK": "structural_risk_gate",
}
DECISION_CONSUMERS = {
    key: frozenset({_CONSUMER_BY_ROLE[definition.decision_role]})
    for key, definition in METRIC_REGISTRY.items()
}

def normalize_metric_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metric_key must be a non-empty string")
    return value.strip().lower()


def metric_definition(metric_key: str) -> MetricDefinition:
    key = normalize_metric_key(metric_key)
    try:
        return METRIC_REGISTRY[key]
    except KeyError as exc:
        raise ValueError(f"unknown metric key: {key}") from exc


def validate_metric_value(metric_key: str, value: Any) -> Any:
    """Validate simple numeric semantics without interpreting investment meaning."""
    definition = metric_definition(metric_key)
    if definition.expected_type != "number" or value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"metric {definition.key} value must be finite numeric")
    number = float(value)
    signed = (
        definition.key.startswith("market.return_")
        or definition.key.startswith("relative.return_vs_btc_")
        or definition.key == "market.drawdown"
        or definition.key.startswith("flows.")
        or definition.key.startswith("derivatives.funding_rate")
        or definition.key.startswith("derivatives.open_interest_change_")
        or definition.key == "derivatives.futures_basis_annualized"
        or definition.key == "sentiment.social_mentions_change_7d"
        or definition.key.startswith(("eth.monetary.net_supply_growth", "eth.staking.active_effective_stake_change"))
        or definition.key.startswith("flows.eth_")
        or definition.key.startswith("onchain.bnb_network_fees_") and definition.key.endswith("_change")
        or definition.key in {
            "onchain.btc.mvrv_zscore", "onchain.btc.lth_net_position_change",
            "btc_valuation.mvrv_zscore", "macro.fed_funds_change_90d", "macro.real_yield_change_90d",
            "macro.broad_dollar_change_90d", "macro.fed_balance_sheet_change_13w",
            "macro.m2_change_6m", "macro.m2_change_12m",
        }
    )
    if not signed and number < 0:
        raise ValueError(f"metric {definition.key} value must be non-negative")
    if definition.key == "market.spot_price" and number <= 0:
        raise ValueError("metric market.spot_price value must be > 0")
    if definition.key in {
        "eth.monetary.current_supply_eth",
        "eth_valuation.realized_price",
        "eth_valuation.realized_cap_usd",
        "flows.eth_etf_aum_usd",
    } and number <= 0:
        raise ValueError(f"metric {definition.key} value must be > 0")
    if definition.key in {"market.ma20", "market.ma50", "market.ma100", "market.ma200", "market.atr14"} and number <= 0:
        raise ValueError(f"metric {definition.key} value must be > 0")
    if definition.key.startswith(("market.return_", "relative.return_vs_btc_")) and number < -1:
        raise ValueError(f"metric {definition.key} return must be >= -1")
    if definition.key.startswith("flows.bnb_stablecoin_supply_change_") and number < -1:
        raise ValueError(f"metric {definition.key} supply change must be >= -1")
    if definition.key == "market.drawdown" and number > 0:
        raise ValueError("metric market.drawdown value must be <= 0")
    if definition.key in {"market.btc_dominance", "market.breadth"} and number > 1:
        raise ValueError(f"metric {definition.key} fraction must be <= 1")
    if definition.key in {
        "derivatives.funding_rate_percentile",
        "sentiment.social_bullish_share",
        "sentiment.social_attention_percentile",
        "eth.staking.active_effective_stake_pct",
        "eth.blobs.utilization_30d",
        "eth.da.ethereum_share_of_tracked_da_bytes_30d",
        "eth.da.ethereum_share_of_tracked_da_fees_30d",
    } and not 0 <= number <= 1:
        raise ValueError(f"metric {definition.key} fraction must be <= 1")
    if definition.key == "sentiment.market_fear_greed" and not 0 <= number <= 100:
        raise ValueError("metric sentiment.market_fear_greed score must be in [0, 100]")
    if definition.key in {
        "derivatives.long_short_account_ratio",
        "derivatives.top_trader_long_short_ratio",
        "derivatives.open_interest_to_market_cap",
        "onchain.btc.mvrv",
        "onchain.btc.market_to_realized_price",
        "onchain.btc.sopr",
        "eth_valuation.mvrv",
        "eth_valuation.price_to_realized_price",
        "valuation.bnb_market_cap_to_annualized_network_fees_90d",
    } and number <= 0:
        raise ValueError(f"metric {definition.key} ratio must be > 0")
    if definition.key.startswith(("derivatives.open_interest_change_", "sentiment.social_mentions_change_")) or definition.key in {
        "onchain.btc.lth_net_position_change",
    }:
        if number < -1:
            raise ValueError(f"metric {definition.key} change must be >= -1")
    if definition.key.startswith("eth.monetary.net_supply_growth") and number < -1:
        raise ValueError(f"metric {definition.key} change must be >= -1")
    return value


# ---------------------------------------------------------------------------
# Observation metadata contracts
#
# Some BNB observations carry the deterministic inputs of a scored
# calculation (a supply change and its historical percentile, a fee window and
# its completeness).  Those inputs are produced by Python inside the provider
# and must survive unchanged to the factor.  The contract below is enforced at
# the persistence/build boundary so a hand-edited, truncated, or
# stale-methodology observation is rejected rather than scored.
# ---------------------------------------------------------------------------

BNB_SUPPLY_CHANGE_PREFIX = "flows.bnb_stablecoin_supply_change_"
BNB_SUPPLY_CHANGE_METHODOLOGY = "pegged_usd_supply_change_abs_change_percentile"
BNB_SUPPLY_SAMPLE_WINDOW_DAYS = 365
BNB_SUPPLY_MIN_SAMPLES = 180
BNB_SUPPLY_CHANGE_HORIZON_DAYS = {"7d": 7, "30d": 30, "90d": 90}

BNB_NETWORK_FEES_PREFIX = "onchain.bnb_network_fees_"
BNB_NETWORK_FEES_SOURCE_DATASET = "summary/fees"
BNB_NETWORK_FEES_DATA_TYPE = "dailyFees"
BNB_NETWORK_FEES_METHODOLOGY = "daily_fees_window_over_complete_utc_days"
BNB_BLOCKSPACE_FEES_METHODOLOGY = "daily_fees_latest_complete_utc_day"
BNB_NETWORK_FEE_WINDOW_DAYS = {"30d": 30, "90d": 90}

_CALIBRATION_STATES = ("CALIBRATED", "UNCALIBRATED")
_OBSERVATION_SOURCE_CONFIDENCE = ("MEDIUM", "LOW")


def _utc_datetime(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _metadata_number(metadata: Mapping[str, Any], field: str, key: str) -> float:
    raw = metadata.get(field)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
        raise ValueError(f"metric {key} metadata.{field} must be finite numeric")
    return float(raw)


def _metadata_integer(metadata: Mapping[str, Any], field: str, key: str) -> int:
    raw = metadata.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"metric {key} metadata.{field} must be an integer")
    return raw


def _metadata_choice(metadata: Mapping[str, Any], field: str, key: str, allowed: tuple[str, ...]) -> str:
    raw = metadata.get(field)
    if not isinstance(raw, str) or raw.strip().upper() not in allowed:
        raise ValueError(f"metric {key} metadata.{field} is unsupported")
    return raw.strip().upper()


def _metadata_text(metadata: Mapping[str, Any], field: str, key: str, allowed: tuple[str, ...]) -> str:
    raw = metadata.get(field)
    if not isinstance(raw, str) or raw.strip().upper() not in tuple(item.upper() for item in allowed):
        raise ValueError(f"metric {key} metadata.{field} is unsupported")
    return raw.strip()


def _metadata_hash(metadata: Mapping[str, Any], field: str, key: str) -> str:
    raw = metadata.get(field)
    if not isinstance(raw, str):
        raise ValueError(f"metric {key} metadata.{field} must be a SHA-256 hex digest")
    digest = raw.strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"metric {key} metadata.{field} must be a SHA-256 hex digest")
    return digest


def _metadata_utc_day(metadata: Mapping[str, Any], field: str, key: str) -> datetime:
    raw = metadata.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"metric {key} metadata.{field} must be a timestamp")
    try:
        return _utc_datetime(raw)
    except ValueError as exc:
        raise ValueError(f"metric {key} metadata.{field} must be a timestamp") from exc


def _check_bnb_supply_change_metadata(key: str, value: Any, metadata: Mapping[str, Any]) -> None:
    horizon = key[len(BNB_SUPPLY_CHANGE_PREFIX):]
    expected_days = BNB_SUPPLY_CHANGE_HORIZON_DAYS[horizon]
    _metadata_text(metadata, "methodology", key, (BNB_SUPPLY_CHANGE_METHODOLOGY,))
    if _metadata_integer(metadata, "window_days", key) != expected_days:
        raise ValueError(f"metric {key} metadata.window_days must be {expected_days}")
    if _metadata_integer(metadata, "sample_window_days", key) != BNB_SUPPLY_SAMPLE_WINDOW_DAYS:
        raise ValueError(f"metric {key} metadata.sample_window_days must be {BNB_SUPPLY_SAMPLE_WINDOW_DAYS}")
    if _metadata_integer(metadata, "min_samples_required", key) != BNB_SUPPLY_MIN_SAMPLES:
        raise ValueError(f"metric {key} metadata.min_samples_required must be {BNB_SUPPLY_MIN_SAMPLES}")
    ratio = _metadata_number(metadata, "supply_change_ratio", key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isclose(float(value), ratio, rel_tol=1e-12, abs_tol=1e-12)
    ):
        raise ValueError(f"metric {key} metadata.supply_change_ratio must match the observation value")
    _metadata_hash(metadata, "source_series_hash", key)
    _metadata_choice(metadata, "source_confidence", key, _OBSERVATION_SOURCE_CONFIDENCE)
    anchor = _metadata_utc_day(metadata, "anchor_date", key)
    base = _metadata_utc_day(metadata, "base_date", key)
    if (anchor.hour, anchor.minute, anchor.second, anchor.microsecond) != (0, 0, 0, 0):
        raise ValueError(f"metric {key} metadata.anchor_date must be a UTC day boundary")
    if base != anchor - timedelta(days=expected_days):
        raise ValueError(f"metric {key} metadata.base_date must be exactly {expected_days} days before anchor_date")
    state = _metadata_choice(metadata, "calibration_state", key, _CALIBRATION_STATES)
    percentile = metadata.get("abs_change_percentile")
    sample_count = _metadata_integer(metadata, "sample_count", key)
    if sample_count < 0:
        raise ValueError(f"metric {key} metadata.sample_count must be non-negative")
    if state == "CALIBRATED":
        if (
            percentile is None
            or isinstance(percentile, bool)
            or not isinstance(percentile, (int, float))
            or not math.isfinite(float(percentile))
        ):
            raise ValueError(f"metric {key} metadata.abs_change_percentile must be finite when calibrated")
        if not 0.0 <= float(percentile) <= 1.0:
            raise ValueError(f"metric {key} metadata.abs_change_percentile must be in [0, 1]")
        if sample_count < BNB_SUPPLY_MIN_SAMPLES:
            raise ValueError(f"metric {key} metadata.sample_count must be >= {BNB_SUPPLY_MIN_SAMPLES} when calibrated")
    else:
        if percentile is not None:
            raise ValueError(f"metric {key} metadata.abs_change_percentile must be null when uncalibrated")
        reason = metadata.get("calibration_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"metric {key} metadata.calibration_reason is required when uncalibrated")


def _check_bnb_network_fees_metadata(key: str, value: Any, metadata: Mapping[str, Any]) -> None:
    _metadata_text(metadata, "methodology", key, (BNB_NETWORK_FEES_METHODOLOGY,))
    _metadata_text(metadata, "source_dataset", key, (BNB_NETWORK_FEES_SOURCE_DATASET,))
    _metadata_text(metadata, "fees_data_type", key, (BNB_NETWORK_FEES_DATA_TYPE,))
    horizon = key[len(BNB_NETWORK_FEES_PREFIX):].split("_", 1)[0]
    expected_days = BNB_NETWORK_FEE_WINDOW_DAYS[horizon]
    if _metadata_integer(metadata, "window_days", key) != expected_days:
        raise ValueError(f"metric {key} metadata.window_days must be {expected_days}")
    if _metadata_integer(metadata, "complete_utc_days", key) != expected_days:
        raise ValueError(f"metric {key} metadata.complete_utc_days must equal the window length")
    _metadata_utc_day(metadata, "anchor_date", key)
    _metadata_hash(metadata, "source_series_hash", key)
    total = _metadata_number(metadata, "window_total_usd", key)
    if total < 0:
        raise ValueError(f"metric {key} metadata.window_total_usd must be non-negative")
    expected = _metadata_number(metadata, "window_change_ratio", key) if key.endswith("_change") else total
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isclose(float(value), expected, rel_tol=1e-12, abs_tol=1e-12)
    ):
        field = "window_change_ratio" if key.endswith("_change") else "window_total_usd"
        raise ValueError(f"metric {key} metadata.{field} must match the observation value")


def _check_bnb_blockspace_fees_metadata(key: str, metadata: Mapping[str, Any]) -> None:
    _metadata_text(metadata, "source_dataset", key, (BNB_NETWORK_FEES_SOURCE_DATASET,))
    _metadata_text(metadata, "fees_data_type", key, (BNB_NETWORK_FEES_DATA_TYPE,))
    _metadata_text(metadata, "methodology", key, (BNB_BLOCKSPACE_FEES_METHODOLOGY,))
    _metadata_utc_day(metadata, "anchor_date", key)
    _metadata_hash(metadata, "source_series_hash", key)


def validate_metric_observation_metadata(
    metric_key: str,
    asset: str,
    value: Any,
    metadata: Mapping[str, Any] | None,
) -> None:
    """Enforce the observation metadata contract for BNB-scored metrics.

    Only metrics whose factor input is a Python-derived deterministic value
    carry a mandatory contract; every other metric keeps free-form metadata.
    """
    key = normalize_metric_key(metric_key)
    metric_definition(key)
    symbol = str(asset).strip().upper()
    if key.startswith(BNB_SUPPLY_CHANGE_PREFIX):
        if symbol != "BNB":
            raise ValueError(f"metric {key} is defined only for BNB")
        if not isinstance(metadata, Mapping):
            raise ValueError(f"metric {key} requires observation metadata")
        _check_bnb_supply_change_metadata(key, value, metadata)
        return
    if key.startswith(BNB_NETWORK_FEES_PREFIX):
        if symbol != "BNB":
            raise ValueError(f"metric {key} is defined only for BNB")
        if not isinstance(metadata, Mapping):
            raise ValueError(f"metric {key} requires observation metadata")
        _check_bnb_network_fees_metadata(key, value, metadata)
        return
    # A BNB blockspace-fee observation must carry the dailyFees contract so a
    # legacy /overview/fees value can never be scored as network gas demand.
    if key == "onchain.blockspace_fees" and symbol == "BNB":
        if not isinstance(metadata, Mapping):
            raise ValueError(f"metric {key} requires observation metadata for BNB")
        _check_bnb_blockspace_fees_metadata(key, metadata)


def known_metric_keys() -> tuple[str, ...]:
    return tuple(METRIC_REGISTRY)


def metrics_for_factor(factor: str, *, asset: str | None = None) -> tuple[MetricDefinition, ...]:
    if not isinstance(factor, str) or not factor.strip():
        raise ValueError("factor must be a non-empty string")
    factor = factor.strip().lower()
    values = tuple(
        definition
        for definition in METRIC_REGISTRY.values()
        if definition.factor == factor
        and (asset is None or definition.applies_to(asset))
    )
    return values


def metrics_for_role(
    decision_role: str,
    *,
    context_group: str | None = None,
    asset: str | None = None,
) -> tuple[MetricDefinition, ...]:
    if not isinstance(decision_role, str) or not decision_role.strip():
        raise ValueError("decision_role must be a non-empty string")
    role = decision_role.strip().upper()
    if role not in _DECISION_ROLES:
        raise ValueError("decision_role is unsupported")
    if context_group is not None and (not isinstance(context_group, str) or not context_group.strip()):
        raise ValueError("context_group must be a non-empty string or null")
    group = context_group.strip().lower() if context_group is not None else None
    return tuple(
        definition
        for definition in METRIC_REGISTRY.values()
        if definition.decision_role == role
        and (group is None or definition.context_group == group)
        and (asset is None or definition.applies_to(asset))
    )


def validate_metric_ownership(
    registry: Mapping[str, MetricDefinition] | None = None,
) -> bool:
    """Validate that scoring and overlay metrics have one explicit owner."""
    values = METRIC_REGISTRY if registry is None else registry
    if not isinstance(values, Mapping) or not values:
        raise ValueError("metric registry must be a non-empty mapping")
    for key, definition in values.items():
        if not isinstance(definition, MetricDefinition) or definition.key != normalize_metric_key(key):
            raise ValueError("metric registry keys must match MetricDefinition.key")
        if not DECISION_CONSUMERS.get(definition.key):
            raise ValueError(f"metric {definition.key} has no downstream decision consumer")
        if definition.is_scoring_factor and definition.factor not in {
            "trend", "valuation", "fundamentals", "onchain", "capital_flows", "relative_strength_btc",
            "btc_valuation", "macro_liquidity",
        }:
            raise ValueError(f"scoring metric {definition.key} has no canonical factor owner")
        if definition.is_event_risk and definition.factor != "event_risk":
            raise ValueError(f"event-risk metric {definition.key} has an incompatible factor")
    return True


__all__ = [
    "BNB_BLOCKSPACE_FEES_METHODOLOGY",
    "BNB_NETWORK_FEES_DATA_TYPE",
    "BNB_NETWORK_FEES_METHODOLOGY",
    "BNB_NETWORK_FEES_PREFIX",
    "BNB_NETWORK_FEES_SOURCE_DATASET",
    "BNB_NETWORK_FEE_WINDOW_DAYS",
    "BNB_SUPPLY_CHANGE_HORIZON_DAYS",
    "BNB_SUPPLY_CHANGE_METHODOLOGY",
    "BNB_SUPPLY_CHANGE_PREFIX",
    "BNB_SUPPLY_MIN_SAMPLES",
    "BNB_SUPPLY_SAMPLE_WINDOW_DAYS",
    "CHAIN_NATIVE_ASSETS",
    "DECISION_CONSUMERS",
    "METRIC_REGISTRY",
    "DECISION_ROLES",
    "REVIEW_TYPES",
    "MetricDefinition",
    "known_metric_keys",
    "metrics_for_factor",
    "metrics_for_role",
    "metric_definition",
    "normalize_metric_key",
    "validate_metric_observation_metadata",
    "validate_metric_ownership",
    "validate_metric_value",
]
