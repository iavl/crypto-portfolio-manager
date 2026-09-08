from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from crypto_portfolio.providers.base import ProviderDiagnostic, ProviderRequest, ProviderUnavailable
from crypto_portfolio.providers.circuit_breaker import CircuitBreaker, CircuitState
from crypto_portfolio.providers.health import (
    contract_providers,
    diagnostic_exit_code,
    doctor_providers,
    smoke_provider,
)
from crypto_portfolio.providers.http import HttpClient, classify_transport_error
from crypto_portfolio.providers.recording import RecordingTransport, ReplayTransport, load_recording
from crypto_portfolio.providers.router import ProviderRouter


class Response:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def read(self, _amount=-1):
        return self.body


def config_for(*names):
    return {
        "version": 1,
        "providers": {name: {"enabled": True} for name in names},
        "cache_ttl_seconds": {"default": 3600, "spot": 600},
        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
        "fallback": {"allow_web": True},
    }


class ProviderReliabilityTests(unittest.TestCase):
    def test_forbidden_and_timeout_taxonomy(self):
        self.assertEqual(classify_transport_error(HTTPError("https://x", 403, "forbidden", {}, None)), "HTTP_403_ACCESS_DENIED")
        self.assertEqual(classify_transport_error(HTTPError("https://x", 403, "blocked", {"Retry-After": "1"}, None)), "HTTP_403_RATE_LIMIT")
        self.assertEqual(classify_transport_error(HTTPError("https://x", 403, "region blocked", {}, None)), "HTTP_403_REGION_RESTRICTED")
        self.assertEqual(classify_transport_error(TimeoutError(), phase="connect"), "CONNECT_TIMEOUT")
        self.assertEqual(classify_transport_error(TimeoutError(), phase="read"), "READ_TIMEOUT")

    def test_retry_jitter_is_injected_and_bounded(self):
        sleeps = []
        responses = iter((Response(500), Response(200, b'{"ok": true}')))
        client = HttpClient(
            opener=lambda _request, timeout: next(responses),
            max_attempts=2,
            backoff_seconds=2,
            random_fn=lambda: 0.5,
            sleeper=sleeps.append,
        )
        self.assertEqual(client.get_json("https://example.test"), {"ok": True})
        self.assertEqual(sleeps, [1.0])

    def test_connect_and_read_timeout_are_distinct(self):
        with self.assertRaises(ProviderUnavailable) as connect:
            HttpClient(opener=lambda _request, timeout: (_ for _ in ()).throw(TimeoutError()), max_attempts=1).get_json("https://example.test")
        self.assertEqual(connect.exception.diagnostic.error_code, "CONNECT_TIMEOUT")

        class ReadTimeout:
            status = 200
            headers = {}

            def read(self, _amount=-1):
                raise TimeoutError()

        with self.assertRaises(ProviderUnavailable) as read:
            HttpClient(opener=lambda _request, timeout: ReadTimeout(), max_attempts=1).get_json("https://example.test")
        self.assertEqual(read.exception.diagnostic.error_code, "READ_TIMEOUT")

    def test_circuit_breaker_transitions(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=lambda: now[0])
        self.assertTrue(breaker.allow())
        breaker.record_failure(retryable=False)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        breaker.record_failure(retryable=True)
        breaker.record_failure(retryable=True)
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertFalse(breaker.allow())
        now[0] = 10.0
        self.assertTrue(breaker.allow())
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        breaker.record_success()
        self.assertEqual((breaker.state, breaker.failures), (CircuitState.CLOSED, 0))

    def test_record_and_replay_redacts_secrets(self):
        with TemporaryDirectory() as directory:
            transport = RecordingTransport(
                directory,
                provider="fixture",
                opener=lambda _request, timeout: Response(200, b'{"value": 1, "token": "secret"}'),
                record_authenticated=True,
            )
            client = HttpClient(
                opener=transport,
            )
            self.assertEqual(client.get_json("https://example.test/data?api_key=secret", headers={"Authorization": "Bearer secret"}), {"value": 1, "token": "secret"})
            path = transport.paths[0]
            raw = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            replay = HttpClient(opener=ReplayTransport(path))
            self.assertEqual(replay.get_json("https://example.test/data?api_key=secret"), {"value": 1, "token": "[REDACTED]"})
            bad = Path(directory) / "bad.json"
            bad.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_recording(bad)

    def test_health_contract_and_smoke_are_offline_with_fake_adapters(self):
        class Client:
            def get_json(self, *_args, **_kwargs):
                return {"symbol": "BTCUSDT", "price": "100"}

        class Provider:
            client = Client()

            def collect(self, request):
                return [{
                    "asset": request.asset,
                    "metric_key": request.metric_keys[0],
                    "value": 100,
                    "unit": "USD",
                    "observed_at": "2026-09-08T00:00:00Z",
                    "fetched_at": "2026-09-08T00:00:00Z",
                    "source": "fake",
                    "confidence": "HIGH",
                }]

        with TemporaryDirectory() as directory:
            router = ProviderRouter({"binance": Provider()}, config=config_for("binance"))
            router.cache = router.cache.__class__(Path(directory) / "cache")
            doctor = doctor_providers(router, "binance")
            self.assertEqual(doctor[0]["network"], "OK")
            self.assertEqual(contract_providers(router, "binance")[0]["status"], "PASS")
            smoke = smoke_provider(router, "BTC")
            self.assertEqual(smoke["status"], "PASS")
            self.assertEqual(smoke["provider_selected"], "binance")

    def test_router_opens_circuit_after_retryable_failures(self):
        class Failing:
            def collect(self, _request):
                raise ProviderUnavailable(
                    "upstream down",
                    diagnostic=ProviderDiagnostic(error_code="HTTP_5XX", retryable=True, detail="upstream down"),
                )

        with TemporaryDirectory() as directory:
            router = ProviderRouter({"binance": Failing()}, config=config_for("binance"))
            router.cache = router.cache.__class__(Path(directory) / "cache")
            request = ProviderRequest("binance", "spot", "BTC", {}, ("market.spot_price",))
            for _ in range(3):
                router.collect((request,), mode="REFRESH")
            result = router.collect((request,), mode="REFRESH")
            self.assertEqual(router.circuit_breakers["binance"].state, CircuitState.OPEN)
            self.assertEqual(result.attempts[0].error_code, "CIRCUIT_OPEN")

    def test_probe_exit_code_skips_unready_but_fails_ready(self):
        self.assertEqual(diagnostic_exit_code(({"config": "NOT_READY", "network": "SKIPPED"},)), 0)
        self.assertEqual(diagnostic_exit_code(({"config": "READY", "network": "FAILED", "error_code": "DNS_RESOLUTION_FAILED"},)), 2)


if __name__ == "__main__":
    unittest.main()
