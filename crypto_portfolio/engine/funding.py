"""Conservative proposal readiness; does not alter strategic allocations."""


def funding_readiness(snapshot, operation, *, settlement_asset):
    positions = {p.symbol:p for p in snapshot.positions}
    rows = []
    for action in operation['execution_actions']:
        if action['strategic_action'] not in {'REDUCE','EXIT'} or not action['proposed_amount_usd']:
            continue
        position = positions.get(action['symbol'])
        facts = position.funding_availability if position else None
        required = action['proposed_amount_usd']
        available = facts.available_value_usd if facts else None
        conditions = []
        if available is None:
            conditions.append('AVAILABILITY_UNKNOWN')
        elif available + 1e-7 < required:
            conditions.append('RELEASE_RESTRICTIONS_OR_REFRESH_REQUIRED')
        if action['purpose'] == 'BUY_FUNDING' and action['symbol'] != settlement_asset:
            conditions.append('CONVERSION_REQUIRED')
        rows.append({'symbol':action['symbol'],'required_value_usd':required,
                     'verified_available_value_usd':available,'conditions':conditions,
                     'observed_at':facts.observed_at if facts else None,
                     'source':facts.source if facts else None})
    return {'status':'CONDITIONAL' if any(r['conditions'] for r in rows) else 'VERIFIED' if rows else 'NOT_REQUIRED',
            'as_of':snapshot.timestamp,'rows':rows,'fees_usd':None,'slippage_usd':None,
            'meaning':'economic holdings remain in NAV; funds and conversions must be ready before order submission'}
