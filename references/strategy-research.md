# Strategy Research Protocol

This document governs comparisons that may later justify a policy change. It
does not authorize changing `config/policy.json`.

## Frozen decision

Before a comparison, record the exact baseline policy hash, candidate policy,
data window, train/validation/holdout boundaries, transaction-cost assumptions,
execution semantics, evaluation metrics, and stop rules. Candidate selection
uses train and validation only. The final holdout is read once after the
candidate is frozen.

The minimum readiness report requires at least 90 distinct review days, two
observed market regimes, 30 frozen execution plans, and 30 reviews with
post-decision execution bars. These are eligibility floors rather than proof
of statistical power. A failed floor returns `INSUFFICIENT_EVIDENCE` and keeps
the canonical policy unchanged.

## Execution labels

Future bars are evaluation labels. They never enter the decision view. A wick
touch alone does not fill a proposal. A bar must open or close through the
zone boundary, only one tranche may progress per bar, and an explicit
`fill_fraction` controls partial liquidity. `plan_valid=false` expires or
invalidates the remainder before that bar can fill. Fees and slippage are
reported at 0, 10, and 25 basis points; none is claimed to be the user's actual
cost without account evidence.

## Required comparisons

Every candidate reports after-cost portfolio return, excess return versus BTC
and the 70/30 BTC/ETH benchmark, maximum drawdown, turnover, constraint
violations, proposal-to-fill conversion, WAIT frequency and duration, idle
stable capital, and parameter stability across periods. Long WAIT behavior is
evaluated for both avoided downside and missed upside.

The registered candidate families are:

- keep gated budget reserved versus rematch only to candidates that already
  passed every entry and risk gate inside the same turnover budget;
- baseline pullback entry versus independently specified multi-horizon support
  selection or breakout/retest entry;
- current satellite target curve versus a flatter entry segment under the same
  aggregate satellite envelope;
- fixed stress diagnostics versus a separately approved soft deployment cap
  or hard risk gate;
- current risk-envelope sizing versus cap-only sizing after the governing
  specification is made consistent;
- score-factor and BNB stablecoin-supply-proxy ablations without relabeling
  stablecoin supply as external net inflow or BNB buying.

No candidate is adopted merely because it is best in-sample. Unstable,
constraint-breaking, cost-sensitive, or data-insufficient results produce
`NO_CHANGE` or `INSUFFICIENT_EVIDENCE`.
