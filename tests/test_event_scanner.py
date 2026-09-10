import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.scoring import score_factors
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.events import EventScanner, EventSourceScanResponse, source_catalog
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.report_packet import build_final_review_output, build_report_packet


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
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {"CRYPTO_PORTFOLIO_DATA_DIR": directory}))

    def test_catalog_has_fixed_btc_eth_and_shared_regulatory_sources(self):
        btc = source_catalog("security", "BTC")
        eth = source_catalog("security", "ETH")
        aave_security = source_catalog("security", "AAVE")
        aave_governance = source_catalog("governance", "AAVE")
        bnb_security = source_catalog("security", "BNB")
        bnb_governance = source_catalog("governance", "BNB")
        regulatory = source_catalog("regulatory", "AAVE")
        self.assertEqual(len(btc), 3)
        self.assertEqual(len(eth), 3)
        self.assertGreaterEqual(len(aave_security), 1)
        self.assertGreaterEqual(len(aave_governance), 1)
        self.assertGreaterEqual(len(bnb_security), 2)
        self.assertGreaterEqual(len(bnb_governance), 2)
        self.assertEqual(len(regulatory), 3)
        self.assertTrue(all(source.required_for_full_coverage for source in btc + eth + regulatory))
        self.assertTrue(all(source.tier == 1 for source in regulatory))

    def test_aave_governance_requires_onchain_and_one_offchain_group(self):
        scanner = EventScanner()
        requests = scanner.build_requests("AAVE", "governance", AS_OF)
        offchain_only = tuple(
            EventSourceScanResponse(request.source_id, request.source_group == "aave-governance-offchain", AS_OF, (), None if request.source_group == "aave-governance-offchain" else "unavailable")
            for request in requests
        )
        incomplete = scanner.scan("AAVE", "governance", AS_OF, responses=offchain_only)
        self.assertEqual(incomplete.source_coverage["coverage_state"], "INSUFFICIENT_SOURCE_COVERAGE")
        complete = scanner.scan(
            "AAVE", "governance", AS_OF,
            responses=tuple(EventSourceScanResponse(request.source_id, True, AS_OF, (), None) for request in requests),
        )
        self.assertEqual(complete.source_coverage["coverage_rule"], "ONCHAIN_AND_ONE_OFFCHAIN")
        self.assertEqual(complete.source_coverage["coverage_state"], "SUFFICIENT")

    def test_aave_governance_accepts_one_complete_offchain_url_per_group(self):
        scanner = EventScanner()
        requests = scanner.build_requests("AAVE", "governance", AS_OF)
        offchain = [request for request in requests if request.source_group == "aave-governance-offchain"]
        onchain = next(request for request in requests if request.source_group == "aave-governance-onchain")
        responses = tuple(
            EventSourceScanResponse(
                request.source_id,
                request is onchain or request is offchain[0],
                AS_OF,
                (),
                None if request is onchain or request is offchain[0] else "source unavailable",
            )
            for request in requests
        )
        result = scanner.scan("AAVE", "governance", AS_OF, responses=responses)
        self.assertEqual(result.source_coverage["coverage_state"], "SUFFICIENT")

    def test_excluded_asset_has_no_event_source_requests(self):
        scanner = EventScanner()
        self.assertEqual(scanner.build_requests("LUNC", "security", AS_OF), ())
        self.assertEqual(scanner.build_requests("LUNC", "regulatory", AS_OF), ())
        calls = []

        def fetch(request):
            calls.append(request)
            raise AssertionError("excluded asset must not reach EventScanner fetch")

        self.assertEqual(scanner.scan_shared_regulatory(("LUNC",), AS_OF, source_fetcher=fetch), {})
        self.assertEqual(calls, [])

    def test_cftc_keeps_one_authority_with_official_transport_candidates(self):
        scanner = EventScanner()
        request = next(
            item for item in scanner.build_requests("MARKET", "regulatory", AS_OF)
            if item.source_id == "cftc-digital-assets"
        )
        self.assertEqual(request.authority, "U.S. CFTC")
        self.assertEqual(request.source_url, "https://www.cftc.gov/PressRoom/PressReleases")
        self.assertEqual(request.source_urls, (
            "https://www.cftc.gov/PressRoom/PressReleases",
            "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
            "https://www.cftc.gov/RSS/RSSENF/rssenf.xml",
        ))

    def test_required_event_sources_expose_structured_transport_metadata(self):
        scanner = EventScanner()
        expected = (
            ("ETH", "security", "ethereum-foundation-security", "https://blog.ethereum.org/feed.xml", "RSS_ATOM", "ethereum-foundation-security"),
            ("ETH", "governance", "ethereum-eips", "https://github.com/ethereum/EIPs", "GITHUB_COMMITS", "ethereum-eips"),
            ("AAVE", "security", "aave-security", "https://governance.aave.com/c/risk/7.json", "DISCOURSE_JSON", "aave-security"),
            ("MARKET", "regulatory", "esma-mica", "https://www.esma.europa.eu/rss.xml", "RSS_ATOM", "esma-regulatory"),
        )
        for asset, category, source_id, endpoint, kind, group in expected:
            with self.subTest(source_id=source_id):
                request = next(
                    item for item in scanner.build_requests(asset, category, AS_OF)
                    if item.source_id == source_id
                )
                self.assertEqual(request.source_url, {
                    "ethereum-foundation-security": "https://ethereum.org/en/security/",
                    "ethereum-eips": "https://eips.ethereum.org/",
                    "aave-security": "https://aave.com/security",
                    "esma-mica": "https://www.esma.europa.eu/press-news/esma-news",
                }[source_id])
                self.assertIn(endpoint, request.source_urls)
                self.assertEqual(request.transport_kind, kind)
                self.assertEqual(request.source_group, group)
                serialized = request.as_dict()
                self.assertEqual(serialized["source_url"], request.source_url)
                self.assertEqual(serialized["source_urls"], list(request.source_urls))
                self.assertEqual(serialized["transport_kind"], kind)
                self.assertEqual(serialized["source_group"], group)

    def test_cftc_local_failure_remains_external_resolution_until_authoritative_response(self):
        plan = MetricCollectionPlan("EVENT_REVIEW", (
            MetricRequest("BTC", "risk.regulatory_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, mode="AUTO", as_of=AS_OF, now=AS_OF)
        cftc = next(item for item in first.pending_event_scans if item.source_id == "cftc-digital-assets")
        self.assertIn("https://www.cftc.gov/RSS/RSSGP/rssgp.xml", cftc.source_urls)
        responses = tuple(
            EventSourceScanResponse(item.source_id, True, AS_OF, (), None)
            for item in first.pending_event_scans
        )
        second = manager.run(
            plan,
            mode="AUTO",
            as_of=AS_OF,
            now=AS_OF,
            event_source_scan_responses=responses,
        )
        second.require_scoring_ready()
        self.assertEqual(second.results[0].status, "SUCCESS")

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
        self.assertEqual(partial.status, "NO_KNOWN_MATERIAL_EVENT_IN_SCANNED_SOURCES")
        self.assertEqual(partial.coverage, 1.0)
        self.assertEqual(partial.confidence, "HIGH")
        self.assertEqual(partial.source_redundancy["independent_groups"], 1)

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
            self.assertFalse(result.finalized)
            self.assertEqual(result.pending_external_resolution, 9)
            self.assertEqual(result.summary["pending_external_resolution"], 9)
            self.assertEqual(result.summary["counts"]["FAILED"], 0)
            self.assertEqual(result.summary["counts"]["PENDING_EXTERNAL_RESOLUTION"], 3)

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

    def test_acquisition_exposes_hard_critical_two_pass_gate(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, mode="AUTO", as_of=AS_OF, now=AS_OF)
        self.assertTrue(first.requires_external_resolution)
        self.assertFalse(first.finalized)
        self.assertEqual(first.pending_external_resolution, 3)
        self.assertEqual(first.pending_event_scans, first.event_scan_requests)
        self.assertEqual(first.hard_critical_unresolved, (("ETH", "risk.security_event_status"),))
        self.assertFalse(first.ready_for_scoring)
        with self.assertRaisesRegex(RuntimeError, "hard-critical event scan"):
            first.require_scoring_ready()
        with self.assertRaisesRegex(RuntimeError, "hard-critical event scan"):
            score_factors({"trend": 50}, acquisition=first)

        responses = tuple(
            EventSourceScanResponse(request.source_id, True, AS_OF, (), None)
            for request in first.pending_event_scans
        )
        second = manager.run(
            plan,
            mode="AUTO",
            as_of=AS_OF,
            now=AS_OF,
            event_source_scan_responses=responses,
        )
        self.assertEqual(second.event_scan_requests, ())
        self.assertEqual(len(second.event_scans), 1)
        self.assertTrue(second.finalized)
        self.assertEqual(second.pending_external_resolution, 0)
        self.assertTrue(second.ready_for_scoring)
        self.assertTrue(second.results[0].status == "SUCCESS")

    def test_final_report_requires_second_pass(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, mode="AUTO", as_of=AS_OF, now=AS_OF)
        packet = build_decision_review_packet(
            review_type="SNAPSHOT_REVIEW",
            market_regime="NORMAL",
            current_weights={"ETH": 1.0},
            target_weights={"ETH": 1.0},
            assessments={"ETH": {"weighted_score": 70, "confidence": "HIGH"}},
        )
        with self.assertRaisesRegex(RuntimeError, "external resolution"):
            build_report_packet(packet, acquisition=first)
        responses = tuple(
            EventSourceScanResponse(request.source_id, True, AS_OF, ())
            for request in first.pending_event_scans
        )
        second = manager.run(
            plan,
            mode="AUTO",
            as_of=AS_OF,
            now=AS_OF,
            event_source_scan_responses=responses,
        )
        report = build_report_packet(packet, acquisition=second)
        output = build_final_review_output(report, acquisition=second)
        self.assertEqual(output["collection"]["pending_external_resolution"], 0)

    def test_two_pass_contract_resolves_every_pending_source_request(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
            MetricRequest("AAVE", "risk.security_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, mode="AUTO", as_of=AS_OF, now=AS_OF)
        requests = first.pending_event_scans
        self.assertTrue(requests)
        self.assertEqual(requests, first.event_scan_requests)
        self.assertFalse(first.ready_for_scoring)

        responses = tuple(
            EventSourceScanResponse(request.source_id, True, AS_OF, (), None)
            for request in requests
        )
        second = manager.run(
            plan,
            mode="AUTO",
            as_of=AS_OF,
            now=AS_OF,
            event_source_scan_responses=responses,
        )
        self.assertEqual(second.pending_event_scans, ())
        self.assertEqual(len(second.event_scans), 2)
        second.require_scoring_ready()
        self.assertTrue(all(item.status == "SUCCESS" for item in second.results))

    def test_incomplete_event_scan_remains_a_critical_failure(self):
        plan = MetricCollectionPlan("EVENT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, as_of=AS_OF, now=AS_OF)
        responses = tuple(
            EventSourceScanResponse(
                request.source_id,
                request.source_id == first.pending_event_scans[0].source_id,
                AS_OF,
                (),
                None if request.source_id == first.pending_event_scans[0].source_id else "unreachable",
            )
            for request in first.pending_event_scans
        )
        result = manager.run(plan, as_of=AS_OF, now=AS_OF, event_source_scan_responses=responses)
        self.assertEqual(result.event_scan_requests, ())
        self.assertEqual(result.results[0].status, "FAILED")
        self.assertEqual(result.summary["critical_failures"], 1)
        self.assertIn("INSUFFICIENT_SOURCE_COVERAGE", result.results[0].event.reason)
        self.assertFalse(result.ready_for_scoring)
        with self.assertRaisesRegex(RuntimeError, "hard-critical event scan"):
            result.require_scoring_ready()

    def test_missing_external_response_is_materialized_as_unreachable(self):
        plan = MetricCollectionPlan("EVENT_REVIEW", (
            MetricRequest("BNB", "risk.security_event_status"),
        ))
        manager = AcquisitionManager(persist=False)
        first = manager.run(plan, as_of=AS_OF, now=AS_OF)
        response = EventSourceScanResponse(
            first.pending_event_scans[0].source_id,
            True,
            AS_OF,
            (),
        )
        second = manager.run(
            plan,
            as_of=AS_OF,
            now=AS_OF,
            event_source_scan_responses=(response,),
        )
        self.assertEqual(second.event_scan_requests, ())
        self.assertTrue(second.ready_for_scoring)
        self.assertEqual(second.results[0].status, "SUCCESS")

    def test_cache_only_missing_hard_critical_scan_is_not_scoring_ready(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.security_event_status"),
        ))
        result = AcquisitionManager(persist=False).run(plan, mode="CACHE_ONLY", as_of=AS_OF, now=AS_OF)
        self.assertFalse(result.ready_for_scoring)
        self.assertEqual(result.hard_critical_unresolved, (("ETH", "risk.security_event_status"),))

    def test_acquisition_maps_shared_market_regulatory_result(self):
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("BTC", "risk.regulatory_event_status"),
            MetricRequest("ETH", "risk.regulatory_event_status"),
            MetricRequest("BNB", "risk.regulatory_event_status"),
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
