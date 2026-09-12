import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from crypto_portfolio.providers.base import (
    ProviderDataError,
    ProviderRequest,
    ProviderResponseError,
    ProviderUnsupportedMetric,
)
from crypto_portfolio.providers.blockchair import (
    BASE_URL,
    ETHEREUM_STATS_PATH,
    BlockchairProvider,
    WEI_PER_ETH,
    parse_stats_payload,
)


FIXTURE = Path(__file__).parent / "fixtures/providers/blockchair_ethereum_stats.json"
FETCHED_AT = "2026-09-09T12:00:00Z"
REQUEST = ProviderRequest(
    "blockchair",
    "onchain",
    "ETH",
    {"as_of": FETCHED_AT},
    ("onchain.transfer_volume",),
)


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class BlockchairProviderTests(unittest.TestCase):
    def test_normalizes_ethereum_stats_with_bounded_metadata(self):
        client = Client(payload())
        provider = BlockchairProvider(
            client=client,
            clock=lambda: datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        )

        response = provider.collect(REQUEST)
        observation = response.observations[0]

        self.assertEqual(response.network_requests, 1)
        self.assertEqual(client.calls, [(BASE_URL + ETHEREUM_STATS_PATH, {})])
        self.assertEqual(observation["asset"], "ETH")
        self.assertEqual(observation["metric_key"], "onchain.transfer_volume")
        self.assertEqual(observation["value"], 10_000_000_000.0)
        self.assertEqual(observation["unit"], "USD")
        self.assertEqual(observation["period"], "1d")
        self.assertEqual(observation["source"], "blockchair")
        self.assertEqual(observation["confidence"], "MEDIUM")
        self.assertEqual(observation["observed_at"], "2026-09-09T10:00:00Z")
        self.assertEqual(observation["metadata"]["raw_unit"], "wei")
        self.assertEqual(observation["metadata"]["window"], "rolling_24h")
        self.assertEqual(observation["metadata"]["context_state"], 25000000)
        self.assertEqual(observation["metadata"]["api_version"], "2.x")
        self.assertNotIn("transactions", observation["metadata"])
        self.assertIsNone(response.payload)

    def test_collect_accepts_block_time_after_request_as_of(self):
        # Acquisition stamps as_of once at collection start; the newest indexed
        # block lands between that stamp and the fetch (a 2026-09-11T23:17Z
        # review lost this race and marked the metric STALE). The gauge anchor
        # is fetched_at, so this must be accepted, not rejected.
        data = payload()
        data["data"]["best_block_time"] = "2026-09-09 12:00:02"
        client = Client(data)
        provider = BlockchairProvider(
            client=client,
            clock=lambda: datetime(2026, 9, 9, 12, 0, 5, tzinfo=timezone.utc),
        )

        response = provider.collect(REQUEST)

        self.assertEqual(response.observations[0]["observed_at"], "2026-09-09T12:00:02Z")
        self.assertEqual(response.observations[0]["fetched_at"], "2026-09-09T12:00:05Z")

    def test_best_block_time_after_fetch_still_fails_closed(self):
        data = payload()
        data["data"]["best_block_time"] = "2026-09-09 12:00:01"
        with self.assertRaises(ProviderDataError):
            parse_stats_payload(data, fetched_at=FETCHED_AT)

    def test_calculates_before_float_conversion(self):
        raw_volume = "1234567890123456789012345"
        raw_price = "0.123456789012345678"
        value = parse_stats_payload(
            {
                "data": {
                    "volume_24h_approximate": raw_volume,
                    "market_price_usd": raw_price,
                    "best_block_time": "2026-09-09 10:00:00",
                }
            },
            fetched_at=FETCHED_AT,
        )
        expected = Decimal(raw_volume) / WEI_PER_ETH * Decimal(raw_price)
        self.assertEqual(value["value"], float(expected))

    def test_zero_volume_is_valid(self):
        data = payload()
        data["data"]["volume_24h_approximate"] = "0"
        observation = parse_stats_payload(data, fetched_at=FETCHED_AT)
        self.assertEqual(observation["value"], 0.0)

    def test_invalid_volume_fails_closed(self):
        for invalid in (None, "", "not-a-number", "-1", "NaN", "Infinity"):
            with self.subTest(value=invalid):
                data = payload()
                if invalid is None:
                    del data["data"]["volume_24h_approximate"]
                else:
                    data["data"]["volume_24h_approximate"] = invalid
                with self.assertRaises(ProviderDataError):
                    parse_stats_payload(data, fetched_at=FETCHED_AT)

    def test_invalid_price_fails_closed(self):
        for invalid in (None, 0, -1, "not-a-number", "NaN", "Infinity"):
            with self.subTest(value=invalid):
                data = payload()
                if invalid is None:
                    del data["data"]["market_price_usd"]
                else:
                    data["data"]["market_price_usd"] = invalid
                with self.assertRaises(ProviderDataError):
                    parse_stats_payload(data, fetched_at=FETCHED_AT)

    def test_invalid_payload_and_timestamp_fail_explicitly(self):
        for invalid in ([], {"data": None}, {"data": []}, {"data": payload()["data"], "context": {"code": 500}}):
            with self.subTest(payload=invalid):
                with self.assertRaises(ProviderResponseError):
                    parse_stats_payload(invalid, fetched_at=FETCHED_AT)

        data = payload()
        data["data"]["best_block_time"] = "not-a-timestamp"
        with self.assertRaises(ProviderDataError):
            parse_stats_payload(data, fetched_at=FETCHED_AT)

    def test_missing_block_time_uses_current_fetch_timestamp(self):
        data = payload()
        del data["data"]["best_block_time"]
        observation = parse_stats_payload(data, fetched_at=FETCHED_AT)
        self.assertEqual(observation["observed_at"], FETCHED_AT)
        self.assertEqual(observation["metadata"]["observed_timestamp_source"], "fetched_at")

    def test_unsupported_assets_and_metrics_are_rejected(self):
        provider = BlockchairProvider(client=Client(payload()), clock=lambda: FETCHED_AT)
        for request in (
            ProviderRequest("blockchair", "onchain", "BTC", {}, ("onchain.transfer_volume",)),
            ProviderRequest("blockchair", "onchain", "ETH", {}, ("onchain.active_addresses",)),
        ):
            with self.subTest(request=request):
                with self.assertRaises(ProviderUnsupportedMetric):
                    provider.collect(request)

        self.assertEqual(provider.capabilities.metric_keys, ("onchain.transfer_volume",))
        self.assertFalse(provider.capabilities.requires_api_key)


if __name__ == "__main__":
    unittest.main()
