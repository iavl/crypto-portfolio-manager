import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory

from crypto_portfolio.events import EventScanner, EventSourceScanResponse
from crypto_portfolio.events.transports import (
    MAX_CANDIDATES,
    EventCandidate,
    EventTransportCache,
    EventTransportSpec,
    StructuredEventTransport,
    _dedup,
    infer_transport_kind,
)


AS_OF = "2026-09-09T00:00:00Z"
FIXTURES = Path(__file__).parent / "fixtures" / "events"


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
        if "governance.aave.com/c/risk/7" in url:
            filename = "aave_risk_page_1.json" if "page=1" in url else "aave_risk_page_0.json"
            return json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        if "governance.aave.com/c/risk/general/12" in url:
            page = json.loads((FIXTURES / "aave_risk_page_0.json").read_text(encoding="utf-8"))
            return {"topic_list": {"topics": page["topic_list"]["topics"], "more_topics_url": None}}
        if "governance.aave.com" in url:
            return {"topic_list": {"topics": [{"id": 7, "title": "Proposal", "created_at": "2026-09-08T00:00:00Z", "url": "/t/proposal/7"}]}}
        raise AssertionError(url)

    def get_text(self, url, **_kwargs):
        self.calls.append(("TEXT", url))
        if url == "https://blog.ethereum.org/feed.xml":
            return (FIXTURES / "ethereum_foundation_feed.xml").read_text(encoding="utf-8")
        if url == "https://www.esma.europa.eu/rss.xml":
            return (FIXTURES / "esma_feed.xml").read_text(encoding="utf-8")
        return "<rss><channel><item><title>Notice</title><pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate><link>https://example.test/notice</link><description>bounded</description></item></channel></rss>"

class StructuredTransportTests(unittest.TestCase):
    def setUp(self):
        self.scanner = EventScanner()
        self.client = Client()
        self.transport = StructuredEventTransport(client=self.client)

    def request(self, asset, category, source_id):
        return next(item for item in self.scanner.build_requests(asset, category, AS_OF) if item.source_id == source_id)

    def test_required_sources_have_structured_transport_endpoints(self):
        expected = (
            ("ETH", "security", "ethereum-foundation-security", "https://blog.ethereum.org/feed.xml", "RSS_ATOM"),
            ("AAVE", "security", "aave-security", "https://governance.aave.com/c/risk/7.json", "DISCOURSE_JSON"),
            ("MARKET", "regulatory", "esma-mica", "https://www.esma.europa.eu/rss.xml", "RSS_ATOM"),
        )
        for asset, category, source_id, endpoint, kind in expected:
            with self.subTest(source_id=source_id):
                request = self.request(asset, category, source_id)
                self.assertIn(endpoint, request.source_urls)
                self.assertEqual(infer_transport_kind(endpoint, source_id), kind)

    def test_ethereum_foundation_rss_succeeds_without_fetching_canonical_html(self):
        result = self.transport.fetch_result(self.request("ETH", "security", "ethereum-foundation-security"))
        self.assertTrue(result.reachable)
        self.assertTrue(result.complete_for_source)
        self.assertIsNone(result.error)
        self.assertEqual(result.transport_kind, "RSS_ATOM")
        self.assertEqual(len(result.candidates), 2)
        self.assertIn(("TEXT", "https://blog.ethereum.org/feed.xml"), self.client.calls)

    def test_aave_discourse_pagination_and_cross_endpoint_deduplication(self):
        result = self.transport.fetch_result(self.request("AAVE", "security", "aave-security"))
        urls = [call[1] for call in self.client.calls if call[0] == "GET" and "governance.aave.com" in call[1]]
        ids = [candidate.external_id for candidate in result.candidates]
        self.assertTrue(result.reachable)
        self.assertTrue(result.complete_for_source)
        self.assertEqual(set(ids), {"discourse:101", "discourse:102"})
        self.assertEqual(ids.count("discourse:101"), 1)
        self.assertTrue(any("https://governance.aave.com/c/risk/7.json" in url and "page=1" in url for url in urls))
        self.assertNotIn("https://evil.example/", " ".join(urls))

    def test_discourse_page_bound_is_incomplete_and_cached_as_incomplete(self):
        class Endless(Client):
            def get_json(self, url, *, params=None, headers=None):
                if "governance.aave.com/c/risk/" in url:
                    self.calls.append(("GET", url, params, headers))
                    return {
                        "topic_list": {
                            "topics": [{"id": 201, "title": "Still paging", "created_at": AS_OF, "url": "/t/still-paging/201"}],
                            "more_topics_url": "/c/risk/7?page=next",
                        }
                    }
                return super().get_json(url, params=params, headers=headers)

        with TemporaryDirectory() as directory:
            client = Endless()
            transport = StructuredEventTransport(
                client=client,
                cache=EventTransportCache(directory),
                max_discourse_pages=2,
            )
            request = self.request("AAVE", "security", "aave-security")
            first = transport.fetch_result(request)
            calls = len(client.calls)
            second = transport.fetch_result(request)
        self.assertTrue(first.reachable)
        self.assertFalse(first.complete_for_source)
        self.assertIn("DISCOURSE_PAGINATION_LIMIT", first.error or "")
        self.assertFalse(second.complete_for_source)
        self.assertEqual(len(client.calls), calls)

    def test_discourse_stops_when_ordered_created_window_is_covered(self):
        class Window(Client):
            def get_json(self, url, *, params=None, headers=None):
                self.calls.append(("GET", url, params, headers))
                if "page=1" in url:
                    return {
                        "topic_list": {
                            "topics": [{
                                "id": 401,
                                "title": "Boundary",
                                "created_at": "2026-08-09T00:00:00Z",
                                "url": "/t/boundary/401",
                            }],
                            "more_topics_url": "/c/risk/7?page=2",
                        }
                    }
                return {
                    "topic_list": {
                        "topics": [{
                            "id": 400,
                            "title": "Recent",
                            "created_at": "2026-09-08T00:00:00Z",
                            "url": "/t/recent/400",
                        }],
                        "more_topics_url": "/c/risk/7?page=1",
                    }
                }

        client = Window()
        request = self.request("AAVE", "security", "aave-security")
        result = StructuredEventTransport(client=client).fetch_result(request)
        urls = [call[1] for call in client.calls]
        self.assertTrue(result.complete_for_source)
        self.assertEqual(len(urls), 4)
        self.assertFalse(any("page=2" in url for url in urls))

    def test_cross_origin_discourse_pagination_is_rejected(self):
        class Evil(Client):
            def get_json(self, url, *, params=None, headers=None):
                if "governance.aave.com/c/risk/" in url:
                    self.calls.append(("GET", url, params, headers))
                    return {
                        "topic_list": {
                            "topics": [{"id": 301, "title": "Untrusted next page", "created_at": AS_OF, "url": "/t/untrusted/301"}],
                            "more_topics_url": "https://evil.example/risk?page=2",
                        }
                    }
                return super().get_json(url, params=params, headers=headers)

        client = Evil()
        result = StructuredEventTransport(client=client).fetch_result(
            self.request("AAVE", "security", "aave-security")
        )
        urls = [call[1] for call in client.calls if call[0] == "GET"]
        self.assertTrue(result.reachable)
        self.assertFalse(result.complete_for_source)
        self.assertIn("DISCOURSE_PAGINATION_ORIGIN_REJECTED", result.error or "")
        self.assertFalse(any("evil.example" in url for url in urls))

    def test_github_releases_filter_prerelease_and_lookback(self):
        response = self.transport.fetch(self.request("BTC", "security", "bitcoin-core-security-advisories"))
        self.assertTrue(response.reachable)
        self.assertTrue(response.complete_for_source)
        self.assertEqual([item["title"] for item in response.items], ["stable"])
        self.assertEqual(response.items[0]["materiality"], "CANDIDATE")
        self.assertEqual(response.items[0]["relevance"], "UNKNOWN")
        releases_call = next(call for call in self.client.calls if "api.github.com/repos/bitcoin/bitcoin/releases" in call[1])
        self.assertNotIn("since", releases_call[2])
        self.assertNotIn("until", releases_call[2])

    def test_rss_and_discourse_are_normalized(self):
        rss = self.transport.fetch(self.request("BTC", "security", "bitcoin-core-releases"))
        self.assertEqual(rss.items[0]["title"], "Notice")
        self.assertIn(("TEXT", "https://bitcoincore.org/en/feed.xml"), self.client.calls)
        self.assertNotIn(("TEXT", "https://bitcoincore.org/en/releases/"), self.client.calls)
    def test_esma_rss_succeeds(self):
        result = self.transport.fetch_result(self.request("MARKET", "regulatory", "esma-mica"))
        self.assertTrue(result.reachable)
        self.assertTrue(result.complete_for_source)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.candidates), 2)

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

    def test_web_only_source_keeps_no_structured_transport_diagnostic(self):
        result = self.transport.fetch_result(self.request("BNB", "security", "bnb-bsc-releases"))
        self.assertFalse(result.reachable)
        self.assertFalse(result.complete_for_source)
        self.assertIn("NO_STRUCTURED_TRANSPORT", result.error or "")

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
        with self.assertRaisesRegex(ValueError, "host-agent"):
            self.scanner.build_result("BTC", "security", AS_OF, (response,))

    def test_dedup_keeps_the_newest_candidates_when_over_the_cap(self):
        spec = EventTransportSpec(
            "overflow-source", "RSS_ATOM", "https://example.test/feed.xml",
            "BTC", "security", "overflow-group", "2026-08-10T00:00:00Z", AS_OF,
        )
        base = datetime(2026, 8, 11, tzinfo=timezone.utc)
        candidates = [
            EventCandidate(
                f"item-{index}",
                f"Item {index}",
                (base + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
                f"https://example.test/item-{index}",
            )
            for index in range(MAX_CANDIDATES + 50)
        ]
        kept = _dedup(candidates, spec)
        self.assertEqual(len(kept), MAX_CANDIDATES)
        # Overflow must drop the oldest items; dropping the newest would hide
        # exactly the events the current scan is responsible for.
        self.assertEqual(kept[0].external_id, f"item-{50}")
        self.assertEqual(kept[-1].external_id, f"item-{MAX_CANDIDATES + 49}")

    def test_shallow_rss_feed_window_is_reported_incomplete(self):
        # A feed whose oldest retained item is newer than the lookback start
        # cannot prove window coverage; it must not count as complete.
        result = self.transport.fetch_result(self.request("BTC", "security", "bitcoin-core-releases"))
        self.assertTrue(result.reachable)
        self.assertFalse(result.complete_for_source)
        self.assertEqual(len(result.candidates), 1)

    def test_empty_rss_feed_is_complete(self):
        class EmptyFeed(Client):
            def get_text(self, url, **kwargs):
                self.calls.append(("TEXT", url))
                return "<rss><channel></channel></rss>"

        result = StructuredEventTransport(client=EmptyFeed()).fetch_result(
            self.request("BTC", "security", "bitcoin-core-releases")
        )
        self.assertTrue(result.reachable)
        self.assertTrue(result.complete_for_source)
        self.assertEqual(result.candidates, ())

    def test_incomplete_but_reachable_source_still_contributes_candidates(self):
        class Endless(Client):
            def get_json(self, url, *, params=None, headers=None):
                if "governance.aave.com/c/risk/" in url:
                    self.calls.append(("GET", url, params, headers))
                    return {
                        "topic_list": {
                            "topics": [{"id": 201, "title": "Still paging", "created_at": AS_OF, "url": "/t/still-paging/201"}],
                            "more_topics_url": "/c/risk/7?page=next",
                        }
                    }
                return super().get_json(url, params=params, headers=headers)

        result = StructuredEventTransport(client=Endless(), max_discourse_pages=2).fetch_result(
            self.request("AAVE", "security", "aave-security")
        )
        self.assertTrue(result.reachable)
        self.assertFalse(result.complete_for_source)
        self.assertIn("discourse:201", [candidate.external_id for candidate in result.candidates])


if __name__ == "__main__":
    unittest.main()
