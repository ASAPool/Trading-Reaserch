# The backtest engine (Phase 5), explained

> **COSTS NOT MODELLED (Phase 7).** Every number the engine produces is
> before fees, spread and slippage.
>
> **SYNTHETIC DATA ONLY.** In Phase 5 the engine has never been run on the real
> BTC history, and it cannot be: the real-data entry point always refuses with
> `REAL DATA LOCKED: no frozen train/validation/final-test split exists`.

## What it is

A **backtester** replays price history one bar (one hour) at a time and asks a
strategy "what do you want to do now?". It then simulates the orders under
fixed, conservative rules and records trades and equity.

This one is deliberately small: the goal is that it **provably cannot use
future information**, not that it has many features. The approved plan it
implements is [`phase5-plan.md`](phase5-plan.md).

| File | What it does |
|---|---|
| `src/quantlab/core/engine.py` | The engine, the orders, the results, the real-data lock |
| `src/quantlab/core/indicators.py` | Two reference indicators that obey DQP-1 at gaps |
| `src/quantlab/stats/metrics.py` | Total return, expectancy, max drawdown, trade count |
| `tests/test_phase5_*.py` | 73 tests on made-up data, each worked out by hand |

No new packages. The engine is market-neutral (it never mentions crypto).

## How it cannot see the future

The strategy only ever receives a **view** of a list that the engine adds a bar
to **after that bar has closed**. Future bars are never put anywhere the strategy
can reach. Asking for a later bar raises `LookAheadError`.

A test proves it: change every bar after time *t* and every decision, trade and
equity value up to *t* stays identical.

## What happens in each bar (always in this order)

1. **At the open, resolve the order decided at the previous close.**
   - *Entry*: fills at this open **only if this bar is exactly one hour after
     the decision bar**. Otherwise it **expires** (`next_bar_missing`, recorded).
     If the stop or target is already on the wrong side of the fill price, the
     entry is **cancelled** (`invalid_bracket_at_fill`).
   - *Stop/target adjustment*: becomes active now, before the open check.
2. **Open check** for a position held from an earlier bar (DQP-1 rule, used on
   every bar):
   - open at or beyond the stop: exit **at the open**
   - open at or beyond the target: exit **at the target price** (no windfall)
   - otherwise, a pending market exit fills **at the open**
3. **Inside the bar**: stop and target both touched: assume the **stop** came
   first. Only one touched: fill exactly at that level. This also applies in
   the entry bar.
4. **The bar closes**: equity = 10,000 + realised P&L + open P&L at the close.
5. **Only now** the strategy sees this bar and may give **one** order for the
   next bar: `Entry(side, stop, target)`, `Exit()`, `Adjust(stop, target)`, or
   nothing.

Stops and targets are **absolute prices chosen at decision time**, so the entry
bar can never influence them.

## Missing hours (DQP-1)

- Missing bars are never filled. Nothing is executed or inferred during them,
  and the strategy is not called (it gets no bars).
- A position open during any missing hour is marked **`gap_exposed`**. The engine
  tracks this as it goes **and** re-checks it with `is_gap_exposed()`; if the
  two ever disagree, the run stops with an error.
- **Approved rule: a market exit pending across a gap.** The exit does **not**
  expire. At the first available bar:

| Position | First open after the gap | Result | Reason recorded |
|---|---|---|---|
| LONG | at or below the stop | exit at that **open** | `stop_after_gap` |
| LONG | at or above the target | exit at the **target** | `target_after_gap` |
| SHORT | at or above the stop | exit at that **open** | `stop_after_gap` |
| SHORT | at or below the target | exit at the **target** | `target_after_gap` |
| either | anything else | exit at that **open** | `exit_after_gap` |

The fill is never better than either the stop/target rule or a plain market
exit at that open, and never uses any price from inside the gap.

## Variant A and Variant B

Every run is done twice, each time with a fresh strategy:

- **A (official)**: every bar as given.
- **B**: hours that quality flags mark as Variant-B-masked are **treated exactly
  like missing hours** (entries expire across them, positions become gap_exposed,
  the first-bar rule applies).

Both results include the DQP-1 checks: `compare_variants` (SENSITIVE TO DATA
QUALITY or not) and `gap_exposure_diagnostic` for each variant. If one variant
has no closed trades, expectancy is undefined, and the comparison is reported
as **SENSITIVITY NOT EVALUABLE** with auditor review required (no number is
invented).

## Money

- Starting equity is **10,000 USD** (locked).
- Every trade uses the same **fixed notional** from `config/settings.toml`
  (`[backtest] fixed_notional`, currently 1,000). It must be above 0 and at most
  10,000: **no leverage**. **No compounding**: after a loss the next trade is
  the same size.
- Quantity = notional / fill price. LONG P&L = quantity x (exit - entry);
  SHORT P&L = quantity x (entry - exit).

## Outputs

- **Trades**: side, quantity, entry/exit time and price, exit reason,
  `gap_exposed`, `touched_flagged_bar`, P&L.
- **Order events**: expired and cancelled entries (never silently dropped).
- **Equity curve**: one point per available bar close (none for missing hours).
- **Metrics**: total return, expectancy, max drawdown, trade count.
- **Open position at end**: if a position is still open when the data ends it
  is **not closed** (that would invent a fill). It is reported separately with
  its open P&L at the last close; total return includes that open P&L,
  expectancy and trade count use closed trades only.

## Exit reasons

| Reason | Meaning |
|---|---|
| `stop_at_open` / `target_at_open` | normal bar opened beyond the stop / target |
| `stop_after_gap` / `target_after_gap` | same, at the first bar after missing hours |
| `stop_intrabar` / `target_intrabar` | the level was touched inside the bar |
| `stop_and_target_in_bar_stop_assumed` | both touched; stop assumed first |
| `market_exit` | requested exit, filled at the next open |
| `exit_after_gap` | requested exit pending across a gap, filled at the first open after it |

## Decisions made while building (not covered by the plan's wording)

These follow the plan's conservative direction. They are written down so they
can be reviewed; none changes an approved rule.

1. **The open check runs before a pending market exit on every bar**, not only
   after gaps (plan section 4 says the open check applies to every bar). On a
   normal bar that opens beyond the target, a requested exit therefore fills at
   the target, not at the better open.
2. **An adjustment decided just before a gap becomes active at the first bar
   after the gap** (plan rule 3.6: "active from the following bar").
3. **A position still open at the end of the data is not force-closed**
   (see Outputs).
4. **Sensitivity is "NOT EVALUABLE"** when a variant has no closed trades.

## How we know it works

- 73 tests on tiny made-up datasets, with the correct answers worked out by hand
  in the test comments: every timing rule, every fill rule, LONG and SHORT, every
  gap rule (including the approved pending-exit rule), sizing, Variant B, labels
  and the real-data lock.
- **Look-ahead proof** and **mirror tests** (a LONG run on a price path and a
  SHORT run on its mirror image give mirror-image trades) on random made-up paths
  with gaps.
- **Sabotage (mutation) testing**: 43 deliberately broken versions of the engine,
  indicators and metrics were run against the tests; **all 43 were caught**.
  Examples: entries that do not expire across a gap, a pending exit that expires
  across a gap or skips the stop/target rule, a target filled at the better open,
  target-first when both levels are touched, compounding, a wrong SHORT P&L sign,
  the strategy seeing every bar, masking disabled in Variant B, the real-data
  lock removed, an SMA window that spans a gap, an EMA that is not reset at a gap.
