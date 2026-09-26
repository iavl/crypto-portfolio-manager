# Strategy V2.1 vs V2.2 — Strict Replay Comparison

Head-to-head replay of the two strategy generations under identical
conditions: same frozen reviews (995 / 913 daily boundaries), same
point-in-time datasets, costs 10 bps fees + 5 bps slippage, `full/
core_existing` scope.

- **V2.1** — the previously validated strategy: volatility-budget engine
  with the fixed 70/30 BTC/ETH anchor core (`core_allocation.mode =
  legacy_anchor`).
- **V2.2** — the new strategy: the same risk engine with the BTC-baseline
  core (`btc_baseline_with_active_tilts`); the ETH tilt stays
  research-only, structural evidence stays research-gated.

Reproduction: `python3 /tmp/v21_v22_compare.py` (driver over the cached
datasets); raw metrics live in `~/.local/share/crypto-portfolio-manager/
research/backtests/v21-vs-v22-comparison.json`. The V2.1 legs reproduce
the published V2.1 validation numbers to the digit (13.78% / 6.94% CAGR).

## 2024-01-01 → 2026-09-24 (995 reviews)

| Metric | V2.1 | V2.2 | Δ |
|---|---|---|---|
| CAGR | +13.78% | +13.87% | +0.09 pp |
| Max drawdown | −23.13% | −23.13% | 0.00 pp |
| Annualized volatility | 18.81% | 18.46% | −0.35 pp |
| Sharpe / Sortino | 0.780 / 1.172 | 0.796 / 1.200 | +0.016 / +0.028 |
| Vol-matched excess (annualized) | +0.06% | +0.39% | +0.33 pp |
| Avg weight BTC / ETH / satellites | 33.5% / 1.1% / 2.2% | 34.0% / 0.0% / 2.5% | ETH −1.1 pp, BTC +0.5 pp |
| Avg cash weight | 63.1% | 63.5% | +0.4 pp |
| Trades / turnover / cost | 270 / 2.7× / $539 | 210 / 2.6× / $512 | −60 trades |
| Days beyond −15% budget | 304 | 304 | 0 |

## 2021-07-01 → 2023-12-31 bear window (913 reviews)

| Metric | V2.1 | V2.2 | Δ |
|---|---|---|---|
| CAGR | +6.94% | +8.33% | +1.40 pp |
| Max drawdown | −28.18% | −28.40% | −0.22 pp |
| Annualized volatility | 17.85% | 19.31% | +1.46 pp |
| Sharpe / Sortino | 0.465 / 0.671 | 0.511 / 0.743 | +0.046 / +0.072 |
| Vol-matched excess (annualized) | +1.21% | +2.26% | +1.05 pp |
| Avg weight BTC / ETH / satellites | 19.5% / 4.6% / ~0% | 21.0% / 4.0% / ~0% | BTC +1.5 pp |
| Avg cash weight | 75.8% | 74.9% | −0.9 pp |
| Trades / turnover / cost | 209 / 1.6× / $283 | 115 / 1.6× / $287 | −94 trades |
| Days beyond −15% budget | 641 | 667 | +26 days |

Reference buy-and-hold (investable, same boundaries): 2024 window BTC
+30.0%, 70/30 +24.0% (both strategies are risk-sized ~35-40% average
risky exposure, so they trail the 100% BTC path by design while earning
positive vol-matched excess); bear window BTC +7.6%, 70/30 +5.5% (both
strategies beat 70/30; V2.2 also beats full BTC).

## Reading the comparison

1. **The bear window is where dropping the fixed anchor pays.** V2.2 adds
   +1.40 pp CAGR and +1.05 pp vol-matched excess with a slightly better
   Sharpe: the ETH sleeve the anchor kept re-establishing (avg 4.6%) was
   a persistent drag while ETH/BTC bled; V2.2 routes that budget to the
   BTC baseline (avg BTC 19.5% → 21.0%). The cost is marginally deeper
   drawdown (−0.22 pp) and 26 more breach days on the already-breached
   15% budget.
2. **The 2024 window is a tie with less churn.** Under V2.1's own
   eligibility gates the ETH anchor sleeve had already collapsed to ~1.1%
   average weight, so removing the anchor changes almost nothing (+0.09
   pp); V2.2 gets there with 60 fewer trades and slightly lower
   volatility. This is consistent with the V2.2 validation finding: the
   ETH anchor's cost was real but mostly realized in the weak-ETH regime.
3. **Neither generation fixes the frozen risk issue.** Both breach the
   15% drawdown budget for hundreds of days in both windows (−23% / −28%
   MaxDD). That is explicitly out of V2.2 scope and queued for a risk
   calibration round (V2.3).
4. **Same risk engine, so the delta is exactly the core-allocation
   change.** Everything else (regime machinery, recovery FSM, satellite
   path, thresholds) is identical between the legs; the volatility budget
   governs both.

## Verdict

V2.2 dominates V2.1 on this evidence: better or equal CAGR, vol-matched
excess, and Sharpe in both windows, with fewer trades — and it removes an
unjustified asset-identity prior. The bear-window improvement is the
clean, mechanistic one (no ETH drag); the 2024 window confirms nothing is
lost when ETH is genuinely strong (BTC absorbs it). Combined with the
V2.2 validation ladder (BTC-only baseline remains the strongest single
configuration), the conclusion is: adopt the BTC-baseline core; treat
every active tilt as research until its admission passes.
