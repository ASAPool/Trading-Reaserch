# Data Quality Policy DQP-1, explained

DQP-1 says how the research system must **use** the market data when
backtesting. It was fixed **before any strategy code or result existed**.
The raw market data is never changed: quality information lives in a
separate file, `quality_flags.json`, which the backtester reads.

The policy itself is `config/data_quality_policy.toml`. Its numbers can
never be tuned to improve results. Any change means a new version (DQP-2)
with a written reason, and old experiments keep the version they used.

## 1. Missing bars

- Never filled, interpolated, or rebuilt from another provider.
- A one-bar return exists only between bars exactly one hour apart.
  Across a gap it is **undefined** (not zero).
- Window indicators (moving averages, ATR, ...) are valid only when their
  whole lookback window has no missing bar. Recursive indicators (EMA, RSI,
  MACD, ...) reset at a gap and warm up again. No signal while an
  indicator it needs is invalid.
- Per-hour statistics leave out gap-spanning price changes; those changes
  still count in profit and loss, and are reported.

## 2. Trades open across a missing Coinbase period

- The position stays open. **Nothing is executed during the missing hours.**
- No stop, target or limit is assumed to have been hit inside the gap.
- Bitstamp (or any other provider) is **never** used to simulate a Coinbase fill.
- At the first available Coinbase bar, a fixed rule applies (identical in
  mirror image for long and short positions):

| Position | First open after the gap | Result |
|---|---|---|
| LONG | at or below the stop | exit at **that open** (gap loss realised) |
| LONG | at or above the target | exit at **the target price** (no windfall) |
| SHORT | at or above the stop | exit at **that open** |
| SHORT | at or below the target | exit at **the target price** |
| either | between stop and target | continue; normal intrabar rules apply |

- Such a trade is flagged **gap_exposed** (see section 6).

## 3. Flags (from `quality_flags.json`)

| Category | Meaning | Main run (A) | Sensitivity run (B) |
|---|---|---|---|
| `corroborated_extreme` | Bitstamp independently observed the move | used normally | used normally |
| `partially_corroborated` | only partly observed | used unchanged | treated like a missing hour |
| `not_corroborated` | not observed on Bitstamp | used unchanged | treated like a missing hour |
| `unverified` | could not be checked | used unchanged | treated like a missing hour |
| `primary_specific_flat` | flat on Coinbase, Bitstamp traded | used unchanged | treated like a missing hour |
| `cross_venue_quiet` | flat or no trading on both | used unchanged | treated like a missing hour |

An hour with several warnings is treated like a missing hour in run B if
**any** of its warnings is not fully corroborated.

## 4. The official data-quality sensitivity test (A vs B)

Every experiment runs twice; **both** results are always reported and
**A is always the official one**.

Units: R = total return, E = expectancy per trade (net return as a fraction
of position value), D = maximum drawdown, all as fractions (0.12 = 12%);
N = number of trades.

**SENSITIVE TO DATA QUALITY** if any of these is true:

1. The sign of R or of E differs between A and B.
2. For any metric: `|a - b| > max(floor, relative x max(|a|, |b|))`

| Metric | floor | relative |
|---|---|---|
| R total return | 0.01 | 10% |
| E expectancy | 0.0005 | 10% |
| D max drawdown | 0.01 | 10% |
| N trade count | 2 trades | 5% |

The floor stops tiny values near zero from producing meaningless
percentage changes.

## 5. Signs and the zero tolerance

A value counts as **positive** if it is above 1e-12, **negative** if below
-1e-12, and **zero** otherwise. This applies only to dimensionless values
(returns, expectancy, P&L divided by starting equity). It exists only to
absorb floating-point rounding noise: 1e-12 of $10,000 is a millionth of a
cent. One real cent (1e-6 of $10,000) is still counted.

## 6. Gap exposure: a DIAGNOSTIC CONTRIBUTION ANALYSIS

This looks at the **recorded** gap_exposed trades of the one real run. It
is **not** an alternative backtest. Deleting trades after the fact does not
recreate the equity path that would have happened, so it **never**
computes an alternative equity curve, total return, Sharpe ratio or
drawdown.

Reported: number of gap_exposed trades, their net P&L, that P&L as a
fraction of starting equity, their share of total net P&L (undefined if
the total is zero), their average trade return, whether subtracting their
P&L flips the sign of total net P&L, and the expectancy of non-gap-exposed
trades versus all trades.

**GAP-EXPOSURE SENSITIVE** (auditor review required) if any of:

1. `sign((P_all - P_gap) / S0) != sign(P_all / S0)`
2. `sign(E_non) != sign(E_all)` (or every trade is gap_exposed)
3. `|P_gap| > max(0.01 x S0, 0.10 x |P_all|)`
4. `|E_all - E_non| > max(0.0005, 0.10 x max(|E_all|, |E_non|))`

This is separate from the A/B test in section 4. Both are always reported.

## 7. Versioning and fingerprints

- `config/data_quality_policy.toml` was committed **before any strategy code
  existed**.
- `config/policy_registry.toml`, added in the **next** commit, records the
  commit hash that contains DQP-1 and the policy's SHA-256 fingerprint. (A
  file cannot contain the hash of its own commit, hence two commits.)
- Fingerprints of the policy are computed with line endings normalised, so
  Windows and Linux agree.
- Every experiment stores: policy version, policy commit, policy
  fingerprint, `quality_flags.json` fingerprint, and its own code commit.
- The backtester refuses to run if the policy file does not match the
  registry, or if `quality_flags.json` was built for a different
  `candles.csv` or policy.

This shows that a policy with exactly this content existed in that commit,
and that every result can be traced back to it. It does not claim more.

## How to build quality_flags.json

```
.venv\Scripts\python.exe -m quantlab.data.quality_flags
```

It re-validates the dataset (read-only), reads the adjudication report and
writes only `data\quality\...\quality_flags.json`. The same inputs always
produce a byte-identical file.
