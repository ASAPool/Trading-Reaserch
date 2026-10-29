# Test strategies (Phase 6), explained

> **These are NOT research strategies.** They exist **only** to verify that the
> engine behaves correctly on **made-up (synthetic) data**. Their results must
> **never** be presented as evidence of profitability.
>
> **COSTS NOT MODELLED (Phase 7).** **REAL-DATA LOCK:** none of these has ever run
> on the real BTC history, and none may until the train / validation / final-test
> split and leakage rules are frozen (Phase 8).

Code: `src/quantlab/strategies/`. Approved plan: [`phase6-plan.md`](phase6-plan.md)
(section 13 holds the owner's binding clarifications).

## Identity: which rules produced a result

Every strategy has a **name**, an explicit **version** and its **settings**.
Every result records them (`result.strategy_identity`), plus a SHA-256 fingerprint
of their canonical text. The identity is built only from those values (never from
a Python class name or a memory address), so the same strategy, version and
settings give the same identity on every run and every computer. The engine
refuses a strategy without an identity, or a factory that produces two different
identities for Variant A and B.

| Name | Version | Settings (approved, synthetic only, not tuned) |
|---|---|---|
| `null` | 1.0.0 | none |
| `buy_and_hold` | 1.0.0 | `side` |
| `ma_cross` | 1.0.0 | `side`, `fast_hours` 3, `slow_hours` 5, `stop_fraction` 0.02, `target_fraction` 0.04 |
| `channel_breakout` | 1.0.0 | `side`, `lookback_hours` 4, `hold_hours` 3 |

`side` is `LONG` or `SHORT`. The SHORT version is always the exact mirror image of
the LONG version.

## null

Never trades. Proves the plumbing: zero trades, equity flat at 10,000.

## buy_and_hold

At every bar close while flat, ask to buy at the next open, with no stop and no
target. If the next hour is missing, the request expires (recorded) and the
strategy asks again at the next bar it sees. It never asks to sell; a position
still open at the end is reported, never closed at an invented price.

## ma_cross

`fast` = average of the last 3 hourly closes, `slow` = average of the last 5.
An average exists only when its whole window is consecutive hours (DQP-1).

At closed bar t (LONG):

| Signal | Condition |
|---|---|
| entry cross | `fast[t-1] <= slow[t-1]` **and** `fast[t] > slow[t]` |
| exit cross | `fast[t-1] >= slow[t-1]` **and** `fast[t] < slow[t]` |

- Equality alone is **not** a cross.
- Both t-1 and t must have valid values and t must directly follow t-1, so the
  first bar that can signal is the **6th consecutive bar** after the start or after
  any gap. A cross is never inferred across a gap or a warm-up.
- While flat, an entry cross asks to buy at the next open with
  stop = close[t] x 0.98 and target = close[t] x 1.04. These are fixed from the
  **decision bar's close** and never recalculated from the fill. If the next open
  is already at or beyond either level, the engine cancels the entry and records
  `invalid_bracket_at_fill`.
- While in a position, an exit cross asks to sell at the next open.
- SHORT: entry on the downward cross, exit on the upward cross,
  stop = close x 1.02, target = close x 0.96.

## channel_breakout

At closed bar t, while flat (LONG):

- The **4 hours immediately before bar t** must all be present (bar t itself is
  **not** part of the lookback). A missing hour anywhere in that span: no signal.
- Signal if close[t] is **strictly above** the highest HIGH of those 4 bars.
  Equality is not a breakout.
- Stop = the lowest LOW of those same 4 bars. No target.
- SHORT: close[t] strictly below their lowest LOW; stop at their highest HIGH.

### The 3-hour exit (real elapsed UTC time, not a bar count)

| Step | Example |
|---|---|
| Signal at the close of the 09:00 bar (closes 10:00) | entry requested |
| Fill at the 10:00 open | position opened |
| Three hours completed at the 13:00 boundary = the close of the 12:00 bar | exit due |
| Fill at the 13:00 open | position closed (`time_exit`) |

The exit time (fill time + 3 hours) is fixed when the entry is decided and is
passed to the engine as an absolute UTC time (`Entry.exit_at`). Without missing
data this gives exactly the same trade as asking to sell at the close of the
12:00 bar. With missing data:

- clock time still passes; the trade is `gap_exposed`; nothing happens inside
  the missing hours
- if the exit fell due during missing hours, it stays pending and follows the
  approved pending-exit-across-gap rule at the first available bar: open beyond
  the stop: exit at that open (`stop_after_gap`); otherwise exit at that open
  (`time_exit_after_gap`)

Example tests: if 12:00 is missing, the exit fills at the 13:00 open; if 13:00 is
missing, at the 14:00 open; if only two bars are seen in the three hours, the exit
is still at 13:00.

## How we know they follow their rules

- Hand-worked examples for every rule, with the arithmetic in the test comments.
- An **independent oracle** (`tests/phase6_helpers.py`): a second, deliberately
  simple calculation of each written rule, using exact fractions and plain time
  differences. It never imports the strategy code or the production indicators (a
  test enforces this). On many random made-up paths with gaps, for both sides and
  both variants, every decision matches the oracle.
- Look-ahead tests (changing the future never changes past decisions), LONG/SHORT
  mirror tests, identity tests, and sabotage testing of every rule.
- Mirror note for `ma_cross`: 2% of a price is not 2% of its mirror image, so the
  mirror test uses a smooth path where neither level is reached (trades end by
  crosses), and the level **formulas** are checked separately.
