"""Bounded standard Beacon API adapter used for health and future fallbacks."""

from __future__ import annotations

import os
from typing import Any, Mapping

from .base import ProviderCapabilities, ProviderRequest, ProviderResponse, ProviderResponseError, ProviderUnsupportedMetric
from .http import HttpClient


DEFAULT_BASE_URL = "https://ethereum-beacon-api.publicnode.com"
NODE_VERSION_PATH = "/eth/v1/node/version"
GENESIS_PATH = "/eth/v1/beacon/genesis"
FINALITY_PATH = "/eth/v1/beacon/states/head/finality_checkpoints"


class EthereumBeaconProvider:
    name = "ethereum_beacon"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        base_url: str | None = None,
    ) -> None:
        selected = base_url or os.environ.get("ETH_BEACON_API_URL") or DEFAULT_BASE_URL
        if not isinstance(selected, str) or not selected.strip():
            raise ValueError("Beacon API base_url must be a non-empty string")
        self.base_url = selected.rstrip("/")
        self.client = client or HttpClient()
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=(),
            historical_series=(),
            supports_batching=False,
            requires_api_key=False,
        )

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderUnsupportedMetric(
            "standard Beacon API has no bounded network aggregate for the requested staking metric"
        )

    def probe(self) -> Mapping[str, Any]:
        version = self.client.get_json(self.base_url + NODE_VERSION_PATH)
        genesis = self.client.get_json(self.base_url + GENESIS_PATH)
        finality = self.client.get_json(self.base_url + FINALITY_PATH)
        for name, value in (("version", version), ("genesis", genesis), ("finality", finality)):
            if not isinstance(value, Mapping):
                raise ProviderResponseError(f"Beacon {name} response is not an object")
        return {
            "version": version.get("data"),
            "genesis": genesis.get("data"),
            "finality": finality.get("data"),
            "bounded": True,
            "validator_registry_scan": False,
        }


__all__ = [
    "DEFAULT_BASE_URL",
    "FINALITY_PATH",
    "GENESIS_PATH",
    "EthereumBeaconProvider",
    "NODE_VERSION_PATH",
]
