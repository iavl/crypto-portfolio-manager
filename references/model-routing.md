# Model Routing

`config/model-routing.json` is the v2 repository default. It names model
presets (`luna_max`, `terra_medium`, `sol_high`, and so on), reasoning effort,
profiles, and fallback policy. The built-in default is `balanced`; available
profiles are `balanced`, `efficient`, `quality`, and `session_compatible`.

The built-in presets use the current GPT-5.6 family IDs exposed by the runtime:

- `LUNA` uses `gpt-5.6-luna` with `max` only.
- `TERRA` uses `gpt-5.6-terra` with `low`, `medium`, or `high` as configured.
- `SOL` uses `gpt-5.6-sol` with `medium`, `high`, or `xhigh` as configured.
- `CURRENT_SESSION` uses the host's current model and `inherit` effort.
- `PYTHON` has no model and no reasoning effort.

`history`, `facts`, `metrics_math`, `technical`, `scoring_math`, `regime`,
`allocation`, `risk`, `rebalance`, and `execution` are Python-owned and cannot
be assigned an LLM preset. A Luna-family preset with any effort other than
`max` is invalid; the value is rejected rather than normalized.

Profile inheritance has one parent. User-local overrides are read from
`~/.config/crypto-portfolio-manager/model-routing.json`, or from
`CRYPTO_PORTFOLIO_MODEL_CONFIG`. Select a profile with
`CRYPTO_PORTFOLIO_MODEL_PROFILE`. Precedence is explicit run argument, the
environment variable, local `active_profile`, then the repository default.
Repository defaults are never rewritten.

The resolver keeps requested and effective routes separate. It uses injected
`RuntimeCapabilities`; without a capability adapter, `AUTO`, `CHATGPT`, and
other unknown host behavior conservatively fall back to `CURRENT_SESSION /
inherit`. A fallback records the reason, and never silently upgrades to a
more expensive model. Use `python3 scripts/model_routing.py --show-effective`
to inspect the result without network access.

Routing metadata stores the selected profile, runtime, configuration hash, and
requested/effective model and effort for each stage. It stores no prompts,
chain-of-thought, or private reasoning. Sol remains conditional: Python owns
the predicate that decides whether a high-impact Sol review is required.
