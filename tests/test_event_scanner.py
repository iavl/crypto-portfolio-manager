import unittest
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.events import EventScanner, EventSourceScanResponse, source_catalog


AS_OF = "2026-09-04T00:00:00Z"


def responses(scanner, asset, category, *, reachable_ids=None, items_by_id=None):
    requests = scanner.build_requests(asset, category, AS_OF)
    reachable_ids = set(reachable_ids or (request.source_id for request in requests))
    items_by_id = items_by_id or {}
    return tuple(
        EventSourceScanResponse(
            request.source_id,
            request.source_id in reachable_ids,
            AS_OF,
            tuple(items_by_id.get(request.source_id, ())),
            None if request.source_id in reachable_ids else "source unavailable",
        )
        for request in requests
    )


class EventScannerTests(unittest.TestCase):
    def test_catalog_has_fixed_btc_eth_and_shared_regulatory_sources(self):
        btc = source_catalog("security", "BTC")
        eth = source_catalog("security", "ETH")
        regulatory = source_catalog("regulatory", "AAVE")
        self.assertEqual(len(btc), 3)
        self.assertEqual(len(eth), 3)
        self.assertEqual(len(regulatory), 3)
        self.assertTrue(all(source.required_for_full_coverage for source in btc + eth + regulatory))
        self.assertTrue(all(source.tier == 1 for source in regulatory))

    def test_scan_request_and_response_schemas_match_models(self):
        scanner = EventScanner()
        request = scanner.build_requests("BTC", "security", AS_OF)[0]
        response = EventSourceScanResponse(request.source_id, True, AS_OF, ())
        root = Path(__file__).parents[1] / "schemas"
        for filename, value in (
            ("event-source-scan-request.schema.json", request.as_dict()),
            ("event-source-scan-response.schema.json", response.as_dict()),
        ):
            schema = json.loads((root / filename).read_text(encoding="utf-8"))
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
            self.assertEqual(errors, [], "\n".join(error.message for error in errors))
        with self.assertRaises(ValueError):
            EventSourceScanResponse.from_mapping({
                "source_id": request.source_id, "reachable": True, "checked_at": AS_OF, "items": [],
            })

    def test_current_scan_is_not_stale_when_last_incident_is_old(self):
        scanner = EventScanner()
        old_item = {"title": "old advisory", "published_at": "2020-01-01T00:00:00Z", "materiality": "MATERIAL"}
        scan = scanner.build_result(
            "ETH", "security", AS_OF,
            responses(scanner, "ETH", "security", items_by_id={"ethereum-foundation-security": (old_item,)}),
        )
        self.assertEqual(scan.status, "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES")
        observation = normalize_metric_result(
            scanner.observation(scan, "risk.security_event_status", fetched_at=AS_OF),
            as_of=AS_OF,
        ).observation
        self.assertEqual(observation.observed_at, AS_OF)
        self.assertEqual(observation.freshness, "CURRENT")

    def test_full_coverage_no_event_and_insufficient_coverage(self):
        scanner = EventScanner()
        full = scanner.scan("BTC", "security", AS_OF, responses=responses(scanner, "BTC", "security"))
        self.assertEqual(full.status, "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES")
        self.assertEqual(full.coverage, 1.0)
        self.assertEqual(full.confidence, "HIGH")
        partial = scanner.scan(
            "BTC", "security", AS_OF,
            responses=responses(scanner, "BTC", "security", reachable_ids={"bitcoin-core-security"}),
        )
        self.assertEqual(partial.status, "INSUFFICIENT_SOURCE_COVERAGE")
        self.assertAlmostEqual(partial.coverage, 1 / 3)
        self.assertEqual(partial.confidence, "LOW")

    def test_material_event_is_retained(self):
        scanner = EventScanner()
        item = {
            "title": "emergency patch",
            "published_at": AS_OF,
            "canonical_url": "https://bitcoincore.org/en/announcement/",
            "summary": "A material protocol security notice.",
            "materiality": "CRITICAL",
        }
        scan = scanner.scan(
            "BTC", "security", AS_OF,
            responses=responses(scanner, "BTC", "security", items_by_id={"bitcoin-core-security": (item,)}),
        )
        self.assertEqual(scan.status, "MATERIAL_EVENT_FOUND")
        self.assertEqual(scan.material_events[0]["title"], "emergency patch")

    def test_shared_regulatory_scan_does_not_repeat_market_sources(self):
        scanner = EventScanner()
        calls = []

        def fetch(request):
            calls.append(request)
            return EventSourceScanResponse(request.source_id, True, AS_OF, ())

        results = scanner.scan_shared_regulatory(("BTC", "ETH", "AAVE"), AS_OF, source_fetcher=fetch)
        self.assertEqual(set(results), {"BTC", "ETH", "AAVE"})
        self.assertEqual(len(calls), len(source_catalog("regulatory", "MARKET")))
        self.assertEqual({request.asset for request in calls}, {"MARKET"})
        self.assertTrue(all(result.status == "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES" for result in results.values()))

    def test_shared_regulatory_event_is_mapped_only_to_affected_asset(self):
        scanner = EventScanner()
        event = {
            "title": "asset-specific notice",
            "published_at": AS_OF,
            "materiality": "MATERIAL",
            "affected_assets": ["AAVE"],
        }
        source_id = source_catalog("regulatory", "MARKET")[0].id
        results = scanner.scan_shared_regulatory(
            ("BTC", "AAVE"), AS_OF,
            responses=responses(scanner, "MARKET", "regulatory", items_by_id={source_id: (event,)}),
        )
        self.assertEqual(results["BTC"].material_events, ())
        self.assertEqual(len(results["AAVE"].material_events), 1)

    def test_cache_only_does_not_call_source_fetcher(self):
        scanner = EventScanner()
        calls = []

        def fetch(request):
            calls.append(request)
            raise AssertionError("CACHE_ONLY must not fetch event sources")

        with self.assertRaises(ValueError):
            scanner.scan("ETH", "security", AS_OF, source_fetcher=fetch, fetch_mode="CACHE_ONLY")
        self.assertEqual(calls, [])

    def test_acquisition_returns_event_source_plan_instead_of_web_fallback(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
            MetricRequest("ETH", "risk.governance_event_status"),
            MetricRequest("ETH", "risk.regulatory_event_status"),
        ))
        with self.subTest(mode="AUTO"):
            result = AcquisitionManager(persist=False).run(
                plan, mode="AUTO", as_of=AS_OF, now=AS_OF,
            )
            self.assertEqual(result.web_fallbacks, ())
            self.assertEqual(len(result.event_scan_requests), 9)
            self.assertEqual({item.category for item in result.event_scan_requests}, {"security", "governance", "regulatory"})
            self.assertTrue(all(item.status == "FAILED" for item in result.results))

        result = AcquisitionManager(persist=False).run(
            plan, mode="CACHE_ONLY", as_of=AS_OF, now=AS_OF,
        )
        self.assertEqual(result.event_scan_requests, ())
        self.assertEqual(result.web_fallbacks, ())

    def test_acquisition_consumes_scans_and_bundles_regulatory_fetches(self):
        plan = MetricCollectionPlan("EVENT_REVIEW", (
            MetricRequest("BTC", "risk.security_event_status"),
            MetricRequest("BTC", "risk.regulatory_event_status"),
            MetricRequest("ETH", "risk.regulatory_event_status"),
            MetricRequest("AAVE", "risk.regulatory_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        calls = []

        def fetch(request):
            calls.append(request)
            return EventSourceScanResponse(request.source_id, True, AS_OF, ())

        result = manager.run(
            plan, mode="REFRESH", as_of=AS_OF, now=AS_OF,
            event_source_fetcher=fetch,
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual({item.asset for item in calls if item.category == "regulatory"}, {"MARKET"})
        self.assertEqual(result.event_scan_requests, ())
        self.assertEqual(len(result.event_scans), 4)
        self.assertTrue(all(item.status == "SUCCESS" for item in result.results))

    def test_acquisition_maps_shared_market_regulatory_result(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("BTC", "risk.regulatory_event_status"),
            MetricRequest("ETH", "risk.regulatory_event_status"),
        ))
        scanner = EventScanner()
        shared = scanner.scan("MARKET", "regulatory", AS_OF, responses=responses(scanner, "MARKET", "regulatory"))
        result = AcquisitionManager(persist=False).run(
            plan, as_of=AS_OF, now=AS_OF, event_scan_results={("MARKET", "regulatory"): shared},
        )
        self.assertEqual(result.event_scan_requests, ())
        self.assertTrue(all(item.status == "SUCCESS" for item in result.results))

    def test_fresh_event_observation_is_reused_and_refresh_reopens_scan(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
        ))
        scanner = EventScanner()
        scan = scanner.scan("ETH", "security", AS_OF, responses=responses(scanner, "ETH", "security"))
        cached = normalize_metric_result(
            scanner.observation(scan, "risk.security_event_status", fetched_at=AS_OF),
            as_of=AS_OF,
        ).observation
        manager = AcquisitionManager(persist=False)
        reused = manager.run(
            plan, mode="AUTO", as_of=AS_OF, now=AS_OF, cached_observations=(cached,),
        )
        self.assertEqual(reused.summary["fresh_observation_hits"], 1)
        self.assertEqual(reused.event_scan_requests, ())
        refreshed = manager.run(
            plan, mode="REFRESH", as_of=AS_OF, now=AS_OF, cached_observations=(cached,),
        )
        self.assertEqual(len(refreshed.event_scan_requests), 3)


if __name__ == "__main__":
    unittest.main()
