"""Canonical, allowlisted sources for on-demand event scans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


EVENT_CATEGORIES = ("security", "governance", "regulatory")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class EventSource:
    """One fixed source that the runtime may request for an event scan."""

    id: str
    category: str
    asset_scope: tuple[str, ...]
    authority: str
    source_type: str
    url: str
    required_for_full_coverage: bool
    tier: int = 1
    name: str | None = None
    transport_urls: tuple[str, ...] = ()
    source_group: str | None = None
    transport_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "event source id").lower())
        category = _text(self.category, "event source category").lower()
        if category not in EVENT_CATEGORIES:
            raise ValueError(f"event source category must be one of {list(EVENT_CATEGORIES)}")
        object.__setattr__(self, "category", category)
        if isinstance(self.asset_scope, str) or not isinstance(self.asset_scope, (tuple, list)):
            raise ValueError("event source asset_scope must be a sequence")
        scope = tuple(_text(item, "event source asset") for item in self.asset_scope)
        scope = tuple(item.upper() for item in scope)
        if not scope or len(scope) != len(set(scope)):
            raise ValueError("event source asset_scope must be non-empty and unique")
        object.__setattr__(self, "asset_scope", scope)
        object.__setattr__(self, "authority", _text(self.authority, "event source authority"))
        object.__setattr__(self, "source_type", _text(self.source_type, "event source type").lower())
        url = _text(self.url, "event source url")
        if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise ValueError("event source url must use http or https")
        object.__setattr__(self, "url", url)
        if not isinstance(self.required_for_full_coverage, bool):
            raise ValueError("event source required_for_full_coverage must be boolean")
        if isinstance(self.tier, bool) or not isinstance(self.tier, int) or self.tier not in {1, 2, 3}:
            raise ValueError("event source tier must be 1, 2, or 3")
        if self.name is not None:
            object.__setattr__(self, "name", _text(self.name, "event source name"))
        if isinstance(self.transport_urls, str) or not isinstance(self.transport_urls, (tuple, list)):
            raise ValueError("event source transport_urls must be a sequence")
        transports = tuple(_text(item, "event source transport url") for item in self.transport_urls)
        for transport in transports:
            parts = urlsplit(transport)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ValueError("event source transport url must use http or https")
        object.__setattr__(self, "transport_urls", tuple(dict.fromkeys((self.url, *transports))))
        group = self.authority if self.source_group is None else self.source_group
        object.__setattr__(self, "source_group", _text(group, "event source group").lower())
        if self.transport_kind is not None:
            kind = _text(self.transport_kind, "event source transport kind").upper()
            if kind not in {
                "GITHUB_RELEASES", "GITHUB_SECURITY_ADVISORIES", "GITHUB_COMMITS",
                "RSS_ATOM", "DISCOURSE_JSON", "RPC_LOGS", "WEB",
            }:
                raise ValueError("event source transport kind is unsupported")
            object.__setattr__(self, "transport_kind", kind)

    @property
    def source_name(self) -> str:
        return self.name or self.authority

    @property
    def transport_candidates(self) -> tuple[str, ...]:
        return self.transport_urls

    def applies_to(self, asset: str) -> bool:
        symbol = _text(asset, "asset").upper()
        return symbol in self.asset_scope or "MARKET" in self.asset_scope

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "asset_scope": list(self.asset_scope),
            "authority": self.authority,
            "source_type": self.source_type,
            "url": self.url,
            "required_for_full_coverage": self.required_for_full_coverage,
            "tier": self.tier,
            "name": self.name,
            "transport_urls": list(self.transport_urls),
            "source_group": self.source_group,
            "transport_kind": self.transport_kind,
        }


EVENT_SOURCE_CATALOG = (
    EventSource(
        "bitcoin-core-security", "security", ("BTC",), "Bitcoin Core", "official",
        "https://github.com/bitcoin/bitcoin/security/advisories", True, name="Bitcoin Core security advisories",
        transport_kind="GITHUB_SECURITY_ADVISORIES", source_group="bitcoin-core-security",
    ),
    EventSource(
        "bitcoin-core-security-advisories", "security", ("BTC",), "Bitcoin Core", "official",
        "https://github.com/bitcoin/bitcoin/releases", True, name="Bitcoin Core repository releases",
        transport_kind="GITHUB_RELEASES", source_group="bitcoin-core-security",
    ),
    EventSource(
        "bitcoin-core-releases", "security", ("BTC",), "Bitcoin Core", "official",
        "https://bitcoincore.org/en/releases/", True, name="Bitcoin Core releases",
        transport_urls=("https://bitcoincore.org/en/feed.xml",), transport_kind="RSS_ATOM", source_group="bitcoin-core-security",
    ),
    EventSource(
        "ethereum-foundation-security", "security", ("ETH",), "Ethereum Foundation", "official",
        "https://ethereum.org/en/security/", True, name="Ethereum Foundation security announcements",
        transport_urls=("https://blog.ethereum.org/feed.xml",), transport_kind="RSS_ATOM",
        source_group="ethereum-foundation-security",
    ),
    EventSource(
        "geth-security-advisories", "security", ("ETH",), "go-ethereum", "official",
        "https://github.com/ethereum/go-ethereum/security/advisories", True, name="go-ethereum advisories",
        transport_kind="GITHUB_SECURITY_ADVISORIES", source_group="geth-security",
    ),
    EventSource(
        "ethereum-consensus-security-advisories", "security", ("ETH",), "Ethereum consensus specs", "official",
        "https://github.com/ethereum/consensus-specs/security/advisories", True, name="Ethereum consensus advisories",
        transport_kind="GITHUB_SECURITY_ADVISORIES", source_group="ethereum-consensus-security",
    ),
    EventSource(
        "bitcoin-bips", "governance", ("BTC",), "Bitcoin BIPs", "official",
        "https://github.com/bitcoin/bips", True, name="Bitcoin Improvement Proposals",
        transport_kind="GITHUB_COMMITS", source_group="bitcoin-governance",
    ),
    EventSource(
        "bitcoin-core-protocol-releases", "governance", ("BTC",), "Bitcoin Core", "official",
        "https://bitcoincore.org/en/releases/", True, name="Bitcoin Core protocol releases", source_group="bitcoin-core-governance",
        transport_urls=("https://bitcoincore.org/en/feed.xml",), transport_kind="RSS_ATOM",
    ),
    EventSource(
        "ethereum-eips", "governance", ("ETH",), "Ethereum EIPs", "official",
        "https://eips.ethereum.org/", True, name="Ethereum Improvement Proposals",
        transport_urls=("https://github.com/ethereum/EIPs",), transport_kind="GITHUB_COMMITS",
        source_group="ethereum-eips",
    ),
    EventSource(
        "ethereum-all-core-devs", "governance", ("ETH",), "Ethereum PM", "official",
        "https://github.com/ethereum/pm", True, name="Ethereum AllCoreDevs coordination",
        transport_kind="GITHUB_COMMITS", source_group="ethereum-all-core-devs",
    ),
    EventSource(
        "ethereum-foundation-protocol", "governance", ("ETH",), "Ethereum Foundation", "official",
        "https://blog.ethereum.org/", True, name="Ethereum protocol announcements", source_group="ethereum-foundation-protocol",
        transport_urls=("https://blog.ethereum.org/feed.xml",), transport_kind="RSS_ATOM",
    ),
    EventSource(
        "aave-security", "security", ("AAVE",), "Aave", "official",
        "https://aave.com/security", True, name="Aave security and risk incidents",
        transport_urls=(
            "https://governance.aave.com/c/risk/7.json",
            "https://governance.aave.com/c/risk/general/12.json",
        ), transport_kind="DISCOURSE_JSON", source_group="aave-security",
    ),
    EventSource(
        "aave-v3-security-advisories", "security", ("AAVE",), "Aave", "official",
        "https://github.com/aave/aave-v3-core/security/advisories", True, name="Aave V3 security advisories",
        transport_kind="GITHUB_SECURITY_ADVISORIES", source_group="aave-security",
    ),
    EventSource(
        "bnb-bsc-security-advisories", "security", ("BNB",), "BNB Chain", "official",
        "https://github.com/bnb-chain/bsc/security/advisories", True, name="BNB Smart Chain security advisories",
        transport_kind="GITHUB_SECURITY_ADVISORIES", source_group="bnb-security",
    ),
    EventSource(
        "bnb-bsc-releases", "security", ("BNB",), "BNB Chain", "official",
        "https://www.bnbchain.org/en/releases", True, name="BNB Chain release notes", source_group="bnb-security",
    ),
    EventSource(
        "aave-governance-forum", "governance", ("AAVE",), "Aave governance", "official",
        "https://governance.aave.com/", True, name="Aave governance forum",
        transport_urls=("https://governance.aave.com/latest.json",), transport_kind="DISCOURSE_JSON", source_group="aave-governance",
    ),
    EventSource(
        "aave-governance-proposals", "governance", ("AAVE",), "Aave governance", "official",
        "https://governance.aave.com/c/governance/4", True, name="Aave governance proposals",
        transport_urls=("https://governance.aave.com/c/governance/4.json",), transport_kind="DISCOURSE_JSON", source_group="aave-governance",
    ),
    EventSource(
        "bnb-beps", "governance", ("BNB",), "BNB Chain BEPs", "official",
        "https://github.com/bnb-chain/BEPs", True, name="BNB Evolution Proposals",
        transport_kind="GITHUB_COMMITS", source_group="bnb-governance",
    ),
    EventSource(
        "bnb-governance", "governance", ("BNB",), "BNB Chain", "official",
        "https://www.bnbchain.org/en/bnb-chain-governance", True, name="BNB Chain governance", source_group="bnb-governance",
    ),
    EventSource(
        "bnb-governor-rpc", "governance", ("BNB",), "BNB Chain", "official",
        "https://bsc-dataseed.bnbchain.org", True, name="BNB Governor proposal events",
        transport_urls=("https://bsc-dataseed-public.bnbchain.org",),
        transport_kind="RPC_LOGS", source_group="bnb-governance",
    ),
    EventSource(
        "sec-digital-assets", "regulatory", ("MARKET",), "U.S. SEC", "official",
        "https://www.sec.gov/news/pressreleases", True, name="SEC press releases",
        transport_urls=("https://www.sec.gov/news/pressreleases.rss",), transport_kind="RSS_ATOM", source_group="sec-regulatory",
    ),
    EventSource(
        "cftc-digital-assets", "regulatory", ("MARKET",), "U.S. CFTC", "official",
        "https://www.cftc.gov/PressRoom/PressReleases", True, name="CFTC press releases",
        transport_urls=(
            "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
            "https://www.cftc.gov/RSS/RSSENF/rssenf.xml",
        ), source_group="cftc-regulatory",
    ),
    EventSource(
        "esma-mica", "regulatory", ("MARKET",), "ESMA", "official",
        "https://www.esma.europa.eu/press-news/esma-news", True, name="ESMA and MiCA notices",
        transport_urls=("https://www.esma.europa.eu/rss.xml",), transport_kind="RSS_ATOM",
        source_group="esma-regulatory",
    ),
)


def source_catalog(category: str | None = None, asset: str | None = None, *, required_only: bool = False) -> tuple[EventSource, ...]:
    normalized_category = None if category is None else _text(category, "event category").lower()
    if normalized_category is not None and normalized_category not in EVENT_CATEGORIES:
        raise ValueError(f"event category must be one of {list(EVENT_CATEGORIES)}")
    normalized_asset = None if asset is None else _text(asset, "asset").upper()
    return tuple(
        source for source in EVENT_SOURCE_CATALOG
        if (normalized_category is None or source.category == normalized_category)
        and (normalized_asset is None or source.applies_to(normalized_asset))
        and (not required_only or source.required_for_full_coverage)
    )


__all__ = [
    "EVENT_CATEGORIES",
    "EVENT_SOURCE_CATALOG",
    "EventSource",
    "source_catalog",
]
