"""Validated provider preferences with optional user-local overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


_ROOT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "data-providers.json"
_DEFAULT_LOCAL_CONFIG = Path.home() / ".config" / "crypto-portfolio-manager" / "data-providers.json"
_SECRET_FIELDS = {"api_key", "api_secret", "authorization", "password", "token"}


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read provider config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("provider config must be an object")
    return value


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), Mapping) and isinstance(value, Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate(config: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(config)
    version = result.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError("provider config version must be 1")
    providers = result.get("providers", {})
    if not isinstance(providers, Mapping):
        raise ValueError("provider config providers must be an object")
    normalized_providers: dict[str, dict[str, Any]] = {}
    for raw_name, raw_settings in providers.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("provider names must be non-empty strings")
        if not isinstance(raw_settings, Mapping):
            raise ValueError(f"provider {raw_name} settings must be an object")
        settings = dict(raw_settings)
        enabled = settings.get("enabled", True)
        if not isinstance(enabled, bool) and (not isinstance(enabled, str) or enabled.strip().upper() != "AUTO"):
            raise ValueError(f"provider {raw_name} enabled must be boolean or AUTO")
        settings["enabled"] = enabled.strip().upper() if isinstance(enabled, str) else enabled
        if "api_key_env" in settings:
            if not isinstance(settings["api_key_env"], str) or not settings["api_key_env"].strip():
                raise ValueError(f"provider {raw_name} api_key_env must be a non-empty string")
            settings["api_key_env"] = settings["api_key_env"].strip()
        if any(str(key).strip().lower() in _SECRET_FIELDS for key in settings):
            raise ValueError(f"provider {raw_name} config must not contain secret values")
        normalized_providers[raw_name.strip().lower()] = settings
    result["version"] = version
    result["providers"] = normalized_providers

    ttl = result.get("cache_ttl_seconds", {})
    if not isinstance(ttl, Mapping):
        raise ValueError("provider cache_ttl_seconds must be an object")
    normalized_ttl: dict[str, int] = {}
    for name, value in ttl.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("provider cache TTLs must be positive integers")
        normalized_ttl[str(name).strip().lower()] = value
    result["cache_ttl_seconds"] = normalized_ttl

    network = result.get("network", {})
    if not isinstance(network, Mapping):
        raise ValueError("provider network settings must be an object")
    normalized_network: dict[str, int] = {}
    for name in ("max_requests_per_review", "max_requests_per_provider"):
        value = network.get(name, 60 if name.endswith("review") else 30)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"provider network {name} must be a positive integer")
        normalized_network[name] = value
    result["network"] = normalized_network

    fallback = result.get("fallback", {})
    if not isinstance(fallback, Mapping) or not isinstance(fallback.get("allow_web", True), bool):
        raise ValueError("provider fallback allow_web must be boolean")
    result["fallback"] = {"allow_web": fallback.get("allow_web", True)}
    return result


def load_provider_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load repository defaults and merge user-local configuration."""
    config = _read(Path(path)) if path is not None else _read(_ROOT_CONFIG)
    candidates: list[Path] = []
    if path is None and _DEFAULT_LOCAL_CONFIG.exists():
        candidates.append(_DEFAULT_LOCAL_CONFIG)
    custom = os.environ.get("CRYPTO_PORTFOLIO_PROVIDER_CONFIG")
    if custom:
        custom_path = Path(custom).expanduser()
        if not custom_path.exists():
            raise ValueError(f"provider config override does not exist: {custom_path}")
        candidates.append(custom_path)
    elif path is not None:
        # An explicit path is a complete caller-selected configuration.
        candidates = []
    for candidate in candidates:
        if candidate.exists():
            config = _merge(config, _read(candidate))
    return _validate(config)


def provider_settings(name: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name must be a non-empty string")
    settings = (config or load_provider_config()).get("providers", {}).get(name.strip().lower(), {})
    if not isinstance(settings, Mapping):
        raise ValueError("provider settings must be an object")
    return dict(settings)


def provider_enabled(
    name: str,
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    environment = environ if environ is not None else os.environ
    settings = provider_settings(name, config)
    enabled = settings.get("enabled", False)
    if enabled is False:
        return False
    if enabled == "AUTO":
        key_name = settings.get("api_key_env")
        return bool(key_name and environment.get(key_name, "").strip())
    if not enabled:
        return False
    if settings.get("api_key_env"):
        return bool(environment.get(settings["api_key_env"], "").strip())
    return True


def provider_api_key(name: str, config: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> str | None:
    environment = environ if environ is not None else os.environ
    settings = provider_settings(name, config)
    env_name = settings.get("api_key_env")
    if not env_name:
        return None
    value = environment.get(env_name, "").strip()
    return value or None


def provider_status(config: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
    loaded = config or load_provider_config()
    environment = environ if environ is not None else os.environ
    rows = []
    for name, settings in loaded.get("providers", {}).items():
        key_env = settings.get("api_key_env")
        rows.append({
            "provider": name,
            "enabled": provider_enabled(name, loaded, environ),
            "credential_present": bool(key_env and environment.get(key_env, "").strip()),
            "api_key_env": key_env,
        })
    return tuple(rows)


resolve_provider_config = load_provider_config
load_data_provider_config = load_provider_config
load_config = load_provider_config


__all__ = [
    "load_provider_config",
    "load_data_provider_config",
    "load_config",
    "provider_api_key",
    "provider_enabled",
    "provider_settings",
    "provider_status",
    "resolve_provider_config",
]
