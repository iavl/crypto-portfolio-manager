"""Immutable local cache for normalized public OHLCV observations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ..models.market import OHLCVSeries
from ..models.volume_profile import VolumeProfile
from .snapshots import runtime_data_dir


def _atomic_write(destination: Path, payload: str) -> None:
    """Write ``payload`` atomically: temp file in the same directory + os.replace.

    Concurrent readers never see a partially written cache entry; both writers
    of identical content resolve to the same final file.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(destination.parent), delete=False
    )
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, destination)
    try:
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _validate_hash(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("ohlcv_hash must be a SHA-256 hex digest")
    result = value.lower()
    if any(character not in "0123456789abcdef" for character in result):
        raise ValueError("ohlcv_hash must be a SHA-256 hex digest")
    return result


def default_market_data_dir() -> Path:
    return runtime_data_dir() / "market-data" / "sha256"


def market_data_path(ohlcv_hash: str, directory: str | Path | None = None) -> Path:
    return Path(directory or default_market_data_dir()) / f"{_validate_hash(ohlcv_hash)}.json"


def cache_ohlcv(
    series: OHLCVSeries | Mapping[str, Any], directory: str | Path | None = None
) -> Path:
    if isinstance(series, Mapping):
        series = OHLCVSeries.from_mapping(series)
    if not isinstance(series, OHLCVSeries):
        raise ValueError("series must be an OHLCVSeries")
    destination = market_data_path(series.ohlcv_hash, directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        series.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    if destination.exists():
        existing = load_ohlcv(series.ohlcv_hash, directory)
        if existing.content_identity() != series.content_identity():
            raise ValueError("content-addressed OHLCV entry is immutable")
        return destination
    _atomic_write(destination, payload)
    if destination.exists():
        existing = load_ohlcv(series.ohlcv_hash, directory)
        if existing.content_identity() != series.content_identity():
            raise ValueError("content-addressed OHLCV entry is immutable")
    return destination


def load_ohlcv(
    ohlcv_hash: str, directory: str | Path | None = None
) -> OHLCVSeries:
    path = market_data_path(ohlcv_hash, directory)
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load cached OHLCV {path}: {exc}") from exc
    try:
        series = OHLCVSeries.from_mapping(data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cached OHLCV {path} is invalid: {exc}") from exc
    if series.ohlcv_hash != _validate_hash(ohlcv_hash):
        raise ValueError("cached OHLCV content does not match requested hash")
    return series


def _validate_profile_hash(value: str) -> str:
    return _validate_hash(value)


def default_volume_profile_dir() -> Path:
    return runtime_data_dir() / "volume-profiles" / "sha256"


def volume_profile_path(profile_hash: str, directory: str | Path | None = None) -> Path:
    return Path(directory or default_volume_profile_dir()) / f"{_validate_profile_hash(profile_hash)}.json"


def cache_volume_profile(
    profile: VolumeProfile | Mapping[str, Any], directory: str | Path | None = None
) -> Path:
    if isinstance(profile, Mapping):
        profile = VolumeProfile.from_mapping(profile)
    if not isinstance(profile, VolumeProfile):
        raise ValueError("profile must be a VolumeProfile")
    destination = volume_profile_path(profile.profile_hash, directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(profile.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if destination.exists():
        existing = load_volume_profile(profile.profile_hash, directory)
        if existing.as_dict() != profile.as_dict():
            raise ValueError("content-addressed volume profile entry is immutable")
        return destination
    _atomic_write(destination, payload)
    if destination.exists():
        existing = load_volume_profile(profile.profile_hash, directory)
        if existing.as_dict() != profile.as_dict():
            raise ValueError("content-addressed volume profile entry is immutable")
    return destination


def load_volume_profile(
    profile_hash: str, directory: str | Path | None = None
) -> VolumeProfile:
    path = volume_profile_path(profile_hash, directory)
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load cached volume profile {path}: {exc}") from exc
    try:
        profile = VolumeProfile.from_mapping(data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cached volume profile {path} is invalid: {exc}") from exc
    if profile.profile_hash != _validate_profile_hash(profile_hash):
        raise ValueError("cached volume profile content does not match requested hash")
    return profile


cache_ohlcv_series = cache_ohlcv
load_ohlcv_by_hash = load_ohlcv
cache_volume_profile_by_hash = cache_volume_profile
load_volume_profile_by_hash = load_volume_profile


__all__ = [
    "cache_ohlcv",
    "cache_ohlcv_series",
    "cache_volume_profile",
    "cache_volume_profile_by_hash",
    "default_market_data_dir",
    "default_volume_profile_dir",
    "load_ohlcv",
    "load_ohlcv_by_hash",
    "load_volume_profile",
    "load_volume_profile_by_hash",
    "market_data_path",
    "volume_profile_path",
]
