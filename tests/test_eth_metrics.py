import json
import math
import unittest
from datetime import date, timedelta
from pathlib import Path

from crypto_portfolio.engine.derived_metrics import (
    calculate_burn_to_issuance,
    calculate_eth_etf_flow_to_aum,
    calculate_exchange_flow_to_market_cap,
    calculate_net_supply_growth,
    calculate_active_effective_stake_pct,
)
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.engine.metric_plan import MetricCollectionPlan, MetricRequest
from crypto_portfolio.engine.derived_metrics import derive_metric_observations
from crypto_portfolio.acquisition import AcquisitionManager, _expand_derived_dependencies
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.blobscan import parse_timeseries as parse_blobscan
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider
from crypto_portfolio.providers.growthepie import parse_da_payload, parse_fundamentals_payload, parse_rent_payload
from crypto_portfolio.providers.ethereum_protocol import block_burn_eth, execution_base_fee_burn
from crypto_portfolio.providers.router import ProviderRouter
from crypto_portfolio.providers.ultrasound_money import UltrasoundMoneyProvider
from crypto_portfolio.providers.cache import ProviderCache
from crypto_portfolio.models.metrics_history import MetricObservation, stable_observation_id
from tempfile import TemporaryDirectory
from crypto_portfolio.providers.sosovalue import parse_etf_flow_history


class _CoinMetricsClient:
    def __init__(self):
        self.rows = []

    def get_json(self, url, **kwargs):
        if "catalog" in url:
            metrics = [
                "SplyCur", "IssTotNtv", "CapMVRVCur", "CapRealUSD",
            ]
            return {"metrics": [{"metric": item, "frequencies": [{"frequency": "1d", "assets": ["eth"]}]} for item in metrics]}
        return {"data": self.rows}


class EthMetricsTests(unittest.TestCase):
    def test_ultrasound_is_direct_primary_for_burn_30d(self):
        class Client:
            def __init__(self):
                self.calls = []

            def get_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return {"d30": {"rate": {"eth_per_minute": "0.1"}, "timestamp": "2026-09-01T00:00:00Z"}}

        request = MetricRequest("ETH", "eth.monetary.burn_30d_eth")
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (request,))
        expanded = _expand_derived_dependencies(plan)
        self.assertEqual({item.metric_key for item in expanded.requests}, {request.metric_key})
        client = Client()
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"ultrasound_money": UltrasoundMoneyProvider(client=client)},
                    config={
                        "providers": {"ultrasound_money": {"enabled": True}},
                        "cache_ttl_seconds": {"default": 3600},
                        "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
                        "fallback": {"allow_web": False},
                    },
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(plan, mode="REFRESH", as_of="2026-09-01T00:00:00Z", now="2026-09-01T00:00:00Z", cached_observations=())
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(len(client.calls), 1)
        normal_plan = build_metric_collection_plan(["ETH"])
        self.assertNotIn("eth.monetary.cumulative_burn_eth", {
            item.metric_key for item in normal_plan.for_asset("ETH")
        })

    def test_burn_fallback_requires_aligned_cumulative_history(self):
        def cumulative(value, observed_at):
            return MetricObservation(
                stable_observation_id("ETH", "eth.monetary.cumulative_burn_eth", observed_at, "etherscan", value),
                "ETH", "eth.monetary.cumulative_burn_eth", "fundamentals", value, "ETH", "current",
                observed_at, observed_at, "etherscan", "CURRENT", "MEDIUM",
                metadata={"methodology": "Etherscan documented wei counter converted to ETH"},
            )

        current = cumulative(100, "2026-09-01T00:00:00Z")
        prior = cumulative(80, "2026-08-02T00:00:00Z")
        values, unresolved = derive_metric_observations(
            (MetricRequest("ETH", "eth.monetary.burn_30d_eth"),),
            {("ETH", "eth.monetary.cumulative_burn_eth"): current},
            {},
            fetched_at="2026-09-01T00:00:00Z",
            as_of="2026-09-01T00:00:00Z",
            historical_observations=(prior,),
        )
        self.assertEqual(values[("ETH", "eth.monetary.burn_30d_eth")]["value"], 20)
        self.assertEqual(unresolved, {})
        missing_values, missing = derive_metric_observations(
            (MetricRequest("ETH", "eth.monetary.burn_30d_eth"),),
            {("ETH", "eth.monetary.cumulative_burn_eth"): current},
            {},
            fetched_at="2026-09-01T00:00:00Z",
            as_of="2026-09-01T00:00:00Z",
        )
        self.assertEqual(missing_values, {})
        self.assertIn(("ETH", "eth.monetary.burn_30d_eth"), missing)

    def test_failed_direct_burn_uses_cumulative_fallback_only_on_demand(self):
        class DirectFailure:
            def collect(self, _request):
                from crypto_portfolio.providers.base import ProviderDiagnostic, ProviderUnavailable

                raise ProviderUnavailable(
                    "Ultrasound unavailable",
                    diagnostic=ProviderDiagnostic(error_code="HTTP_429", status_code=429, detail="rate limited"),
                )

        class CumulativeProvider:
            def collect(self, request):
                return [{
                    "asset": request.asset,
                    "metric_key": "eth.monetary.cumulative_burn_eth",
                    "value": 100,
                    "unit": "ETH",
                    "period": "current",
                    "observed_at": "2026-09-01T00:00:00Z",
                    "fetched_at": "2026-09-01T00:00:00Z",
                    "source": "etherscan",
                    "confidence": "MEDIUM",
                    "metadata": {"methodology": "Etherscan documented wei counter converted to ETH"},
                }]

        prior = MetricObservation(
            stable_observation_id("ETH", "eth.monetary.cumulative_burn_eth", "2026-08-02T00:00:00Z", "etherscan", 80),
            "ETH", "eth.monetary.cumulative_burn_eth", "fundamentals", 80, "ETH", "current",
            "2026-08-02T00:00:00Z", "2026-08-02T00:00:00Z", "etherscan", "CURRENT", "MEDIUM",
            metadata={"methodology": "Etherscan documented wei counter converted to ETH"},
        )
        plan = MetricCollectionPlan("SNAPSHOT_REVIEW", (
            MetricRequest("ETH", "eth.monetary.burn_30d_eth"),
        ))
        config = {
            "providers": {
                "ultrasound_money": {"enabled": True},
                "etherscan": {"enabled": True},
            },
            "cache_ttl_seconds": {"default": 3600},
            "network": {"max_requests_per_review": 60, "max_requests_per_provider": 30},
            "fallback": {"allow_web": False},
        }
        with TemporaryDirectory() as directory:
            result = AcquisitionManager(
                ProviderRouter(
                    {"ultrasound_money": DirectFailure(), "etherscan": CumulativeProvider()},
                    config=config,
                    cache=ProviderCache(Path(directory) / "cache"),
                ),
                persist=False,
            ).run(
                plan,
                mode="REFRESH",
                as_of="2026-09-01T00:00:00Z",
                now="2026-09-01T00:00:00Z",
                cached_observations=(prior,),
            )
        self.assertEqual(result.results[0].status, "SUCCESS")
        self.assertEqual(result.observations[0].source, "python-derived")
        self.assertEqual([item["provider"] for item in result.attempts], ["ultrasound_money", "etherscan"])

    def test_burn_to_issuance_is_python_derived_only(self):
        from crypto_portfolio.providers.routes import provider_chain

        self.assertEqual(provider_chain("eth.monetary.burn_to_issuance_30d", "ETH"), ())
        expanded = _expand_derived_dependencies(MetricCollectionPlan(
            "SNAPSHOT_REVIEW",
            (MetricRequest("ETH", "eth.monetary.burn_to_issuance_30d"),),
        ))
        self.assertEqual(
            {item.metric_key for item in expanded.requests},
            {"eth.monetary.burn_to_issuance_30d", "eth.monetary.burn_30d_eth", "eth.monetary.issuance_30d_eth"},
        )

    def test_registry_scope_and_fdv_not_applicable(self):
        self.assertEqual(metric_definition("eth.monetary.current_supply_eth").asset_scope, ("ETH",))
        self.assertFalse(metric_definition("valuation.fdv").applies_to("ETH"))
        self.assertFalse(metric_definition("valuation.fdv_market_cap_ratio").applies_to("ETH"))
        self.assertEqual(metric_definition("eth.structural.builder_largest_share").decision_role, "STRUCTURAL_RISK")

    def test_pure_eth_derivations_fail_closed(self):
        self.assertTrue(math.isclose(calculate_net_supply_growth(101, 100), 0.01))
        self.assertIsNone(calculate_burn_to_issuance(1, 0))
        self.assertTrue(math.isclose(calculate_active_effective_stake_pct(30, 100), 0.3))
        self.assertTrue(math.isclose(calculate_exchange_flow_to_market_cap(-10, 100), -0.1))
        self.assertTrue(math.isclose(calculate_eth_etf_flow_to_aum(-10, 100), -0.1))
        with self.assertRaises(ValueError):
            calculate_active_effective_stake_pct(1, 0)

    def test_eth_metric_plan_is_scoped(self):
        policy = load_policy()
        plan = build_metric_collection_plan(["ETH", "AAVE"], policy=policy)
        eth = {item.metric_key for item in plan.for_asset("ETH")}
        aave = {item.metric_key for item in plan.for_asset("AAVE")}
        self.assertIn("eth.l2.rent_paid_30d_usd", eth)
        self.assertIn("relative.return_vs_btc_180d", eth)
        self.assertNotIn("relative.return_vs_btc_365d", eth)
        self.assertIn("flows.eth_etf_net_to_aum_30d", eth)
        self.assertNotIn("valuation.fdv_market_cap_ratio", eth)
        self.assertNotIn("eth.l2.rent_paid_30d_usd", aave)
        self.assertNotIn("relative.return_vs_btc_365d", {item.metric_key for item in plan.for_asset("BTC")})

    def test_relative_strength_uses_only_active_horizons(self):
        asset = [100.0 * 1.001**index for index in range(181)]
        btc = [100.0] * 181
        result = calculate_relative_strength(asset, btc, symbol="ETH")
        self.assertIsNone(getattr(result, "relative_365d", None))
        self.assertEqual(set(result.risk_adjusted_excess_returns), {"30d", "90d", "180d"})
        self.assertEqual(result.coverage, 1.0)

    def test_coinmetrics_eth_catalog_and_derived_realized_price(self):
        client = _CoinMetricsClient()
        for index in range(32):
            observed = date(2026, 8, 1) + timedelta(days=index)
            client.rows.append({
                "time": observed.isoformat() + "T00:00:00Z",
                "SplyCur": 100 + index / 31,
                "IssTotNtv": 1,
                "SplyStkedNtv": 30 + index / 31,
                "CapMVRVCur": 1.2,
                "CapRealUSD": 500 + index,
            })
        result = CoinMetricsProvider(client=client).collect(ProviderRequest(
            "coinmetrics_community", "ethereum_valuation", "ETH",
            {"as_of": "2026-09-01T00:00:00Z"},
            (
                "eth.monetary.current_supply_eth",
                "eth.monetary.issuance_30d_eth",
                "eth_valuation.mvrv",
                "eth_valuation.realized_price",
            ),
        ))
        values = {item["metric_key"]: item["value"] for item in result}
        self.assertEqual(values["eth.monetary.current_supply_eth"], 101)
        self.assertEqual(values["eth.monetary.issuance_30d_eth"], 31)
        self.assertEqual(values["eth_valuation.mvrv"], 1.2)
        self.assertAlmostEqual(values["eth_valuation.realized_price"], 531 / 101)

    def test_eth_sosovalue_normalized_flow_uses_eth_aum(self):
        payload = {
            "code": 0,
            "data": [
                {"date": f"2026-08-{index:02d}", "total_net_inflow": 10, "total_net_assets": 1000}
                for index in range(1, 32)
            ],
        }
        result = parse_etf_flow_history(
            payload,
            ("flows.eth_etf_net_to_aum_7d", "flows.eth_etf_aum_usd"),
            asset="ETH",
            fetched_at="2026-09-01T00:00:00Z",
            as_of="2026-09-01T23:00:00Z",
        )
        values = {item["metric_key"]: item["value"] for item in result}
        self.assertAlmostEqual(values["flows.eth_etf_net_to_aum_7d"], 0.07)
        self.assertEqual(values["flows.eth_etf_aum_usd"], 1000)

    def test_eth_sosovalue_missing_aum_is_a_derived_input_failure(self):
        payload = {
            "code": 0,
            "data": [
                {"date": f"2026-08-{index:02d}", "total_net_inflow": 10}
                for index in range(1, 32)
            ],
        }
        from crypto_portfolio.providers.sosovalue import SoSoValueProvider
        class Client:
            def post_json(self, *_args, **_kwargs):
                return payload

        result = SoSoValueProvider(client=Client(), api_key="fake").collect(
            ProviderRequest("sosovalue", "etf", "ETH", {}, ("flows.eth_etf_net_to_aum_7d",))
        )
        self.assertEqual(result.observations, ())
        self.assertEqual(result.diagnostics["flows.eth_etf_net_to_aum_7d"]["error_code"], "DERIVED_INPUT_UNAVAILABLE")

    def test_eth_normalized_etf_flow_owns_flow_factor(self):
        result = calculate_flow_factor({
            "flows.etf_net_7d": -10_000_000,
            "flows.eth_etf_net_to_aum_7d": 0.005,
        }, symbol="ETH")
        self.assertEqual(result.state, "POSITIVE")
        self.assertAlmostEqual(result.normalized_flow, 0.005)

    def test_public_eth_providers_reject_unclassified_data(self):
        rent = {"data": [{"date": f"2026-08-{index:02d}", "rent_paid_usd": 1} for index in range(1, 32)]}
        self.assertEqual(parse_rent_payload(rent, ("eth.l2.rent_paid_30d_usd",), fetched_at="2026-09-01T00:00:00Z")[0]["value"], 30)
        da = {"data": [{"date": "2026-08-31", "layer": "ethereum", "data_bytes": 10, "fees_usd": 2}, {"date": "2026-08-31", "layer": "other", "data_bytes": 5, "fees_usd": 1}]}
        share = parse_da_payload(da, ("eth.da.ethereum_share_of_tracked_da_bytes_30d",), fetched_at="2026-09-01T00:00:00Z")[0]["value"]
        self.assertAlmostEqual(share, 10 / 15)
        blob = json.loads((Path(__file__).parent / "fixtures/providers/blobscan_timeseries.json").read_text())
        self.assertEqual(parse_blobscan(blob, ("eth.blobs.count_1d",), fetched_at="2026-09-01T00:00:00Z")[0]["value"], 42000)
    def test_growthepie_current_master_and_fundamentals_contract(self):
        master = {
            "chains": {
                "arbitrum": {"deployment": "PROD", "chain_type": "rollup", "da_layer": "Ethereum (blobs)"},
                "base": {"deployment": "PROD", "chain_type": "rollup", "da_layer": "Ethereum (blobs)"},
                "celestia-rollup": {"deployment": "PROD", "chain_type": "rollup", "da_layer": "Celestia"},
            },
            "metrics": {
                "rent_paid": {"supported_chains": ["arbitrum", "base"]},
            },
        }
        rows = []
        for index in range(30):
            day = date(2026, 8, 3) + timedelta(days=index)
            stamp = day.isoformat()
            for origin, rent, blob, fee in (
                ("arbitrum", 2, 10, 4),
                ("base", 3, 20, 6),
                ("celestia-rollup", 99, 30, 10),
            ):
                rows.extend((
                    {"metric_key": "rent_paid_usd", "origin_key": origin, "date": stamp, "value": rent},
                    {"metric_key": "blob_size_bytes", "origin_key": origin, "date": stamp, "value": blob},
                    {"metric_key": "costs_blobs_usd", "origin_key": origin, "date": stamp, "value": fee},
                ))
        values = parse_fundamentals_payload(
            rows,
            master,
            ("eth.l2.rent_paid_30d_usd", "eth.da.ethereum_blob_data_30d_mb", "eth.da.ethereum_share_of_tracked_da_bytes_30d"),
            fetched_at="2026-09-02T00:00:00Z",
        )
        by_key = {item["metric_key"]: item["value"] for item in values}
        self.assertEqual(by_key["eth.l2.rent_paid_30d_usd"], 150)
        self.assertAlmostEqual(by_key["eth.da.ethereum_blob_data_30d_mb"], 900 / 1_000_000)
        self.assertAlmostEqual(by_key["eth.da.ethereum_share_of_tracked_da_bytes_30d"], 900 / 1800)

    def test_ethereum_protocol_burn_uses_canonical_block_fields(self):
        block = {
            "baseFeePerGas": "0x64",
            "gasUsed": "0x3e8",
            "blobGasUsed": "0x0",
            "excessBlobGas": "0x0",
        }
        self.assertEqual(execution_base_fee_burn(block), 100_000)
        self.assertGreaterEqual(block_burn_eth(block), 0)


if __name__ == "__main__":
    unittest.main()
