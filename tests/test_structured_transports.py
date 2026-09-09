import unittest
from tempfile import TemporaryDirectory

from crypto_portfolio.events import EventScanner, EventSourceScanResponse
from crypto_portfolio.events.transports import (
    BNB_GOVERNOR_ADDRESS,
    EventTransportCache,
    RPC_EVENT_TOPICS,
    StructuredEventTransport,
)


AS_OF = "2026-09-09T00:00:00Z"


class Client:
    def __init__(self):
        self.calls = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        if "releases" in url:
            return [
                {"id": 1, "name": "stable", "published_at": "2026-09-08T00:00:00Z", "html_url": "https://github.com/a/b/releases/v1", "prerelease": False},
                {"id": 2, "name": "pre", "published_at": "2026-09-08T00:00:00Z", "html_url": "https://github.com/a/b/releases/v2", "prerelease": True},
                {"id": 3, "name": "old", "published_at": "2020-01-01T00:00:00Z", "html_url": "https://github.com/a/b/releases/v0", "prerelease": False},
            ]
        if "governance.aave.com" in url:
            return {"topic_list": {"topics": [{"id": 7, "title": "Proposal", "created_at": "2026-09-08T00:00:00Z", "url": "/t/proposal/7"}]}}
        if url == "https://bsc-dataseed.bnbchain.org" or "bsc-dataseed-public" in url:
            return {"jsonrpc": "2.0", "id": 1, "result": []}
        raise AssertionError(url)

    def get_text(self, url, **_kwargs):
        self.calls.append(("TEXT", url))
        return "<rss><channel><item><title>Notice</title><pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate><link>https://example.test/notice</link><description>bounded</description></item></channel></rss>"

    def post_json(self, url, *, json_body, idempotent=False, **_kwargs):
        self.calls.append(("POST", url, json_body, idempotent))
        if json_body["method"] == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x100"}
        return {"jsonrpc": "2.0", "id": 2, "result": [{
            "address": BNB_GOVERNOR_ADDRESS,
            "topics": [RPC_EVENT_TOPICS[0], "0x01"],
            "blockNumber": "0x100",
            "transactionHash": "0x" + "1" * 64,
            "timestamp": "2026-09-08T00:00:00Z",
        }]}


class StructuredTransportTests(unittest.TestCase):
    def setUp(self):
        self.scanner = EventScanner()
        self.client = Client()
        self.transport = StructuredEventTransport(client=self.client)

    def request(self, asset, category, source_id):
        return next(item for item in self.scanner.build_requests(asset, category, AS_OF) if item.source_id == source_id)

    def test_github_releases_filter_prerelease_and_lookback(self):
        response = self.transport.fetch(self.request("BTC", "security", "bitcoin-core-security-advisories"))
        self.assertTrue(response.reachable)
        self.assertTrue(response.complete_for_source)
        self.assertEqual([item["title"] for item in response.items], ["stable"])
        self.assertEqual(response.items[0]["materiality"], "CANDIDATE")
        self.assertEqual(response.items[0]["relevance"], "UNKNOWN")

    def test_rss_and_discourse_are_normalized(self):
        rss = self.transport.fetch(self.request("BTC", "security", "bitcoin-core-releases"))
        self.assertEqual(rss.items[0]["title"], "Notice")
        self.assertIn(("TEXT", "https://bitcoincore.org/en/feed.xml"), self.client.calls)
        self.assertNotIn(("TEXT", "https://bitcoincore.org/en/releases/"), self.client.calls)
        discourse = self.transport.fetch(self.request("AAVE", "governance", "aave-governance-proposals"))
        self.assertEqual(discourse.items[0]["canonical_url"], "https://governance.aave.com/t/proposal/7")

    def test_rpc_decodes_only_allowlisted_events(self):
        response = self.transport.fetch(self.request("BNB", "governance", "bnb-governor-rpc"))
        self.assertTrue(response.reachable)
        self.assertEqual(len(response.items), 1)
        self.assertIn("BNB Governor proposal", response.items[0]["title"])
        logs_call = next(call for call in self.client.calls if call[0] == "POST" and call[2]["method"] == "eth_getLogs")
        self.assertEqual(logs_call[2]["params"][0]["address"], BNB_GOVERNOR_ADDRESS)

    def test_rpc_log_without_timestamp_is_not_marked_complete(self):
        class NoTimestamp(Client):
            def post_json(self, url, *, json_body, idempotent=False, **kwargs):
                if json_body["method"] == "eth_blockNumber":
                    return {"jsonrpc": "2.0", "id": 1, "result": "0x100"}
                return {"jsonrpc": "2.0", "id": 2, "result": [{
                    "address": BNB_GOVERNOR_ADDRESS,
                    "topics": [RPC_EVENT_TOPICS[0], "0x01"],
                    "blockNumber": "0x100",
                    "transactionHash": "0x" + "1" * 64,
                }]}

        response = StructuredEventTransport(client=NoTimestamp()).fetch(
            self.request("BNB", "governance", "bnb-governor-rpc")
        )
        self.assertFalse(response.reachable)
        self.assertFalse(response.complete_for_source)

    def test_complete_empty_source_needs_no_external_classification(self):
        class Empty(Client):
            def get_json(self, url, **kwargs):
                return []

        response = StructuredEventTransport(client=Empty()).fetch(self.request("BTC", "security", "bitcoin-core-security-advisories"))
        self.assertEqual(EventSourceScanResponse.from_mapping(response.as_dict()).items, ())
        scan = self.scanner.scan(
            "BTC", "security", AS_OF,
            responses=tuple(
                EventSourceScanResponse(item.source_id, True, AS_OF, (), None)
                for item in self.scanner.build_requests("BTC", "security", AS_OF)
            ),
        )
        self.assertEqual(scan.status, "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES")

    def test_transport_cache_reuses_normalized_candidates(self):
        with TemporaryDirectory() as directory:
            cached = EventTransportCache(directory)
            transport = StructuredEventTransport(client=self.client, cache=cached)
            request = self.request("BTC", "security", "bitcoin-core-security-advisories")
            first = transport.fetch(request)
            calls = len(self.client.calls)
            second = transport.fetch(request)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(len(self.client.calls), calls)

    def test_candidate_response_cannot_be_treated_as_a_clean_scan(self):
        request = self.request("BTC", "security", "bitcoin-core-security-advisories")
        response = self.transport.fetch(request)
        with self.assertRaisesRegex(ValueError, "LUNA_MAX"):
            self.scanner.build_result("BTC", "security", AS_OF, (response,))


if __name__ == "__main__":
    unittest.main()
