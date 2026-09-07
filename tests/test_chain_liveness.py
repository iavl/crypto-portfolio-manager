from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_portfolio.acquisition import AcquisitionManager
from crypto_portfolio.engine.metric_normalization import normalize_metric_result
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.risk import (
    apply_chain_liveness_deployment_cap,
    chain_liveness_deployment_factor,
    run_risk_gate,
)
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.providers.base import ProviderDiagnostic, ProviderRequest, ProviderUnavailable
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.providers.chain_liveness import (
    ChainLivenessAssessment,
    ChainLivenessProvider,
    ChainLivenessSource,
    CONFLICT,
)
from crypto_portfolio.providers.config import load_provider_config
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.probe import probe_provider
from crypto_portfolio.providers.routes import build_provider_requests, metric_reuse_ttl_seconds, provider_chain


NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)
NOW_TEXT = "2026-09-06T00:00:00Z"


def source(asset, source_id, source_type, group, url=None):
    return ChainLivenessSource(
        source_id,
        asset,
        url or f"https://{source_id}.example.test/rpc",
        source_type,
        1,
        group,
    )


def btc_tip(age_seconds, *, height=100, block_hash="0xabc"):
    return [{
        "height": height,
        "id": block_hash,
        "timestamp": int(NOW.timestamp() - age_seconds),
    }]


def evm_block(age_seconds, *, number="0x64", block_hash="0xabc"):
    return {
        "number": number,
        "hash": block_hash,
        "timestamp": hex(int(NOW.timestamp() - age_seconds)),
    }


class RpcClient:
    def __init__(self, *, get=None, post=None):
        self.get_values = list(get or [])
        self.post_values = {key: list(value) for key, value in (post or {}).items()}
        self.calls = []

    def get_json(self, url, **_kwargs):
        self.calls.append(("GET", url))
        value = self.get_values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def post_json(self, url, *, json_body, idempotent=False, **_kwargs):
        method = json_body["method"]
        self.calls.append((method, url, idempotent))
        value = self.post_values[method].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FailingProvider:
    capabilities = None

    def __init__(self):
        self.calls = 0

    def collect(self, _request):
        self.calls += 1
        raise ProviderUnavailable("provider unavailable")


def chain_config():
    config = load_provider_config()
    return {
        "version": config["version"],
        "providers": {"chain_liveness": {"enabled": True}},
        "cache_ttl_seconds": {"default": 3600, "chain_liveness": 300},
        "network": config["network"],
        "fallback": {"allow_web": True},
    }


class ChainLivenessProviderTests(unittest.TestCase):
    def provider(self, client, *sources):
        return ChainLivenessProvider(
            client=client,
            clock=lambda: NOW,
            sources=sources,
        )

    def test_applicability_registration_and_route(self):
        definition = metric_definition("risk.chain_liveness_status")
        self.assertEqual(definition.asset_scope, ("BTC", "ETH", "SOL", "BNB"))
        for asset in ("BTC", "ETH", "SOL", "BNB"):
            self.assertTrue(definition.applies_to(asset))
        for asset in ("AAVE", "LINK"):
            self.assertFalse(definition.applies_to(asset))
        config = load_provider_config()
        self.assertTrue(config["providers"]["chain_liveness"]["enabled"])
        self.assertEqual(provider_chain("risk.chain_liveness_status"), ("chain_liveness",))
        request = MetricRequest("AAVE", "risk.chain_liveness_status")
        self.assertEqual(build_provider_requests((request,)), ())

    def test_btc_fresh_and_single_stale_tip(self):
        fresh = self.provider(
            RpcClient(get=[btc_tip(10)]),
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
        ).assess("BTC")
        self.assertEqual(fresh.status, "HEALTHY")
        self.assertEqual(fresh.confidence, "HIGH")
        stale = self.provider(
            RpcClient(get=[btc_tip(4 * 3600)]),
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
        ).assess("BTC")
        self.assertEqual(stale.status, "DEGRADED")
        self.assertNotEqual(stale.status, "HALTED")

    def test_btc_severe_staleness_requires_independent_quorum(self):
        client = RpcClient(get=[btc_tip(4 * 3600, height=100), btc_tip(4 * 3600, height=100)])
        result = self.provider(
            client,
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
            source("BTC", "btc-two", "bitcoin_mempool", "two"),
        ).assess("BTC")
        self.assertEqual(result.status, "HALTED")
        self.assertEqual(result.confidence, "HIGH")
        self.assertEqual(result.sources_checked, ("btc-one", "btc-two"))

    def test_primary_failure_uses_fallback_and_keeps_failure_diagnostic_safe(self):
        error = ProviderUnavailable(
            "TLS failed for api_key=secret",
            diagnostic=ProviderDiagnostic(
                endpoint="https://primary.example.test/rpc?api_key=secret",
                error_code="TLS_CERTIFICATE_VERIFY_FAILED",
                detail="api_key=secret",
            ),
        )
        client = RpcClient(get=[error, btc_tip(10)])
        result = self.provider(
            client,
            source("BTC", "btc-one", "bitcoin_esplora", "one", "https://primary.example.test/rpc?api_key=secret"),
            source("BTC", "btc-two", "bitcoin_mempool", "two"),
        ).assess("BTC")
        self.assertEqual(result.status, "HEALTHY")
        self.assertEqual(result.confidence, "MEDIUM")
        self.assertNotIn("secret", str(result.as_dict()))

    def test_evm_finality_and_unsupported_finalized_tag(self):
        client = RpcClient(post={
            "eth_getBlockByNumber": [evm_block(10), evm_block(100)],
        })
        result = self.provider(
            client,
            source("ETH", "eth-one", "evm_json_rpc", "one"),
        ).assess("ETH")
        self.assertEqual(result.status, "HEALTHY")
        self.assertEqual(result.finalized_height_or_slot, 100)

        unsupported = RpcClient(post={
            "eth_getBlockByNumber": [
                evm_block(10),
                {"error": {"code": -32601, "message": "method not found"}},
            ],
        })
        reduced = self.provider(
            unsupported,
            source("ETH", "eth-one", "evm_json_rpc", "one"),
        ).assess("ETH")
        self.assertEqual(reduced.status, "HEALTHY")
        self.assertEqual(reduced.confidence, "MEDIUM")
        self.assertIsNone(reduced.finalized_age_seconds)

    def test_evm_delayed_finality_and_corrobated_halt(self):
        delayed_client = RpcClient(post={
            "eth_getBlockByNumber": [evm_block(10), evm_block(1800)],
        })
        delayed = self.provider(
            delayed_client,
            source("ETH", "eth-one", "evm_json_rpc", "one"),
        ).assess("ETH")
        self.assertEqual(delayed.status, "DEGRADED")

        halted_client = RpcClient(post={
            "eth_getBlockByNumber": [
                evm_block(700, block_hash="0xdef"), evm_block(3000),
                evm_block(700, number="0x64", block_hash="0xdef"), evm_block(3000, number="0x64", block_hash="0xdef"),
            ],
        })
        halted = self.provider(
            halted_client,
            source("ETH", "eth-one", "evm_json_rpc", "one"),
            source("ETH", "eth-two", "evm_json_rpc", "two"),
        ).assess("ETH")
        self.assertEqual(halted.status, "HALTED")

    def test_bnb_uses_evm_fallback(self):
        failure = ProviderUnavailable("RPC down")
        client = RpcClient(post={
            "eth_getBlockByNumber": [failure, evm_block(10), evm_block(20)],
        })
        result = self.provider(
            client,
            source("BNB", "bnb-one", "evm_json_rpc", "one"),
            source("BNB", "bnb-two", "evm_json_rpc", "two"),
        ).assess("BNB")
        self.assertEqual(result.status, "HEALTHY")
        self.assertEqual(result.confidence, "MEDIUM")

    def test_solana_health_and_fallback(self):
        client = RpcClient(post={
            "getHealth": [{"result": "ok"}],
            "getSlot": [{"result": 100}],
            "getBlockTime": [{"result": int(NOW.timestamp() - 10)}],
        })
        result = self.provider(
            client,
            source("SOL", "sol-one", "solana_json_rpc", "one"),
        ).assess("SOL")
        self.assertEqual(result.status, "HEALTHY")
        self.assertEqual(result.finalized_height_or_slot, 100)

        fallback = RpcClient(post={
            "getHealth": [ProviderUnavailable("HTTP 429"), {"result": "ok"}],
            "getSlot": [{"result": 100}],
            "getBlockTime": [{"result": int(NOW.timestamp() - 10)}],
        })
        reduced = self.provider(
            fallback,
            source("SOL", "sol-one", "solana_json_rpc", "one"),
            source("SOL", "sol-two", "solana_json_rpc", "two"),
        ).assess("SOL")
        self.assertEqual(reduced.status, "HEALTHY")
        self.assertEqual(reduced.confidence, "MEDIUM")

    def test_all_transport_failure_is_unknown_not_halted(self):
        client = RpcClient(get=[ProviderUnavailable("down"), ProviderUnavailable("down")])
        provider = self.provider(
            client,
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
            source("BTC", "btc-two", "bitcoin_mempool", "two"),
        )
        assessment = provider.assess("BTC")
        self.assertEqual(assessment.status, "UNKNOWN")
        with self.assertRaises(ProviderUnavailable):
            provider.collect(ProviderRequest("chain_liveness", "chain_liveness", "BTC", {}, ("risk.chain_liveness_status",)))
        self.assertNotEqual(assessment.status, "HALTED")

    def test_conflicting_current_sources_fail_closed(self):
        client = RpcClient(get=[btc_tip(4 * 3600, block_hash="0xabc"), btc_tip(4 * 3600, block_hash="0xdef")])
        provider = self.provider(
            client,
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
            source("BTC", "btc-two", "bitcoin_mempool", "two"),
        )
        assessment = provider.assess("BTC")
        self.assertEqual(assessment.status, CONFLICT)
        with self.assertRaises(ProviderUnavailable):
            provider.collect(ProviderRequest("chain_liveness", "chain_liveness", "BTC", {}, ("risk.chain_liveness_status",)))

    def test_collection_success_metadata_uses_assessment_time(self):
        provider = self.provider(
            RpcClient(get=[btc_tip(10)]),
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
        )
        response = provider.collect(ProviderRequest(
            "chain_liveness", "chain_liveness", "BTC", {}, ("risk.chain_liveness_status",)
        ))
        observation = response.observations[0]
        self.assertEqual(observation["observed_at"], NOW_TEXT)
        self.assertEqual(observation["value"], "HEALTHY")
        self.assertEqual(observation["metadata"]["head_observed_at"], "2026-09-05T23:59:50Z")
        self.assertEqual(observation["metadata"]["head_finalized_distance"], None)
        self.assertIn("provider_source_ids", observation["metadata"])

    def test_probe_is_opt_in_and_supports_asset_targeting(self):
        provider = self.provider(
            RpcClient(get=[btc_tip(10)]),
            source("BTC", "btc-one", "bitcoin_esplora", "one"),
        )
        router = ProviderRouter({"chain_liveness": provider}, config=chain_config())
        result = probe_provider(router, "chain_liveness", asset="BTC")[0]
        self.assertEqual(result["assessment"], "HEALTHY")
        self.assertEqual(result["asset"], "BTC")
        self.assertEqual(result["network"], "OK")
        self.assertNotIn("raw", str(result).lower())


class ChainLivenessAcquisitionTests(unittest.TestCase):
    def observation(self, observed_at):
        return normalize_metric_result({
            "asset": "ETH",
            "metric_key": "risk.chain_liveness_status",
            "value": "HEALTHY",
            "observed_at": observed_at,
            "fetched_at": observed_at,
            "source": "chain_liveness",
            "confidence": "HIGH",
        }).observation

    def test_reuse_ttl_and_fetch_modes(self):
        self.assertEqual(metric_reuse_ttl_seconds("risk.chain_liveness_status", {"chain_liveness": 300}), 300)
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("ETH", "risk.chain_liveness_status"),))
        source_value = source("ETH", "eth-one", "evm_json_rpc", "one")
        client = RpcClient(post={"eth_getBlockByNumber": [evm_block(10), evm_block(20)]})
        provider = ChainLivenessProvider(client=client, clock=lambda: NOW, sources=[source_value])
        with TemporaryDirectory() as directory:
            router = ProviderRouter(
                {"chain_liveness": provider},
                config=chain_config(),
                cache=ProviderCache(Path(directory) / "cache"),
            )
            fresh = self.observation((NOW - timedelta(minutes=3)).isoformat().replace("+00:00", "Z"))
            result = AcquisitionManager(router, persist=False).run(
                plan, mode="AUTO", now=NOW_TEXT, as_of=NOW_TEXT, cached_observations=(fresh,)
            )
            self.assertEqual(provider.client.calls, [])
            self.assertEqual(result.summary["fresh_observation_hits"], 1)

            stale = self.observation((NOW - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"))
            result = AcquisitionManager(router, persist=False).run(
                plan, mode="AUTO", now=NOW_TEXT, as_of=NOW_TEXT, cached_observations=(stale,)
            )
            self.assertEqual(len(provider.client.calls), 2)
            self.assertEqual(result.results[0].status, "SUCCESS")

            provider.client.post_values["eth_getBlockByNumber"] = [evm_block(10), evm_block(20)]
            before = len(provider.client.calls)
            result = AcquisitionManager(router, persist=False).run(
                plan, mode="REFRESH", now=NOW_TEXT, as_of=NOW_TEXT, cached_observations=(fresh,)
            )
            self.assertEqual(len(provider.client.calls), before + 2)
            self.assertEqual(result.results[0].status, "SUCCESS")

    def test_cache_only_never_calls_provider_and_inapplicable_never_routes(self):
        provider = FailingProvider()
        router = ProviderRouter({"chain_liveness": provider}, config=chain_config())
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "risk.chain_liveness_status"),
            MetricRequest("AAVE", "risk.chain_liveness_status"),
        ))
        result = AcquisitionManager(router, persist=False).run(
            plan, mode="CACHE_ONLY", now=NOW_TEXT, as_of=NOW_TEXT, cached_observations=()
        )
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result.results[0].status, "FAILED")
        self.assertEqual(result.results[1].status, "NOT_APPLICABLE")
        self.assertTrue(result.hard_critical_unresolved)
        self.assertEqual(result.web_fallbacks, ())

    def test_historical_replay_does_not_query_current_chain_state(self):
        client = RpcClient(post={"eth_getBlockByNumber": [evm_block(10), evm_block(20)]})
        provider = ChainLivenessProvider(
            client=client,
            clock=lambda: NOW,
            sources=[source("ETH", "eth-one", "evm_json_rpc", "one")],
        )
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (MetricRequest("ETH", "risk.chain_liveness_status"),))
        result = AcquisitionManager(
            ProviderRouter({"chain_liveness": provider}, config=chain_config()),
            persist=False,
        ).run(plan, now=NOW_TEXT, as_of="2026-09-05T00:00:00Z", cached_observations=())
        self.assertEqual(client.calls, [])
        self.assertEqual(result.results[0].status, "FAILED")
        self.assertEqual(result.results[0].event.refresh_error_code, "HISTORICAL_LIVENESS_UNAVAILABLE")
        self.assertTrue(result.hard_critical_unresolved)


class ChainLivenessRiskTests(unittest.TestCase):
    def test_risk_gate_consequences(self):
        target = {"ETH": 0.5, "BTC": 0.4, "USDT": 0.1}
        healthy = run_risk_gate(target, chain_liveness={"ETH": "HEALTHY"})
        self.assertTrue(healthy.ok)

        degraded = run_risk_gate(target, chain_liveness={"ETH": "DEGRADED"})
        self.assertTrue(degraded.ok)
        self.assertEqual(degraded.deployment_caps["ETH"], 0.25)
        self.assertEqual(apply_chain_liveness_deployment_cap(1000, "DEGRADED"), 250)
        self.assertEqual(chain_liveness_deployment_factor("HEALTHY"), 1.0)

        halted = run_risk_gate(target, chain_liveness={"ETH": "HALTED"})
        self.assertFalse(halted.ok)
        self.assertIn("CHAIN_LIVENESS_HALTED", {item.code for item in halted.errors})

        reduced = run_risk_gate(
            target,
            chain_liveness={"ETH": "HALTED"},
            actions=({"symbol": "ETH", "action": "REDUCE", "amount_usd": 10},),
        )
        self.assertTrue(reduced.ok)

        unknown = run_risk_gate(target, chain_liveness={"ETH": "UNKNOWN"})
        self.assertFalse(unknown.ok)
        self.assertIn("CHAIN_LIVENESS_UNAVAILABLE", {item.code for item in unknown.errors})

    def test_assessment_status_and_non_native_rejection(self):
        assessment = ChainLivenessAssessment(
            "ETH", "DEGRADED", NOW_TEXT, "LOW", 100, "0xabc", NOW_TEXT, 10,
            99, NOW_TEXT, 100, ("eth-one",), ("eth-one",), evidence={}
        )
        result = run_risk_gate(
            {"ETH": 0.5, "BTC": 0.4, "USDT": 0.1},
            assessments={"ETH": {"chain_liveness": assessment}},
        )
        self.assertEqual(result.deployment_caps["ETH"], 0.25)
        with self.assertRaises(ValueError):
            run_risk_gate(
                {"ETH": 0.5, "BTC": 0.4, "USDT": 0.1},
                chain_liveness={"AAVE": "HALTED"},
            )


if __name__ == "__main__":
    unittest.main()
