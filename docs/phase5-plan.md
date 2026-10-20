# Phase 5 plan: the smallest trustworthy backtester

**STATUS: APPROVED by the owner in full.** Section 6 (pending market exits
across a missing-data gap) was approved at the start of the Phase 5 build
session, as written. **Phase 5 is built:** see `docs/backtest-engine.md`.

Build this in a fresh Claude Code session. Read `docs/PROJECT_HANDOFF.md`
first. Do not widen the scope. Explain progress to the owner in plain language,
one step at a time.

---

## 1. Goal

An engine that replays history one bar at a time and **provably cannot use
future information**. Correctness over features.

## 2. Scope (deliberately small)

- One position at a time; LONG or SHORT.
- Order types: market entry at the next valid open; market exit at the next
  valid open; an optional protective stop and take-profit attached to the
  position.
- **Trading costs are zero** and every result is labelled
  `COSTS NOT MODELLED (Phase 7)`.
- Market-neutral code only (`core/`, `stats/`); no new packages.

## 3. Timing rules (locked)

1. A strategy sees bar t only **after it has closed**, and only bars up to and
   including t. Any attempt to read a later bar raises an error.
2. A decision made at the close of bar t may only create an order for **the next
   valid bar**.
3. **Entries:** fill only at the next bar's open, and only if that bar is
   **exactly one hour after bar t**. Otherwise the entry **expires** (recorded,
   not silently dropped).
4. **Stops and targets attached to an entry are absolute prices determined
   entirely from information available at or before bar t.** The entry bar's
   high, low, close, or any later value can never influence them. (So "stop = 2%
   below the fill price" is not allowed in Phase 5, because the fill price is not
   known at bar t.)
5. **Bracket sanity at the fill:** if, at the entry open, the stop or target is
   already on the wrong side of the fill price (LONG: stop >= fill or target <= fill;
   SHORT mirrored), the entry is **cancelled** and recorded as
   `invalid_bracket_at_fill`. No position is opened.
6. **Adjustments:** a stop/target change may only be decided after a bar closes,
   and becomes active from the **following** bar (including that bar's open check).
7. No decision can act on the same bar it was made on.

Required tests: entry expires when the next bar is missing; brackets cannot be
influenced by entry-bar or later values (changing them leaves the orders
identical); an adjustment decided at bar t is not active during bar t and is
active at bar t+1's open; invalid bracket at fill cancels the entry.

## 4. Fills inside a bar (conservative, mirror-symmetric LONG/SHORT)

For each bar while a position is open, in this order:

1. **Open already past a level** (same rule as DQP-1, applied to every bar):
   - past the stop: exit at the **open**
   - past the target: exit at the **target price** (no windfall)
2. **Stop and target both inside the bar's range:** assume the **stop was hit
   first** (pessimistic). Fill at the stop price.
3. **Only one level touched:** fill exactly at that level.
4. The bracket is active from the entry bar's open onward, so a position can be
   stopped out within its entry bar.

## 5. Position size (locked)

- `starting_equity = 10_000` USD.
- `fixed_notional` USD per trade, set in config; must satisfy
  `0 < fixed_notional <= starting_equity` (no leverage).
- `quantity = fixed_notional / entry_fill_price`, identical math for LONG and SHORT.
- P&L: LONG `quantity * (exit - entry)`; SHORT `quantity * (entry - exit)`.
- **No compounding**: later trades use the same `fixed_notional` whatever the
  equity is.
- Equity curve at each bar close = `starting_equity + realized P&L +
  unrealized P&L` (open position marked at that bar's close).

## 6. Pending market EXIT across a missing-data gap  (APPROVED)

Situation: at the close of bar t the strategy requests a market exit, but the
next hourly bar is missing.

Approved rule:
1. The exit request **does not expire** (unlike an entry). It stays pending.
2. During the missing hours **nothing is executed** and nothing is inferred
   (DQP-1). The position stays open and the trade is marked `gap_exposed`.
3. The strategy cannot cancel or change the pending exit during the gap (it
   receives no bars, so it makes no decisions).
4. At the **first available bar after the gap**:
   a. first apply DQP-1 `first_bar_after_gap` to the position's stop/target:
      if the open is past the stop, exit at the **open**; if past the target,
      exit at the **target price**;
   b. otherwise fill the pending exit at **that bar's open**.
   The exit reason records which applied (`stop_after_gap`, `target_after_gap`,
   or `exit_after_gap`).
5. Result: the fill is never better than either the stop/target rule or a plain
   market exit at the first available open would give, and never uses any price
   from inside the gap.

## 7. DQP-1 inside the engine

- Bars are split into **segments** at gaps; one-bar returns across a gap are
  undefined.
- Reference indicators (to prove the rules, not for research): a moving average
  that is unavailable until its window is complete again after a gap, and an EMA
  that resets at a gap and re-warms.
- No signal while any indicator it needs is unavailable.
- Positions open during any missing hour are `gap_exposed`; at the first bar after
  a gap, `core.gap_execution.first_bar_after_gap` is applied.
- **Variant A and Variant B** are produced for every run; Variant B treats the
  Variant-B-masked hours from quality flags exactly like missing hours. Results
  include `stats.sensitivity.compare_variants` and
  `stats.sensitivity.gap_exposure_diagnostic`.
- Every result carries `experiment_data_identity` (policy version, commit,
  fingerprint, quality-flags fingerprint, code commit) when run on a real dataset.

## 8. REAL-DATA LOCK (locked)

- **Phase 5 must not run any strategy, toy strategy, benchmark, speed check or
  performance calculation on the real BTC history.** Synthetic, made-up data only.
- The same holds for Phases 6 and 7: they may build strategy and cost machinery
  using synthetic data, but **no strategy performance may be evaluated on real
  BTC history until the train / validation / final-test split and the leakage
  rules are frozen** (the "split freeze", see roadmap).
- The final test period must remain completely untouched until its proper
  evaluation stage.
- **Enforced in code:** the engine has a synthetic entry point used by tests, and
  a real-dataset entry point that in Phase 5 **always refuses** with
  `REAL DATA LOCKED: no frozen train/validation/final-test split exists`.
  A test proves the lock. Only the future split-freeze phase may unlock it, and
  only for the train/validation periods.

## 9. Outputs

- Trade list: entry/exit time and price, side, quantity, exit reason,
  `gap_exposed`, whether a flagged bar was touched.
- Equity curve (section 5).
- Minimal metrics needed by DQP-1: total return, expectancy, max drawdown, trade
  count. Full statistics come later.
- Every result labelled `COSTS NOT MODELLED (Phase 7)`.

## 10. Deliberately excluded

Real strategies (Phase 6), costs/slippage/delays (Phase 7), the split freeze,
experiment storage, compounding/position sizing, multiple positions, limit or
stop entries, leverage.

## 11. Testing

- Tiny made-up datasets (5-15 bars) with the correct results **worked out by hand
  in the test comments**.
- Every fill rule, every timing rule (section 3), every gap rule (sections 6-7),
  sizing math (section 5), Variant B, the refusal checks and the real-data lock.
- **Look-ahead proof:** changing every bar after time t leaves every decision up
  to t identical.
- **Mirror tests:** a LONG run on a price path and a SHORT run on its mirror image
  give mirror-image results.
- **Mutation (sabotage) testing:** disable each rule in turn; a test must fail.
- The full existing suite (155 tests) must still pass.

## 12. Files

`src/quantlab/core/engine.py`, `src/quantlab/core/indicators.py`,
`src/quantlab/stats/metrics.py`, `tests/test_phase5_*.py`,
`docs/backtest-engine.md`. Market-neutral; no new packages.

## 13. Revised roadmap (holdout protection first)

5. Smallest trustworthy backtester (synthetic data only)
6. Hand-verifiable test strategies (synthetic data only)
7. Costs and execution assumptions (synthetic data only)
8. **Split freeze:** define and freeze train / validation / final-test periods and
   leakage rules, fingerprinted and registered like DQP-1. Boundaries are chosen
   by date, before any real-data strategy result exists. Nothing is evaluated here.
9. Experiment tracking
10. First real-data evaluations: train/validation periods only
11. Six-agent research architecture
12. First multi-agent experiment
13. ETH, SOL, XRP
14. Paper trading (simulated only)
15. Dashboard

The final test period is opened only once, at its proper evaluation stage.
