import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.events import (
    ApiEventMaterialityClassifier,
    EventResolver,
    EventScanner,
    EventSourceScanResponse,
    build_exchange_document,
    classifier_from_environment,
    parse_exchange_document,
    validate_exchange_responses,
)


AS_OF = "2026-09-09T00:00:00Z"


class CandidateTransport:
    def __init__(self, *, empty=False, reachable=True):
        self.calls = []
        self.empty = empty
        self.reachable = reachable

    def fetch(self, request):
        self.calls.append(request)
        if not self.reachable:
            return EventSourceScanResponse(request.source_id, False, AS_OF, (), "source unavailable")
        if self.empty:
            return EventSourceScanResponse(request.source_id, True, AS_OF, (), None)
        item = {
            "external_id": f"{request.source_id}:1",
            "title": "bounded candidate",
            "published_at": AS_OF,
            "canonical_url": f"https://example.test/{request.source_id}/1",
            "summary": "untrusted source excerpt",
            "materiality": "CANDIDATE",
            "relevance": "UNKNOWN",
        }
        return EventSourceScanResponse(request.source_id, True, AS_OF, (item,), None)


class ClearClassifier:
    mode = "host"
    backend = "test"
    model = None

    def classify(self, *, request, response):
        return EventSourceScanResponse(
            response.source_id,
            response.reachable,
            response.checked_at,
            tuple({**item, "materiality": False, "relevance": "IRRELEVANT"} for item in response.items),
            None,
            response.conflict,
            response.complete_for_source,
        )


class MutatingClassifier(ClearClassifier):
    def classify(self, *, request, response):
        item = {**response.items[0], "canonical_url": "https://example.test/mutated"}
        return EventSourceScanResponse(response.source_id, True, response.checked_at, (item,), None)


class ApiClient:
    def __init__(self):
        self.calls = []

    def post_json(self, url, *, json_body, headers, idempotent):
        self.calls.append((url, json_body, headers, idempotent))
        item = json.loads(json_body["messages"][1]["content"])["items"][0]
        item["materiality"] = False
        item["relevance"] = "IRRELEVANT"
        return {"choices": [{"message": {"content": json.dumps({"items": [item]})}}]}


class EventResolutionTests(unittest.TestCase):
    def setUp(self):
        self.scanner = EventScanner()
        self.request = next(
            item for item in self.scanner.build_requests("BTC", "security", AS_OF)
            if item.source_id == "bitcoin-core-security-advisories"
        )

    def test_candidate_without_classifier_stays_pending(self):
        resolver = EventResolver(transport=CandidateTransport())
        response = resolver.resolve(self.request)
        self.assertEqual(response.items[0]["materiality"], "CANDIDATE")
        self.assertEqual(response.error, "EVENT_CLASSIFICATION_REQUIRED")
        self.assertEqual(resolver.diagnostics[0].status, "CLASSIFICATION_PENDING")

    def test_classifier_resolves_candidates_and_preserves_identity(self):
        resolver = EventResolver(transport=CandidateTransport(), classifier=ClearClassifier())
        response = resolver.resolve(self.request)
        self.assertFalse(response.items[0]["materiality"])
        self.assertEqual(response.items[0]["canonical_url"], "https://example.test/bitcoin-core-security-advisories/1")
        self.assertEqual(resolver.diagnostics[0].status, "CLASSIFIED")

    def test_classifier_identity_mutation_is_fail_closed(self):
        resolver = EventResolver(transport=CandidateTransport(), classifier=MutatingClassifier())
        response = resolver.resolve(self.request)
        self.assertEqual(response.items[0]["materiality"], "CANDIDATE")
        self.assertIn("EVENT_CLASSIFICATION_SCHEMA_ERROR", response.error)
        self.assertEqual(resolver.diagnostics[0].status, "CLASSIFICATION_FAILED")

    def test_unreachable_source_does_not_invoke_classifier(self):
        classifier = ClearClassifier()
        resolver = EventResolver(transport=CandidateTransport(reachable=False), classifier=classifier)
        response = resolver.resolve(self.request)
        self.assertFalse(response.reachable)
        self.assertEqual(resolver.diagnostics[0].status, "FETCH_FAILED")

    def test_transport_conflict_is_not_clean_evidence(self):
        class ConflictTransport(CandidateTransport):
            def fetch(self, request):
                response = super().fetch(request)
                return EventSourceScanResponse(
                    response.source_id,
                    response.reachable,
                    response.checked_at,
                    response.items,
                    response.error,
                    True,
                    response.complete_for_source,
                )

        resolver = EventResolver(transport=ConflictTransport(), classifier=ClearClassifier())
        response = resolver.resolve(self.request)
        self.assertEqual(response.items[0]["materiality"], "CANDIDATE")
        self.assertEqual(resolver.diagnostics[0].status, "CONFLICT")

    def test_openai_compatible_classifier_is_bounded_and_validated(self):
        client = ApiClient()
        response = ApiEventMaterialityClassifier(
            endpoint="https://classifier.example/v1/chat/completions",
            api_key="secret",
            client=client,
        ).classify(
            request=self.request,
            response=CandidateTransport().fetch(self.request),
        )
        self.assertFalse(response.items[0]["materiality"])
        self.assertEqual(client.calls[0][2]["Authorization"], "Bearer secret")
        self.assertTrue(client.calls[0][3])

    def test_api_mode_without_key_stays_unavailable(self):
        classifier = classifier_from_environment({"EVENT_CLASSIFIER_MODE": "api"})
        self.assertEqual(classifier.backend, "openai")
        response = EventResolver(transport=CandidateTransport(), classifier=classifier).resolve(self.request)
        self.assertIn("EVENT_CLASSIFIER_UNAVAILABLE", response.error)

    def test_transport_wiring_and_shared_regulatory_scan(self):
        transport = CandidateTransport(empty=True)
        scanner = EventScanner(transport=transport)
        plan = MetricCollectionPlan("EVENT_REVIEW", (
            MetricRequest("BTC", "risk.security_event_status"),
            MetricRequest("BTC", "risk.regulatory_event_status"),
            MetricRequest("ETH", "risk.regulatory_event_status"),
        ))
        result = AcquisitionManager(event_scanner=scanner, persist=False).run(plan, as_of=AS_OF, now=AS_OF)
        self.assertEqual({request.asset for request in transport.calls}, {"BTC", "MARKET"})
        self.assertEqual(sum(request.asset == "MARKET" for request in transport.calls), 3)
        self.assertEqual(result.pending_event_scans, ())
        self.assertTrue(result.ready_for_scoring)

    def test_configured_classifier_resolves_all_btc_eth_event_metrics(self):
        scanner = EventScanner(transport=CandidateTransport(), classifier=ClearClassifier())
        plan = MetricCollectionPlan("EVENT_REVIEW", tuple(
            MetricRequest(asset, f"risk.{category}_event_status")
            for asset in ("BTC", "ETH")
            for category in ("security", "governance", "regulatory")
        ))
        result = AcquisitionManager(event_scanner=scanner, persist=False).run(plan, as_of=AS_OF, now=AS_OF)
        self.assertEqual(result.pending_event_scans, ())
        self.assertEqual(len(result.event_scans), 6)
        self.assertTrue(result.ready_for_scoring)
        self.assertTrue(all(item.status == "SUCCESS" for item in result.results))

    def test_explicit_fetcher_overrides_scanner_transport(self):
        transport = CandidateTransport(empty=True)
        scanner = EventScanner(transport=transport)
        calls = []

        def fetch(request):
            calls.append(request)
            return EventSourceScanResponse(request.source_id, True, AS_OF, (), None)

        result = AcquisitionManager(event_scanner=scanner, persist=False).run(
            MetricCollectionPlan("EVENT_REVIEW", (MetricRequest("BTC", "risk.security_event_status"),)),
            as_of=AS_OF,
            now=AS_OF,
            event_source_fetcher=fetch,
        )
        self.assertEqual(len(transport.calls), 0)
        self.assertEqual(len(calls), 3)
        self.assertTrue(result.ready_for_scoring)

    def test_cache_only_never_calls_scanner_transport(self):
        transport = CandidateTransport(empty=True)
        result = AcquisitionManager(event_scanner=EventScanner(transport=transport), persist=False).run(
            MetricCollectionPlan("EVENT_REVIEW", (MetricRequest("BTC", "risk.security_event_status"),)),
            mode="CACHE_ONLY",
            as_of=AS_OF,
            now=AS_OF,
        )
        self.assertEqual(transport.calls, [])
        self.assertFalse(result.ready_for_scoring)

    def test_host_exchange_round_trip_and_schema(self):
        original = CandidateTransport().fetch(self.request)
        document = build_exchange_document((self.request,), (original,))
        parsed_requests, parsed_responses, pending = parse_exchange_document(document)
        classified = ClearClassifier().classify(request=self.request, response=original)
        final = validate_exchange_responses(parsed_requests, (classified,), pending)
        self.assertEqual(final[0].items[0]["materiality"], False)
        schema = json.loads((Path(__file__).parents[1] / "schemas" / "event-classification-exchange.schema.json").read_text())
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_exchange_cannot_expand_allowlisted_transport_urls(self):
        response = CandidateTransport().fetch(self.request)
        document = build_exchange_document((self.request,), (response,))
        document["requests"][0]["source_urls"] = ["https://attacker.example/feed.xml"]
        document["pending_requests"][0]["source_urls"] = ["https://attacker.example/feed.xml"]
        with self.assertRaisesRegex(ValueError, "fixed source catalog"):
            parse_exchange_document(document)


if __name__ == "__main__":
    unittest.main()
