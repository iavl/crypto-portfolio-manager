import unittest
from unittest.mock import patch

from crypto_portfolio.providers.base import ProviderDataError, ProviderInsufficientHistory, ProviderRequest
from crypto_portfolio.providers.config import load_provider_config, provider_enabled
from crypto_portfolio.providers.ethereum_protocol import (
    DEFAULT_RPC_URL,
    EthereumProtocolProvider,
    blob_base_fee,
    blob_fee_burn,
)
from crypto_portfolio.providers.router import ProviderRouter


BLOCK = {
    "number": "0x10",
    "timestamp": "0x68bf0e00",
    "baseFeePerGas": "0x64",
    "gasUsed": "0x3e8",
    "blobGasUsed": "0x64",
    "excessBlobGas": "0x0",
}


class RpcClient:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"jsonrpc": "2.0", "id": 1, "result": BLOCK}

    def post_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class EthereumProtocolProviderTests(unittest.TestCase):
    def test_config_is_enabled_without_a_credential(self):
        config = load_provider_config()
        self.assertTrue(config["providers"]["ethereum_protocol"]["enabled"])
        self.assertTrue(provider_enabled("ethereum_protocol", config, {}))

    def test_rpc_override_and_disabled_config_control_registration(self):
        base = {
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        enabled = {
            **base,
            "providers": {"ethereum_protocol": {
                "enabled": True,
                "base_url_env": "ETHEREUM_RPC_URL",
                "base_url_default": DEFAULT_RPC_URL,
            }},
        }
        with patch.dict("os.environ", {"ETHEREUM_RPC_URL": "https://rpc.example.test"}, clear=True):
            router = ProviderRouter(config=enabled, http_client=object())
        self.assertEqual(router.providers["ethereum_protocol"].rpc_url, "https://rpc.example.test")

        disabled = {**enabled, "providers": {"ethereum_protocol": {**enabled["providers"]["ethereum_protocol"], "enabled": False}}}
        self.assertNotIn("ethereum_protocol", ProviderRouter(config=disabled, http_client=object()).providers)

    def test_probe_makes_one_latest_block_rpc_call_and_returns_normalized_fields(self):
        client = RpcClient()
        result = EthereumProtocolProvider(client=client).probe()
        self.assertEqual(len(client.calls), 1)
        url, kwargs = client.calls[0]
        self.assertEqual(url, DEFAULT_RPC_URL)
        self.assertTrue(kwargs["idempotent"])
        self.assertEqual(kwargs["json_body"]["method"], "eth_getBlockByNumber")
        self.assertEqual(kwargs["json_body"]["params"], ["latest", False])
        self.assertEqual(result["latest_block_number"], 16)
        self.assertTrue(result["required_execution_fields"])
        self.assertTrue(result["blob_fields_present"])
        self.assertNotIn("baseFeePerGas", result)
        self.assertNotIn("result", result)

    def test_probe_validates_required_fields(self):
        client = RpcClient({"result": {"number": "0x1"}})
        with self.assertRaises(ProviderDataError):
            EthereumProtocolProvider(client=client).probe()

    def test_blob_fee_and_supplied_block_batch_remain_exact(self):
        self.assertEqual(blob_base_fee({"excessBlobGas": "0x0"}), 1)
        self.assertEqual(blob_fee_burn({"excessBlobGas": "0x0", "blobGasUsed": "0x64"}), 100)
        client = RpcClient()
        provider = EthereumProtocolProvider(client=client)
        response = provider.collect(ProviderRequest(
            "ethereum_protocol", "ethereum_protocol", "ETH", {"blocks": [BLOCK]},
            ("eth.monetary.burn_30d_eth",),
        ))
        self.assertEqual(response.network_requests, 0)
        self.assertEqual(len(client.calls), 0)
        self.assertAlmostEqual(response.observations[0]["value"], (100_000 + 100) / 10**18)

    def test_missing_block_batch_fails_closed_without_rpc(self):
        client = RpcClient()
        with self.assertRaises(ProviderInsufficientHistory):
            EthereumProtocolProvider(client=client).collect(ProviderRequest(
                "ethereum_protocol", "ethereum_protocol", "ETH", {},
                ("eth.monetary.burn_30d_eth",),
            ))
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
