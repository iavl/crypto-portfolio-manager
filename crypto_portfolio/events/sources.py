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

    @property
    def source_name(self) -> str:
        return self.name or self.authority

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
        }


EVENT_SOURCE_CATALOG = (
    EventSource(
        "bitcoin-core-security", "security", ("BTC",), "Bitcoin Core", "official",
        "https://github.com/bitcoin/bitcoin/security/advisories", True, name="Bitcoin Core security advisories",
    ),
    EventSource(
        "bitcoin-core-security-advisories", "security", ("BTC",), "Bitcoin Core", "official",
        "https://github.com/bitcoin/bitcoin/releases", True, name="Bitcoin Core repository releases",
    ),
    EventSource(
        "bitcoin-core-releases", "security", ("BTC",), "Bitcoin Core", "official",
        "https://bitcoincore.org/en/releases/", True, name="Bitcoin Core releases",
    ),
    EventSource(
        "ethereum-foundation-security", "security", ("ETH",), "Ethereum Foundation", "official",
        "https://ethereum.org/en/security/", True, name="Ethereum security",
    ),
    EventSource(
        "geth-security-advisories", "security", ("ETH",), "go-ethereum", "official",
        "https://github.com/ethereum/go-ethereum/security/advisories", True, name="go-ethereum advisories",
    ),
    EventSource(
        "ethereum-consensus-security-advisories", "security", ("ETH",), "Ethereum consensus specs", "official",
        "https://github.com/ethereum/consensus-specs/security/advisories", True, name="Ethereum consensus advisories",
    ),
    EventSource(
        "bitcoin-bips", "governance", ("BTC",), "Bitcoin BIPs", "official",
        "https://github.com/bitcoin/bips", True, name="Bitcoin Improvement Proposals",
    ),
    EventSource(
        "bitcoin-core-protocol-releases", "governance", ("BTC",), "Bitcoin Core", "official",
        "https://bitcoincore.org/en/releases/", True, name="Bitcoin Core protocol releases",
    ),
    EventSource(
        "ethereum-eips", "governance", ("ETH",), "Ethereum EIPs", "official",
        "https://eips.ethereum.org/", True, name="Ethereum Improvement Proposals",
    ),
    EventSource(
        "ethereum-all-core-devs", "governance", ("ETH",), "Ethereum PM", "official",
        "https://github.com/ethereum/pm", True, name="Ethereum AllCoreDevs coordination",
    ),
    EventSource(
        "ethereum-foundation-protocol", "governance", ("ETH",), "Ethereum Foundation", "official",
        "https://blog.ethereum.org/", True, name="Ethereum protocol announcements",
    ),
    EventSource(
        "sec-digital-assets", "regulatory", ("MARKET",), "U.S. SEC", "official",
        "https://www.sec.gov/news/pressreleases", True, name="SEC press releases",
    ),
    EventSource(
        "cftc-digital-assets", "regulatory", ("MARKET",), "U.S. CFTC", "official",
        "https://www.cftc.gov/PressRoom/PressReleases", True, name="CFTC press releases",
    ),
    EventSource(
        "esma-mica", "regulatory", ("MARKET",), "ESMA", "official",
        "https://www.esma.europa.eu/press-news/esma-news", True, name="ESMA and MiCA notices",
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


event_sources = source_catalog


def event_sources_for(asset: str, category: str) -> tuple[EventSource, ...]:
    return source_catalog(category, asset)


EVENT_SOURCES = EVENT_SOURCE_CATALOG
get_event_sources = source_catalog


__all__ = [
    "EVENT_CATEGORIES",
    "EVENT_SOURCE_CATALOG",
    "EVENT_SOURCES",
    "EventSource",
    "event_sources",
    "event_sources_for",
    "get_event_sources",
    "source_catalog",
]
