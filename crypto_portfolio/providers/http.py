"""Small bounded stdlib-only JSON HTTP client for public providers."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
import json
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .base import (
    ProviderAuthenticationError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
)


_SECRET_NAMES = {"api_key", "apikey", "api-secret", "api_secret", "authorization", "cookie", "password", "secret", "token"}


def _secret_name(value: Any) -> bool:
    name = str(value).strip().lower().replace("-", "_")
    return name in _SECRET_NAMES or "api_key" in name or name.endswith(("_secret", "_token")) or "authorization" in name


def redact_secrets(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Return a JSON-shaped value with secret-bearing fields redacted."""
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if _secret_name(key) else redact_secrets(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        result = re.sub(r"(?i)Bearer\s+[^,\s]+", "Bearer [REDACTED]", result)
        result = re.sub(
            r"(?i)((?:api[_-]?key|api[_-]?secret|authorization|password|token|secret)\s*[:=]\s*)[^,\s]+",
            r"\1[REDACTED]",
            result,
        )
        return result
    return value


def redact_url(url: str, secrets: tuple[str, ...] = ()) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if _secret_name(key) else redact_secrets(value, secrets)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _retry_after(headers: Any) -> float | None:
    value = None
    if headers is not None:
        try:
            value = headers.get("Retry-After")
        except AttributeError:
            value = None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(value))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class HttpClient:
    """GET-only JSON client with bounded retries and response-size checks."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: float = 12.0,
        max_attempts: int = 3,
        max_response_bytes: int = 5_000_000,
        user_agent: str = "crypto-portfolio-manager/0.1",
        sleeper: Callable[[float], None] = time.sleep,
        backoff_seconds: float = 0.25,
    ) -> None:
        if isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be > 0")
        if isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if isinstance(max_response_bytes, bool) or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")
        self.opener = opener or urlopen
        self.timeout = float(timeout)
        self.max_attempts = int(max_attempts)
        self.max_response_bytes = int(max_response_bytes)
        self.user_agent = user_agent
        self.sleeper = sleeper
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.request_count = 0

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
            raise ProviderResponseError("provider URL must use http or https")
        if params:
            encoded = [(key, value) for key, value in params.items() if value is not None]
            separator = "&" if urlsplit(url).query else "?"
            url = url + (separator + urlencode(encoded) if encoded else "")
        safe_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        safe_headers.setdefault("Accept", "application/json")
        safe_headers.setdefault("User-Agent", self.user_agent)
        request = Request(redact_url(url), headers=safe_headers, method="GET")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            self.request_count += 1
            try:
                response = self.opener(request, timeout=self.timeout)
                status = int(getattr(response, "status", response.getcode() if hasattr(response, "getcode") else 200))
                if status >= 400:
                    raise HTTPError(url, status, f"HTTP {status}", getattr(response, "headers", None), None)
                raw = self._read(response)
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProviderResponseError("provider returned invalid UTF-8 JSON") from exc
            except HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if not retryable or attempt + 1 >= self.max_attempts:
                    if exc.code == 429:
                        raise ProviderRateLimited(f"provider rate limited request ({exc.code})") from exc
                    if 500 <= exc.code <= 599:
                        raise ProviderUnavailable(f"provider server error ({exc.code})") from exc
                    if exc.code in {401, 403}:
                        raise ProviderAuthenticationError(f"provider authentication rejected ({exc.code})") from exc
                    raise ProviderResponseError(f"provider request failed ({exc.code})") from exc
                self._sleep(attempt, getattr(exc, "headers", None))
            except (TimeoutError, socket.timeout, URLError, OSError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    raise ProviderUnavailable("provider network request failed") from exc
                self._sleep(attempt, None)
            except ProviderResponseError:
                raise
        raise ProviderUnavailable("provider request failed") from last_error

    get = get_json

    def _read(self, response: Any) -> bytes:
        headers = getattr(response, "headers", None)
        try:
            content_length = headers.get("Content-Length") if headers is not None else None
            if content_length is not None and int(content_length) > self.max_response_bytes:
                raise ProviderResponseError("provider response exceeds size limit")
        except (TypeError, ValueError):
            pass
        try:
            raw = response.read(self.max_response_bytes + 1)
        except TypeError:
            raw = response.read()
        if not isinstance(raw, (bytes, bytearray)):
            raise ProviderResponseError("provider response body is not bytes")
        if len(raw) > self.max_response_bytes:
            raise ProviderResponseError("provider response exceeds size limit")
        return bytes(raw)

    def _sleep(self, attempt: int, headers: Any) -> None:
        delay = _retry_after(headers)
        if delay is None:
            delay = self.backoff_seconds * (2**attempt)
        self.sleeper(min(max(0.0, delay), 30.0))


__all__ = [
    "HttpClient",
    "ProviderAuthenticationError",
    "ProviderRateLimited",
    "ProviderResponseError",
    "ProviderUnavailable",
    "redact_secrets",
    "redact_url",
]
