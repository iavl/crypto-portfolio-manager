"""Persist and independently rebuild the public inputs of execution plans."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..engine.technical import build_technical_snapshot, completed_candles
from ..engine.volume_profile import build_multi_horizon_profiles
from ..models.market import SpotPrice
from ..models.time import parse_timestamp
from .market_data import cache_ohlcv, cache_volume_profile, load_ohlcv, load_volume_profile
from .snapshots import runtime_data_dir


def cache_execution_inputs(series, spot, *, policy, as_of=None, profile_series=None, root=None):
    root = Path(root or runtime_data_dir())
    as_of = as_of or spot.observed_at
    daily = replace(series, candles=completed_candles(series, as_of=as_of))
    snapshot = build_technical_snapshot(daily, spot, as_of=as_of, policy=policy, profile_series=profile_series)
    cache_ohlcv(daily, root/'market-data'/'sha256')
    source = profile_series or daily
    profiles = build_multi_horizon_profiles(source, policy=policy,
        lookback_days=policy.volume_profile['lookback_days'], atr_value=snapshot.atr14, as_of=as_of)
    for profile in profiles.values():
        start, end = parse_timestamp(profile.metadata['range_start']), parse_timestamp(profile.metadata['range_end'])
        used = replace(source, candles=tuple(c for c in source.candles if start <= parse_timestamp(c.timestamp) <= end))
        if used.ohlcv_hash != profile.ohlcv_hash:
            raise ValueError('profile input hash mismatch')
        cache_ohlcv(used, root/'market-data'/'sha256')
        cache_volume_profile(profile, root/'volume-profiles'/'sha256')
    return snapshot


def validate_execution_artifacts(plans, *, policy, root=None):
    root = Path(root or runtime_data_dir())
    rebuilt = {}
    for symbol, plan in plans.items():
        summary = plan.technical_summary
        if not plan.ohlcv_hash:
            raise ValueError('EXECUTION_ARTIFACT_REQUIRED: missing daily OHLCV hash')
        daily = load_ohlcv(plan.ohlcv_hash, root/'market-data'/'sha256')
        if daily.symbol != symbol or daily.timeframe != '1D':
            raise ValueError('execution daily input asset/timeframe mismatch')
        metadata = plan.ohlcv_metadata or {}
        if metadata.get('fetched_at'):
            daily = replace(daily, fetched_at=metadata['fetched_at'])
        spot = SpotPrice(symbol, summary['spot_price'], summary['spot_observed_at'], summary['spot_source'],
            summary.get('spot_fetched_at'), summary.get('spot_venue'), summary.get('spot_market'), summary.get('spot_quote_currency'))
        as_of = metadata.get('as_of', spot.observed_at)
        base = build_technical_snapshot(daily, spot, as_of=as_of, policy=policy, volume_profiles={})
        profiles = {}
        refs = (plan.volume_profile_metadata or {}).get('profiles', {})
        if plan.volume_profile_hash and not refs:
            raise ValueError('EXECUTION_ARTIFACT_REQUIRED: profile input references missing')
        for days, ref in refs.items():
            profile = load_volume_profile(ref['profile_hash'], root/'volume-profiles'/'sha256')
            source = load_ohlcv(ref['ohlcv_hash'], root/'market-data'/'sha256')
            if (profile.symbol != symbol or source.symbol != symbol or profile.ohlcv_hash != source.ohlcv_hash
                or source.timeframe != profile.timeframe or int(days) != profile.lookback_days
                or parse_timestamp(profile.as_of) > parse_timestamp(as_of)):
                raise ValueError('execution profile provenance mismatch')
            for name in ('venue', 'market', 'quote_currency'):
                if getattr(source, name) != getattr(daily, name):
                    raise ValueError('execution profile venue mismatch')
            computed = build_multi_horizon_profiles(source, policy=policy, lookback_days=[int(days)],
                                                   atr_value=base.atr14, as_of=as_of).get(int(days))
            if computed is None or computed.profile_hash != profile.profile_hash:
                raise ValueError('execution profile does not reproduce from raw candles')
            profiles[int(days)] = profile
        snapshot = build_technical_snapshot(daily, spot, as_of=as_of, policy=policy, volume_profiles=profiles)
        expected = snapshot.technical_summary()
        for key, value in expected.items():
            if key == 'selected_zones':
                continue
            if summary.get(key) != value:
                raise ValueError(f'execution technical calculation mismatch: {key}')
        from ..engine.entry import build_entry_plan
        from ..models.factor_packet import thaw_packet_value
        from ..models.policy import policy_hash
        context = thaw_packet_value(plan.planning_context)
        if not context or context.get('policy_hash') != policy_hash(policy):
            raise ValueError('EXECUTION_PLANNING_CONTEXT_REQUIRED: matching policy inputs required')
        reproduced = build_entry_plan(symbol, plan.approved_amount_usd, snapshot, context['regime'],
            context['portfolio_confidence'], context['action'], policy=policy, **context['options'])
        if reproduced.as_dict() != plan.as_dict():
            raise ValueError('execution plan does not reproduce from stored planning inputs')
        rebuilt[symbol] = snapshot
    return rebuilt
