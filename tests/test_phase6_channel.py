"""Phase 6: channel_breakout (4-hour lookback, 3-hour clock-time exit) and the
engine's time exit. Hand-worked examples, the independent oracle, look-ahead
and mirror tests. MADE-UP data only; TEST STRATEGY, not research.

Fixed notional 1,000 USD: a fill at 125 buys exactly 8 units.

Common hand-worked setup (LONG):
  h0 (o 120, h 122, l 119, c 121)   h1 (121, 123, 118, 122)
  h2 (122, 123, 120, 122)            h3 (122, 124, 121, 123)
  h4 (123, 125, 122, 125): close 125 > highest high of h0-h3 (124) -> ENTRY,
      stop = lowest low of h0-h3 = 118, exit_at = h4 close (05:00) + 3 h = 08:00.
  h5 (125, 127, 124, 126): fills at the 05:00 OPEN 125 -> 8 units.
  h6 (126, 128, 125, 127)   h7 (127, 128, 126, 128)
  Three hours complete at the 08:00 boundary = the CLOSE of the h7 bar.
  h8 (130, 131, 129, 130): the exit fills at the 08:00 OPEN 130 -> 8 * 5 = +40.
"""

import unittest

import phase6_helpers as h6
from phase5_helpers import BASE, T, Script, bar, mirror_bar, run
from phase6_helpers import LONG, SHORT, assert_matches_oracle, oracle_channel, run_recorded
from quantlab.core.engine import Adjust, Entry, Exit, StrategyError
from quantlab.strategies.channel_breakout import ChannelBreakout

SETUP = {0: bar(0, 120, 122, 119, 121), 1: bar(1, 121, 123, 118, 122),
         2: bar(2, 122, 123, 120, 122), 3: bar(3, 122, 124, 121, 123),
         4: bar(4, 123, 125, 122, 125), 5: bar(5, 125, 127, 124, 126),
         6: bar(6, 126, 128, 125, 127), 7: bar(7, 127, 128, 126, 128),
         8: bar(8, 130, 131, 129, 130), 9: bar(9, 131, 132, 130, 131)}
MIRROR = 250.0


def path(*hours, extra=()):
    return [SETUP[i] for i in hours] + list(extra)


def first_trade(bars, side=LONG):
    if side == SHORT:
        bars = [mirror_bar(b, MIRROR) for b in bars]
    v = run_recorded(bars, lambda: ChannelBreakout(side=side))[0].variant_a
    return v.trades[0], v


class TestHandWorked(unittest.TestCase):

    def test_long_entry_stop_and_three_hour_exit(self):
        result, rec, _ = run_recorded(path(0, 1, 2, 3, 4, 5, 6, 7, 8), ChannelBreakout)
        order = rec.log[4][2]
        self.assertEqual(order, Entry(LONG, stop=118, target=None, exit_at=T(8)))
        self.assertEqual([o for _, _, o in rec.log[:4]], [None] * 4)   # fewer than 4 previous bars
        t = result.variant_a.trades[0]
        self.assertEqual((t.entry_time, t.entry_price, t.quantity, t.exit_time, t.exit_price,
                          t.exit_reason, t.net_pnl, t.gap_exposed),
                         (T(5), 125, 8, T(8), 130, "time_exit", 40, False))
        self.assertEqual(t.exit_time - t.entry_time, T(3) - T(0))      # exactly 3 hours

    def test_time_exit_equals_market_exit_requested_at_the_bar_closing_at_the_boundary(self):
        bars = path(0, 1, 2, 3, 4, 5, 6, 7, 8)
        scripted = run(bars, {4: Entry(LONG, 118), 7: Exit()}).variant_a.trades[0]
        timed = run_recorded(bars, ChannelBreakout)[0].variant_a.trades[0]
        self.assertEqual((scripted.exit_time, scripted.exit_price, scripted.net_pnl),
                         (timed.exit_time, timed.exit_price, timed.net_pnl))
        self.assertEqual((scripted.exit_reason, timed.exit_reason), ("market_exit", "time_exit"))

    def test_short_is_the_exact_mirror(self):
        # Mirror around 250: close 125 < lowest low of h0-h3 (250 - 124 = 126);
        # stop = highest high = 250 - 118 = 132. Exit at the 08:00 open 120: 8 * 5 = +40.
        result, rec, _ = run_recorded([mirror_bar(b, MIRROR) for b in path(0, 1, 2, 3, 4, 5, 6, 7, 8)],
                                      lambda: ChannelBreakout(side=SHORT))
        self.assertEqual(rec.log[4][2], Entry(SHORT, stop=132, target=None, exit_at=T(8)))
        t = result.variant_a.trades[0]
        self.assertEqual((t.side, t.entry_price, t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (SHORT, 125, T(8), 120, "time_exit", 40))

    def test_equality_is_not_a_breakout(self):
        flat_top = bar(4, 123, 124, 122, 124)          # close 124 == highest high 124
        _, rec, _ = run_recorded(path(0, 1, 2, 3, extra=[flat_top]), ChannelBreakout)
        self.assertIsNone(rec.log[4][2])
        flat_bottom = bar(4, 119, 120, 118, 118)       # SHORT: close 118 == lowest low 118
        _, rec, _ = run_recorded(path(0, 1, 2, 3, extra=[flat_bottom]),
                                 lambda: ChannelBreakout(side=SHORT))
        self.assertIsNone(rec.log[4][2])

    def test_missing_hour_anywhere_in_lookback_means_no_signal(self):
        # Rising bars: every close is above every earlier high. h2 is missing.
        # h4 (lookback h0-h3), h5 (h1-h4), h6 (h2-h5) all include the missing h2:
        # no signal. h7 (lookback h3-h6, all present): the first entry.
        def rising(i):
            return bar(i, 100 + 2 * i, 101 + 2 * i, 99 + 2 * i, 101 + 2 * i)
        bars = [rising(i) for i in (0, 1, 3, 4, 5, 6, 7, 8)]
        _, rec, _ = run_recorded(bars, ChannelBreakout)
        decided = [(b.open_time, type(o).__name__) for b, _, o in rec.log if o is not None]
        self.assertEqual(decided[0], (T(7), "Entry"))
        entry = next(o for _, _, o in rec.log if o is not None)
        self.assertEqual(entry.stop, 99 + 2 * 3)          # lowest low of h3-h6 = h3's 105

    def test_settings_validation(self):
        for kw in ({"lookback_hours": 0}, {"hold_hours": 0}, {"hold_hours": 1.5},
                   {"side": "long"}, {"lookback_hours": True}):
            with self.subTest(kw), self.assertRaises(ValueError):
                ChannelBreakout(**kw)


class TestThreeHourExitAcrossMissingData(unittest.TestCase):
    """Entry filled at 05:00 (125, 8 units), stop 118 (SHORT mirror: 125, stop 132),
    exit due at 08:00 real UTC time. Each case runs LONG and its SHORT mirror."""

    def check(self, bars, exit_hour, price, reason, pnl, gap_exposed):
        for side in (LONG, SHORT):
            with self.subTest(side=side):
                t, v = first_trade(bars, side)
                want_price = price if side == LONG else MIRROR - price
                self.assertEqual((t.side, t.entry_time, t.exit_time, t.exit_price, t.exit_reason,
                                  t.net_pnl, t.gap_exposed),
                                 (side, T(5), T(exit_hour), want_price, reason, pnl, gap_exposed))

    def test_bar_closing_at_the_boundary_is_missing(self):
        # h7 (07:00-08:00) missing. Due at 08:00; the first available bar h8 opens at
        # 08:00 after missing data -> pending-exit-across-gap rule: open 130 is inside
        # the stop, so fill at that open.
        self.check(path(0, 1, 2, 3, 4, 5, 6, 8), 8, 130, "time_exit_after_gap", 40, True)

    def test_due_bar_itself_is_missing(self):
        # h8 missing: the exit stays pending through it and fills at the h9 open 131.
        self.check(path(0, 1, 2, 3, 4, 5, 6, 7, 9), 9, 131, "time_exit_after_gap", 48, True)

    def test_open_beyond_stop_after_gap_exits_at_that_open(self):
        # h6-h8 missing; h9 opens 110, below the stop 118 -> exit at the OPEN 110:
        # 8 * (110 - 125) = -120.
        crash = bar(9, 110, 112, 108, 111)
        self.check(path(0, 1, 2, 3, 4, 5, extra=[crash]), 9, 110, "stop_after_gap", -120, True)

    def test_clock_time_not_bar_count(self):
        # h6 missing. Only TWO bars (h5, h7) are observed before 08:00, yet the exit
        # still happens at the 08:00 open of h8 (ordinary time exit, no gap at h8).
        self.check(path(0, 1, 2, 3, 4, 5, 7, 8), 8, 130, "time_exit", 40, True)

    def test_not_due_yet_after_a_gap_continues(self):
        t, v = first_trade(path(0, 1, 2, 3, 4, 5, 7, 8))
        self.assertEqual(t.exit_time, T(8))    # h7 (after the gap) did not exit early


class TestEngineTimeExit(unittest.TestCase):

    def test_open_check_comes_first_target_after_gap(self):
        # LONG at 100, stop 90, target 120, exit_at h2. h2 missing; h3 opens 130 ->
        # target rule first: exit at the TARGET 120 (no windfall).
        bars = [bar(0, 100, 100, 100, 100), bar(1, 100, 100, 100, 100), bar(3, 130, 131, 129, 130)]
        t = run(bars, {0: Entry(LONG, 90, 120, exit_at=T(2))}).variant_a.trades[0]
        self.assertEqual((t.exit_price, t.exit_reason, t.net_pnl), (120, "target_after_gap", 200))

    def test_adjustment_keeps_the_exit_time(self):
        bars = [bar(h, 100, 100, 100, 100) for h in range(6)]
        t = run(bars, {0: Entry(LONG, 90, None, exit_at=T(4)), 1: Adjust(95, None)}).variant_a.trades[0]
        self.assertEqual((t.exit_time, t.exit_reason), (T(4), "time_exit"))

    def test_not_due_by_end_of_data_is_reported_open(self):
        bars = [bar(h, 100, 100, 100, 100) for h in range(3)]
        v = run(bars, {0: Entry(LONG, exit_at=T(10))}).variant_a
        self.assertEqual(v.trades, ())
        self.assertEqual(v.open_position_at_end["exit_at"], T(10))

    def test_invalid_exit_times_refused(self):
        bars = [bar(h, 100, 100, 100, 100) for h in range(3)]
        for x in (T(1), T(0), BASE.replace(tzinfo=None), "2030-01-01T05:00Z", 3600):
            with self.subTest(exit_at=x), self.assertRaises(StrategyError):
                run(bars, {0: Entry(LONG, exit_at=x)})


class TestAgainstIndependentOracle(unittest.TestCase):

    def test_random_paths_both_sides_both_variants(self):
        total = 0
        for seed in range(8):
            bars = h6.random_path(100 + seed)
            for side in (LONG, SHORT):
                with self.subTest(seed=seed, side=side):
                    result, rec_a, rec_b = run_recorded(bars, lambda: ChannelBreakout(side=side),
                                                        flagged=h6.random_masks(seed, bars))
                    total += assert_matches_oracle(self, rec_a, oracle_channel, side=side)
                    total += assert_matches_oracle(self, rec_b, oracle_channel, side=side)
                    reasons = {t.exit_reason for t in result.variant_a.trades}
                    self.assertIn("time_exit", reasons)
        self.assertGreater(total, 300)


class TestLookAhead(unittest.TestCase):

    def test_changing_the_future_never_changes_past_decisions(self):
        bars = h6.random_path(7)
        _, full, _ = run_recorded(bars, ChannelBreakout)
        for cut in (8, 64, 201, 299):
            later_start = int((bars[cut].open_time - T(0)).total_seconds() // 3600) + 1
            altered = bars[:cut + 1] + h6.random_path(500 + cut, n=40, start_hour=later_start,
                                                      start_price=1500)
            _, rec, _ = run_recorded(altered, ChannelBreakout)
            with self.subTest(cut=cut):
                self.assertEqual(full.log[:cut + 1], rec.log[:cut + 1])


class TestMirror(unittest.TestCase):

    def test_long_on_path_equals_short_on_mirror(self):
        reasons = set()
        for seed in (11, 12, 13):
            with self.subTest(seed=seed):
                bars = h6.random_path(seed)
                a = run_recorded(bars, ChannelBreakout)[0].variant_a
                m = run_recorded([mirror_bar(b, h6.MIRROR_C) for b in bars],
                                 lambda: ChannelBreakout(side=SHORT))[0].variant_a
                self.assertGreater(len(a.trades), 10)
                self.assertEqual(len(a.trades), len(m.trades))
                for x, y in zip(a.trades, m.trades):
                    self.assertEqual((x.side, y.side), (LONG, SHORT))
                    self.assertEqual((x.entry_time, x.exit_time, x.exit_reason, x.gap_exposed),
                                     (y.entry_time, y.exit_time, y.exit_reason, y.gap_exposed))
                    self.assertEqual((h6.MIRROR_C - x.entry_price, h6.MIRROR_C - x.exit_price),
                                     (y.entry_price, y.exit_price))
                    self.assertAlmostEqual(x.net_pnl / x.quantity, y.net_pnl / y.quantity, places=9)
                self.assertEqual([(e.kind, e.reason, e.decided_at) for e in a.order_events],
                                 [(e.kind, e.reason, e.decided_at) for e in m.order_events])
                reasons |= {t.exit_reason for t in a.trades}
        self.assertTrue({"time_exit", "time_exit_after_gap", "stop_intrabar"} <= reasons, reasons)


if __name__ == "__main__":
    unittest.main()
