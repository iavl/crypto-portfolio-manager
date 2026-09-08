"""Secret-free HTTP response recording and deterministic replay transports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

from .base import ProviderDiagnostic, ProviderResponseError
from .http import redact_secrets, redact_url


_SECRET_HEADER_MARKERS = ("api", "auth", "token", "secret", "cookie", "password")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"recording {field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class RecordingEnvelope:
    provider: str
    method: str
    endpoint: str
    recorded_at: str
    status_code: int
    response: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider").lower())
        object.__setattr__(self, "method", _text(self.method, "method").upper())
        object.__setattr__(self, "endpoint", redact_url(_text(self.endpoint, "endpoint")))
        recorded_at = _text(self.recorded_at, "recorded_at")
        try:
            parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("recording recorded_at must be RFC3339") from exc
        if parsed.tzinfo is None:
            raise ValueError("recording recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", recorded_at)
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("recording status_code must be an HTTP status")
        try:
            json.dumps(self.response, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("recording response must be finite JSON") from exc
        object.__setattr__(self, "response", redact_secrets(self.response))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RecordingEnvelope":
        if not isinstance(value, Mapping):
            raise ValueError("provider recording must be an object")
        required = {"provider", "method", "endpoint", "recorded_at", "status_code", "response"}
        missing = required - set(value)
        if missing:
            raise ValueError(f"provider recording is missing fields: {', '.join(sorted(missing))}")
        unknown = set(value) - required
        if unknown:
            raise ValueError(f"provider recording contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(**{key: value[key] for key in required})

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "method": self.method,
            "endpoint": self.endpoint,
            "recorded_at": self.recorded_at,
            "status_code": self.status_code,
            "response": redact_secrets(self.response),
        }


def _header_secrets(request: Request) -> tuple[str, ...]:
    values = []
    for key, value in request.header_items():
        if any(marker in key.lower() for marker in _SECRET_HEADER_MARKERS):
            values.append(str(value))
    return tuple(values)


def _query_has_secret(url: str) -> bool:
    return any(
        any(marker in key.lower().replace("-", "_") for marker in ("api_key", "apikey", "token", "secret", "password", "authorization"))
        for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)
    )


class _RecordingResponse:
    def __init__(self, response: Any, recorder: "RecordingTransport", request: Request) -> None:
        self._response = response
        self._recorder = recorder
        self._request = request
        self.status = getattr(response, "status", response.getcode() if hasattr(response, "getcode") else 200)
        self.headers = getattr(response, "headers", {})
        self._recorded = False

    def read(self, amount: int = -1) -> bytes:
        try:
            value = self._response.read(amount)
        except TypeError:
            value = self._response.read()
        if not isinstance(value, (bytes, bytearray)):
            return value
        if not self._recorded:
            self._recorded = True
            self._recorder.record(self._request, int(self.status), bytes(value))
        return value


class RecordingTransport:
    """Wrap an urllib-compatible opener and persist only safe JSON responses."""

    def __init__(
        self,
        directory: str | Path,
        *,
        provider: str = "unknown",
        opener: Callable[..., Any] | None = None,
        record_authenticated: bool = False,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.provider = _text(provider, "provider").lower()
        self.opener = opener or urlopen
        self.record_authenticated = record_authenticated
        self.paths: list[Path] = []

    def __call__(self, request: Request, timeout: float, *, context: Any = None) -> Any:
        try:
            parameters = inspect.signature(self.opener).parameters
            accepts_context = "context" in parameters or any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
            accepts_timeout_keyword = "timeout" in parameters or any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_context = False
            accepts_timeout_keyword = True
        if accepts_context:
            response = self.opener(request, timeout=timeout, context=context)
        elif accepts_timeout_keyword:
            response = self.opener(request, timeout=timeout)
        else:
            response = self.opener(request, timeout)
        return _RecordingResponse(response, self, request)

    def record(self, request: Request, status_code: int, body: bytes) -> Path | None:
        secrets = _header_secrets(request)
        if (secrets or _query_has_secret(request.full_url)) and not self.record_authenticated:
            return None
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        endpoint = redact_url(request.full_url, secrets)
        envelope = RecordingEnvelope(
            provider=self.provider,
            method=request.method or "GET",
            endpoint=endpoint,
            recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            status_code=status_code,
            response=redact_secrets(response, secrets),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{envelope.method} {envelope.endpoint}".encode()).hexdigest()[:16]
        path = self.directory / f"{envelope.provider}-{digest}.json"
        path.write_text(json.dumps(envelope.as_dict(), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        if path not in self.paths:
            self.paths.append(path)
        return path


class _ReplayResponse:
    def __init__(self, envelope: RecordingEnvelope) -> None:
        self.status = envelope.status_code
        self.headers = {}
        self._body = json.dumps(envelope.response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def read(self, _amount: int = -1) -> bytes:
        return self._body


class ReplayTransport:
    """Serve one recording to the existing HttpClient without network access."""

    def __init__(self, path: str | Path) -> None:
        self.envelope = load_recording(path)

    def __call__(self, request: Request, timeout: float, *, context: Any = None) -> _ReplayResponse:
        endpoint = redact_url(request.full_url)
        method = (request.method or "GET").upper()
        if endpoint != self.envelope.endpoint or method != self.envelope.method:
            raise ProviderResponseError(
                "replay request does not match recording",
                diagnostic=ProviderDiagnostic(
                    endpoint=self.envelope.endpoint,
                    method=method,
                    error_code="PROVIDER_SCHEMA_ERROR",
                    detail="replay request does not match recording",
                ),
            )
        return _ReplayResponse(self.envelope)


def load_recording(path: str | Path) -> RecordingEnvelope:
    recording_path = Path(path).expanduser()
    value = json.loads(recording_path.read_text(encoding="utf-8"))
    return RecordingEnvelope.from_mapping(value)


__all__ = [
    "RecordingEnvelope",
    "RecordingTransport",
    "ReplayTransport",
    "load_recording",
]
