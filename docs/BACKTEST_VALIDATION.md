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

## Commands

```bash
python3 scripts/backtest.py build-dataset --run-id strategy-validation-2024-present
python3 scripts/backtest.py audit-data ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py run ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py run ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present --allow-usdt-approximation
python3 scripts/backtest.py evaluate-decisions ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py evaluate-scores ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present
python3 scripts/backtest.py report ~/.local/share/crypto-portfolio-manager/research/backtests/strategy-validation-2024-present/run.json
```

The first `run` command emits `BLOCKED_BY_DATA_MANIFEST` when formal USD or
point-in-time requirements are not met. The explicit approximation flag runs
the same frozen experiment and keeps the limitation in every artifact.

`result.json`, valuation/trade CSVs, Chinese Markdown/HTML, and SVG equity
curves are generated from finalized result values. The renderer does not
recalculate scores, targets, trades, or performance.

## Interpretation

Historical performance is a diagnostic because the current policy may have
been influenced by data from the evaluation period. It is not an expected-
return estimate. Fixed present-day satellite membership also carries survivor
bias. With a medium-term horizon, one historical cycle cannot establish a
reliable confidence interval. Policy changes remain separately preregistered
research and are never adopted automatically from a best backtest result.
