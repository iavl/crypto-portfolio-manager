from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_portfolio.providers.base import ProviderDataError, ProviderRequest
from crypto_portfolio.providers.bnb_rpc import BNBRPCProvider
from crypto_portfolio.providers.cache import ProviderCache


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class RpcClient:
    def __init__(self, blocks):
        self.blocks = blocks
        self.calls = []

    def post_json(self, url, *, json_body, idempotent=False, **_kwargs):
        self.calls.append((url, json_body, idempotent))
        if isinstance(json_body, list):
            return [
                {"jsonrpc": "2.0", "id": item["id"], "result": hex(len(self.blocks[int(item["params"][0], 16)]["transactions"]))}
                for item in json_body
            ]
        method = json_body["method"]
        if method == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": json_body["id"], "result": hex(len(self.blocks) - 1)}
        if method == "eth_getBlockByNumber":
            block = self.blocks[int(json_body["params"][0], 16)]
            return {"jsonrpc": "2.0", "id": json_body["id"], "result": block}
        raise AssertionError(method)


def blocks():
    timestamps = (
        "2026-09-01T00:00:00Z",
        "2026-09-01T12:00:00Z",
        "2026-09-01T23:59:00Z",
        "2026-09-02T00:00:00Z",
        "2026-09-02T12:00:00Z",
        "2026-09-02T23:59:00Z",
        "2026-09-03T00:00:00Z",
        "2026-09-03T12:00:00Z",
        "2026-09-03T23:59:00Z",
    )
    return [
        {
            "number": hex(index),
            "timestamp": hex(_epoch(timestamp)),
            "hash": f"0x{index:064x}",
            "transactions": [f"0x{item:064x}" for item in range(index + 1)],
        }
        for index, timestamp in enumerate(timestamps)
    ]


class BnbRpcTests(unittest.TestCase):
    def request(self, as_of="2026-09-03T00:00:00Z"):
        return ProviderRequest(
            "bnb_rpc", "onchain", "BNB", {"as_of": as_of}, ("onchain.transaction_count",)
        )

    def test_counts_only_one_completed_utc_day_and_batches_block_requests(self):
        with TemporaryDirectory() as directory:
            client = RpcClient(blocks())
            provider = BNBRPCProvider(
                client=client,
                cache=ProviderCache(Path(directory) / "cache"),
                confirmation_blocks=0,
                clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
            )
            result = provider.collect(self.request())
        self.assertEqual(result.observations[0]["value"], 4 + 5 + 6)
        self.assertEqual(result.observations[0]["observed_at"], "2026-09-03T00:00:00Z")
        self.assertEqual(result.observations[0]["metadata"]["methodology"], "sum eth_getBlockTransactionCountByNumber over canonical blocks in one completed UTC day")
        self.assertTrue(any(isinstance(call[1], list) for call in client.calls))

    def test_cached_daily_value_avoids_a_second_rpc_scan(self):
        with TemporaryDirectory() as directory:
            client = RpcClient(blocks())
            provider = BNBRPCProvider(
                client=client,
                cache=ProviderCache(Path(directory) / "cache"),
                confirmation_blocks=0,
                clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
            )
            provider.collect(self.request())
            calls = len(client.calls)
            cached = provider.collect(self.request())
        self.assertEqual(cached.observations[0]["value"], 15)
        self.assertEqual(len(client.calls), calls)

    def test_current_incomplete_day_is_excluded(self):
        with TemporaryDirectory() as directory:
            provider = BNBRPCProvider(
                client=RpcClient(blocks()),
                cache=ProviderCache(Path(directory) / "cache"),
                confirmation_blocks=0,
            )
            result = provider.collect(self.request("2026-09-02T12:00:00Z"))
        self.assertEqual(result.observations[0]["value"], 1 + 2 + 3)
        self.assertEqual(result.observations[0]["metadata"]["day"], "2026-09-01")

    def test_malformed_block_count_fails_closed(self):
        class BadCount(RpcClient):
            def post_json(self, url, *, json_body, idempotent=False, **kwargs):
                result = super().post_json(url, json_body=json_body, idempotent=idempotent, **kwargs)
                if isinstance(json_body, list):
                    result[0]["result"] = "bad"
                return result

        with TemporaryDirectory() as directory, self.assertRaises(ProviderDataError):
            BNBRPCProvider(
                client=BadCount(blocks()),
                cache=ProviderCache(Path(directory) / "cache"),
                confirmation_blocks=0,
            ).collect(self.request())

    def test_cached_tail_continuity_mismatch_is_rejected(self):
        with TemporaryDirectory() as directory:
            chain = blocks()
            client = RpcClient(chain)
            provider = BNBRPCProvider(
                client=client,
                cache=ProviderCache(Path(directory) / "cache"),
                confirmation_blocks=0,
            )
            provider.collect(self.request())
            chain[5] = {**chain[5], "hash": "0x" + "f" * 64}
            with self.assertRaisesRegex(ProviderDataError, "continuity mismatch"):
                provider.collect(self.request("2026-09-04T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
