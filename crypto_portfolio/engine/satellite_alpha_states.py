"""Derive per-satellite alpha states from frozen signals and policy.

Strategy V2.3. Replay records freeze the per-boundary BTC-relative signal
VALUES (evidence); the discrete state is a policy decision over those
values (which signals are admitted). This module is the single dispatcher
that turns ``policy.satellite_alpha`` plus frozen signals into the state
mapping the allocation engine consumes, so every ladder rung derives its
own states from identical frozen evidence.
"""

from __future__ import annotations

from typing import Any, Mapping

from . import aave_relative_alpha, bnb_relative_alpha

# Asset -> state function. Adding an asset alpha model means adding it here;
# research-only assets (SOL) deliberately have no entry and can never
# produce a production state.
_ASSET_STATE_FUNCTIONS = {
    "BNB": bnb_relative_alpha.bnb_alpha_state,
    "AAVE": aave_relative_alpha.aave_alpha_state,
}


def satellite_alpha_states(
    satellite_alpha: Mapping[str, Mapping[str, Any]] | None,
    signals_by_symbol: Mapping[str, Mapping[str, float | None]] | None,
) -> dict[str, str]:
    """States for every alpha_admitted satellite the policy configures.

    A configured asset without frozen signals is NEUTRAL (fail-closed: no
    evidence never manufactures alpha). Assets without a model are absent
    entirely — the allocation layer's research_only gate covers them.
    """
    states: dict[str, str] = {}
    for raw_symbol, entry in (satellite_alpha or {}).items():
        symbol = str(raw_symbol).strip().upper()
        if not isinstance(entry, Mapping) or entry.get("mode") != "alpha_admitted":
            continue
        state_function = _ASSET_STATE_FUNCTIONS.get(symbol)
        if state_function is None:
            continue
        signals = (signals_by_symbol or {}).get(symbol) or {}
        states[symbol] = state_function(
            signals, tuple(entry.get("admitted_signals") or ()),
        )
    return states


__all__ = ["satellite_alpha_states"]
