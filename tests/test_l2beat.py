import copy
import unittest
from unittest.mock import patch

from crypto_portfolio.providers.base import ProviderRequest, ProviderResponseError
from crypto_portfolio.providers.l2beat import (
    ACTIVITY_PATH,
    BASE_URL,
    OPENAPI_PATH,
    PROJECTS_PATH,
    TVS_PATH,
    L2BeatProvider,
)
from crypto_portfolio.providers.probe import probe_provider, validate_l2beat_openapi
from crypto_portfolio.providers.router import ProviderRouter


AS_OF = "2026-09-07T00:00:00Z"


def openapi_document():
    return {
        "openapi": "3.1.0",
        "servers": [{"url": BASE_URL}],
        "components": {
            "securitySchemes": {
                "apiKeyAuth": {"in": "query", "name": "apiKey", "type": "apiKey"},
            },
        },
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/v1/projects": {"get": {}},
            "/v1/tvs": {"get": {"parameters": [{"in": "query", "name": "range", "schema": {"enum": ["7d", "30d", "90d", "180d", "1y", "max"]}}]}},
            "/v1/activity": {"get": {"parameters": [{"in": "query", "name": "range", "schema": {"enum": ["30d", "90d", "180d", "1y", "max"]}}]}},
        },
    }


def tvs_rows():
    return [{
        "timestamp": 1788739200,
        "totalTvs": 123456789,
        "bySource": {"native": 1, "canonical": 2, "external": 3},
        "byCategory": {
            "stablecoins": 1,
            "eth": 2,
            "btc": 3,
            "other": 4,
            "publicRwa": 5,
            "restrictedRwa": 6,
        },
    }]


def activity_rows():
    return [{"timestamp": 1788739200, "txCount": 42, "uopsCount": 0}]


class Client:
    def __init__(self):
        self.calls = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        if url.endswith(OPENAPI_PATH):
            return openapi_document()
        if url.endswith(PROJECTS_PATH):
            return [{"id": "arb-one", "slug": "arb-one", "name": "Arbitrum One"}]
        if url.endswith("/v1/project/arb-one"):
            return {"id": "arb-one", "slug": "arb-one", "name": "Arbitrum One", "hostChain": "Ethereum"}
        if url.endswith(f"{TVS_PATH}/arb-one"):
            return tvs_rows()
        if url.endswith(f"{ACTIVITY_PATH}/arb-one"):
            return activity_rows()
        raise AssertionError(f"unexpected L2BEAT endpoint: {url}")


class L2BeatContractTests(unittest.TestCase):
    def test_verified_openapi_requires_query_api_key(self):
        contract = validate_l2beat_openapi(openapi_document())
        self.assertEqual(contract["openapi_version"], "3.1.0")
        self.assertEqual(contract["auth_scheme"], "apiKey query parameter")
        self.assertEqual(contract["operation_security"], "inherits top-level security")
        public = copy.deepcopy(openapi_document())
        public.pop("security")
        with self.assertRaises(ProviderResponseError):
            validate_l2beat_openapi(public)

    def test_provider_uses_official_query_contract_and_paths(self):
        client = Client()
        provider = L2BeatProvider(client=client, api_key="fake-key")
        response = provider.collect(ProviderRequest(
            "l2beat",
            "ethereum_l2",
            "ETH",
            {"as_of": AS_OF},
            ("eth.l2.tvs_usd", "eth.l2.activity_30d"),
        ))
        self.assertEqual({item["metric_key"] for item in response.observations}, {
            "eth.l2.tvs_usd", "eth.l2.activity_30d",
        })
        self.assertTrue(provider.capabilities.requires_api_key)
        self.assertTrue(all(call[1]["apiKey"] == "fake-key" for call in client.calls))
        self.assertEqual(
            next(call[1]["range"] for call in client.calls if call[0].endswith(f"{TVS_PATH}/arb-one")),
            "30d",
        )
        self.assertEqual(
            next(call[1]["range"] for call in client.calls if call[0].endswith(f"{ACTIVITY_PATH}/arb-one")),
            "30d",
        )

    def test_probe_reports_openapi_and_all_read_operations_without_secret(self):
        client = Client()
        config = {
            "providers": {"l2beat": {"enabled": "AUTO", "api_key_env": "L2BEAT_API_KEY"}},
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        provider = L2BeatProvider(client=client, api_key="fake-key")
        router = ProviderRouter({"l2beat": provider}, config=config)
        with patch.dict("os.environ", {"L2BEAT_API_KEY": "fake-key"}, clear=True):
            rows = probe_provider(router, "l2beat")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["auth_scheme"], "apiKey query parameter")
        self.assertEqual({row.get("operation_path") for row in rows[2:]}, {"/v1/tvs", "/v1/activity"})
        self.assertTrue(all(row["http_status"] == 200 for row in rows))
        self.assertNotIn("fake-key", str(rows))


if __name__ == "__main__":
    unittest.main()
