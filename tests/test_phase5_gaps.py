"""Phase 5: missing-data gaps inside the engine (plan sections 6 and 7).

Includes the APPROVED pending-market-exit-across-a-gap rule:
  - the exit request stays active through the gap (it does not expire)
  - nothing is executed or inferred during the missing hours
  - the position becomes gap_exposed
  - at the first available bar: open past the stop -> exit at that OPEN;
    open past the target -> exit at the TARGET price; otherwise the pending
    exit fills at that OPEN.
Symmetric for LONG and SHORT. Expected numbers worked out by hand. MADE-UP data.

Common setup: decided at h0 (close 100), filled at h1 open 100 -> 10 units.
Hours 2 and 3 are MISSING. The first bar after the gap is h4.
"""

import unittest

from phase5_helpers import U, Script, T, bar, flat, run
from quantlab.core.engine import Adjust, Entry, Exit
from quantlab.core.gap_execution import LONG, SHORT

BEFORE = [flat(0, 100), flat(1, 100)]
LONG_BRACKET = Entry(LONG, 90, 120)
SHORT_BRACKET = Entry(SHORT, 110, 80)


def run_case(after, orders):
    made = []                         # a fresh strategy per variant; [0] is Variant A's
    result = run(BEFORE + after, strategy=lambda: made.append(Script(orders)) or made[-1])
    return result.variant_a, made[0]


class TestPendingExitAcrossGap(unittest.TestCase):

    def check(self, entry, first_bar, price, reason, pnl):
        v, s = run_case([first_bar], {0: entry, 1: Exit()})
        self.assertEqual(len(v.trades), 1)
        t = v.trades[0]
        self.assertEqual((t.side, t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (entry.side, T(4), price, reason, pnl))
        self.assertTrue(t.gap_exposed)
        self.assertEqual(v.order_events, ())               # the exit did NOT expire
        # Nothing happened during the missing hours: no strategy call, no equity point.
        self.assertEqual([c[0] for c in s.calls], [T(0), T(1), T(4)])
        self.assertEqual([t for t, _ in v.equity_curve], [T(1), T(2), T(5)])
        self.assertEqual(v.missing_times, (U(2), U(3)))
        return t

    # ---- LONG (stop 90, target 120)
    def test_long_open_beyond_stop_exits_at_open(self):
        self.check(LONG_BRACKET, bar(4, 85, 88, 80, 86), 85, "stop_after_gap", -150)

    def test_long_open_exactly_at_stop_exits_at_open(self):
        self.check(LONG_BRACKET, bar(4, 90, 92, 88, 91), 90, "stop_after_gap", -100)

    def test_long_open_beyond_target_exits_at_target(self):
        self.check(LONG_BRACKET, bar(4, 130, 135, 128, 131), 120, "target_after_gap", 200)

    def test_long_otherwise_fills_at_open(self):
        # The bar later trades through both levels (low 80, high 125), but the
        # position was already closed at the open, so no intrabar rule applies.
        self.check(LONG_BRACKET, bar(4, 105, 125, 80, 100), 105, "exit_after_gap", 50)

    def test_long_without_levels_fills_at_open(self):
        self.check(Entry(LONG), bar(4, 70, 72, 60, 65), 70, "exit_after_gap", -300)

    # ---- SHORT (stop 110, target 80): the mirror image
    def test_short_open_beyond_stop_exits_at_open(self):
        self.check(SHORT_BRACKET, bar(4, 115, 120, 112, 114), 115, "stop_after_gap", -150)

    def test_short_open_exactly_at_stop_exits_at_open(self):
        self.check(SHORT_BRACKET, bar(4, 110, 112, 108, 109), 110, "stop_after_gap", -100)

    def test_short_open_beyond_target_exits_at_target(self):
        self.check(SHORT_BRACKET, bar(4, 70, 72, 65, 69), 80, "target_after_gap", 200)

    def test_short_otherwise_fills_at_open(self):
        self.check(SHORT_BRACKET, bar(4, 95, 120, 75, 100), 95, "exit_after_gap", 50)

    def test_short_without_levels_fills_at_open(self):
        self.check(Entry(SHORT), bar(4, 130, 140, 128, 135), 130, "exit_after_gap", -300)

    def test_fill_never_better_than_stop_rule_or_plain_open(self):
        # For every first open, the fill is the worse (for the trader) of the
        # bracket rule and a plain market exit at that open.
        for side, entry in ((LONG, LONG_BRACKET), (SHORT, SHORT_BRACKET)):
            for o in range(60, 141, 5):
                with self.subTest(side=side, open=o):
                    v, _ = run_case([bar(4, o, o, o, o)], {0: entry, 1: Exit()})
                    fill = v.trades[0].exit_price
                    if side == LONG:
                        self.assertLessEqual(fill, o)
                    else:
                        self.assertGreaterEqual(fill, o)

    def test_exit_requested_with_no_bar_left_stays_open_and_is_reported(self):
        v, _ = run_case([], {0: LONG_BRACKET, 1: Exit()})
        self.assertEqual(v.trades, ())
        p = v.open_position_at_end
        self.assertTrue(p["exit_requested_unfilled"])
        self.assertEqual((p["mark_price"], p["unrealized_pnl"]), (100, 0))


class TestPositionHeldAcrossGap(unittest.TestCase):

    def test_continue_then_later_exit_is_gap_exposed(self):
        # h4 opens 100, inside 90..120 -> continue; position view says gap_exposed.
        # Exit decided at h4, fills at h5 open 102 -> 10 * 2 = +20, market_exit.
        v, s = run_case([bar(4, 100, 105, 95, 102), flat(5, 102)], {0: LONG_BRACKET, 4: Exit()})
        positions = {c[0]: c[2] for c in s.calls}
        self.assertFalse(positions[T(1)].gap_exposed)
        self.assertTrue(positions[T(4)].gap_exposed)
        t = v.trades[0]
        self.assertEqual((t.exit_time, t.exit_price, t.exit_reason, t.net_pnl, t.gap_exposed),
                         (T(5), 102, "market_exit", 20, True))

    def test_continue_then_ordinary_intrabar_rules_apply_in_first_bar(self):
        for entry, b4, price in ((LONG_BRACKET, bar(4, 100, 105, 89, 95), 90),
                                 (SHORT_BRACKET, bar(4, 100, 111, 95, 105), 110)):
            with self.subTest(side=entry.side):
                v, _ = run_case([b4], {0: entry})
                t = v.trades[0]
                self.assertEqual((t.exit_time, t.exit_price, t.exit_reason, t.gap_exposed),
                                 (T(4), price, "stop_intrabar", True))

    def test_dqp1_first_bar_rule_without_pending_exit(self):
        cases = [(LONG_BRACKET, 85, 85, "stop_after_gap"), (LONG_BRACKET, 130, 120, "target_after_gap"),
                 (SHORT_BRACKET, 115, 115, "stop_after_gap"), (SHORT_BRACKET, 70, 80, "target_after_gap")]
        for entry, o, price, reason in cases:
            with self.subTest(side=entry.side, open=o):
                v, _ = run_case([bar(4, o, o, o, o)], {0: entry})
                t = v.trades[0]
                self.assertEqual((t.exit_price, t.exit_reason, t.gap_exposed), (price, reason, True))

    def test_adjustment_decided_before_gap_is_active_at_first_bar(self):
        # Stop raised to 99 at h1's close; h4 opens 98 <= 99 -> exit at the open.
        v, _ = run_case([bar(4, 98, 99, 97, 98)], {0: LONG_BRACKET, 1: Adjust(99, 120)})
        t = v.trades[0]
        self.assertEqual((t.exit_price, t.exit_reason, t.net_pnl), (98, "stop_after_gap", -20))


class TestGapExposedFlag(unittest.TestCase):

    def test_trade_entirely_after_a_gap_is_not_exposed(self):
        bars = [flat(0, 100), flat(2, 100), flat(3, 100), flat(4, 101)]   # h1 missing
        v = run(bars, {2: Entry(LONG), 3: Exit()}).variant_a
        self.assertFalse(v.trades[0].gap_exposed)

    def test_trade_closed_before_a_gap_is_not_exposed(self):
        bars = [flat(0, 100), flat(1, 100), flat(2, 101), flat(4, 101)]   # h3 missing
        v = run(bars, {0: Entry(LONG), 1: Exit()}).variant_a
        t = v.trades[0]
        self.assertEqual((t.exit_time, t.gap_exposed), (T(2), False))

    def test_entry_never_fills_across_a_gap(self):
        bars = [flat(0, 100), flat(4, 100), flat(5, 100)]
        v = run(bars, {0: Entry(LONG)}).variant_a
        self.assertEqual(v.order_events[0].reason, "next_bar_missing")
        self.assertIsNone(v.open_position_at_end)


if __name__ == "__main__":
    unittest.main()
