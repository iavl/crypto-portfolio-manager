"""Small bounded stdlib-only JSON HTTP client for public providers."""

from __future__ import annotations

from email.utils import parsedate_to_datetime
import errno
import inspect
import json
import os
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .base import (
    ProviderAuthenticationError,
    ProviderDiagnostic,
    ProviderError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderUnavailable,
    ProviderUnsupportedMetric,
)


_SECRET_NAMES = {"api_key", "apikey", "api-secret", "api_secret", "authorization", "cookie", "password", "secret", "token"}
TRANSPORT_ERROR_CODES = (
    "TLS_CERTIFICATE_VERIFY_FAILED",
    "TLS_HANDSHAKE_FAILED",
    "DNS_RESOLUTION_FAILED",
    "CONNECT_TIMEOUT",
    "READ_TIMEOUT",
    "CONNECTION_REFUSED",
    "CONNECTION_RESET",
    "PROXY_ERROR",
    "HTTP_400",
    "HTTP_401",
    "HTTP_403",
    "HTTP_404",
    "HTTP_429",
    "HTTP_5XX",
    "INVALID_JSON",
    "RESPONSE_TOO_LARGE",
    "PROVIDER_PLAN_RESTRICTED",
    "PROVIDER_UNSUPPORTED",
    "PROVIDER_SCHEMA_ERROR",
    "UNKNOWN_NETWORK_ERROR",
)


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


def _readable_path(value: str | Path, field: str, *, directory: bool = False) -> Path:
    path = Path(value).expanduser()
    exists = path.is_dir() if directory else path.is_file()
    if not exists:
        kind = "directory" if directory else "file"
        raise ValueError(f"{field} must point to an existing readable {kind}")
    if not os.access(path, os.R_OK):
        raise ValueError(f"{field} is not readable")
    return path


def _verified_context(context: ssl.SSLContext) -> ssl.SSLContext:
    if not isinstance(context, ssl.SSLContext):
        raise TypeError("ssl_context must be an ssl.SSLContext")
    if context.verify_mode == ssl.CERT_NONE or not context.check_hostname:
        raise ValueError("ssl_context must require certificate verification and hostname checks")
    return context


def build_ssl_context(
    ssl_context: ssl.SSLContext | None = None,
    *,
    ca_bundle: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ssl.SSLContext:
    """Build an HTTPS context without permitting verification to be disabled."""
    if ssl_context is not None:
        return _verified_context(ssl_context)
    environment = environ if environ is not None else os.environ
    configured_bundle = ca_bundle or environment.get("CRYPTO_PORTFOLIO_CA_BUNDLE")
    cert_file = environment.get("SSL_CERT_FILE")
    cert_dir = environment.get("SSL_CERT_DIR")
    try:
        if configured_bundle:
            bundle = _readable_path(configured_bundle, "CRYPTO_PORTFOLIO_CA_BUNDLE")
            context = ssl.create_default_context(cafile=str(bundle))
        elif cert_file or cert_dir:
            file_path = _readable_path(cert_file, "SSL_CERT_FILE") if cert_file else None
            dir_path = _readable_path(cert_dir, "SSL_CERT_DIR", directory=True) if cert_dir else None
            context = ssl.create_default_context()
            context.load_verify_locations(
                cafile=str(file_path) if file_path else None,
                capath=str(dir_path) if dir_path else None,
            )
        else:
            context = ssl.create_default_context()
    except (OSError, ssl.SSLError) as exc:
        source = "CRYPTO_PORTFOLIO_CA_BUNDLE" if configured_bundle else "SSL_CERT_FILE/SSL_CERT_DIR"
        raise ValueError(f"invalid {source} trust configuration: {redact_secrets(str(exc))}") from exc
    return _verified_context(context)


def _reason(error: BaseException) -> Any:
    return getattr(error, "reason", None)


def classify_transport_error(error: BaseException, *, phase: str | None = None) -> str:
    """Return a stable, non-secret error code for a transport/provider failure."""
    diagnostic = getattr(error, "diagnostic", None)
    if isinstance(diagnostic, ProviderDiagnostic):
        return diagnostic.error_code
    if isinstance(diagnostic, Mapping) and diagnostic.get("error_code"):
        return str(diagnostic["error_code"]).strip().upper()
    if isinstance(error, HTTPError):
        if error.code == 429:
            return "HTTP_429"
        if 400 <= error.code <= 499:
            return f"HTTP_{error.code}"
        if 500 <= error.code <= 599:
            return "HTTP_5XX"
    nested = _reason(error)
    candidates = (error, nested) if nested is not None else (error,)
    if any(isinstance(item, (ssl.SSLCertVerificationError, ssl.CertificateError)) for item in candidates):
        return "TLS_CERTIFICATE_VERIFY_FAILED"
    text = " ".join(str(item) for item in candidates if item is not None).lower()
    if "certificate_verify_failed" in text or "certificate verify failed" in text or "unable to get local issuer" in text or "self signed certificate" in text:
        return "TLS_CERTIFICATE_VERIFY_FAILED"
    if any(isinstance(item, ssl.SSLError) for item in candidates):
        return "TLS_HANDSHAKE_FAILED"
    if isinstance(nested, socket.gaierror) or isinstance(error, socket.gaierror):
        return "DNS_RESOLUTION_FAILED"
    if any(value in text for value in ("name or service not known", "nodename nor servname", "temporary failure in name resolution", "getaddrinfo failed")):
        return "DNS_RESOLUTION_FAILED"
    if phase and phase.strip().lower() in {"read", "response"} and isinstance(error, (TimeoutError, socket.timeout)):
        return "READ_TIMEOUT"
    if isinstance(error, (TimeoutError, socket.timeout)) or isinstance(nested, (TimeoutError, socket.timeout)):
        return "CONNECT_TIMEOUT"
    if isinstance(error, ConnectionRefusedError) or isinstance(nested, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    if isinstance(error, (ConnectionResetError, RemoteDisconnected)) or isinstance(nested, (ConnectionResetError, RemoteDisconnected)):
        return "CONNECTION_RESET"
    if isinstance(error, URLError) and any(value in text for value in ("proxy", "tunnel", "407")):
        return "PROXY_ERROR"
    if isinstance(error, ProviderUnsupportedMetric):
        return "PROVIDER_PLAN_RESTRICTED" if any(value in text for value in ("plan", "tier", "permission", "subscription", "upgrade")) else "PROVIDER_UNSUPPORTED"
    if isinstance(error, ProviderAuthenticationError):
        return "HTTP_401"
    if isinstance(error, ProviderResponseError):
        if "json" in text or "utf-8" in text:
            return "INVALID_JSON"
        if "size limit" in text or "too large" in text:
            return "RESPONSE_TOO_LARGE"
        return "PROVIDER_SCHEMA_ERROR"
    if isinstance(error, ProviderError):
        return "UNKNOWN_NETWORK_ERROR" if isinstance(error, ProviderUnavailable) else "PROVIDER_SCHEMA_ERROR"
    if isinstance(error, OSError) and getattr(error, "errno", None) == errno.ECONNREFUSED:
        return "CONNECTION_REFUSED"
    if isinstance(error, OSError) and getattr(error, "errno", None) == errno.ECONNRESET:
        return "CONNECTION_RESET"
    return "UNKNOWN_NETWORK_ERROR"


def _retryable(error_code: str) -> bool:
    return error_code in {
        "HTTP_429", "HTTP_5XX", "DNS_RESOLUTION_FAILED", "CONNECT_TIMEOUT", "READ_TIMEOUT",
        "CONNECTION_REFUSED", "CONNECTION_RESET", "PROXY_ERROR", "UNKNOWN_NETWORK_ERROR",
    }


def _detail(error: BaseException, secrets: tuple[str, ...] = ()) -> str:
    return redact_secrets(str(error), secrets).strip() or error.__class__.__name__


def _diagnostic(
    error: BaseException,
    *,
    endpoint: str,
    method: str = "GET",
    attempt: int,
    phase: str | None = None,
    status_code: int | None = None,
    error_code: str | None = None,
    secrets: tuple[str, ...] = (),
    retryable: bool | None = None,
) -> ProviderDiagnostic:
    code = error_code or classify_transport_error(error, phase=phase)
    return ProviderDiagnostic(
        endpoint=redact_url(endpoint, secrets),
        method=method,
        attempt=attempt,
        error_code=code,
        exception_class=error.__class__.__name__,
        detail=_detail(error, secrets),
        retryable=_retryable(code) if retryable is None else bool(retryable and _retryable(code)),
        status_code=status_code,
    )


def _with_diagnostic(
    error: ProviderError,
    *,
    endpoint: str,
    method: str = "GET",
    attempt: int,
    phase: str | None = None,
    status_code: int | None = None,
    secrets: tuple[str, ...] = (),
) -> ProviderError:
    diagnostic = _diagnostic(
        error,
        endpoint=endpoint,
        method=method,
        attempt=attempt,
        phase=phase,
        status_code=status_code,
        secrets=secrets,
    )
    if isinstance(error.diagnostic, ProviderDiagnostic):
        diagnostic = error.diagnostic
    return error.__class__(str(error), diagnostic=diagnostic)


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


classify_error = classify_transport_error
classify_exception = classify_transport_error


class HttpClient:
    """Small JSON client with bounded retries and response-size checks."""

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
        ssl_context: ssl.SSLContext | None = None,
        ca_bundle: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be > 0")
        if isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if isinstance(max_response_bytes, bool) or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")
        self.opener = opener
        self.timeout = float(timeout)
        self.max_attempts = int(max_attempts)
        self.max_response_bytes = int(max_response_bytes)
        self.user_agent = user_agent
        self.sleeper = sleeper
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.environ = dict(environ if environ is not None else os.environ)
        self.ssl_context = build_ssl_context(ssl_context, ca_bundle=ca_bundle, environ=self.environ)
        self.ca_source = (
            "explicit_context" if ssl_context is not None else
            "configured" if ca_bundle or self.environ.get("CRYPTO_PORTFOLIO_CA_BUNDLE") else
            "environment" if self.environ.get("SSL_CERT_FILE") or self.environ.get("SSL_CERT_DIR") else
            "default"
        )
        self.request_count = 0

    def transport_metadata(self) -> dict[str, Any]:
        """Return safe local TLS/proxy metadata for diagnostic output."""
        return {
            "python_ssl": ssl.OPENSSL_VERSION,
            "ca_source": self.ca_source,
            "proxy": "detected" if any(self.environ.get(name) for name in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")) else "none",
            "verify_mode": self.ssl_context.verify_mode.name if hasattr(self.ssl_context.verify_mode, "name") else str(self.ssl_context.verify_mode),
            "check_hostname": self.ssl_context.check_hostname,
        }

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool = False,
    ) -> Any:
        if not isinstance(method, str) or not method.strip():
            raise ValueError("provider HTTP method must be a non-empty string")
        method = method.strip().upper()
        if not isinstance(idempotent, bool):
            raise ValueError("provider request idempotent must be boolean")
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
            raise ProviderResponseError("provider URL must use http or https")
        if params:
            encoded = [(key, value) for key, value in params.items() if value is not None]
            separator = "&" if urlsplit(url).query else "?"
            url = url + (separator + urlencode(encoded) if encoded else "")
        safe_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        safe_headers.setdefault("Accept", "application/json")
        safe_headers.setdefault("User-Agent", self.user_agent)
        body = None
        if json_body is not None:
            try:
                body = json.dumps(
                    json_body,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    "provider request body is not finite JSON",
                    diagnostic=_diagnostic(
                        exc,
                        endpoint=url,
                        method=method,
                        attempt=1,
                        error_code="PROVIDER_SCHEMA_ERROR",
                    ),
                ) from exc
            safe_headers.setdefault("Content-Type", "application/json")
        request_secrets = tuple(
            value for key, value in safe_headers.items()
            if _secret_name(key)
        )
        request = Request(redact_url(url, request_secrets), data=body, headers=safe_headers, method=method)
        retry_allowed = method == "GET" or idempotent
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            self.request_count += 1
            try:
                response = self._open(request)
                status = int(getattr(response, "status", response.getcode() if hasattr(response, "getcode") else 200))
                if status >= 400:
                    raise HTTPError(request.full_url, status, f"HTTP {status}", getattr(response, "headers", None), None)
                raw = self._read(response)
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProviderResponseError(
                        "provider returned invalid UTF-8 JSON",
                        diagnostic=_diagnostic(
                            exc,
                            endpoint=request.full_url,
                            method=method,
                            attempt=attempt + 1,
                            error_code="INVALID_JSON",
                            secrets=request_secrets,
                        ),
                    ) from exc
            except HTTPError as exc:
                last_error = exc
                retryable = retry_allowed and (exc.code == 429 or 500 <= exc.code <= 599)
                if not retryable or attempt + 1 >= self.max_attempts:
                    diagnostic = _diagnostic(
                        exc,
                        endpoint=request.full_url,
                        method=method,
                        attempt=attempt + 1,
                        status_code=exc.code,
                        secrets=request_secrets,
                        retryable=retry_allowed,
                    )
                    if exc.code == 429:
                        raise ProviderRateLimited(f"provider rate limited request ({exc.code})", diagnostic=diagnostic) from exc
                    if 500 <= exc.code <= 599:
                        raise ProviderUnavailable(f"provider server error ({exc.code})", diagnostic=diagnostic) from exc
                    if exc.code in {401, 403}:
                        raise ProviderAuthenticationError(f"provider authentication rejected ({exc.code})", diagnostic=diagnostic) from exc
                    raise ProviderResponseError(f"provider request failed ({exc.code})", diagnostic=diagnostic) from exc
                self._sleep(attempt, getattr(exc, "headers", None))
            except (TimeoutError, socket.timeout, URLError, OSError) as exc:
                last_error = exc
                retryable = retry_allowed and _retryable(classify_transport_error(exc))
                if not retryable or attempt + 1 >= self.max_attempts:
                    diagnostic = _diagnostic(
                        exc,
                        endpoint=request.full_url,
                        method=method,
                        attempt=attempt + 1,
                        secrets=request_secrets,
                        retryable=retry_allowed,
                    )
                    raise ProviderUnavailable(
                        f"provider network request failed: {diagnostic.error_code}: {diagnostic.detail}",
                        diagnostic=diagnostic,
                    ) from exc
                self._sleep(attempt, None)
            except ProviderResponseError as exc:
                if exc.diagnostic is None:
                    raise _with_diagnostic(
                        exc,
                        endpoint=request.full_url,
                        method=method,
                        attempt=attempt + 1,
                        secrets=request_secrets,
                    ) from exc
                raise
        raise ProviderUnavailable("provider request failed") from last_error

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        return self.request_json("GET", url, params=params, headers=headers)

    def post_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool = False,
    ) -> Any:
        return self.request_json(
            "POST",
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            idempotent=idempotent,
        )

    def _open(self, request: Request) -> Any:
        if self.opener is not None:
            try:
                signature = inspect.signature(self.opener)
                parameters = signature.parameters
                accepts_context = "context" in parameters or any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
                )
            except (TypeError, ValueError):
                accepts_context = False
            if accepts_context:
                return self.opener(request, timeout=self.timeout, context=self.ssl_context)
            return self.opener(request, timeout=self.timeout)
        return urlopen(request, timeout=self.timeout, context=self.ssl_context)

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
    "TRANSPORT_ERROR_CODES",
    "build_ssl_context",
    "classify_error",
    "classify_exception",
    "classify_transport_error",
    "ProviderAuthenticationError",
    "ProviderRateLimited",
    "ProviderResponseError",
    "ProviderUnavailable",
    "redact_secrets",
    "redact_url",
]
