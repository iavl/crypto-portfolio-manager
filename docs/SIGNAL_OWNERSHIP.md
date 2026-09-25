# Signal Ownership (Strategy V2)

One signal family, one primary owner with authority over target allocation.
Other layers may **read** the same signal for their own question, but only
the primary owner may let it move target sizing. This document is the
contract; `crypto_portfolio/engine/signal_ownership.py` derives the live
report from the canonical policy and flags every residual overlap with
`MULTIPLE_POLICY_AUTHORITY`.

## Ownership table

| Signal | Primary owner | May also read it ( scoped ) |
| --- | --- | --- |
| Trend / momentum | Asset score | Regime (context, reduced weight), Execution (direction confirmation) |
| Relative strength | Asset selection | Asset score (as one factor) |
| Fundamentals | Asset score | — |
| Valuation | Asset score | — |
| Macro liquidity | Regime | Asset score (BTC profile factor) |
| Market breadth | Regime | — |
| Market volatility | Risk engine | Regime (systemic volatility domain) |
| Correlation | Risk engine | — |
| Beta | Risk engine | — |
| Portfolio drawdown | Risk engine (emergency brake) | — |
| ATR | Execution | — |
| Support / resistance | Execution | — |
| Volume profile | Execution | — |
| Breakout | Execution | — |
| Event / security | Hard risk gate | — |
| Liveness | Hard risk gate | — |

## Layer questions

Each layer answers exactly one question:

- **Asset score / asset selection** — what assets are worth taking risk in?
- **Risk engine** — how much risk should the portfolio take? (volatility
  budget for normal sizing; drawdown only as the staged emergency brake)
- **Regime** — how much systemic risk does the whole market allow? (macro
  liquidity, breadth, systemic volatility, market-wide flows, systemic
  event state; trend is context, not authority)
- **Execution** — when and how should an approved change be traded?
  (timing, tranches, zones, volume confirmation)
- **Hard risk gate** — what must block new risk outright? (events,
  security, liveness)

## Phase 3 decisions

1. **Regime trend authority reduced** (`regime_model.domain_weights`):
   trend 0.30 → 0.15, volatility 0.25 → 0.30, flows 0.25 → 0.30, breadth
   0.20 → 0.25. The regime now answers the systemic question; BTC's own
   trend remains as reduced context pending Phase 6 walk-forward
   validation of the weights.
2. **Bounded WAIT lifetime** (`execution_overlay.wait.expiry_reviews`,
   placeholder 5): a technical WAIT may not veto a strategic approval
   forever. After the configured consecutive WAIT reviews with the
   strategic thesis intact (not CAPITAL_PRESERVATION, thesis not broken),
   the entry plan deploys the configured `max_initial_tranche` fraction at
   market (`entry_mode = MARKET_TIMEOUT`, reserve policy
   `TIMEOUT_RESERVE`) instead of issuing another WAIT. Execution still
   owns timing; it just cannot block deployment indefinitely.
3. **Deployment caps compose by minimum**, never by product
   (`execution.deployment_factor_composition = minimum_cap`): independent
   permission caps cannot silently multiply into extreme shrinkage.

## Known residual overlaps (flagged, intentional)

- `trend_momentum` is read by asset score (primary), regime (0.15 context
  weight), and execution (direction-confirmation timing). The flag stays
  until Phase 6 either removes the regime trend domain or validates it.
- `macro_liquidity` is read by the regime (primary) and the BTC scoring
  profile (one factor). Same treatment.
- `portfolio_drawdown` owns the emergency brake in both risk-engine modes
  and additionally owns normal sizing while `risk_engine.mode` is
  `legacy_drawdown` — the frozen V1 behavior kept for A/B replay.
