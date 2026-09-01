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

STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USD", "CASH"}
CORE_SYMBOLS = {"BTC", "ETH"}
DEFAULT_SATELLITES = {"SOL", "BNB", "LINK", "AAVE"}


def classify(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in STABLE_SYMBOLS:
        return "stablecoin" if s not in {"USD", "CASH"} else "cash"
    if s in CORE_SYMBOLS:
        return "core"
    if s in DEFAULT_SATELLITES:
        return "satellite"
    return "other"


def normalize(data: dict[str, Any]) -> dict[str, Any]:
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
        item.setdefault("asset_type", classify(symbol))
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

    return {
        "timestamp": data.get("timestamp"),
        "base_currency": data.get("base_currency", "USD"),
        "total_value_usd": total,
        "stablecoin_weight": stable / total,
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
