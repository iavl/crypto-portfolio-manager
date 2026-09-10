import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from crypto_portfolio.acquisition import AcquisitionResult
from crypto_portfolio.data_collection import build_failed_data_fetches
from crypto_portfolio.engine.decision_packet import build_decision_review_packet
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.report_packet import build_final_review_output, build_report_packet
from crypto_portfolio.models.report_packet import ReportPacket


NOW = "2026-09-07T00:00:00Z"
OLD = "2026-08-01T00:00:00Z"


def result(asset, metric_key, status="FAILED", *, reason="unavailable", **fields):
    payload = {
        "asset": asset,
        "metric_key": metric_key,
        "status": status,
        "reason": reason,
        "timestamp": NOW,
        "source": "provider-router",
    }
    payload.update(fields)
    if status == "SUCCESS":
        payload.pop("reason", None)
        payload.setdefault("value", 1)
        payload.setdefault("unit", "count" if metric_key.endswith("developer_activity") else "USD")
        payload.setdefault("observed_at", NOW)
        payload.setdefault("fetched_at", NOW)
        payload.setdefault("confidence", "HIGH")
    return normalize_metric_result(payload)


def acquisition(results, *, attempts=(), event_scans=(), web_fallbacks=(), review_type="SNAPSHOT_REVIEW"):
    plan = MetricCollectionPlan(
        review_type,
        tuple(MetricRequest(item.event.asset, item.event.metric_key) for item in results),
    )
    return AcquisitionResult(plan, tuple(results), tuple(web_fallbacks), attempts=tuple(attempts), event_scans=tuple(event_scans))


def report_decision(asset="ETH", review_type="SNAPSHOT_REVIEW"):
    return build_decision_review_packet(
        review_type=review_type,
        market_regime="NORMAL",
        current_weights={asset: 1},
        target_weights={asset: 1},
        assessments={asset: {"weighted_score": 70, "confidence": "HIGH", "factor_scores": {"trend": 70}}},
    )


class ReportFailureTests(unittest.TestCase):
    def test_single_provider_failure_keeps_final_metric_and_safe_attempt(self):
        failed = result(
            "ETH",
            "fundamentals.tvl",
            reason="GitHub API rate limit exceeded GITHUB_TOKEN=fake-secret",
        )
        attempts = ({
            "provider": "github",
            "asset": "ETH",
            "metric_keys": ["fundamentals.tvl"],
            "status": "FAILED",
            "error_code": "HTTP_403_RATE_LIMIT",
            "reason": "rate limited GITHUB_TOKEN=fake-secret",
            "endpoint": "https://api.github.com/repos/example?api_key=fake-secret",
            "method": "GET",
            "attempt": 2,
            "exception_class": "ProviderRateLimited",
            "detail": "request was rate limited",
            "retryable": True,
            "log": "Traceback\nProviderRateLimited: GITHUB_TOKEN=fake-secret",
            "status_code": 403,
            "request_hash": "must-not-escape",
        },)
        rows = build_failed_data_fetches(acquisition((failed,), attempts=attempts))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["failure_stage"], "PROVIDER")
        self.assertEqual(rows[0]["error_code"], "HTTP_403_RATE_LIMIT")
        self.assertEqual(rows[0]["provider"], "github")
        self.assertEqual(rows[0]["attempts"][0]["status_code"], 403)
        self.assertEqual(rows[0]["attempts"][0]["exception_class"], "ProviderRateLimited")
        self.assertEqual(rows[0]["attempts"][0]["method"], "GET")
        self.assertEqual(rows[0]["attempts"][0]["attempt"], 2)
        self.assertIn("Traceback", rows[0]["attempts"][0]["log"])
        encoded = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("fake-secret", encoded)
        self.assertNotIn("request_hash", encoded)

    def test_successful_fallback_is_not_a_final_failure(self):
        success = result("BNB", "valuation.market_cap", "SUCCESS", value=100, unit="USD")
        attempts = (
            {"provider": "coingecko", "asset": "BNB", "metric_keys": ["valuation.market_cap"], "status": "FAILED", "error_code": "HTTP_429"},
            {"provider": "coinmetrics_community", "asset": "BNB", "metric_keys": ["valuation.market_cap"], "status": "SUCCESS"},
        )
        self.assertEqual(build_failed_data_fetches(acquisition((success,), attempts=attempts)), ())

    def test_exhausted_attempts_are_one_ordered_metric_row(self):
        failed = result("BNB", "valuation.market_cap", reason="all valuation providers exhausted")
        attempts = tuple({
            "provider": provider,
            "asset": "BNB",
            "metric_keys": ["valuation.market_cap"],
            "status": status,
            "error_code": code,
            "reason": reason,
        } for provider, status, code, reason in (
            ("coingecko", "FAILED", "HTTP_429", "rate limited"),
            ("coinmetrics_community", "UNSUPPORTED", "PROVIDER_UNSUPPORTED", "not in catalog"),
        ))
        rows = build_failed_data_fetches(acquisition((failed,), attempts=attempts))
        self.assertEqual(len(rows), 1)
        self.assertEqual([item["provider"] for item in rows[0]["attempts"]], [
            "coingecko", "coinmetrics_community",
        ])

    def test_stale_refresh_failure_preserves_last_observation(self):
        stale = result(
            "AAVE",
            "fundamentals.tvl",
            "STALE",
            reason="last observation is stale as of 2026-09-07T00:00:00Z; refresh failed: HTTP_500",
            last_observation_at=OLD,
            refresh_provider="defillama",
            refresh_endpoint="https://api.llama.fi/tvl/aave",
            refresh_error_code="HTTP_500",
            refresh_error_detail="upstream unavailable",
        )
        rows = build_failed_data_fetches(acquisition((stale,)))
        self.assertEqual(rows[0]["status"], "STALE")
        self.assertEqual(rows[0]["last_observation_at"], OLD)
        self.assertEqual(rows[0]["provider"], "defillama")
        self.assertIn("refresh failed", rows[0]["reason"])
        self.assertEqual(
            build_failed_data_fetches(acquisition((result("AAVE", "fundamentals.tvl", "STALE", reason="old"),))),
            (),
        )

    def test_unavailable_statuses_are_excluded(self):
        results = (
            result("ETH", "fundamentals.developer_activity", "SKIPPED", reason="OPTIONAL_PROVIDER_UNAVAILABLE"),
            result("BTC", "fundamentals.tvl", "NOT_APPLICABLE", reason="not an application metric"),
            result("ETH", "fundamentals.tvl", "CONFLICT", reason="sources disagree"),
        )
        self.assertEqual(build_failed_data_fetches(acquisition(results)), ())

    def test_derived_and_event_scan_stages_are_structured(self):
        derived = result(
            "ETH",
            "valuation.fdv_market_cap_ratio",
            reason="DERIVED_INPUT_UNAVAILABLE: missing dependencies: valuation.fdv",
        )
        derived_row = build_failed_data_fetches(acquisition((derived,)))[0]
        self.assertEqual(derived_row["failure_stage"], "DERIVED")
        self.assertNotIn("provider", derived_row)
        self.assertIn("DERIVED_INPUT_UNAVAILABLE", derived_row["reason"])

        event = result("BNB", "risk.security_event_status", reason="required source coverage failed")
        scan = {
            "asset": "BNB",
            "category": "security",
            "status": "INSUFFICIENT_SOURCE_COVERAGE",
        }
        event_row = build_failed_data_fetches(
            acquisition((event,), event_scans=(scan,), review_type="EVENT_REVIEW"),
        )[0]
        self.assertEqual(event_row["failure_stage"], "EVENT_SCAN")
        self.assertEqual(event_row["error_code"], "INSUFFICIENT_SOURCE_COVERAGE")
        self.assertTrue(event_row["critical"])
        self.assertIn("CRITICAL DATA FAILURE", event_row["decision_effect"])

    def test_validation_and_web_fallback_stages_do_not_guess_providers(self):
        rejected = result("ETH", "fundamentals.tvl", reason="provider value rejected: invalid payload")
        validation = build_failed_data_fetches(acquisition((rejected,), attempts=({
            "provider": "defillama",
            "asset": "ETH",
            "metric_keys": ["fundamentals.tvl"],
            "status": "SUCCESS",
        },)))[0]
        self.assertEqual(validation["failure_stage"], "VALIDATION")
        self.assertEqual(validation["provider"], "defillama")

        web = result("ARB", "market.spot_price", reason="no configured source")
        self.assertEqual(build_failed_data_fetches(acquisition((web,), web_fallbacks=({
            "asset": "ARB",
            "metric_key": "market.spot_price",
            "reason": "no configured source",
        },))), ())

    def test_rows_sort_critical_then_asset_and_metric(self):
        rows = build_failed_data_fetches(acquisition((
            result("ETH", "fundamentals.tvl", reason="x"),
            result("BNB", "risk.security_event_status", reason="x"),
            result("AAVE", "risk.security_event_status", reason="x"),
        ), review_type="EVENT_REVIEW"), review_type="EVENT_REVIEW")
        self.assertEqual([(row["critical"], row["asset"], row["metric_key"]) for row in rows], [
            (True, "AAVE", "risk.security_event_status"),
            (True, "BNB", "risk.security_event_status"),
            (False, "ETH", "fundamentals.tvl"),
        ])

    def test_report_packet_round_trip_and_immutability(self):
        failed = result("ETH", "fundamentals.tvl", reason="provider unavailable")
        acq = acquisition((failed,), attempts=({
            "provider": "github",
            "asset": "ETH",
            "metric_keys": ["fundamentals.tvl"],
            "status": "FAILED",
            "error_code": "HTTP_500",
        },))
        packet = build_report_packet(report_decision(), acquisition=acq)
        self.assertEqual(
            build_failed_data_fetches(acq.as_dict()),
            build_failed_data_fetches(acq),
        )
        restored = ReportPacket.from_mapping(packet.as_dict())
        self.assertEqual(restored.failed_data_fetches, packet.failed_data_fetches)
        with self.assertRaises(TypeError):
            packet.failed_data_fetches[0]["reason"] = "changed"
        with self.assertRaises(TypeError):
            packet.failed_data_fetches[0]["attempts"][0]["status"] = "changed"

        incomplete = packet.as_dict()
        incomplete.pop("failed_data_fetches")
        with self.assertRaises(ValueError):
            ReportPacket.from_mapping(incomplete)

        output = build_final_review_output(packet, acquisition=acq)
        self.assertEqual(output["collection"]["failed_data_fetches"], packet.as_dict()["failed_data_fetches"])
        self.assertEqual(output["report_packet"]["failed_data_fetches"], packet.as_dict()["failed_data_fetches"])

    def test_no_failure_packet_and_schema_rejects_malformed_records(self):
        success = result("ETH", "fundamentals.developer_activity", "SUCCESS", value=2, unit="count")
        packet = build_report_packet(report_decision(), acquisition=acquisition((success,)))
        self.assertEqual(packet.failed_data_fetches, ())
        output = build_final_review_output(packet, acquisition=acquisition((success,)))
        self.assertEqual(output["collection"]["failed_data_fetches"], [])

        schema = json.loads((Path(__file__).parents[1] / "schemas" / "report-packet.schema.json").read_text())
        self.assertEqual(list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(packet.as_dict())), [])
        invalid = copy.deepcopy(packet.as_dict())
        invalid["failed_data_fetches"] = [{
            "asset": "ETH",
            "metric_key": "fundamentals.tvl",
            "status": "CONFLICT",
            "failure_stage": "PROVIDER",
            "reason": "bad",
            "critical": False,
            "decision_role": "SCORING_FACTOR",
            "decision_effect": "bad",
            "attempts": [],
            "unknown": True,
        }]
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
        with self.assertRaises(ValueError):
            ReportPacket.from_mapping(invalid)

    def test_failure_strings_are_redacted_from_packet_and_output(self):
        failed = result(
            "ETH",
            "fundamentals.developer_activity",
            reason="request failed GITHUB_TOKEN=fake-secret",
        )
        acq = acquisition((failed,), attempts=({
            "provider": "github",
            "asset": "ETH",
            "metric_keys": ["fundamentals.developer_activity"],
            "status": "FAILED",
            "error_code": "HTTP_403_RATE_LIMIT",
            "endpoint": "https://api.github.com/repos/example?api_key=fake-secret",
            "detail": '{"body":"raw-provider-body", "headers":{"Authorization":"Bearer fake-secret"}}',
        },))
        packet = build_report_packet(report_decision(), acquisition=acq)
        output = build_final_review_output(packet, acquisition=acq)
        encoded = json.dumps({
            "failures": packet.as_dict()["failed_data_fetches"],
            "output": output,
        }, ensure_ascii=False)
        self.assertNotIn("fake-secret", encoded)
        self.assertNotIn("raw-provider-body", encoded)
        self.assertNotIn("Authorization", encoded)

    def test_report_separates_decision_required_optional_and_provider_failures(self):
        failed = result("ETH", "fundamentals.tvl", reason="required provider unavailable")
        skipped = result(
            "ETH", "eth.staking.staking_apr_7d", "SKIPPED",
            reason="OPTIONAL_PROVIDER_UNAVAILABLE: Rated subscription is not active",
        )
        acq = AcquisitionResult(
            MetricCollectionPlan("SNAPSHOT_REVIEW", tuple(
                MetricRequest(item.event.asset, item.event.metric_key) for item in (failed, skipped)
            )),
            (failed, skipped),
            summary={
                "optional_data": [{
                    "asset": "ETH",
                    "metric_key": "eth.staking.staking_apr_7d",
                    "status": "SKIPPED",
                    "reason": "Rated subscription is not active",
                }],
                "provider_operational_failures": [{
                    "provider": "rated",
                    "status": "PROVIDERUNAVAILABLE",
                    "error_code": "RATED_SUBSCRIPTION_INACTIVE",
                }],
            },
        )
        packet = build_report_packet(report_decision(), acquisition=acq)
        self.assertEqual(len(packet.decision_blocking_failures), 0)
        self.assertEqual(len(packet.required_scoring_failures), 1)
        self.assertEqual(len(packet.optional_data_unavailable), 1)
        self.assertEqual(packet.provider_operational_failures[0]["error_code"], "RATED_SUBSCRIPTION_INACTIVE")
        output = build_final_review_output(packet, acquisition=acq)
        self.assertIn("optional_data_unavailable", output["debug_report"])
        self.assertIn("provider_operational_failures", output["debug_report"])


if __name__ == "__main__":
    unittest.main()
