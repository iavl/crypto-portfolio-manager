# Historical Strategy Validation

This subsystem evaluates the current strategy without changing
`config/policy.json` and without writing to portfolio snapshots, decisions, or
status events. Research artifacts live under
`$CRYPTO_PORTFOLIO_DATA_DIR/research/backtests/<run_id>/` (or the default
runtime data directory).

## Evidence classes

Results are kept separate because they answer different questions:

- `STRICT_POINT_IN_TIME` preserves missing semantic factors and unresolved
  event/liveness gates. A blocked or all-WAIT path is a valid result.
- `SYNTHETIC_ASSUMPTIONS` fills only otherwise unreconstructable positive-
  weight semantic factor judgments with the preregistered values 30, 50, and
  70. It is a mechanism sensitivity experiment, not score validation.
- Decision mark-to-market evaluates current-contract frozen decisions. Paper
  outcomes for `PENDING` and `NOT_EXECUTED` decisions never become account
  performance; `CONFIRMED` still needs independent fill evidence.
- Forward validation retains the existing readiness floors in
  `references/strategy-research.md`. The floors are eligibility checks, not
  statistical significance.

Historical data is classified as `PUBLISHED_AT_TIME`,
`HISTORICAL_APPROXIMATION`, or `UNRECONSTRUCTABLE`. Only the first class can
support a formal point-in-time result. Binance public OHLCV is USDT quoted; a
run uses the explicit `stablecoin_peg_assumption=true` default and labels the
valuation basis `USD_ASSUMED_STABLECOIN_PEG`. This means USDT/USDC-like quote
assets are valued at 1 USD by assumption; it is not evidence that the peg was
exact at every historical hour.

## Fixed experiment

The default `BacktestSpec` freezes the requested experiment: 2024-01-01 through
the last completed UTC day, 2022-01-01 warm-up, USD 100,000, all-cash and
59.5/25.5/15 BTC/ETH/USD starts, core and full fixed universes, daily reviews,
14-day full reviews, 10 bps fee, 5 bps slippage, 0/10/25 bps sensitivity, and
semantic scenarios 30/50/70. The spec binds the Git SHA and canonical policy
hash. Core-only research uses an independently resolved policy with an empty
satellite universe; it never modifies the canonical policy.

The default quantity engine uses daily decision bars and executes at the next
daily open. This matches the medium-term horizon and avoids inventing an
intraday fill. An optional `execution_timeframe=1H` can provide hourly fills,
but it requires a complete hourly series. Daily mode reports daily maximum
drawdown; it cannot prove an intraday drawdown that occurred inside a daily
candle. Missing held-asset prices, missing execution bars, overlapping label
periods, and price-boundary mismatches fail closed.

## Reconstructed inputs and their limits

Only inputs derivable from the frozen OHLCV are reconstructed. The regime
breadth domain uses `research/historical_builder.py:breadth_above_ma` — the
fraction of scope symbols whose close is above their own 200-day simple moving
average at the review boundary, computed from candles completed at that
boundary only. This is a research proxy: production derives `market.breadth`
from CoinGecko's fraction of the top-20 non-stable universe with a positive
30d return. The proxy differs in universe and horizon, and a symbol with less
than a full window leaves the domain UNKNOWN rather than fabricating a value.

Deterministic evidence with full dated public history is harvested alongside
the OHLCV: DeFiLlama stablecoin total supply and ETH chain fees, SoSoValue
BTC/ETH ETF net flow and AUM, FRED realtime-vintage WALCL/DFF, and CoinMetrics
BTC MVRV. `build-dataset` fetches them (SosoValue and FRED need their API keys;
each missing source degrades to MISSING, never fabricates), and the replay
derives documented proxy scores for `capital_flows`, `macro_liquidity`,
`btc_valuation`, and ETH `onchain` from the values that were published at each
decision boundary, plus the regime flows domain. An asset whose entire
positive-weight scoring profile is reconstructable this way (BTC with the full
harvest) counts as critical-data-complete in replay; the judgment layer
(fundamentals, satellite valuation, events, liveness) stays MISSING or UNKNOWN
on purpose — it cannot be replayed without look-ahead, and a run does not
silently invent it.

## Commands

```bash
python3 scripts/backtest.py build-dataset --run-id strategy-validation-2024-present
python3 scripts/backtest.py build-dataset --run-id strategy-validation-2021-2023-bear \
    --warmup-start-at 2020-11-01T00:00:00Z --start-at 2021-07-01T00:00:00Z \
    --end-at 2023-12-31T00:00:00Z
python3 scripts/backtest.py audit-data ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py run ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py run ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present --allow-usdt-approximation
python3 scripts/backtest.py evaluate-decisions ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py evaluate-scores ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py report ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present/run.json
python3 scripts/strategy_validity.py ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
```

`--start-at`, `--warmup-start-at`, and `--end-at` override the window of the
default spec (or of `--spec`) after the fact; the warm-up must still precede
the start, and indicator history (200-day moving averages, trend structure)
needs the longer warm-up, so a window that starts early must push the warm-up
earlier too. The bear-market window above exists to exercise the regime
machinery across the 2021 top, the 2022 drawdown, and the 2023 recovery with
the same frozen experiment; it is a second run id, not a change to the default.

Each `run.json` also publishes `review_calendar` per scope: candidate decision
boundaries, produced reviews, skipped boundaries, and per-symbol counts of
boundaries whose next daily candle is missing. A skipped boundary is a data
gap, not a deliberate no-trade day, and a large skip count narrows what the
run can claim.

The first `run` command emits `BLOCKED_BY_DATA_MANIFEST` when formal USD or
point-in-time requirements are not met. The explicit approximation flag runs
the same frozen experiment and keeps the limitation in every artifact.

`summary.json`, valuation/trade CSVs, Chinese Markdown/HTML, and SVG equity
curves are generated from finalized result values. The renderer does not
recalculate scores, targets, trades, or performance. `scripts/backtest.py
report` also runs the validity gate over the run directory it is given, so the
rendered report and `summary.json` carry the same verdict; the gate reads the
CSVs from that same directory, so always render into the run's own `report/`
directory rather than a copy elsewhere.

## Deterministic diagnostics (checks only, no thresholds)

Every experiment result carries four read-only diagnostics; none of them
changes engine accounting, policy, or verdicts beyond a WARNING finding.

- **Exposure timing** (`exposure_timing`): compounds the BTC leg on the
  strategy's realized risky-weight path and on the constant weight equal to
  that path's average. The signed difference is the timing contribution —
  positive means exposure was held when the leg moved up. A companion
  `exposure_matched_btc_cash` benchmark (the same constant average exposure,
  invested with costs) joins the comparison table.
- **Regime floor pinning** (`regime_floor_diagnostics`): shares of reviews
  with a DEFENSIVE-or-worse label vs reviews at or below the `-0.6D` own
  drawdown floor, whether the market domains ever pushed defense beyond the
  floor, and the drawdown budget overlay's binding share. The validity gate
  raises `REGIME_PINNED_BY_OWN_DRAWDOWN` (WARNING) when the label sat on its
  floor for a majority of reviews with no market-driven defense: in such a
  window the regime engine added no information beyond the book's own P&L.
- **Cash yield sensitivity** (`cash_yield_sensitivity`): the strategy's own
  realized path re-credited with 4%/5% annual stable yield. The replay books
  the stable leg at exactly zero; for a mandate that spends most of its life
  in cash this quantifies how much of "low return" is that assumption. It is
  a diagnostic and is not compared against the zero-yield benchmarks.
- **Drawdown budget sensitivity** (`scripts/backtest.py budget-sensitivity`):
  replays the strict core experiment under alternative
  `risk.max_portfolio_drawdown` values (default 0.15/0.20/0.25) and asserts
  the budget was held (`MaxDD <= D + tolerance`, zero breach days). With the
  overlay enabled `MaxDD ≈ D` is a construction of
  `risky_cap = 1 - |drawdown| / D`, so the outputs to read are the exposure
  and return response to the budget, not the bound itself.

## Before reading any report

Run `scripts/strategy_validity.py <run_dir>` first. It decides whether the run
is a test of the strategy at all, and the verdict is one of:

- `DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY` — the run did not exercise the
  strategy; its performance numbers describe a path, not a strategy;
- `VALID_RUN_UNDERPOWERED_INFERENCE` — a real run whose sample cannot support
  an inference;
- `NO_STRUCTURAL_OBJECTION_FOUND` — no structural objection was found.

`manifest.strict_ready` is not a substitute. It only asserts that OHLCV data is
complete (`research/data_audit.py`); an empty `blockers` list says nothing about
factor availability, scoring coverage, or whether any decision has a realized
outcome. A run can be `strict_ready` and still be degenerate.

Read performance only together with the two fair comparisons the report now
publishes:

- `static_initial_weights_investable` — the experiment's own starting weights
  held untouched. Answers "did the active decisions add anything over doing
  nothing".
- `vol_matched_btc_cash_investable` — a constant BTC/cash mix whose weight is
  solved in closed form to the strategy's own annualized volatility. Answers
  "was the risk that was taken worth it".

A 100% BTC benchmark is the policy anchor, but it is not a fair risk
comparison: a risk-reducing strategy loses to it by construction. Losing to it
proves nothing on its own.

## Interpretation

Historical performance is a diagnostic because the current policy may have
been influenced by data from the evaluation period. It is not an expected-
return estimate. Fixed present-day satellite membership also carries survivor
bias. With a medium-term horizon, one historical cycle cannot establish a
reliable confidence interval. Policy changes remain separately preregistered
research and are never adopted automatically from a best backtest result.

When the verdict is `DEGENERATE_NOT_A_TEST_OF_THE_STRATEGY`, every performance
number in that run must be read as a description of the mechanism under a
degenerate input, never as evidence about the strategy. A degraded run answers
"what does the risk machinery do when almost no evidence is available", which
is a legitimate and useful question — just not the one a performance table
appears to answer.
