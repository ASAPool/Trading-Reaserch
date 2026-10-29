"""Phase 6: ma_cross (fast 3 h, slow 5 h, stop 2%, target 4% from the decision
close). Hand-worked examples, the independent oracle on random made-up paths,
look-ahead and mirror tests. MADE-UP data only; TEST STRATEGY, not research.

Fixed notional 1,000 USD: a fill at 100 buys 10 units.
"""

import math
import random
import unittest

import phase6_helpers as h6
from phase5_helpers import T, U, bar, flat, mirror_bar
from phase6_helpers import LONG, SHORT, assert_matches_oracle, oracle_ma_cross, run_recorded
from quantlab.core.engine import Entry, Exit
from quantlab.strategies.ma_cross import MACross


def entries(recorder):
    return [b.open_time for b, _, o in recorder.log if isinstance(o, Entry)]


def exits(recorder):
    return [b.open_time for b, _, o in recorder.log if isinstance(o, Exit)]


def closes(*pairs):
    return [flat(hour, c) for hour, c in pairs]


class TestHandWorked(unittest.TestCase):

    # Closes: h0-h4 95, h5 100, h6 100, h7-h10 99 (all flat bars).
    PATH = closes((0, 95), (1, 95), (2, 95), (3, 95), (4, 95), (5, 100), (6, 100),
                  (7, 99), (8, 99), (9, 99), (10, 99))

    def test_long_full_cycle(self):
        # h4: first slow value (95), but t-1 = h3 has no slow value -> no signal.
        # h5: prev fast 95 <= prev slow 95; now fast (95+95+100)/3 = 96.67 >
        #     slow (4*95+100)/5 = 96 -> ENTRY cross. stop 100*0.98 = 98, target 104.
        # h6: fill at the open 100 -> 10 units. fast 98.33 > slow 97 (no exit cross)
        # h7: fast 99.67 > slow 97.8 ; h8: fast 99.33 > slow 98.6
        # h9: prev 99.33 >= 98.6; now fast 99 < slow (100+100+99+99+99)/5 = 99.4 -> EXIT cross
        # h10: exit fills at the open 99 -> P&L 10 * (99 - 100) = -10
        result, rec, _ = run_recorded(self.PATH, MACross)
        self.assertEqual(entries(rec), [T(5)])
        self.assertEqual(exits(rec), [T(9)])
        order = rec.log[5][2]
        self.assertAlmostEqual(order.stop, 98, places=12)
        self.assertAlmostEqual(order.target, 104, places=12)
        (t,) = result.variant_a.trades
        self.assertEqual((t.side, t.entry_time, t.entry_price, t.exit_time, t.exit_price,
                          t.exit_reason, t.net_pnl), (LONG, T(6), 100, T(10), 99, "market_exit", -10))

    def test_short_is_the_exact_mirror(self):
        # Mirror around 200: closes 105 x5, 100, 100, 101 x4. Same cross times;
        # SHORT stop 100*1.02 = 102, target 96. P&L 10 * (100 - 101) = -10.
        result, rec, _ = run_recorded([mirror_bar(b, 200) for b in self.PATH],
                                      lambda: MACross(side=SHORT))
        self.assertEqual((entries(rec), exits(rec)), ([T(5)], [T(9)]))
        order = rec.log[5][2]
        self.assertEqual(order.side, SHORT)
        self.assertAlmostEqual(order.stop, 102, places=12)
        self.assertAlmostEqual(order.target, 96, places=12)
        (t,) = result.variant_a.trades
        self.assertEqual((t.side, t.entry_price, t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (SHORT, 100, T(10), 101, "market_exit", -10))

    def test_equality_alone_is_not_a_cross(self):
        # h4: fast (100+100+97)/3 = 99 < slow (400+97)/5 = 99.4
        # h5 close 103: fast (100+97+103)/3 = 100 == slow (100+100+100+97+103)/5 = 100 -> NOT a cross
        # h6 close 106: prev equal (100 <= 100); fast (97+103+106)/3 = 102 > slow 506/5 = 101.2 -> cross
        path = closes((0, 100), (1, 100), (2, 100), (3, 100), (4, 97), (5, 103), (6, 106), (7, 106))
        _, rec, _ = run_recorded(path, MACross)
        self.assertEqual(entries(rec), [T(6)])

    def test_equal_and_unchanged_averages_never_signal(self):
        _, rec, _ = run_recorded(closes(*[(i, 100) for i in range(12)]), MACross)
        self.assertEqual([o for _, _, o in rec.log], [None] * 12)

    def test_no_cross_inferred_across_a_gap_or_warm_up(self):
        # Without a gap the cross happens at h6 (prev 95 = 95; fast 96.67 > slow 96).
        no_gap = closes(*[(i, 95) for i in range(6)], (6, 100), (7, 100))
        self.assertEqual(entries(run_recorded(no_gap, MACross)[1]), [T(6)])
        # With h5 missing, both averages restart: slow is next valid at h10 and the
        # first evaluable bar is h11 (equal: no cross). h12 close 106: prev 100 = 100,
        # fast 102 > slow 101.2 -> the first entry is at h12, not h6.
        gap = closes(*[(i, 95) for i in range(5)], *[(i, 100) for i in range(6, 12)],
                     (12, 106), (13, 106))
        _, rec, _ = run_recorded(gap, MACross)
        self.assertEqual(entries(rec), [T(12)])
        self.assertEqual([o for b, _, o in rec.log if b.open_time < T(12)], [None] * 11)

    def test_levels_come_from_decision_close_not_fill(self):
        # Decided at h5 (close 100): stop 98, target 104. Fill at h6 open 101 -> still 98 / 104,
        # NOT 101*0.98 = 98.98 / 105.04.
        path = closes(*[(i, 95) for i in range(5)], (5, 100)) + [flat(6, 101)]
        p = run_recorded(path, MACross)[0].variant_a.open_position_at_end
        self.assertEqual(p["entry_price"], 101)
        self.assertAlmostEqual(p["stop"], 98, places=12)
        self.assertAlmostEqual(p["target"], 104, places=12)

    def test_next_open_invalidating_levels_cancels_entry(self):
        for o in (97, 98, 104, 105):     # at/below the stop 98, or at/above the target 104
            with self.subTest(open=o):
                path = closes(*[(i, 95) for i in range(5)], (5, 100)) + [flat(6, o)]
                v = run_recorded(path, MACross)[0].variant_a
                self.assertEqual([(e.kind, e.reason, e.decided_at) for e in v.order_events],
                                 [("entry_cancelled", "invalid_bracket_at_fill", T(5))])
                self.assertEqual(v.trades, ())
                self.assertIsNone(v.open_position_at_end)

    def test_settings_validation(self):
        for kw in ({"fast_hours": 5, "slow_hours": 5}, {"fast_hours": 0}, {"slow_hours": 2.5},
                   {"stop_fraction": 0}, {"target_fraction": 1}, {"stop_fraction": math.nan},
                   {"side": "BUY"}, {"fast_hours": True}):
            with self.subTest(kw), self.assertRaises(ValueError):
                MACross(**kw)


class TestAgainstIndependentOracle(unittest.TestCase):

    def test_random_paths_both_sides_both_variants(self):
        total = 0
        for seed in range(8):
            bars = h6.random_path(seed)
            for side in (LONG, SHORT):
                with self.subTest(seed=seed, side=side):
                    result, rec_a, rec_b = run_recorded(bars, lambda: MACross(side=side),
                                                        flagged=h6.random_masks(seed, bars))
                    total += assert_matches_oracle(self, rec_a, oracle_ma_cross, side=side)
                    total += assert_matches_oracle(self, rec_b, oracle_ma_cross, side=side)
                    self.assertEqual(len(rec_a.log), len(bars))
        self.assertGreater(total, 300)       # the comparison is not vacuous


class TestLookAhead(unittest.TestCase):

    def test_changing_the_future_never_changes_past_decisions(self):
        bars = h6.random_path(42)
        _, full, _ = run_recorded(bars, MACross)
        for cut in (10, 77, 150, 299):
            later_start = int((bars[cut].open_time - T(0)).total_seconds() // 3600) + 1
            altered = bars[:cut + 1] + h6.random_path(900 + cut, n=40, start_hour=later_start,
                                                      start_price=700)
            _, rec, _ = run_recorded(altered, MACross)
            with self.subTest(cut=cut):
                self.assertEqual(full.log[:cut + 1], rec.log[:cut + 1])


class TestMirror(unittest.TestCase):

    def smooth_path(self, seed):
        # Small swings (well inside 2% / 4% of ~1000), so trades end only by crosses.
        rnd = random.Random(seed)
        out, hour = [], 0
        for i in range(600):
            if rnd.random() < 0.04:
                hour += rnd.randint(1, 3)
            c = 1000 + round(6 * math.sin(i / 3)) + rnd.randint(-1, 1)
            out.append(bar(hour, c, c + 1, c - 1, c))
            hour += 1
        return out

    def test_long_on_path_equals_short_on_mirror(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                bars = self.smooth_path(seed)
                a = run_recorded(bars, MACross)[0].variant_a
                m = run_recorded([mirror_bar(b, h6.MIRROR_C) for b in bars],
                                 lambda: MACross(side=SHORT))[0].variant_a
                self.assertGreater(len(a.trades), 10)
                self.assertTrue({t.exit_reason for t in a.trades} <= {"market_exit", "exit_after_gap"})
                self.assertEqual(len(a.trades), len(m.trades))
                for x, y in zip(a.trades, m.trades):
                    self.assertEqual((x.side, y.side), (LONG, SHORT))
                    self.assertEqual((x.entry_time, x.exit_time, x.exit_reason, x.gap_exposed),
                                     (y.entry_time, y.exit_time, y.exit_reason, y.gap_exposed))
                    self.assertEqual((h6.MIRROR_C - x.entry_price, h6.MIRROR_C - x.exit_price),
                                     (y.entry_price, y.exit_price))

    def test_mirrored_level_formulas(self):
        # Percent levels are not additive mirror images of each other (2% of p is not
        # 2% of C - p); the rule mirrors the FORMULA: LONG close*(1-s), close*(1+g);
        # SHORT close*(1+s), close*(1-g).
        path = closes(*[(i, 95) for i in range(5)], (5, 100), (6, 100))
        long_order = run_recorded(path, MACross)[1].log[5][2]
        short_order = run_recorded([mirror_bar(b, 200) for b in path],
                                   lambda: MACross(side=SHORT))[1].log[5][2]
        self.assertAlmostEqual(long_order.stop, 100 * 0.98)
        self.assertAlmostEqual(short_order.stop, 100 * 1.02)
        self.assertAlmostEqual(long_order.target, 100 * 1.04)
        self.assertAlmostEqual(short_order.target, 100 * 0.96)


if __name__ == "__main__":
    unittest.main()
