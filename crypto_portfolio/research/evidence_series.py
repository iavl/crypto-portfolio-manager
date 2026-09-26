"""Point-in-time deterministic evidence series for historical replay.

The strict replay used to run on OHLCV alone, so every non-price factor was
``MISSING``, coverage collapsed to 0.35, and the buy side of the strategy was
structurally blocked.  This module harvests the evidence sources that have
full, dated public history — DeFiLlama stablecoin supply and chain fees,
SoSoValue ETF net flows and AUM, FRED realtime-vintage macro series, and
CoinMetrics BTC MVRV — into frozen observation series, and derives
deterministic proxy factor scores from them.

These scores are documented research proxies for the judgment the live
pipeline forms over the same underlying evidence; they are not the live
scores.  Judgment-only factors (fundamentals, satellite valuation) stay
``MISSING`` in replay on purpose.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..models.evidence import FactorScore
from ..models.time import normalize_timestamp, parse_timestamp

STABLECOIN_GROWTH_SENSITIVITY = 0.02
ETF_FLOW_SENSITIVITY = 0.05
WALCL_CHANGE_SENSITIVITY = 0.02
DFF_CHANGE_SENSITIVITY = 0.01
FEES_CHANGE_SENSITIVITY = 0.20
FLOW_STATE_GROWTH_BAND = 0.005
FLOW_STATE_GROWTH_BAND_NO_ETF = 0.015

_CAPITAL_FLOWS_RELIABILITY = 0.9
_CAPITAL_FLOWS_STABLE_ONLY_RELIABILITY = 0.6
_MACRO_LIQUIDITY_RELIABILITY = 0.7
_ONCHAIN_RELIABILITY = 0.7
_BTC_VALUATION_RELIABILITY = 0.8

# MVRV breakpoints for the btc_valuation proxy: undervalued below 1.0,
# rich above 3.0, linearly mapped in between on the LOWER_IS_BETTER side.
_MVRV_RICH = 3.0
_MVRV_CHEAP = 1.0


def _clip_score(value: float) -> float:
    return min(100.0, max(0.0, value))


@dataclass(frozen=True)
class EvidencePoint:
    observed_at: str
    value: float
    available_at: str | None = None

    def __post_init__(self) -> None:
        observed = normalize_timestamp(self.observed_at, "observed_at")
        object.__setattr__(self, "observed_at", observed)
        if self.available_at is not None:
            object.__setattr__(self, "available_at", normalize_timestamp(self.available_at, "available_at"))
        # A publication lag (available after observed) is normal; knowing a
        # value before it existed is look-ahead leakage and rejected here.
        if parse_timestamp(self.available_at or observed) < parse_timestamp(observed):
            raise ValueError("evidence point cannot be available before it is observed")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("evidence point value must be numeric")
        value = float(self.value)
        if not math.isfinite(value):
            raise ValueError("evidence point value must be finite")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class ObservationSeries:
    series_id: str
    metric: str
    source: str
    unit: str
    fetched_at: str
    points: tuple[EvidencePoint, ...]

    def __post_init__(self) -> None:
        for name in ("series_id", "metric", "source", "unit", "fetched_at"):
            text = getattr(self, name)
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{name} must be a non-empty string")
        object.__setattr__(self, "fetched_at", normalize_timestamp(self.fetched_at, "fetched_at"))
        if not self.points:
            raise ValueError("observation series must contain at least one point")
        moments = [parse_timestamp(point.observed_at) for point in self.points]
        if any(later <= earlier for earlier, later in zip(moments, moments[1:])):
            raise ValueError("observation series points must be strictly increasing")
        object.__setattr__(self, "points", tuple(self.points))

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "metric": self.metric,
            "source": self.source,
            "unit": self.unit,
            "fetched_at": self.fetched_at,
            "points": [
                {"observed_at": point.observed_at, "value": point.value, "available_at": point.available_at}
                for point in self.points
            ],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ObservationSeries":
        points = tuple(
            EvidencePoint(
                row["observed_at"], row["value"], row.get("available_at"),
            )
            for row in value["points"]
        )
        return cls(
            series_id=value["series_id"], metric=value["metric"], source=value["source"],
            unit=value["unit"], fetched_at=value["fetched_at"], points=points,
        )

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _as_of_index(self, as_of: str) -> int:
        moment = parse_timestamp(as_of)
        times = [parse_timestamp(point.available_at or point.observed_at) for point in self.points]
        return bisect_right(times, moment) - 1

    def latest_as_of(self, as_of: str) -> EvidencePoint | None:
        index = self._as_of_index(as_of)
        return self.points[index] if index >= 0 else None

    def change_over_days(self, as_of: str, days: int) -> float | None:
        """Relative change of the latest known value against ~days earlier."""
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("days must be a positive integer")
        index = self._as_of_index(as_of)
        if index < 1:
            return None
        boundary = parse_timestamp(self.points[index].observed_at) - timedelta(days=days)
        observed = [parse_timestamp(point.observed_at) for point in self.points]
        base_index = bisect_right(observed, boundary) - 1
        if base_index < 0:
            return None
        base = self.points[base_index].value
        if base == 0.0:
            return None
        return self.points[index].value / base - 1.0

    def sum_over_days(self, as_of: str, days: int) -> float | None:
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("days must be a positive integer")
        index = self._as_of_index(as_of)
        if index < 0:
            return None
        boundary = parse_timestamp(self.points[index].observed_at) - timedelta(days=days - 1)
        selected = [
            point.value for point in self.points[:index + 1]
            if parse_timestamp(point.observed_at) >= boundary
        ]
        if not selected:
            return None
        return sum(selected)

    def difference_over_days(self, as_of: str, days: int) -> float | None:
        """Absolute latest-minus-base difference, for level series like rates."""
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("days must be a positive integer")
        index = self._as_of_index(as_of)
        if index < 1:
            return None
        boundary = parse_timestamp(self.points[index].observed_at) - timedelta(days=days)
        observed = [parse_timestamp(point.observed_at) for point in self.points]
        base_index = bisect_right(observed, boundary) - 1
        if base_index < 0:
            return None
        return self.points[index].value - self.points[base_index].value


def parse_stablecoin_supply_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """DeFiLlama /stablecoincharts rows to dated total-supply points."""
    if not isinstance(payload, list):
        raise ValueError("stablecoin chart response must be a list")
    points: list[EvidencePoint] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ValueError(f"stablecoin row {index} is malformed")
        raw_date = row.get("date")
        # The API serves unix seconds as a JSON string (or a number).
        try:
            stamp = float(str(raw_date))
        except (TypeError, ValueError):
            raise ValueError(f"stablecoin row {index} has no numeric date") from None
        moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
        if (moment.hour, moment.minute, moment.second) != (0, 0, 0):
            moment = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        total = row.get("totalCirculatingUSD")
        if not isinstance(total, Mapping) or total.get("peggedUSD") is None:
            continue
        value = float(total["peggedUSD"])
        if not math.isfinite(value):
            continue
        points.append(EvidencePoint(moment.isoformat().replace("+00:00", "Z"), value))
    if not points:
        raise ValueError("stablecoin chart response has no usable rows")
    return tuple(points)


def parse_chain_fees_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """DeFiLlama /overview/fees totalDataChart pairs to dated daily fees."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("totalDataChart"), list):
        raise ValueError("chain fees response has no totalDataChart")
    points: list[EvidencePoint] = []
    for index, row in enumerate(payload["totalDataChart"]):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f"chain fees row {index} is malformed")
        stamp = float(row[0])
        if stamp > 100_000_000_000:  # DefiLlama serves millisecond stamps.
            stamp /= 1000.0
        moment = datetime.fromtimestamp(stamp, tz=timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        )
        value = float(row[1])
        if not math.isfinite(value):
            continue
        points.append(EvidencePoint(moment.isoformat().replace("+00:00", "Z"), value))
    if not points:
        raise ValueError("chain fees history has no usable rows")
    return tuple(points)


_DERIVED_CHAIN_KEYS = ("borrowed", "staking", "pool2")


def _day_after(stamp: float) -> str:
    """DeFiLlama daily aggregates publish at the end of their UTC day.

    A row stamped day D aggregates activity through D 23:59:59 UTC and is
    therefore only knowable from D+1 onwards; that publication lag is the
    ``available_at`` contract for every structural series here.
    """
    moment = datetime.fromtimestamp(stamp, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    observed = moment.isoformat().replace("+00:00", "Z")
    available = (moment + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    return observed, available


def _is_base_chain(key: str) -> bool:
    """True for real chains; False for llama's derived component entries.

    ``chainTvls`` mixes base chains with ``<chain>-borrowed`` /
    ``<chain>-staking`` / ``<chain>-pool2`` components and chain-less
    global ``borrowed`` / ``staking`` / ``pool2`` buckets. Protocol TVL is
    the sum of base chains only.
    """
    if key in _DERIVED_CHAIN_KEYS:
        return False
    return not any(key.endswith(f"-{name}") for name in _DERIVED_CHAIN_KEYS)


def _aggregate_protocol_series(
    entries: Mapping[str, Any], *, borrowed: bool,
) -> tuple[EvidencePoint, ...]:
    daily: dict[int, float] = {}
    for key, entry in entries.items():
        if borrowed:
            usable = key.endswith("-borrowed")
        else:
            usable = _is_base_chain(key)
        if not usable:
            continue
        rows = entry.get("tvl") if isinstance(entry, Mapping) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            try:
                stamp = float(row["date"])
                value = float(row["totalLiquidityUSD"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            daily[int(stamp)] = daily.get(int(stamp), 0.0) + value
    points = []
    for stamp in sorted(daily):
        observed, available = _day_after(stamp)
        points.append(EvidencePoint(observed, daily[stamp], available))
    if not points:
        raise ValueError("protocol chainTvls response has no usable rows")
    return tuple(points)


def parse_protocol_tvl_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """Aggregate DeFiLlama protocol chainTvls to daily protocol TVL."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("chainTvls"), Mapping):
        raise ValueError("protocol response has no chainTvls")
    return _aggregate_protocol_series(payload["chainTvls"], borrowed=False)


def parse_protocol_borrowed_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """Aggregate the ``<chain>-borrowed`` entries to daily borrowed USD."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("chainTvls"), Mapping):
        raise ValueError("protocol response has no chainTvls")
    return _aggregate_protocol_series(payload["chainTvls"], borrowed=True)


def parse_chain_tvl_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """/v2/historicalChainTvl rows to daily chain TVL points."""
    if not isinstance(payload, list):
        raise ValueError("chain TVL response must be a list")
    points: list[EvidencePoint] = []
    for index, row in enumerate(payload):
        if not isinstance(row, Mapping) or row.get("tvl") is None:
            raise ValueError(f"chain TVL row {index} is malformed")
        stamp = float(row["date"])
        value = float(row["tvl"])
        if not math.isfinite(value):
            continue
        observed, available = _day_after(stamp)
        points.append(EvidencePoint(observed, value, available))
    if not points:
        raise ValueError("chain TVL response has no usable rows")
    return tuple(points)


def parse_chain_fees_history_published(payload: Any) -> tuple[EvidencePoint, ...]:
    """Chain fees with the structural publication-lag contract."""
    points = parse_chain_fees_history(payload)
    return tuple(
        EvidencePoint(
            point.observed_at,
            point.value,
            (parse_timestamp(point.observed_at) + timedelta(days=1))
            .isoformat().replace("+00:00", "Z"),
        )
        for point in points
    )


def parse_stablecoin_chain_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """Per-chain stablecoin chart rows to dated circulating-supply points."""
    points = parse_stablecoin_supply_history(payload)
    return tuple(
        EvidencePoint(
            point.observed_at,
            point.value,
            (parse_timestamp(point.observed_at) + timedelta(days=1))
            .isoformat().replace("+00:00", "Z"),
        )
        for point in points
    )


def parse_etf_flow_history(payload: Any) -> tuple[tuple[EvidencePoint, ...], tuple[EvidencePoint, ...]]:
    """SoSoValue v2 history to (net-flow points, AUM points).

    Reuses the provider's settled-row parser so the replay matches the live
    semantics: rows with unset flow are pending, not zero, and are skipped.
    """
    from ..providers.sosovalue import _history_points
    points = _history_points(payload)
    flows = tuple(EvidencePoint(item.observed_at, item.flow) for item in points)
    aum = tuple(
        EvidencePoint(item.observed_at, item.aum)
        for item in points if item.aum is not None
    )
    if not flows:
        raise ValueError("ETF history has no settled flow rows")
    return flows, aum


def parse_fred_vintage_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """FRED/ALFRED vintage rows to points available at their realtime start.

    Each row carries the observation date and the realtime window during
    which that value was the published one; the point becomes available at
    ``realtime_start`` so a replay decision at T can only see values FRED had
    actually published by T.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("observations"), list):
        raise ValueError("FRED response has no observations list")
    by_observation: dict[str, EvidencePoint] = {}
    for index, row in enumerate(payload["observations"]):
        if not isinstance(row, Mapping):
            raise ValueError(f"FRED row {index} is malformed")
        raw_value = row.get("value")
        if not isinstance(raw_value, str) or not raw_value.strip() or raw_value.strip() == ".":
            continue
        value = float(raw_value)
        if not math.isfinite(value):
            continue
        observed = f"{str(row['date']).strip()}T00:00:00Z"
        realtime_start = str(row.get("realtime_start") or row["date"]).strip()
        available = f"{realtime_start}T00:00:00Z"
        candidate = EvidencePoint(observed, value, available_at=available)
        current = by_observation.get(observed)
        # Keep the value that was published first; later revisions of the
        # same observation date did not exist at decision time.
        if current is None or available < (current.available_at or current.observed_at):
            by_observation[observed] = candidate
    points = sorted(by_observation.values(), key=lambda item: item.observed_at)
    if not points:
        raise ValueError("FRED vintage history has no numeric rows")
    return tuple(points)


def parse_coinmetrics_mvrv_history(payload: Any) -> tuple[EvidencePoint, ...]:
    """CoinMetrics v4 asset-metrics rows for CapMVRVCur to dated points."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("CoinMetrics response has no data list")
    points: list[EvidencePoint] = []
    for index, row in enumerate(payload["data"]):
        if not isinstance(row, Mapping):
            raise ValueError(f"CoinMetrics row {index} is malformed")
        raw_value = row.get("CapMVRVCur")
        if raw_value is None:
            continue
        value = float(raw_value)
        if not math.isfinite(value):
            continue
        # CoinMetrics serves full RFC3339 timestamps (often with nanosecond
        # precision); normalize through the shared parser instead of pasting.
        observed = normalize_timestamp(str(row["time"]).strip(), "CoinMetrics time")
        points.append(EvidencePoint(observed, value))
    if not points:
        raise ValueError("CoinMetrics MVRV history has no usable rows")
    return tuple(points)


def capital_flows_score(
    stablecoin_growth_30d: float | None,
    etf_net_to_aum_30d: float | None,
) -> tuple[float, float] | None:
    """Score and reliability for the capital_flows factor.

    Stablecoin 30d supply growth of ±2% moves the score by ±20; ETF 30d
    net flow of ±5% of AUM adds another ±20 where an ETF exists.  Missing
    stablecoin data returns None (fail-defensive); missing ETF data keeps
    the stablecoin-only view at a lower reliability.
    """
    if stablecoin_growth_30d is None:
        return None
    if etf_net_to_aum_30d is None:
        score = 50.0 + 20.0 * stablecoin_growth_30d / STABLECOIN_GROWTH_SENSITIVITY
        return _clip_score(score), _CAPITAL_FLOWS_STABLE_ONLY_RELIABILITY
    score = (
        50.0
        + 20.0 * stablecoin_growth_30d / STABLECOIN_GROWTH_SENSITIVITY
        + 20.0 * etf_net_to_aum_30d / ETF_FLOW_SENSITIVITY
    )
    return _clip_score(score), _CAPITAL_FLOWS_RELIABILITY


def macro_liquidity_score(
    walcl_change_13w: float | None, dff_change_90d: float | None,
) -> tuple[float, float] | None:
    """Balance-sheet expansion is liquidity-positive; rate rises negative."""
    if walcl_change_13w is None or dff_change_90d is None:
        return None
    score = (
        50.0
        + 20.0 * walcl_change_13w / WALCL_CHANGE_SENSITIVITY
        - 20.0 * dff_change_90d / DFF_CHANGE_SENSITIVITY
    )
    return _clip_score(score), _MACRO_LIQUIDITY_RELIABILITY


def onchain_score(fees_change_30d: float | None) -> tuple[float, float] | None:
    """Rising real fee demand is bullish settlement/DA activity evidence."""
    if fees_change_30d is None:
        return None
    score = 50.0 + 20.0 * fees_change_30d / FEES_CHANGE_SENSITIVITY
    return _clip_score(score), _ONCHAIN_RELIABILITY


def btc_valuation_score(mvrv: float | None) -> tuple[float, float] | None:
    """MVRV below 1.0 reads cheap (score 75); above 3.0 reads rich (score 20)."""
    if mvrv is None:
        return None
    if mvrv <= _MVRV_CHEAP:
        score = 75.0
    elif mvrv >= _MVRV_RICH:
        score = 20.0
    else:
        score = 75.0 - (mvrv - _MVRV_CHEAP) / (_MVRV_RICH - _MVRV_CHEAP) * 55.0
    return _clip_score(score), _BTC_VALUATION_RELIABILITY


def market_flow_state(
    stablecoin_growth_30d: float | None, etf_net_7d_usd: float | None,
) -> str | None:
    """POSITIVE / NEUTRAL / NEGATIVE for the regime flows domain."""
    if stablecoin_growth_30d is None:
        return None
    if etf_net_7d_usd is None:
        if stablecoin_growth_30d >= FLOW_STATE_GROWTH_BAND_NO_ETF:
            return "POSITIVE"
        if stablecoin_growth_30d <= -FLOW_STATE_GROWTH_BAND_NO_ETF:
            return "NEGATIVE"
        return "NEUTRAL"
    if stablecoin_growth_30d >= FLOW_STATE_GROWTH_BAND and etf_net_7d_usd > 0:
        return "POSITIVE"
    if stablecoin_growth_30d <= -FLOW_STATE_GROWTH_BAND and etf_net_7d_usd < 0:
        return "NEGATIVE"
    return "NEUTRAL"


def etf_net_to_aum(
    flows: ObservationSeries, aum: ObservationSeries, as_of: str, *, days: int = 30,
) -> float | None:
    flow_sum = flows.sum_over_days(as_of, days)
    latest_aum = aum.latest_as_of(as_of)
    if flow_sum is None or latest_aum is None or latest_aum.value <= 0:
        return None
    return flow_sum / latest_aum.value


_STRUCTURAL_SERIES_BY_SYMBOL = {
    "AAVE": {
        "tvl": "defillama:protocol:aave:tvl",
        "borrowed": "defillama:protocol:aave:borrowed",
        "fees": "defillama:fees:aave",
    },
    "SOL": {
        "chain_tvl": "defillama:chain:tvl:SOL",
        "fees": "defillama:fees:SOL",
        "stablecoins": "defillama:stablecoins:SOL",
    },
    "BNB": {
        "chain_tvl": "defillama:chain:tvl:BNB",
        "fees": "defillama:fees:BNB",
        "stablecoins": "defillama:stablecoins:BNB",
    },
}


def _structural_series(
    series: Mapping[str, ObservationSeries],
) -> dict[str, dict[str, ObservationSeries]]:
    result: dict[str, dict[str, ObservationSeries]] = {}
    for symbol, ids in _STRUCTURAL_SERIES_BY_SYMBOL.items():
        picked = {
            name: series[series_id]
            for name, series_id in ids.items()
            if series_id in series
        }
        if picked:
            result[symbol] = picked
    return result


@dataclass(frozen=True)
class EvidenceContext:
    """Frozen evidence bundle consumed per review boundary, point-in-time."""

    stablecoin: ObservationSeries | None = None
    fees: Mapping[str, ObservationSeries] | None = None
    etf_flows: Mapping[str, ObservationSeries] | None = None
    etf_aum: Mapping[str, ObservationSeries] | None = None
    fred: Mapping[str, ObservationSeries] | None = None
    mvrv: ObservationSeries | None = None
    # Strategy V2.2 Phase D structural series, research-gated: present only
    # when the caller opts in, so default replay behavior never changes
    # until the structural ranking power is validated (plan 7.8).
    structural: Mapping[str, Mapping[str, ObservationSeries]] | None = None

    @classmethod
    def from_series(
        cls,
        series: Mapping[str, ObservationSeries],
        *,
        include_structural: bool = False,
    ) -> "EvidenceContext":
        def optional(key: str) -> ObservationSeries | None:
            return series.get(key)

        return cls(
            stablecoin=optional("defillama:stablecoins:totalCirculatingUSD"),
            fees={"ETH": series["defillama:fees:ETH"]} if "defillama:fees:ETH" in series else None,
            etf_flows={
                asset: series[f"sosovalue:etf:{asset}:netflow"]
                for asset in ("BTC", "ETH")
                if f"sosovalue:etf:{asset}:netflow" in series
            } or None,
            etf_aum={
                asset: series[f"sosovalue:etf:{asset}:aum"]
                for asset in ("BTC", "ETH")
                if f"sosovalue:etf:{asset}:aum" in series
            } or None,
            fred={
                name: series[f"fred:{name}"]
                for name in ("WALCL", "DFF")
                if f"fred:{name}" in series
            } or None,
            mvrv=optional("coinmetrics:btc:CapMVRVCur"),
            structural=(
                _structural_series(series) if include_structural else None
            ),
        )

    def factor_scores(self, symbol: str, as_of: str) -> dict[str, FactorScore]:
        symbol = symbol.strip().upper()
        result: dict[str, FactorScore] = {}
        growth = (
            self.stablecoin.change_over_days(as_of, 30) if self.stablecoin is not None else None
        )
        flows = (self.etf_flows or {}).get(symbol)
        aum = (self.etf_aum or {}).get(symbol)
        etf_ratio = (
            etf_net_to_aum(flows, aum, as_of) if flows is not None and aum is not None else None
        )
        capital = capital_flows_score(growth, etf_ratio)
        if capital is not None:
            score, reliability = capital
            result["capital_flows"] = FactorScore(
                "capital_flows", score, availability="AVAILABLE", reliability=reliability,
            )
        if symbol == "BTC":
            fred = self.fred or {}
            walcl = fred.get("WALCL")
            dff = fred.get("DFF")
            macro = macro_liquidity_score(
                walcl.change_over_days(as_of, 91) if walcl is not None else None,
                dff.difference_over_days(as_of, 90) if dff is not None else None,
            )
            if macro is not None:
                score, reliability = macro
                result["macro_liquidity"] = FactorScore(
                    "macro_liquidity", score, availability="AVAILABLE", reliability=reliability,
                )
            if self.mvrv is not None:
                point = self.mvrv.latest_as_of(as_of)
                valuation = btc_valuation_score(point.value if point is not None else None)
                if valuation is not None:
                    score, reliability = valuation
                    result["btc_valuation"] = FactorScore(
                        "btc_valuation", score, availability="AVAILABLE", reliability=reliability,
                    )
        fees_series = (self.fees or {}).get(symbol)
        if fees_series is not None:
            onchain = onchain_score(fees_series.change_over_days(as_of, 30))
            if onchain is not None:
                score, reliability = onchain
                result["onchain"] = FactorScore(
                    "onchain", score, availability="AVAILABLE", reliability=reliability,
                )
        if self.structural is not None:
            # Structural evidence never overwrites an already-available
            # factor; it only fills gaps (plan 7.8 research gating).
            for name, factor in self.structural_factor_scores(symbol, as_of).items():
                if name not in result:
                    result[name] = factor
        return result

    def structural_factor_scores(self, symbol: str, as_of: str) -> dict[str, FactorScore]:
        """Deterministic structural factor scores from the Phase D series.

        Bounded linear mappings in the established evidence style: 90-day
        TVL growth of +/-50% moves fundamentals by +/-20 points, fee growth
        reuses the onchain sensitivity, and chain stablecoin supply adds
        +/-10 points at +/-20%. Research-gated: this only runs for contexts
        built with ``include_structural`` (plan 7.8 — no position authority
        until ranking power is validated).
        """
        series = (self.structural or {}).get(symbol)
        if not series:
            return {}
        result: dict[str, FactorScore] = {}
        if symbol == "AAVE":
            tvl = series.get("tvl")
            borrowed = series.get("borrowed")
            growth_90 = tvl.change_over_days(as_of, 90) if tvl is not None else None
            borrow_90 = borrowed.change_over_days(as_of, 90) if borrowed is not None else None
            if growth_90 is not None:
                score = 50.0 + 20.0 * growth_90 / 0.5
                if borrow_90 is not None:
                    score += 10.0 * borrow_90 / 0.5
                result["fundamentals"] = FactorScore(
                    "fundamentals", _clip_score(score),
                    availability="AVAILABLE", reliability=0.7,
                )
        else:
            chain_tvl = series.get("chain_tvl")
            stablecoins = series.get("stablecoins")
            growth_90 = chain_tvl.change_over_days(as_of, 90) if chain_tvl is not None else None
            stable_90 = stablecoins.change_over_days(as_of, 90) if stablecoins is not None else None
            if growth_90 is not None:
                score = 50.0 + 20.0 * growth_90 / 0.5
                if stable_90 is not None:
                    score += 10.0 * stable_90 / 0.2
                result["fundamentals"] = FactorScore(
                    "fundamentals", _clip_score(score),
                    availability="AVAILABLE", reliability=0.7,
                )
        fees = series.get("fees")
        onchain = onchain_score(fees.change_over_days(as_of, 30)) if fees is not None else None
        if onchain is not None:
            score, reliability = onchain
            result["onchain"] = FactorScore(
                "onchain", score, availability="AVAILABLE", reliability=reliability * 0.7,
            )
        return result

    def market_flow_state(self, as_of: str) -> str | None:
        growth = (
            self.stablecoin.change_over_days(as_of, 30) if self.stablecoin is not None else None
        )
        etf_7d = None
        flows = self.etf_flows or {}
        if "BTC" in flows:
            etf_7d = flows["BTC"].sum_over_days(as_of, 7)
        return market_flow_state(growth, etf_7d)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _series_filename(series_id: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in series_id) + ".json"


def _load_cached(root: Path, series_id: str) -> ObservationSeries | None:
    path = root / "series" / _series_filename(series_id)
    if not path.is_file():
        return None
    try:
        return ObservationSeries.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


_EVIDENCE_SERIES_IDS = (
    "defillama:stablecoins:totalCirculatingUSD",
    "defillama:fees:ETH",
    "sosovalue:etf:BTC:netflow",
    "sosovalue:etf:BTC:aum",
    "sosovalue:etf:ETH:netflow",
    "sosovalue:etf:ETH:aum",
    "fred:WALCL",
    "fred:DFF",
    "coinmetrics:btc:CapMVRVCur",
    "defillama:protocol:aave:tvl",
    "defillama:protocol:aave:borrowed",
    "defillama:fees:aave",
    "defillama:chain:tvl:SOL",
    "defillama:chain:tvl:BNB",
    "defillama:fees:SOL",
    "defillama:fees:BNB",
    "defillama:stablecoins:SOL",
    "defillama:stablecoins:BNB",
)


def load_evidence_series(root: str | Path) -> dict[str, ObservationSeries]:
    """Load cached evidence series from a dataset root, hash-verified."""
    base = Path(root)
    result: dict[str, ObservationSeries] = {}
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    by_id = {entry["series_id"]: entry for entry in manifest["series"]}
    for series_id in _EVIDENCE_SERIES_IDS:
        entry = by_id.get(series_id)
        if entry is None or entry.get("status") == "UNAVAILABLE" or entry.get("row_count", 0) == 0:
            continue
        series = _load_cached(base, series_id)
        if series is None:
            raise ValueError(f"evidence series is missing from the dataset: {series_id}")
        if series.content_hash != entry.get("content_sha256"):
            raise ValueError(f"evidence series hash mismatch: {series_id}")
        result[series_id] = series
    return result


def acquire_evidence_series(
    spec: Any,
    root: Path,
    *,
    http_client: Any | None = None,
    sosovalue_provider: Any | None = None,
    fred_api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch or reuse the evidence series and persist them in the dataset.

    Fail-closed per series: an unavailable source is recorded as UNAVAILABLE
    in the manifest and the replay simply keeps that factor MISSING; it never
    fabricates values.
    """
    from ..models.backtest import HistoricalSeriesManifest
    from ..providers.config import provider_api_key
    from ..providers.defillama import (
        BASE_URL as LLAMA_BASE,
        CHAIN_FEES_PATH,
        STABLECOINS_BASE_URL,
        STABLECOIN_CHARTS_PATH,
    )
    from ..providers.http import HttpClient
    from ..providers.sosovalue import SoSoValueProvider, _ETF_TYPES
    from ..providers.fred import BASE_URL as FRED_BASE, OBSERVATIONS_PATH

    client = http_client or HttpClient()
    fetched_at = normalize_timestamp(datetime.now(timezone.utc).isoformat(), "fetched_at")
    warmup_date = str(spec.warmup_start_at)[:10]
    end_date = str(spec.end_at)[:10]

    def manifest_entry(
        series: ObservationSeries, metric: str, symbol: str, quality: str,
        limitations: Sequence[str] = (),
    ) -> HistoricalSeriesManifest:
        observed = [point.observed_at for point in series.points]
        return HistoricalSeriesManifest(
            series_id=series.series_id, symbol=symbol, metric=metric, timeframe="1D",
            source=series.source, quote_currency="NONE",
            observed_start_at=min(observed), observed_end_at=max(observed),
            fetched_at=series.fetched_at, available_at_field="available_at",
            point_in_time_quality=quality, content_sha256=series.content_hash,
            row_count=len(series.points), missing_intervals=0,
            consumer=("scoring", "regime"), limitations=tuple(limitations),
        )

    entries: list[HistoricalSeriesManifest] = []
    acquired: dict[str, ObservationSeries] = {}
    failures: list[dict[str, str]] = []

    def store(series: ObservationSeries, metric: str, symbol: str, quality: str = "PUBLISHED_AT_TIME", limitations: Sequence[str] = ()) -> None:
        acquired[series.series_id] = series
        entries.append(manifest_entry(series, metric, symbol, quality, limitations))
        _write_json(root / "series" / _series_filename(series.series_id), series.as_dict())

    def fail(series_id: str, metric: str, symbol: str, reason: str) -> None:
        failures.append({"series_id": series_id, "reason": reason})
        entries.append(HistoricalSeriesManifest(
            series_id=series_id, symbol=symbol, metric=metric, timeframe="1D",
            source=series_id.split(":", 1)[0], quote_currency="NONE",
            observed_start_at=None, observed_end_at=None, fetched_at=fetched_at,
            available_at_field=None, point_in_time_quality="UNRECONSTRUCTABLE",
            content_sha256=None, row_count=0, missing_intervals=0,
            consumer=("scoring", "regime"),
            limitations=(reason,), status="UNAVAILABLE",
        ))

    def fetch(series_id: str) -> ObservationSeries | None:
        return _load_cached(root, series_id)

    # 1. DeFiLlama stablecoin total supply (free, no key).
    supply_id = "defillama:stablecoins:totalCirculatingUSD"
    try:
        series = fetch(supply_id) or ObservationSeries(
            supply_id, "market.stablecoin_supply", "defillama", "USD", fetched_at,
            parse_stablecoin_supply_history(
                client.get_json(STABLECOINS_BASE_URL + STABLECOIN_CHARTS_PATH + "/all"),
            ),
        )
        store(series, "market.stablecoin_supply", "MARKET")
    except Exception as exc:
        fail(supply_id, "market.stablecoin_supply", "MARKET", f"{exc.__class__.__name__}: {exc}")

    # 2. DeFiLlama ETH chain fees (free, no key). The breakdown chart would
    # push the payload past the client's size limit; only the aggregate
    # totalDataChart is needed.
    fees_id = "defillama:fees:ETH"
    try:
        series = fetch(fees_id) or ObservationSeries(
            fees_id, "onchain.blockspace_fees", "defillama", "USD", fetched_at,
            parse_chain_fees_history(
                client.get_json(
                    LLAMA_BASE + CHAIN_FEES_PATH
                    + "/ethereum?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true",
                ),
            ),
        )
        store(series, "onchain.blockspace_fees", "ETH",
              limitations=("PROXY_FOR_ETH_ONCHAIN_ACTIVITY_IN_REPLAY",))
    except Exception as exc:
        fail(fees_id, "onchain.blockspace_fees", "ETH", f"{exc.__class__.__name__}: {exc}")

    # 3. SoSoValue BTC/ETH ETF net flow and AUM (API key).
    provider = sosovalue_provider or SoSoValueProvider(
        api_key=provider_api_key("sosovalue"),
    )
    for asset in ("BTC", "ETH"):
        flow_id = f"sosovalue:etf:{asset}:netflow"
        aum_id = f"sosovalue:etf:{asset}:aum"
        cached_flow = fetch(flow_id)
        cached_aum = fetch(aum_id)
        if cached_flow is not None and cached_aum is not None:
            store(cached_flow, "flows.etf_net", asset)
            store(cached_aum, "flows.etf_aum", asset)
            continue
        try:
            flows, aum = parse_etf_flow_history(provider.history_payload(_ETF_TYPES[asset]))
            fetched = normalize_timestamp(datetime.now(timezone.utc).isoformat(), "fetched_at")
            store(ObservationSeries(
                flow_id, "flows.etf_net", "sosovalue", "USD", fetched, flows,
            ), "flows.etf_net", asset)
            store(ObservationSeries(
                aum_id, "flows.etf_aum", "sosovalue", "USD", fetched, aum,
            ), "flows.etf_aum", asset)
        except Exception as exc:
            reason = f"{exc.__class__.__name__}: {exc}"
            for series_id, metric in ((flow_id, "flows.etf_net"), (aum_id, "flows.etf_aum")):
                if fetch(series_id) is None:
                    fail(series_id, metric, asset, reason)

    # 4. FRED realtime-vintage macro (API key).
    key = fred_api_key or provider_api_key("fred")
    today = datetime.now(timezone.utc).date().isoformat()
    realtime_end = min(end_date, today)
    for name, metric in (("WALCL", "macro.walcl"), ("DFF", "macro.dff")):
        series_id = f"fred:{name}"
        try:
            series = fetch(series_id) or ObservationSeries(
                series_id, metric, "fred", "index", fetched_at,
                parse_fred_vintage_history(client.get_json(
                    FRED_BASE + OBSERVATIONS_PATH,
                    params={
                        "series_id": name, "api_key": key, "file_type": "json",
                        "observation_start": warmup_date,
                        "realtime_start": warmup_date, "realtime_end": realtime_end,
                    },
                )),
            )
            store(series, metric, "BTC", quality="PUBLISHED_AT_TIME",
                  limitations=("FIRST_PUBLISHED_VINTAGE_PER_OBSERVATION",))
        except Exception as exc:
            fail(series_id, metric, "BTC", f"{exc.__class__.__name__}: {exc}")

    # 5. Structural point-in-time series (Strategy V2.2 Phase D, free, no
    # key). Every series carries the day+1 publication-lag contract: a row
    # stamped day D aggregates through D 23:59:59 UTC and is available_at
    # D+1. Fail-closed per series; a missing source keeps the factor
    # MISSING, never fabricated.
    def structural(series_id: str, metric: str, symbol: str, url: str, parser: Any,
                   limitations: Sequence[str] = ()) -> None:
        try:
            series = fetch(series_id) or ObservationSeries(
                series_id, metric, "defillama", "USD", fetched_at,
                parser(client.get_json(url)),
            )
            store(series, metric, symbol, quality="PUBLISHED_AT_TIME",
                  limitations=limitations)
        except Exception as exc:
            fail(series_id, metric, symbol, f"{exc.__class__.__name__}: {exc}")

    aave_slug = "aave"
    try:
        aave_protocol_payload = client.get_json(f"{LLAMA_BASE}/protocol/{aave_slug}")
    except Exception as exc:
        aave_protocol_payload = None
        for series_id, metric in (
            ("defillama:protocol:aave:tvl", "fundamentals.protocol_tvl"),
            ("defillama:protocol:aave:borrowed", "fundamentals.protocol_borrowed"),
        ):
            fail(series_id, metric, "AAVE", f"{exc.__class__.__name__}: {exc}")
    if aave_protocol_payload is not None:
        for series_id, metric, parser in (
            ("defillama:protocol:aave:tvl", "fundamentals.protocol_tvl", parse_protocol_tvl_history),
            ("defillama:protocol:aave:borrowed", "fundamentals.protocol_borrowed", parse_protocol_borrowed_history),
        ):
            try:
                series = fetch(series_id) or ObservationSeries(
                    series_id, metric, "defillama", "USD", fetched_at,
                    parser(aave_protocol_payload),
                )
                store(series, metric, "AAVE", quality="PUBLISHED_AT_TIME",
                      limitations=("BASE_CHAIN_AGGREGATE_NO_STAKING_POOL2",))
            except Exception as exc:
                fail(series_id, metric, "AAVE", f"{exc.__class__.__name__}: {exc}")
    structural(
        "defillama:fees:aave", "fundamentals.protocol_fees", "AAVE",
        f"{LLAMA_BASE}{CHAIN_FEES_PATH}/{aave_slug}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true",
        parse_chain_fees_history_published,
    )
    for symbol, chain, metric_prefix in (("SOL", "Solana", "chain"), ("BNB", "BSC", "chain")):
        structural(
            f"defillama:chain:tvl:{symbol}", f"{metric_prefix}.tvl", symbol,
            f"{LLAMA_BASE}/v2/historicalChainTvl/{chain}",
            parse_chain_tvl_history,
        )
        structural(
            f"defillama:fees:{symbol}", "onchain.chain_fees", symbol,
            f"{LLAMA_BASE}{CHAIN_FEES_PATH}/{chain}?excludeTotalDataChart=false&excludeTotalDataChartBreakdown=true",
            parse_chain_fees_history_published,
        )
        structural(
            f"defillama:stablecoins:{symbol}", "market.chain_stablecoin_supply", symbol,
            STABLECOINS_BASE_URL + STABLECOIN_CHARTS_PATH + "/" + chain,
            parse_stablecoin_chain_history,
        )

    # 6. CoinMetrics BTC MVRV (community, no key).
    mvrv_id = "coinmetrics:btc:CapMVRVCur"
    try:
        series = fetch(mvrv_id) or ObservationSeries(
            mvrv_id, "btc_valuation.mvrv", "coinmetrics", "ratio", fetched_at,
            parse_coinmetrics_mvrv_history(client.get_json(
                "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                params={"assets": "btc", "metrics": "CapMVRVCur", "frequency": "1d",
                        "start_time": warmup_date, "end_time": end_date, "page_size": 10000},
            )),
        )
        store(series, "btc_valuation.mvrv", "BTC",
              limitations=("PROXY_FOR_LIVE_BGEOMETRICS_MVRV_ZSCORE",))
    except Exception as exc:
        fail(mvrv_id, "btc_valuation.mvrv", "BTC", f"{exc.__class__.__name__}: {exc}")

    return {
        "entries": entries,
        "acquired": {series_id: series.as_dict() for series_id, series in acquired.items()},
        "failures": failures,
    }


__all__ = [
    "EvidenceContext",
    "EvidencePoint",
    "ObservationSeries",
    "acquire_evidence_series",
    "btc_valuation_score",
    "capital_flows_score",
    "etf_net_to_aum",
    "load_evidence_series",
    "macro_liquidity_score",
    "market_flow_state",
    "onchain_score",
    "parse_chain_fees_history",
    "parse_chain_fees_history_published",
    "parse_chain_tvl_history",
    "parse_coinmetrics_mvrv_history",
    "parse_protocol_borrowed_history",
    "parse_protocol_tvl_history",
    "parse_stablecoin_chain_history",
    "parse_etf_flow_history",
    "parse_fred_vintage_history",
    "parse_stablecoin_supply_history",
]
