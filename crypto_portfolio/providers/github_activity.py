"""Bounded GitHub public-API provider for canonical developer activity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Mapping
from urllib.parse import quote

from ..metrics_registry import metric_definition
from ..models.time import normalize_timestamp, parse_timestamp
from .base import ProviderCapabilities, ProviderDataError, ProviderRequest, ProviderResponse, ProviderUnsupportedMetric
from .http import HttpClient


BASE_URL = "https://api.github.com"
WINDOW_DAYS = 30
MAX_PAGES_PER_REPOSITORY = 10
PAGE_SIZE = 100
REPOSITORY_ALLOWLIST = {
    "ETH": (
        "ethereum/go-ethereum",
        "ethereum/consensus-specs",
        "ethereum/EIPs",
    ),
    "AAVE": (
        "aave/aave-v3-core",
        "aave/aave-v3-deploy",
    ),
}


def _now(clock: Any | None = None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        value = value.isoformat()
    return normalize_timestamp(value, "fetched_at")


def _commit_timestamp(item: Mapping[str, Any]) -> str | None:
    commit = item.get("commit")
    if not isinstance(commit, Mapping):
        return None
    author = commit.get("author")
    committer = commit.get("committer")
    raw = author.get("date") if isinstance(author, Mapping) else None
    raw = raw or (committer.get("date") if isinstance(committer, Mapping) else None)
    if not isinstance(raw, str):
        return None
    try:
        return normalize_timestamp(raw, "GitHub commit timestamp")
    except ValueError:
        return None


def count_commits(payloads: list[Any], *, start: str, end: str) -> int:
    """Count unique commits from bounded GitHub commit-page payloads."""
    start_time = parse_timestamp(start)
    end_time = parse_timestamp(end)
    seen: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, list):
            raise ProviderDataError("GitHub commits response must be a list")
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            sha = item.get("sha")
            observed = _commit_timestamp(item)
            if not isinstance(sha, str) or not sha.strip() or observed is None:
                continue
            if start_time <= parse_timestamp(observed) <= end_time:
                seen.add(sha.strip())
    return len(seen)


class GitHubActivityProvider:
    name = "github"

    def __init__(
        self,
        *,
        client: HttpClient | Any | None = None,
        clock: Any | None = None,
        token: str | None = None,
    ) -> None:
        self.client = client or HttpClient()
        self.clock = clock
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.capabilities = ProviderCapabilities(
            provider=self.name,
            metric_keys=("fundamentals.developer_activity",),
            historical_series=("fundamentals.developer_activity",),
            supports_batching=False,
            requires_api_key=True,
        )

    def _headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def collect(self, request: ProviderRequest) -> ProviderResponse:
        if request.dataset != "github" or request.metric_keys != ("fundamentals.developer_activity",):
            raise ProviderUnsupportedMetric("GitHub only supports developer activity")
        repositories = REPOSITORY_ALLOWLIST.get(request.asset)
        if not repositories:
            raise ProviderUnsupportedMetric(f"GitHub has no canonical repository allowlist for {request.asset}")
        fetched_at = _now(self.clock)
        end = request.parameters.get("end") or request.parameters.get("as_of") or fetched_at
        # A live review may use a short future fence to absorb clock skew. Do
        # not persist an observation whose fact time is after its fetch time.
        if parse_timestamp(end) > parse_timestamp(fetched_at):
            end = fetched_at
        start = request.parameters.get("start") or normalize_timestamp(
            (parse_timestamp(end) - timedelta(days=WINDOW_DAYS)).isoformat(),
            "start",
        )
        payloads: list[Any] = []
        for repository in repositories:
            for page in range(1, MAX_PAGES_PER_REPOSITORY + 1):
                payload = self.client.get_json(
                    f"{BASE_URL}/repos/{quote(repository, safe='/')}/commits",
                    params={
                        "since": start,
                        "until": end,
                        "per_page": PAGE_SIZE,
                        "page": page,
                    },
                    headers=self._headers(),
                )
                if not isinstance(payload, list):
                    raise ProviderDataError("GitHub commits response must be a list")
                payloads.append(payload)
                if len(payload) < PAGE_SIZE:
                    break
        value = count_commits(payloads, start=start, end=end)
        return ProviderResponse(({
            "asset": request.asset,
            "metric_key": "fundamentals.developer_activity",
            "value": value,
            "unit": metric_definition("fundamentals.developer_activity").unit,
            "period": "30d",
            "observed_at": normalize_timestamp(end, "end"),
            "fetched_at": fetched_at,
            "source": self.name,
            "confidence": "MEDIUM",
            "metadata": {
                "source_dataset": "github_commits",
                "methodology": "unique_commits_on_default_branches_trailing_30d",
                "repositories": list(repositories),
                "window": "30d",
            },
        },))


__all__ = [
    "BASE_URL",
    "GitHubActivityProvider",
    "MAX_PAGES_PER_REPOSITORY",
    "PAGE_SIZE",
    "REPOSITORY_ALLOWLIST",
    "WINDOW_DAYS",
    "count_commits",
]
