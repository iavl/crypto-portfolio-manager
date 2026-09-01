#!/usr/bin/env python3
"""Normalize and validate a structured portfolio snapshot.

Usage:
    python scripts/portfolio_snapshot.py snapshot.json

This helper does not OCR screenshots. The agent first extracts screenshot values visually,
then can write a JSON snapshot and use this script for arithmetic consistency checks.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "core_symbols": ["BTC", "ETH"],
    "satellite_symbols": ["SOL", "BNB", "LINK", "AAVE"],
    "stable_symbols": ["USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH"],
    "min_stablecoin_weight": 0.10,
    "max_portfolio_drawdown": 0.20,
}

_CONFIG_MISSING = object()
_SYMBOL_FIELDS = ("core_symbols", "satellite_symbols", "stable_symbols")


def _copy_default_config() -> dict[str, Any]:
    return {
        key: value.copy() if isinstance(value, list) else value
        for key, value in DEFAULT_CONFIG.items()
    }


def _normalize_symbols(field: str, raw_symbols: Any) -> list[str]:
    if not isinstance(raw_symbols, list):
        raise ValueError(f"config.{field} must be a list of strings")

    symbols: list[str] = []
    for index, raw_symbol in enumerate(raw_symbols):
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError(f"config.{field}[{index}] must be a non-empty string")
        symbol = raw_symbol.strip().upper()
        if symbol in symbols:
            raise ValueError(f"config.{field} contains duplicate symbol {symbol}")
        symbols.append(symbol)
    return symbols


def _normalize_fraction(field: str, raw_value: Any, *, exclusive_minimum: bool) -> float:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"config.{field} must be a number")
    try:
        value = float(raw_value)
    except OverflowError as exc:
        raise ValueError(f"config.{field} must be a finite number") from exc
    minimum_ok = value > 0 if exclusive_minimum else value >= 0
    if not math.isfinite(value) or not minimum_ok or value > 1:
        bound = "(0, 1]" if exclusive_minimum else "[0, 1]"
        raise ValueError(f"config.{field} must be in {bound}")
    return value


def resolve_config(raw_config: Any = _CONFIG_MISSING) -> dict[str, Any]:
    """Return validated user settings merged with the documented defaults."""
    if raw_config is _CONFIG_MISSING:
        return _copy_default_config()
    if not isinstance(raw_config, dict):
        raise ValueError("config must be an object")

    unknown = sorted(set(raw_config) - set(DEFAULT_CONFIG))
    if unknown:
        raise ValueError(f"config contains unknown fields: {', '.join(unknown)}")

    config = _copy_default_config()
    for field in _SYMBOL_FIELDS:
        if field in raw_config:
            config[field] = _normalize_symbols(field, raw_config[field])
    if "min_stablecoin_weight" in raw_config:
        config["min_stablecoin_weight"] = _normalize_fraction(
            "min_stablecoin_weight",
            raw_config["min_stablecoin_weight"],
            exclusive_minimum=False,
        )
    if "max_portfolio_drawdown" in raw_config:
        config["max_portfolio_drawdown"] = _normalize_fraction(
            "max_portfolio_drawdown",
            raw_config["max_portfolio_drawdown"],
            exclusive_minimum=True,
        )

    owners: dict[str, str] = {}
    for field in _SYMBOL_FIELDS:
        for symbol in config[field]:
            if symbol in owners:
                raise ValueError(
                    f"symbol {symbol} appears in both {owners[symbol]} and {field}"
                )
            owners[symbol] = field
    return config


def _classify(symbol: str, config: dict[str, Any]) -> str:
    s = symbol.upper().strip()
    if s in config["stable_symbols"]:
        return "stablecoin" if s not in {"USD", "CASH"} else "cash"
    if s in config["core_symbols"]:
        return "core"
    if s in config["satellite_symbols"]:
        return "satellite"
    return "other"


def classify(symbol: str, config: dict[str, Any] | None = None) -> str:
    """Classify a symbol using defaults or a partial user configuration."""
    return _classify(symbol, resolve_config() if config is None else resolve_config(config))


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    config = resolve_config(data.get("config", _CONFIG_MISSING))
    positions = data.get("positions")
    if not isinstance(positions, list) or not positions:
        raise ValueError("positions must be a non-empty list")

    clean_positions: list[dict[str, Any]] = []
    total = 0.0
    for i, raw in enumerate(positions):
        if not isinstance(raw, dict):
            raise ValueError(f"position {i} must be an object")
        symbol = str(raw.get("symbol", "")).upper().strip()
        if not symbol:
            raise ValueError(f"position {i} is missing symbol")
        value = float(raw.get("value_usd", -1))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"position {symbol} has invalid value_usd")
        total += value
        item = dict(raw)
        item["symbol"] = symbol
        item["value_usd"] = value
        item.setdefault("asset_type", _classify(symbol, config))
        clean_positions.append(item)

    if total <= 0:
        raise ValueError("portfolio total must be > 0")

    for item in clean_positions:
        item["computed_weight"] = item["value_usd"] / total

    stable = sum(
        p["value_usd"]
        for p in clean_positions
        if p["asset_type"] in {"stablecoin", "cash"}
    )
    stablecoin_weight = stable / total
    core = sum(p["value_usd"] for p in clean_positions if p["asset_type"] == "core")
    satellite = sum(p["value_usd"] for p in clean_positions if p["asset_type"] == "satellite")

    warnings: list[str] = []
    reported_total = data.get("reported_total_value")
    if reported_total is not None:
        reported_total = float(reported_total)
        if reported_total > 0:
            gap = abs(total - reported_total) / reported_total
            if gap > 0.01:
                warnings.append(
                    f"sum of position values differs from reported total by {gap:.2%}"
                )

    for p in clean_positions:
        displayed = p.get("displayed_weight")
        if displayed is not None:
            displayed = float(displayed)
            if abs(displayed - p["computed_weight"]) > 0.01:
                warnings.append(
                    f"{p['symbol']} displayed weight differs from computed weight by "
                    f"{abs(displayed - p['computed_weight']):.2%}"
                )

    peak = data.get("portfolio_peak_value")
    drawdown = None
    if peak is not None:
        peak = float(peak)
        if peak >= total and peak > 0:
            drawdown = total / peak - 1.0
        elif peak > 0:
            warnings.append("portfolio_peak_value is below current total; drawdown omitted")

    if stablecoin_weight < config["min_stablecoin_weight"]:
        warnings.append(
            f"stablecoin weight {stablecoin_weight:.2%} is below configured minimum "
            f"{config['min_stablecoin_weight']:.2%}"
        )
    if drawdown is not None and drawdown < -config["max_portfolio_drawdown"]:
        warnings.append(
            f"portfolio drawdown {drawdown:.2%} exceeds configured maximum "
            f"{config['max_portfolio_drawdown']:.2%}"
        )

    return {
        "config": config,
        "timestamp": data.get("timestamp"),
        "base_currency": data.get("base_currency", "USD"),
        "total_value_usd": total,
        "stablecoin_weight": stablecoin_weight,
        "core_weight": core / total,
        "satellite_weight": satellite / total,
        "portfolio_drawdown": drawdown,
        "positions": clean_positions,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = normalize(data)
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))


if __name__ == "__main__":
    main()
