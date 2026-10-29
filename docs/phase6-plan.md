# Phase 6 plan: hand-verifiable test strategies

**STATUS: APPROVED by the owner**, with the binding clarifications in
section 13 (they take precedence over anything earlier in this file).

Read `docs/PROJECT_HANDOFF.md`, `docs/phase5-plan.md` and
`docs/backtest-engine.md` first. Do not widen the scope.

---

## 1. Goal

Prove that **strategies plugged into the Phase 5 engine do exactly what their
written rules say**, and nothing else, so that later research results can be
trusted to reflect the rules rather than bugs.

These are **test strategies, not research**. They are chosen because every
trade can be checked by hand, not because they might make money. No claim
about profitability is made or implied.

## 2. Locks that stay in force

- **REAL-DATA LOCK:** synthetic, made-up data only. No strategy, benchmark,
  speed check or performance calculation touches the real BTC history.
  `run_real_dataset()` keeps refusing. A test proves the strategies package
  cannot load data files.
- **No parameter tuning.** Parameters are fixed in the strategy documentation
  before any test is written. No search over parameters, no "best" settings.
- DQP-1 is fixed. Phase 5 engine rules are unchanged (except the one small
  addition in section 6, which needs approval).
- One position at a time, fixed notional, no compounding, no leverage.
- Costs stay zero and every result says `COSTS NOT MODELLED (Phase 7)`.

## 3. Scope: four test strategies

Each one has a plain-language rule sheet in `docs/strategies.md`, a version
number, and validated parameters. Each runs as LONG or SHORT (`side`
parameter); the SHORT version is the exact mirror image of the LONG version.

| # | Name | Rule (LONG; SHORT is the mirror) | What it proves |
|---|---|---|---|
| 1 | `null` | Never trades. | Zero trades, flat equity at 10,000, labels present. A baseline for the plumbing. |
| 2 | `buy_and_hold` | Enter at the first possible bar; never exit. | Entry timing, entry expiry across a gap (then retry next bar), open position reported at end, mark-to-market equity. |
| 3 | `ma_cross` | Fast SMA crosses above slow SMA: enter. Crosses below: exit. Stop and target are fixed percentages of the **decision bar's close** (known at decision time). | Window-indicator gap rules (no signal until both windows are complete again after a gap), market exits, pending exit across a gap, stops and targets. |
| 4 | `channel_breakout` | Close above the highest high of the previous N bars: enter; stop at the lowest low of those N bars; exit after K bars held. | Lookback windows that must not span a gap, absolute stops from past data only, time-based exits, adjustments. |

A "cross" needs two consecutive contiguous bars with valid values; a cross
measured across a gap does not count (DQP-1: undefined across gaps).

## 4. Strategy interface (small)

- New market-neutral package `src/quantlab/strategies/`, added to the
  "never imports `quantlab.markets`" structure test, and forbidden from
  importing `quantlab.data` or opening files.
- Each strategy: `name`, `version`, a frozen parameter object that rejects
  bad values (e.g. fast window >= slow window, non-positive percentages),
  `warmup_bars` (documented), and `on_bar(view, position)` returning one
  Phase 5 order or nothing.
- A small registry maps names to strategies so a run records exactly which
  strategy, version and parameters were used.

## 5. How correctness is proven

1. **Hand-worked tests:** tiny made-up datasets (8-20 bars) for every rule of
   every strategy; expected signals, fills and P&L worked out in the test
   comments.
2. **Independent reference calculation (tests only):** a second, deliberately
   simple, non-incremental calculation of each strategy's signals, written
   separately from the strategy code. On many random made-up price paths
   with gaps, the engine's recorded decisions must equal the reference
   exactly.
3. **Gap discipline:** no order is ever issued while a needed indicator is
   unavailable, checked on random paths with gaps.
4. **Look-ahead proof** (as in Phase 5) for each strategy: changing every bar
   after t leaves every decision up to t identical.
5. **Mirror tests:** LONG on a path and SHORT on its mirror image give
   mirror-image trades.
6. **Determinism and isolation:** same inputs give an identical result twice;
   Variant A and B get fresh strategy instances with no shared state.
7. **Mutation (sabotage) testing:** break each strategy rule in turn
   (off-by-one windows, cross across a gap allowed, stop from the wrong bar,
   wrong side, warm-up skipped...); a test must fail every time.
8. The full existing suite (228 tests) must still pass.

## 6. One small engine addition (needs approval)

`run_synthetic` records a **strategy identity** in the result: strategy name,
version, and parameters. Nothing else in the engine changes. Without this, a
result cannot say which rules produced it.

## 7. Outputs

The Phase 5 result (trades, order events, equity curve, minimal metrics,
Variant A/B, sensitivity and gap diagnostics) plus the strategy identity.
Every result labelled `COSTS NOT MODELLED (Phase 7)` and `SYNTHETIC`.

## 8. Deliberately excluded

Real data of any kind; parameter optimisation or comparison of parameter
sets; "good" or research strategies; costs, slippage, delays (Phase 7); new
statistics beyond Phase 5's metrics; multiple positions or markets;
limit/stop entries; leverage; compounding; the split freeze (Phase 8);
experiment storage (Phase 9).

## 9. Files

`src/quantlab/strategies/__init__.py`, `base.py` (interface, parameters,
registry), `null.py`, `buy_and_hold.py`, `ma_cross.py`,
`channel_breakout.py`; `tests/test_phase6_*.py`; `docs/strategies.md`;
updates to the handoff, README and structure test. No new packages.

## 10. Steps (each tested before the next)

1. Interface, parameters, registry, structure-test update; `null`.
2. `buy_and_hold`.
3. `ma_cross` + hand tests + reference + gap/mirror/look-ahead tests.
4. `channel_breakout` + the same.
5. Strategy identity in results (section 6).
6. Mutation testing; full suite; docs; commit and push.
7. Owner runs the full suite on the laptop.

## 11. What success looks like

Every strategy's every decision matches its written rules, by hand on tiny
examples and by the independent reference on many random synthetic paths;
every sabotaged rule is caught; the real data is still untouched.

## 12. Open questions for the owner

1. Approve the four strategies in section 3 (or remove/replace any)?
2. Approve the small engine addition in section 6?
3. Fixed parameters proposed for the tests (synthetic only, not tuned):
   `ma_cross` fast 3 / slow 5 bars, stop 2% and target 4% from the decision
   close; `channel_breakout` N = 4 bars, exit after K = 3 bars held.
   Approve these?

## 13. Owner's approval and binding clarifications

Approved: all four strategies, the strategy identity, and the fixed synthetic
settings. Clarifications:

1. The four strategies exist **only to verify engine behaviour on synthetic
   data**. They are not research strategies; their results must never be
   presented as evidence of profitability.
2. **Strategy identity**: every result deterministically records the strategy
   name, an explicit version, and all settings used, never relying only on a
   class name or memory representation. Same strategy/version/settings, same
   recorded identity, every time.
3. **ma_cross, exactly** (LONG; SHORT is the exact mirror):
   - entry cross at closed bar t only when `fast[t-1] <= slow[t-1]` and `fast[t] > slow[t]`
   - exit cross only when `fast[t-1] >= slow[t-1]` and `fast[t] < slow[t]`
   - equality alone is not a cross; t-1 and t must both have valid, fully
     warmed-up values; a cross is never inferred across a gap or warm-up
   - fast 3 h, slow 5 h, stop 2% and target 4% from the **decision bar close**,
     fixed at decision time, never recalculated from the fill; if the next open
     makes them invalid under Phase 5 rules, the entry is cancelled and recorded.
4. **channel_breakout, exactly**: LONG signal at closed bar t when `close[t]` is
   strictly greater than the highest HIGH of the 4 immediately preceding hourly
   bars (bar t excluded). Those 4 bars must be genuinely consecutive hourly
   observations; a missing hour anywhere in the lookback means no signal.
   LONG stop = lowest LOW of the same 4 bars. SHORT mirrored (close strictly below
   the lowest LOW; stop at the highest HIGH). Equality is not a breakout.
5. **3-hour exit uses real elapsed UTC time**, not observed bars. A fill at the
   10:00 open has completed three hours at the 13:00 boundary. If data is missing
   while the position is held, clock time still passes, the trade is gap_exposed,
   nothing is executed inside missing hours, and an exit that fell due during the
   missing period stays pending and follows the approved pending-exit-across-gap
   rule at the first available bar. Hand-worked tests make the timing unambiguous.
6. The test oracle is **genuinely independent**: it never calls the production
   strategy code or its signal helpers.
7. **REAL-DATA LOCK is absolute**: no Phase 6 strategy runs on real BTC data; no
   performance numbers, previews, smoke tests, benchmarks or speed tests on it.
