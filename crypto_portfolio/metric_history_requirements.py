"""Explicit history requirements for decision-relevant metrics."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics_registry import metric_definition, normalize_metric_key


HISTORY_MODES = ("CURRENT", "BOUNDED", "FULL_AVAILABLE")


@dataclass(frozen=True)
class MetricHistoryRequirement:
    """The minimum source history needed by one metric's methodology."""

    mode: str
    days: int | None = None
    tolerance_days: int = 0

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().upper()
        if mode not in HISTORY_MODES:
            raise ValueError(f"history mode must be one of {HISTORY_MODES}")
        if self.days is not None and (
            isinstance(self.days, bool) or not isinstance(self.days, int) or self.days < 1
        ):
            raise ValueError("history days must be a positive integer or null")
        if mode == "CURRENT" and self.days is not None:
            raise ValueError("CURRENT history must not declare a history window")
        if mode == "BOUNDED" and self.days is None:
            raise ValueError("BOUNDED history requires a history window")
        if mode == "FULL_AVAILABLE" and self.days is not None:
            raise ValueError("FULL_AVAILABLE history must not declare a fixed window")
        if isinstance(self.tolerance_days, bool) or not isinstance(self.tolerance_days, int) or self.tolerance_days < 0:
            raise ValueError("history tolerance must be a non-negative integer")
        object.__setattr__(self, "mode", mode)

    @property
    def cohort(self) -> str:
        return f"{self.mode}:{self.days if self.days is not None else 'CURRENT'}"

    def as_dict(self) -> dict[str, int | str | None]:
        return {"mode": self.mode, "days": self.days, "tolerance_days": self.tolerance_days}


CURRENT = MetricHistoryRequirement("CURRENT")
BOUNDED_14D = MetricHistoryRequirement("BOUNDED", 14, 3)
BOUNDED_45D = MetricHistoryRequirement("BOUNDED", 45, 7)
BOUNDED_105D = MetricHistoryRequirement("BOUNDED", 105, 7)
BOUNDED_195D = MetricHistoryRequirement("BOUNDED", 195, 7)
BOUNDED_380D = MetricHistoryRequirement("BOUNDED", 380, 7)
FULL_AVAILABLE = MetricHistoryRequirement("FULL_AVAILABLE")


# Keep this table explicit. A suffix does not establish whether a metric is a
# windowed aggregation, a current observation, or a full-history derivation.
_REQUIREMENTS: dict[str, MetricHistoryRequirement] = {
    "market.return_30d": BOUNDED_45D,
    "market.return_90d": BOUNDED_105D,
    "market.return_180d": BOUNDED_195D,
    "relative.return_vs_btc_30d": BOUNDED_45D,
    "relative.return_vs_btc_90d": BOUNDED_105D,
    "relative.return_vs_btc_180d": BOUNDED_195D,
    "flows.etf_net_7d": BOUNDED_14D,
    "flows.etf_net_30d": BOUNDED_45D,
    "flows.btc_etf_net_to_aum_7d": BOUNDED_14D,
    "flows.btc_etf_net_to_aum_30d": BOUNDED_45D,
    "flows.eth_etf_net_to_aum_7d": BOUNDED_14D,
    "flows.eth_etf_net_to_aum_30d": BOUNDED_45D,
    "fundamentals.fees_30d": BOUNDED_45D,
    "fundamentals.revenue_30d": BOUNDED_45D,
    "derivatives.funding_rate_24h_avg": BOUNDED_14D,
    "derivatives.funding_rate_7d_avg": BOUNDED_14D,
    "derivatives.open_interest_change_7d": BOUNDED_14D,
    "derivatives.long_liquidations_7d_usd": BOUNDED_14D,
    "derivatives.short_liquidations_7d_usd": BOUNDED_14D,
    "eth.monetary.issuance_30d_eth": BOUNDED_45D,
    "eth.monetary.issuance_365d_eth": BOUNDED_380D,
    "eth.monetary.net_supply_growth_30d": BOUNDED_45D,
    "eth.monetary.net_supply_growth_90d": BOUNDED_105D,
    "eth.monetary.net_supply_growth_365d": BOUNDED_380D,
    "eth.monetary.burn_30d_eth": BOUNDED_45D,
    "eth.monetary.burn_365d_eth": FULL_AVAILABLE,
    "eth.monetary.burn_to_issuance_30d": BOUNDED_45D,
    "eth.monetary.burn_to_issuance_365d": FULL_AVAILABLE,
    "tokenomics.annualized_emissions": BOUNDED_380D,
    "tokenomics.supply_growth": BOUNDED_380D,
    "btc_valuation.mvrv_zscore": FULL_AVAILABLE,
    "onchain.btc.mvrv_zscore": FULL_AVAILABLE,
    "eth.staking.active_effective_stake_change_30d": BOUNDED_45D,
    "eth.staking.active_effective_stake_change_90d": BOUNDED_105D,
    "flows.eth_active_stake_change_to_supply_30d": BOUNDED_45D,
    "eth.staking.staking_apr_7d": BOUNDED_14D,
    "eth.staking.staking_apr_30d": BOUNDED_45D,
    "eth.l2.rent_paid_30d_usd": BOUNDED_45D,
    "eth.l2.rent_paid_90d_usd": BOUNDED_105D,
    "eth.l2.activity_30d": BOUNDED_45D,
    "eth.da.ethereum_blob_data_30d_mb": BOUNDED_45D,
    "eth.da.ethereum_blob_fees_30d_usd": BOUNDED_45D,
    "eth.da.ethereum_share_of_tracked_da_bytes_30d": BOUNDED_45D,
    "eth.da.ethereum_share_of_tracked_da_fees_30d": BOUNDED_45D,
    "eth.blobs.count_30d": BOUNDED_45D,
    "eth.blobs.data_bytes_30d": BOUNDED_45D,
    "eth.blobs.blob_transactions_30d": BOUNDED_45D,
    "eth.blobs.utilization_30d": BOUNDED_45D,
}


def history_requirement(metric_key: str) -> MetricHistoryRequirement:
    """Return the canonical source-history requirement for ``metric_key``."""
    key = normalize_metric_key(metric_key)
    metric_definition(key)
    return _REQUIREMENTS.get(key, CURRENT)


__all__ = [
    "BOUNDED_14D",
    "BOUNDED_45D",
    "BOUNDED_105D",
    "BOUNDED_195D",
    "BOUNDED_380D",
    "CURRENT",
    "FULL_AVAILABLE",
    "HISTORY_MODES",
    "MetricHistoryRequirement",
    "history_requirement",
]
