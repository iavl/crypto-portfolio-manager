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
    calculate_staked_supply_pct,
)
from crypto_portfolio.engine.factors.relative_strength import calculate_relative_strength
from crypto_portfolio.engine.factors.flows import calculate_flow_factor
from crypto_portfolio.engine.metric_plan import build_metric_collection_plan
from crypto_portfolio.metrics_registry import metric_definition
from crypto_portfolio.models.policy import load_policy
from crypto_portfolio.providers.base import ProviderRequest
from crypto_portfolio.providers.blobscan import parse_timeseries as parse_blobscan
from crypto_portfolio.providers.coinmetrics import CoinMetricsProvider
from crypto_portfolio.providers.growthepie import parse_da_payload, parse_fundamentals_payload, parse_rent_payload
from crypto_portfolio.providers.l2beat import ethereum_project_ids, parse_tvs_payload
from crypto_portfolio.providers.ethereum_protocol import block_burn_eth, execution_base_fee_burn
from crypto_portfolio.providers.sosovalue import parse_etf_flow_history


class _CoinMetricsClient:
    def __init__(self):
        self.rows = []

    def get_json(self, url, **kwargs):
        if "catalog" in url:
            metrics = [
                "SplyCur", "IssTotNtv", "SplyStkedNtv", "SplyActStkedNtv",
                "SplyTotStkedNtv", "CapMVRVCur", "CapRealUSD",
            ]
            return {"metrics": [{"metric": item, "frequencies": [{"frequency": "1d", "assets": ["eth"]}]} for item in metrics]}
        return {"data": self.rows}


class EthMetricsTests(unittest.TestCase):
    def test_registry_scope_and_fdv_not_applicable(self):
        self.assertEqual(metric_definition("eth.monetary.current_supply_eth").asset_scope, ("ETH",))
        self.assertFalse(metric_definition("valuation.fdv_market_cap_ratio").applies_to("ETH"))
        self.assertEqual(metric_definition("eth.structural.builder_largest_share").decision_role, "STRUCTURAL_RISK")

    def test_pure_eth_derivations_fail_closed(self):
        self.assertTrue(math.isclose(calculate_net_supply_growth(101, 100), 0.01))
        self.assertIsNone(calculate_burn_to_issuance(1, 0))
        self.assertTrue(math.isclose(calculate_staked_supply_pct(30, 100), 0.3))
        self.assertTrue(math.isclose(calculate_exchange_flow_to_market_cap(-10, 100), -0.1))
        self.assertTrue(math.isclose(calculate_eth_etf_flow_to_aum(-10, 100), -0.1))
        with self.assertRaises(ValueError):
            calculate_staked_supply_pct(1, 0)

    def test_eth_metric_plan_is_scoped(self):
        policy = load_policy()
        plan = build_metric_collection_plan(["ETH", "AAVE"], policy=policy)
        eth = {item.metric_key for item in plan.for_asset("ETH")}
        aave = {item.metric_key for item in plan.for_asset("AAVE")}
        self.assertIn("eth.l2.rent_paid_30d_usd", eth)
        self.assertIn("relative.return_vs_btc_365d", eth)
        self.assertIn("flows.eth_etf_net_to_aum_30d", eth)
        self.assertNotIn("valuation.fdv_market_cap_ratio", eth)
        self.assertNotIn("eth.l2.rent_paid_30d_usd", aave)
        self.assertNotIn("relative.return_vs_btc_365d", {item.metric_key for item in plan.for_asset("BTC")})

    def test_relative_strength_includes_365d(self):
        asset = [100.0 * 1.001**index for index in range(366)]
        btc = [100.0] * 366
        result = calculate_relative_strength(asset, btc, symbol="ETH")
        self.assertIsNotNone(result.relative_365d)
        self.assertIn("365d", result.risk_adjusted_excess_returns)
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
                "eth.staking.staked_supply_pct",
                "eth_valuation.mvrv",
                "eth_valuation.realized_price",
            ),
        ))
        values = {item["metric_key"]: item["value"] for item in result}
        self.assertEqual(values["eth.monetary.current_supply_eth"], 101)
        self.assertEqual(values["eth.monetary.issuance_30d_eth"], 31)
        self.assertAlmostEqual(values["eth.staking.staked_supply_pct"], 31 / 101)
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
        l2beat_fixture = Path(__file__).parent / "fixtures/providers"
        ids = ethereum_project_ids(json.loads((l2beat_fixture / "l2beat_projects.json").read_text()))
        tvs = parse_tvs_payload(
            json.loads((l2beat_fixture / "l2beat_tvs.json").read_text()),
            ids,
            fetched_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(tvs["value"], 123456789.0)

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
