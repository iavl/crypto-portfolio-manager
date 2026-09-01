# Investment Policy

## Objective

Manage a crypto portfolio over approximately 6–12 months with three simultaneous objectives:

1. Seek to outperform BTC over comparable periods.
2. Increase long-term absolute return.
3. Avoid large portfolio drawdowns, using the configured portfolio drawdown risk budget (20% by default).

These objectives can conflict. Capital preservation has priority when the risk model enters `CAPITAL_PRESERVATION`.

## Eligible universe

### Core

- Default core assets: BTC and ETH.
- The user may replace the core asset list in the snapshot `config`.

BTC is the primary benchmark and default risk asset. The configured core list controls core classification and risk tiering; the benchmark remains BTC unless separately changed.

### Satellite / tactical large-cap universe

Default examples:

- SOL
- BNB
- LINK
- AAVE

The user may replace the satellite asset list in the snapshot `config`.

This is not an automatic buy list. Any eligible asset may have 0% target weight.

### Excluded

- small-cap altcoins;
- illiquid tokens;
- meme/speculative tokens without durable analyzable fundamentals;
- futures/perpetuals;
- leveraged tokens;
- margin positions;
- borrowed positions.

## Stablecoins and cash

- Minimum allocation: the configured `min_stablecoin_weight` floor, 10% by default.
- No fixed maximum.
- Cash allocation should rise as aggregate risk/reward deteriorates.
- Stablecoin issuer, depeg, custody, venue, and smart-contract risks must be considered; “stablecoin” does not mean risk-free.

## Core/satellite principle

Configured core assets should normally be the majority of risky assets. With no custom configuration, this means BTC + ETH.

Satellites are used only to seek additional expected return when the evidence justifies their higher volatility, drawdown, tokenomics, execution, regulatory, or protocol-specific risk.

A useful decision question is:

> If this capital were currently held as stablecoin, would the portfolio intentionally buy this asset today instead of BTC?

If not, an existing holding should not receive special treatment simply because it is already owned.

## Cost basis

Cost basis may influence:

- P&L reporting;
- tax-awareness when the user requests it;
- staged profit-taking;
- behavioral/risk context.

Cost basis must not determine whether a position is fundamentally attractive now. Avoid sunk-cost anchoring.

## New-asset admission

A new asset should normally enter `WATCHLIST` first and must satisfy most of these conditions:

- clearly large market capitalization relative to the crypto universe;
- deep spot liquidity across reputable venues;
- sufficiently long and reliable price history;
- understandable token utility/economic model;
- credible data for supply, emissions, unlocks, concentration, and governance;
- analyzable protocol/product fundamentals;
- no unresolved severe security/governance issue;
- no near-term supply event that dominates the investment thesis;
- risk-adjusted case materially better than simply adding BTC.

Popularity alone is not evidence.

## Staking policy

Staking is optional and secondary to portfolio allocation.

Only consider it when:

- the yield is material after fees and inflation;
- lockup/unstaking constraints are acceptable;
- smart-contract and validator risk are understood;
- slashing and custody risks are acceptable;
- liquid staking token depeg/liquidity risk is evaluated when applicable;
- the yield does not induce holding an otherwise unattractive asset.

Never justify a weak asset allocation solely because staking APY is high.
