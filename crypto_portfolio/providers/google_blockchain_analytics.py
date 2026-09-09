"""Bounded Google Blockchain Analytics adapter for native ETH transfers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import (
    ProviderCapabilities,
    ProviderDataError,
    ProviderDiagnostic,
    ProviderRequest,
    ProviderResponse,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
)
from .http import HttpClient


PROVIDER_NAME = "google_blockchain_analytics"
METRIC = "onchain.transfer_volume"
BASE_DATASET = "bigquery-public-data.crypto_ethereum"
DEFAULT_TABLES = {
    "transactions": f"{BASE_DATASET}.transactions",
    "receipts": f"{BASE_DATASET}.receipts",
    "traces": f"{BASE_DATASET}.traces",
}
WEI_PER_ETH = Decimal(10**18)
DEFAULT_MAXIMUM_BYTES_BILLED = 10_000_000_000


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    return normalize_timestamp(value.isoformat() if isinstance(value, datetime) else value, "fetched_at")


def _decimal(value: Any, field: str, *, non_negative: bool = True) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ProviderDataError(f"{field} is not numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderDataError(f"{field} is not numeric") from exc
    if not number.is_finite() or (non_negative and number < 0):
        raise ProviderDataError(f"{field} is invalid")
    return number


def _wei(value: Any, field: str = "value_wei") -> Decimal:
    number = _decimal(value, field)
    if number != number.to_integral_value():
        raise ProviderDataError(f"{field} must be an integer")
    return number


def _row_value(row: Any, *names: str) -> Any:
    for name in names:
        if isinstance(row, Mapping) and name in row:
            return row[name]
        getter = getattr(row, "get", None)
        if callable(getter):
            value = getter(name)
            if value is not None:
                return value
        if hasattr(row, name):
            return getattr(row, name)
    return None


def _success(row: Any, *names: str) -> bool:
    value = _row_value(row, *names)
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value != 0
    return str(value).strip().lower() not in {"0", "false", "failed", "reverted", "error"}


def _addresses(row: Any, field: str) -> tuple[str, str]:
    sender = _row_value(row, "from_address", "from", "sender")
    recipient = _row_value(row, "to_address", "to", "recipient")
    if not isinstance(sender, str) or not isinstance(recipient, str) or not sender.strip() or not recipient.strip():
        raise ProviderDataError(f"{field} transfer is missing sender or recipient")
    return sender.strip().lower(), recipient.strip().lower()


def native_transfer_volume_wei(
    top_level_rows: Iterable[Any],
    internal_rows: Iterable[Any],
) -> Decimal:
    """Sum successful, non-zero, non-self native transfers without root double-counting."""
    total = Decimal(0)
    seen_transactions: set[str] = set()
    for index, row in enumerate(top_level_rows):
        transaction_hash = _row_value(row, "transaction_hash", "hash", "tx_hash")
        if not isinstance(transaction_hash, str) or not transaction_hash.strip():
            raise ProviderDataError(f"top-level transfer row {index} is missing transaction hash")
        transaction_hash = transaction_hash.strip().lower()
        if transaction_hash in seen_transactions or not _success(row, "receipt_success", "receipt_status", "success", "status"):
            continue
        sender, recipient = _addresses(row, f"top-level row {index}")
        value = _wei(_row_value(row, "value_wei", "value"))
        seen_transactions.add(transaction_hash)
        if value > 0 and sender != recipient:
            total += value
    seen_traces: set[tuple[str, str]] = set()
    for index, row in enumerate(internal_rows):
        transaction_hash = _row_value(row, "transaction_hash", "hash", "tx_hash")
        if not isinstance(transaction_hash, str) or not transaction_hash.strip():
            raise ProviderDataError(f"internal transfer row {index} is missing transaction hash")
        trace_address = _row_value(row, "trace_address", "trace_path", "path")
        if isinstance(trace_address, (list, tuple)):
            trace_key = ",".join(str(item) for item in trace_address)
            is_root = not trace_address
        else:
            trace_key = str(trace_address or "").strip()
            is_root = trace_key in {"", "0", "[]"}
        if is_root:
            continue
        identity = (transaction_hash.strip().lower(), trace_key)
        if identity in seen_traces or not _success(row, "trace_success", "success", "status"):
            continue
        sender, recipient = _addresses(row, f"internal row {index}")
        value = _wei(_row_value(row, "value_wei", "value"))
        seen_traces.add(identity)
        if value > 0 and sender != recipient:
            total += value
    return total


def build_transfer_volume_query(
    target_day: date,
    *,
    tables: Mapping[str, str] = DEFAULT_TABLES,
) -> str:
    if not isinstance(target_day, date):
        raise ValueError("target_day must be a date")
    try:
        table_names = {name: str(tables[name]) for name in ("transactions", "receipts", "traces")}
    except (KeyError, TypeError) as exc:
        raise ValueError("tables must contain transactions, receipts, and traces") from exc
    if any(not name or "`" in name for name in table_names.values()):
        raise ValueError("BigQuery table names must be non-empty and unquoted")
    return f"""
WITH top_level AS (
  SELECT
    t.hash AS transaction_hash,
    CAST(t.value AS BIGNUMERIC) AS value_wei,
    t.from_address,
    t.to_address
  FROM `{table_names['transactions']}` AS t
  JOIN `{table_names['receipts']}` AS r
    ON r.transaction_hash = t.hash
   AND r.block_number = t.block_number
  WHERE DATE(t.block_timestamp) = @target_day
    AND r.status = 1
    AND DATE(r.block_timestamp) = @target_day
    AND CAST(t.value AS BIGNUMERIC) > 0
    AND t.from_address != t.to_address
), internal AS (
  SELECT
    tr.transaction_hash,
    CAST(tr.value AS BIGNUMERIC) AS value_wei,
    tr.from_address,
    tr.to_address
  FROM `{table_names['traces']}` AS tr
  WHERE DATE(tr.block_timestamp) = @target_day
    AND tr.status = 1
    AND ARRAY_LENGTH(tr.trace_address) > 0
    AND CAST(tr.value AS BIGNUMERIC) > 0
    AND tr.from_address != tr.to_address
)
SELECT COALESCE(SUM(value_wei), 0) AS transfer_volume_wei
FROM (
  SELECT value_wei FROM top_level
  UNION ALL
  SELECT value_wei FROM internal
)
""".strip()


def _target_day(request: ProviderRequest) -> date:
    explicit = request.parameters.get("target_day")
    if explicit is not None:
        if not isinstance(explicit, str):
            raise ProviderDataError("target_day must be YYYY-MM-DD")
        try:
            return date.fromisoformat(explicit)
        except ValueError as exc:
            raise ProviderDataError("target_day must be YYYY-MM-DD") from exc
    anchor = request.parameters.get("as_of") or request.parameters.get("end")
    if anchor is None:
        anchor = _now()
    return (parse_timestamp(anchor).date() - timedelta(days=1))


def _query_value(job: Any) -> Decimal:
    try:
        rows = job.result() if hasattr(job, "result") else job
        row = next(iter(rows))
    except (StopIteration, TypeError, AttributeError) as exc:
        raise ProviderDataError("BigQuery returned no transfer-volume row") from exc
    value = _row_value(row, "transfer_volume_wei")
    if value is None:
        raise ProviderResponseError("BigQuery result has no transfer_volume_wei field")
    return _wei(value, "transfer_volume_wei")


def _job_config(*, dry_run: bool, maximum_bytes_billed: int, target_day: date) -> Any:
    try:
        from google.cloud import bigquery
    except ImportError:
        return {
            "dry_run": dry_run,
            "use_query_cache": False,
            "maximum_bytes_billed": maximum_bytes_billed,
            "query_parameters": [("target_day", "DATE", target_day.isoformat())],
        }
    config = bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_query_cache=False,
        maximum_bytes_billed=maximum_bytes_billed,
    )
    config.query_parameters = [bigquery.ScalarQueryParameter("target_day", "DATE", target_day.isoformat())]
    return config


def _daily_close(price_provider: Any, target_day: date) -> tuple[Decimal, str]:
    if hasattr(price_provider, "daily_close"):
        value = price_provider.daily_close(target_day)
        return _decimal(value, "ETH/USD daily close", non_negative=False), "injected_daily_close"
    start = datetime.combine(target_day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(target_day, time.max, tzinfo=timezone.utc)
    series = price_provider.candles("ETH", timeframe="1D", start=start, end=end)
    candles = getattr(series, "candles", series)
    candidates = [item for item in candles if getattr(item, "completed", True)]
    if not candidates:
        raise ProviderDataError("daily ETH/USD price has no completed candle")
    candle = candidates[-1]
    close = getattr(candle, "close", None)
    if close is None and isinstance(candle, Mapping):
        close = candle.get("close")
    return _decimal(close, "ETH/USD daily close", non_negative=False), "binance_completed_1d_close"


class GoogleBlockchainAnalyticsProvider:
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        client: Any | None = None,
        project: str | None = None,
        price_provider: Any | None = None,
        http_client: HttpClient | Any | None = None,
        maximum_bytes_billed: int = DEFAULT_MAXIMUM_BYTES_BILLED,
        tables: Mapping[str, str] = DEFAULT_TABLES,
        clock: Any | None = None,
    ) -> None:
        if isinstance(maximum_bytes_billed, bool) or not isinstance(maximum_bytes_billed, int) or maximum_bytes_billed < 1:
            raise ValueError("maximum_bytes_billed must be a positive integer")
        self.client = client
        self.project = project.strip() if isinstance(project, str) else project
        self.maximum_bytes_billed = maximum_bytes_billed
        self.tables = dict(tables)
        self.clock = clock
        self.http_client = http_client or (client if hasattr(client, "get_json") else HttpClient())
        if price_provider is None:
            from .binance import BinanceProvider
            price_provider = BinanceProvider(client=self.http_client)
        self.price_provider = price_provider
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(METRIC,),
            historical_series=(METRIC,),
            supports_batching=False,
            requires_api_key=False,
        )

    @classmethod
    def from_environment(
        cls,
        project: str | None,
        *,
        price_provider: Any | None = None,
        http_client: Any | None = None,
        maximum_bytes_billed: int = DEFAULT_MAXIMUM_BYTES_BILLED,
    ) -> "GoogleBlockchainAnalyticsProvider":
        if not isinstance(project, str) or not project.strip():
            raise ProviderUnavailable(
                "Google Cloud project is not configured",
                diagnostic=ProviderDiagnostic(error_code="CREDENTIAL_MISSING", detail="GOOGLE_CLOUD_PROJECT is not configured"),
            )
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise ProviderUnavailable(
                "google-cloud-bigquery is not installed",
                diagnostic=ProviderDiagnostic(error_code="ADAPTER_UNAVAILABLE", detail="google-cloud-bigquery is not installed"),
            ) from exc
        try:
            client = bigquery.Client(project=project.strip())
        except Exception as exc:
            raise ProviderUnavailable(
                "Google Application Default Credentials are unavailable",
                diagnostic=ProviderDiagnostic(error_code="CREDENTIAL_MISSING", detail="Google ADC could not create a BigQuery client"),
            ) from exc
        return cls(
            client=client,
            project=project,
            price_provider=price_provider,
            http_client=http_client,
            maximum_bytes_billed=maximum_bytes_billed,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.asset != "ETH" or request.metric_keys != (METRIC,):
            raise ProviderUnsupportedMetric("Google Blockchain Analytics only supports ETH transfer volume")
        if self.client is None:
            raise ProviderUnavailable(
                "Google Blockchain Analytics client is not configured",
                diagnostic=ProviderDiagnostic(error_code="CREDENTIAL_MISSING", detail="BigQuery client is unavailable"),
            )
        target_day = _target_day(request)
        query = build_transfer_volume_query(target_day, tables=self.tables)
        dry_run = self.client.query(query, job_config=_job_config(
            dry_run=True,
            maximum_bytes_billed=self.maximum_bytes_billed,
            target_day=target_day,
        ))
        estimated = getattr(dry_run, "total_bytes_processed", None)
        if estimated is None:
            raise ProviderResponseError(
                "BigQuery dry run did not expose total_bytes_processed",
                diagnostic=ProviderDiagnostic(error_code="PROVIDER_SCHEMA_CHANGED", detail="dry-run bytes are unavailable"),
            )
        estimated_bytes = _wei(estimated, "estimated bytes")
        if estimated_bytes > self.maximum_bytes_billed:
            raise ProviderUnavailable(
                "BigQuery estimate exceeds maximum bytes billed",
                diagnostic=ProviderDiagnostic(
                    error_code="QUERY_BUDGET_EXCEEDED",
                    detail=f"estimated bytes exceed configured maximum {self.maximum_bytes_billed}",
                ),
            )
        job = self.client.query(query, job_config=_job_config(
            dry_run=False,
            maximum_bytes_billed=self.maximum_bytes_billed,
            target_day=target_day,
        ))
        volume_wei = _query_value(job)
        price_client = getattr(self.price_provider, "client", None)
        before_price_requests = getattr(price_client, "request_count", None)
        price, price_methodology = _daily_close(self.price_provider, target_day)
        after_price_requests = getattr(price_client, "request_count", None)
        if isinstance(before_price_requests, int) and isinstance(after_price_requests, int):
            price_requests = max(0, after_price_requests - before_price_requests)
        else:
            price_requests = 0 if price_methodology == "injected_daily_close" else 1
        if price <= 0:
            raise ProviderDataError("ETH/USD daily close must be > 0")
        volume_eth = volume_wei / WEI_PER_ETH
        value_usd = volume_eth * price
        if not value_usd.is_finite() or value_usd < 0 or value_usd > Decimal(str(float("1e308"))):
            raise ProviderDataError("derived transfer volume USD is invalid")
        fetched_at = _now(self.clock)
        observed_at = normalize_timestamp(
            datetime.combine(target_day, time.max, tzinfo=timezone.utc).isoformat(),
            "observed_at",
        )
        return ProviderResponse(({
            "asset": "ETH",
            "metric_key": METRIC,
            "value": float(value_usd),
            "unit": metric_definition(METRIC).unit,
            "period": "1d",
            "observed_at": observed_at,
            "fetched_at": fetched_at,
            "source": self.name,
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": BASE_DATASET,
                "source_url": "https://cloud.google.com/blockchain-analytics/docs",
                "source_metric": "native_eth_value_transfers",
                "methodology": "successful_top_level_and_internal_eth_value_transfers_same_day_close",
                "day": target_day.isoformat(),
                "native_volume_eth": str(volume_eth),
                "price_source": price_methodology,
                "price_usd": str(price),
                "includes_internal_transfers": True,
                "excludes_failed_zero_and_self_transfers": True,
                "query_maximum_bytes_billed": self.maximum_bytes_billed,
                "estimated_bytes_processed": str(estimated_bytes),
            },
        },), network_requests=2 + price_requests)


__all__ = [
    "BASE_DATASET",
    "DEFAULT_MAXIMUM_BYTES_BILLED",
    "DEFAULT_TABLES",
    "GoogleBlockchainAnalyticsProvider",
    "METRIC",
    "native_transfer_volume_wei",
    "build_transfer_volume_query",
]
