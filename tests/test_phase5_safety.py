"""Phase 5: look-ahead proof, LONG/SHORT mirror symmetry, Variant A/B,
metrics, labels and the REAL-DATA LOCK. MADE-UP data only.
"""

import random
import unittest

import phase5_helpers as h
from phase5_helpers import U, Script, T, bar, flat, mirror_bar, run
from quantlab.core.bars import Bar
from quantlab.core.engine import (
    REAL_DATA_LOCK_MESSAGE, Adjust, Entry, Exit, LookAheadError, RealDataLockedError,
    format_report, run_real_dataset, run_synthetic, BacktestConfig,
)
from quantlab.core.gap_execution import LONG, SHORT
from quantlab.stats.metrics import COSTS_LABEL, compute_metrics, max_drawdown

MIRROR_C = 2000.0     # mirror: every price p becomes 2000 - p (prices stay near 1000)


def random_path(seed, n=400, start_hour=0, start_price=1000, gap_prob=0.06):
    """Integer-priced made-up bars (exact in floating point) with random gaps."""
    rnd = random.Random(seed)
    bars, close, hour = [], start_price, start_hour
    while len(bars) < n:
        if rnd.random() < gap_prob:
            hour += rnd.randint(1, 4)                 # missing hours
        o = close + rnd.randint(-4, 4)
        c = o + rnd.randint(-8, 8)
        hi = max(o, c) + rnd.randint(0, 6)
        lo = min(o, c) - rnd.randint(0, 6)
        bars.append(bar(hour, o, hi, lo, c))
        close, hour = c, hour + 1
    return bars


class Momentum:
    """Test-only rule (not research). sign=+1 on the original path, -1 on the
    mirrored path: then the mirrored run takes the mirror-image decisions."""

    def __init__(self, sign=1):
        self.sign = sign

    def on_bar(self, view, position):
        b, s = view.latest, self.sign
        if position is not None:
            held = int((b.open_time - position.entry_time).total_seconds() // h.H)
            if held >= 4:
                return Exit()
            if held == 2:
                e = position.entry_price
                return Adjust(e - 1 if position.side == LONG else e + 1, position.target)
            return None
        if len(view) < 2 or view.missing_before(-1):
            return None                               # one-bar return undefined across a gap
        move = (b.close - view[-2].close) * s
        up, down = (LONG, SHORT) if s > 0 else (SHORT, LONG)
        if move >= 3:
            return Entry(up, b.close - 20 * s, b.close + 30 * s)
        if move <= -3:
            return Entry(down, b.close + 20 * s, b.close - 30 * s)
        return None


def run_momentum(bars, sign=1, flagged=None):
    return run(bars, strategy=lambda: Momentum(sign), flagged=flagged)


class TestLookAheadProof(unittest.TestCase):

    def test_strategy_only_ever_sees_closed_bars(self):
        bars = random_path(1, n=60)
        made = []
        run(bars, strategy=lambda: made.append(Script({})) or made[-1])
        calls = made[0].calls
        self.assertEqual([(c[0], c[1]) for c in calls],
                         [(b.open_time, k + 1) for k, b in enumerate(bars)])

        class Peek:
            def on_bar(self, view, position):
                view[-1]
                view[len(view) - 1]
                return view[len(view)]
        with self.assertRaises(LookAheadError):
            run(bars, strategy=Peek)

    def test_changing_every_bar_after_t_leaves_everything_up_to_t_identical(self):
        bars = random_path(2)
        full = run_momentum(bars).variant_a
        self.assertGreater(len(full.trades), 10)
        for cut in (5, 50, 137, 250, 398):
            t_cut = bars[cut].open_time
            future = random_path(100 + cut, n=60, start_hour=int((t_cut - h.BASE).total_seconds() // h.H) + 1,
                                 start_price=500)
            altered = run_momentum(bars[:cut + 1] + future).variant_a
            with self.subTest(cut=cut):
                def upto(v):
                    return ([d for d in v.decisions if d[0] <= t_cut],
                            [e for e in v.equity_curve if e[0] <= bars[cut].close_time],
                            [tr for tr in v.trades if tr.exit_time <= t_cut],
                            [e for e in v.order_events if e.resolved_at and e.resolved_at <= t_cut])
                self.assertEqual(upto(full), upto(altered))
                self.assertEqual(len(upto(full)[0]), cut + 1)


class TestMirrorSymmetry(unittest.TestCase):

    def test_long_on_path_and_short_on_mirror_give_mirror_results(self):
        reasons = set()
        for seed in (4, 5, 6):
            with self.subTest(seed=seed):
                bars = random_path(seed)
                mirrored = [mirror_bar(b, MIRROR_C) for b in bars]
                a = run_momentum(bars, 1).variant_a
                m = run_momentum(mirrored, -1).variant_a
                self.assertGreater(len(a.trades), 10)
                self.assertTrue(any(t.gap_exposed for t in a.trades))
                self.assertEqual(len(a.trades), len(m.trades))
                flip = {LONG: SHORT, SHORT: LONG}
                for x, y in zip(a.trades, m.trades):
                    self.assertEqual((flip[x.side], x.entry_time, x.exit_time, x.exit_reason,
                                      x.gap_exposed),
                                     (y.side, y.entry_time, y.exit_time, y.exit_reason,
                                      y.gap_exposed))
                    self.assertEqual(MIRROR_C - x.entry_price, y.entry_price)
                    self.assertEqual(MIRROR_C - x.exit_price, y.exit_price)
                    # identical per-unit profit; quantity = notional / own fill price
                    self.assertAlmostEqual(x.net_pnl / x.quantity, y.net_pnl / y.quantity, places=9)
                    self.assertAlmostEqual(y.quantity, 1000 / y.entry_price, places=12)
                self.assertEqual([(e.kind, e.reason, e.decided_at) for e in a.order_events],
                                 [(e.kind, e.reason, e.decided_at) for e in m.order_events])
                reasons |= {t.exit_reason for t in a.trades}
        # the random paths exercise the gap rules too, including the approved
        # pending-exit-across-a-gap rule (exit_after_gap)
        self.assertTrue({"stop_intrabar", "target_intrabar", "stop_at_open", "market_exit",
                         "stop_after_gap", "exit_after_gap"} <= reasons, reasons)


class TestVariants(unittest.TestCase):

    def test_variant_b_treats_masked_hours_exactly_like_missing(self):
        bars = [flat(i, 100) for i in range(6)]
        flagged = {U(2): True, U(4): False}             # h2 masked in B; h4 corroborated
        r = run(bars, {0: Entry(LONG), 3: Exit()}, flagged=flagged)
        a, b = r.variant_a, r.variant_b
        self.assertEqual((a.variant, b.variant, r.official), ("A", "B", "A"))
        self.assertEqual((a.missing_times, b.missing_times), ((), (U(2),)))
        self.assertEqual(len(b.equity_curve), 5)
        ta, tb = a.trades[0], b.trades[0]
        self.assertEqual((ta.gap_exposed, ta.touched_flagged_bar), (False, True))
        self.assertEqual((tb.gap_exposed, tb.touched_flagged_bar), (True, True))  # h4 still present

    def test_entry_into_masked_hour_expires_only_in_b(self):
        bars = [flat(i, 100) for i in range(4)]
        r = run(bars, {1: Entry(LONG)}, flagged={U(2): True})
        self.assertEqual(r.variant_a.order_events, ())
        self.assertEqual([e.reason for e in r.variant_b.order_events], ["next_bar_missing"])

    def test_touched_flagged_bar_false_when_no_flag_in_trade(self):
        bars = [flat(i, 100) for i in range(6)]
        r = run(bars, {0: Entry(LONG), 1: Exit()}, flagged={U(4): False})
        self.assertFalse(r.variant_a.trades[0].touched_flagged_bar)

    def test_sensitivity_labels(self):
        # A: stopped at 90 in masked h2 -> -100, R -0.01, E -0.1, D 0.01.
        # B: h2 missing; h3 continues; exit at h4 open 100 -> 0, R 0. Sign of R differs.
        bars = [flat(0, 100), flat(1, 100), bar(2, 100, 100, 50, 60), flat(3, 100), flat(4, 100)]
        orders = {0: Entry(LONG, 90), 3: lambda p: Exit() if p else None}
        r = run(bars, orders, flagged={U(2): True})
        ma, mb = r.variant_a.metrics, r.variant_b.metrics
        self.assertAlmostEqual(ma.total_return, -0.01, places=15)   # 9900 / 10000 - 1
        self.assertEqual((ma.expectancy, ma.max_drawdown, ma.trade_count), (-0.1, 0.01, 1))
        self.assertEqual((mb.total_return, mb.expectancy, mb.max_drawdown, mb.trade_count),
                         (0, 0, 0, 1))
        self.assertEqual(r.sensitivity["label"], "SENSITIVE TO DATA QUALITY")
        self.assertTrue(r.sensitivity["auditor_review_required"])
        self.assertEqual(r.variant_a.gap_diagnostic["verdict"], "NO GAP EXPOSURE")
        self.assertEqual(r.variant_b.gap_diagnostic["gap_exposed_trades"], 1)

        same = run(bars, orders)
        self.assertEqual(same.sensitivity["label"], "NOT SENSITIVE TO DATA QUALITY")
        self.assertEqual(same.sensitivity["reasons"], [])

        none = run(bars, {})
        self.assertIn("NOT EVALUABLE", none.sensitivity["label"])
        self.assertTrue(none.sensitivity["auditor_review_required"])

    def test_variants_get_fresh_strategy_instances(self):
        made = []
        run([flat(0, 100)], strategy=lambda: made.append(Script({})) or made[-1])
        self.assertEqual(len(made), 2)
        self.assertIsNot(made[0], made[1])


class TestMetrics(unittest.TestCase):

    def test_hand_worked_metrics(self):
        # peak 10500, trough 9450 -> (10500 - 9450) / 10500 = 0.1 ; R = 9900/10000 - 1 = -0.01
        m = compute_metrics(10_000, [10_000, 10_500, 9_450, 9_900], [0.05, -0.1, 0.02])
        self.assertAlmostEqual(m.max_drawdown, 0.1, places=15)
        self.assertAlmostEqual(m.total_return, -0.01, places=15)
        self.assertAlmostEqual(m.expectancy, (0.05 - 0.1 + 0.02) / 3, places=15)
        self.assertEqual((m.trade_count, m.costs), (3, COSTS_LABEL))

    def test_starting_equity_is_the_first_peak(self):
        self.assertEqual(max_drawdown(10_000, [9_000, 9_500]), 0.1)
        self.assertEqual(max_drawdown(10_000, [10_100, 10_200]), 0)

    def test_no_trades_means_undefined_expectancy(self):
        m = compute_metrics(10_000, [10_000], [])
        self.assertIsNone(m.expectancy)
        self.assertEqual((m.trade_count, m.total_return), (0, 0))


class TestLabelsAndLock(unittest.TestCase):

    def test_every_result_says_costs_not_modelled(self):
        r = run([flat(0, 100), flat(1, 100), flat(2, 101)], {0: Entry(LONG), 1: Exit()})
        self.assertEqual(COSTS_LABEL, "COSTS NOT MODELLED (Phase 7)")
        for obj in (r, r.variant_a, r.variant_b, r.variant_a.metrics, r.variant_b.metrics):
            self.assertEqual(obj.costs, COSTS_LABEL)
        text = format_report(r)
        self.assertTrue(text.startswith(COSTS_LABEL) and text.endswith(COSTS_LABEL))
        self.assertIn("Variant A (OFFICIAL)", text)
        self.assertIn("SYNTHETIC", text)
        self.assertTrue(r.data_identity["dataset"].startswith("SYNTHETIC"))
        self.assertEqual(r.data_identity["policy_version"], "DQP-1")

    def test_real_dataset_entry_point_always_refuses(self):
        self.assertEqual(REAL_DATA_LOCK_MESSAGE,
                         "REAL DATA LOCKED: no frozen train/validation/final-test split exists")
        for args, kwargs in (((), {}), (("data/coinbase-exchange",), {"strategy": None})):
            with self.assertRaises(RealDataLockedError) as cm:
                run_real_dataset(*args, **kwargs)
            self.assertEqual(str(cm.exception), REAL_DATA_LOCK_MESSAGE)

    def test_synthetic_entry_point_refuses_anything_but_a_synthetic_dataset(self):
        bars = (flat(0, 100),)
        with self.assertRaises(RealDataLockedError) as cm:
            run_synthetic(bars, lambda: Script({}), BacktestConfig(1000), h.POLICY)
        self.assertIn("REAL DATA LOCKED", str(cm.exception))

    def test_engine_code_cannot_load_market_data(self):
        for rel in ("core/engine.py", "core/indicators.py", "stats/metrics.py"):
            text = (h.ROOT / "src" / "quantlab" / rel).read_text(encoding="utf-8")
            with self.subTest(rel):
                for forbidden in ("quantlab.data", "quantlab.markets", "open(", "csv", "Path("):
                    self.assertNotIn(forbidden, text)

    def test_bars_are_plain_core_bars(self):
        self.assertIsInstance(flat(0, 1), Bar)


if __name__ == "__main__":
    unittest.main()
