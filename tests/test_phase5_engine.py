"""Phase 5: timing rules (plan section 3), fills inside a bar (section 4) and
position size (section 5). Every expected number is worked out by hand in the
comments. MADE-UP data only.

Default fixed notional 1,000 USD: an entry at 100 buys 10 units.
"""

import unittest

import phase5_helpers as h
from phase5_helpers import T, bar, flat, run, run_a
from quantlab.core.engine import (
    Adjust, BacktestConfig, EngineError, Entry, Exit, LookAheadError, StrategyError,
)
from quantlab.core.gap_execution import LONG, SHORT
from quantlab.core.indicators import SimpleMovingAverage, all_available


def one_trade(bars, orders):
    v = run_a(bars, orders)
    if len(v.trades) != 1:
        raise AssertionError(f"expected exactly one trade, got {v.trades}")
    return v.trades[0]


# ------------------------------------------------------- timing rules ----

class TestEntryTiming(unittest.TestCase):

    def test_entry_fills_at_next_open_never_at_decision_bar(self):
        # Decided at the close of hour 0 (close 100). Fills at hour 1's OPEN (100).
        bars = [flat(0, 100), bar(1, 100, 105, 99, 104), bar(2, 104, 106, 103, 105)]
        v = run_a(bars, {0: Entry(LONG)})
        p = v.open_position_at_end
        self.assertEqual((p["entry_time"], p["entry_price"], p["quantity"]), (T(1), 100, 10))
        # Equity at each close: h0 10000; h1 10000 + 10*(104-100) = 10040; h2 + 10*(105-100) = 10050
        self.assertEqual([e for _, e in v.equity_curve], [10000, 10040, 10050])
        self.assertEqual([t for t, _ in v.equity_curve], [T(1), T(2), T(3)])  # bar CLOSE times
        self.assertEqual(p["unrealized_pnl"], 50)
        self.assertEqual(v.trades, ())
        self.assertIn("NOT closed", p["note"])

    def test_entry_expires_when_next_bar_is_missing(self):
        bars = [flat(0, 100), flat(2, 100), flat(3, 100)]       # hour 1 missing
        v = run_a(bars, {0: Entry(LONG, 90, 110)})
        self.assertEqual(len(v.order_events), 1)
        e = v.order_events[0]
        self.assertEqual((e.kind, e.side, e.decided_at, e.resolved_at, e.reason),
                         ("entry_expired", LONG, T(0), T(2), "next_bar_missing"))
        self.assertEqual(v.trades, ())
        self.assertIsNone(v.open_position_at_end)

    def test_entry_on_last_bar_expires_at_end_of_data(self):
        v = run_a([flat(0, 100), flat(1, 100)], {1: Entry(SHORT)})
        e = v.order_events[0]
        self.assertEqual((e.kind, e.decided_at, e.resolved_at, e.reason),
                         ("entry_expired", T(1), None, "end_of_data"))

    def test_strategy_cannot_read_the_entry_bar(self):
        class Peeker:
            def on_bar(self, view, position):
                return Entry(LONG, stop=view[len(view)].low)   # the next, unclosed bar
        with self.assertRaises(LookAheadError):
            run([flat(0, 100), flat(1, 100)], strategy=Peeker)

    def test_brackets_cannot_be_influenced_by_entry_bar_or_later_values(self):
        class CloseMinus10:
            def on_bar(self, view, position):
                if len(view) == 1:
                    c = view.latest.close
                    return Entry(LONG, stop=c - 10, target=c + 20)
        first = [flat(0, 100)]
        laters = [[bar(1, 100, 101, 99, 100), flat(2, 100)],
                  [bar(1, 100, 150, 95, 140), bar(2, 140, 160, 130, 150)],
                  [bar(1, 100, 100, 89, 89)]]
        orders = set()
        for later in laters:
            v = run_a(first + later, strategy=CloseMinus10)
            orders.add(v.decisions[0])
        self.assertEqual(orders, {(T(0), Entry(LONG, 90, 120))})

    def test_invalid_bracket_at_fill_cancels_entry(self):
        cases = [  # (order, open of the fill bar)
            (Entry(LONG, stop=99), 98),             # stop above the fill
            (Entry(LONG, stop=98), 98),             # stop equal to the fill
            (Entry(LONG, target=101), 101),         # target equal to the fill
            (Entry(LONG, target=101), 102),         # target below the fill
            (Entry(SHORT, stop=101), 101),
            (Entry(SHORT, stop=101), 102),
            (Entry(SHORT, target=99), 99),
            (Entry(SHORT, target=99), 98),
        ]
        for order, o in cases:
            with self.subTest(order=order, open=o):
                v = run_a([flat(0, 100), bar(1, o, o, o, o)], {0: order})
                self.assertEqual([(e.kind, e.reason) for e in v.order_events],
                                 [("entry_cancelled", "invalid_bracket_at_fill")])
                self.assertIsNone(v.open_position_at_end)
                self.assertEqual(v.trades, ())

    def test_valid_bracket_at_fill_opens(self):
        for order in (Entry(LONG, 97, 103), Entry(SHORT, 103, 97)):
            with self.subTest(order=order):
                v = run_a([flat(0, 100), flat(1, 100)], {0: order})
                self.assertEqual(v.order_events, ())
                self.assertEqual(v.open_position_at_end["entry_price"], 100)


class TestAdjustmentTiming(unittest.TestCase):

    def test_adjustment_not_active_on_decision_bar_but_active_at_next_open(self):
        # h1: fill 100, 10 units, stop 90. Adjust stop to 95 decided at h1's close.
        #     h1 low 94 would have hit 95 - but the adjustment is not active yet.
        # h2: open 94 <= 95 -> exit at the OPEN 94 (stop_at_open). The old stop 90
        #     is never touched (low 93), so only the adjusted stop can explain this.
        #     P&L = 10 * (94 - 100) = -60
        bars = [flat(0, 100), bar(1, 100, 101, 94, 99), bar(2, 94, 96, 93, 95)]
        t = one_trade(bars, {0: Entry(LONG, 90, 200), 1: Adjust(95, 200)})
        self.assertEqual((t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (T(2), 94, "stop_at_open", -60))

    def test_adjustment_can_remove_a_level(self):
        # target 105 removed at h1's close; h2 high 110 then does not exit.
        bars = [flat(0, 100), flat(1, 100), bar(2, 100, 110, 99, 108)]
        v = run_a(bars, {0: Entry(LONG, 90, 105), 1: Adjust(90, None)})
        self.assertEqual(v.trades, ())
        self.assertIsNone(v.open_position_at_end["target"])


class TestMarketExit(unittest.TestCase):

    def test_exit_fills_at_next_open_not_at_decision_close(self):
        # h1 fill 100; exit requested at h1's close (102); fills at h2 open 103.
        # P&L = 10 * (103 - 100) = +30
        bars = [flat(0, 100), bar(1, 100, 102, 100, 102), bar(2, 103, 104, 101, 101)]
        t = one_trade(bars, {0: Entry(LONG), 1: Exit()})
        self.assertEqual((t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (T(2), 103, "market_exit", 30))

    def test_bracket_open_check_comes_before_pending_exit(self):
        # Same order on every bar: h2 opens 103, past the target 102 -> exit at
        # the TARGET 102 (no windfall), not at the open. P&L = 10 * 2 = +20
        bars = [flat(0, 100), bar(1, 100, 101, 100, 101), bar(2, 103, 104, 101, 101)]
        t = one_trade(bars, {0: Entry(LONG, 90, 102), 1: Exit()})
        self.assertEqual((t.exit_price, t.exit_reason, t.net_pnl), (102, "target_at_open", 20))


# ---------------------------------------------------- fills in a bar ----

class TestFillsLong(unittest.TestCase):
    # Entered at h1 open 100 (10 units), stop 90, target 120, decided at h0.
    START = [flat(0, 100), flat(1, 100)]
    ORDER = {0: Entry(LONG, 90, 120)}

    def check(self, b2, price, reason, pnl, exit_hour=2, start=None):
        t = one_trade((start or self.START) + [b2], self.ORDER)
        self.assertEqual((t.exit_time, t.exit_price, t.exit_reason, t.net_pnl),
                         (T(exit_hour), price, reason, pnl))

    def test_open_past_stop_exits_at_open(self):
        self.check(bar(2, 85, 88, 80, 86), 85, "stop_at_open", -150)   # 10 * (85 - 100)

    def test_open_exactly_at_stop_exits_at_open(self):
        self.check(bar(2, 90, 95, 88, 92), 90, "stop_at_open", -100)

    def test_open_past_target_exits_at_target_not_open(self):
        self.check(bar(2, 130, 135, 128, 131), 120, "target_at_open", 200)  # not 300

    def test_both_levels_inside_bar_assumes_stop_first(self):
        self.check(bar(2, 100, 125, 85, 110), 90, "stop_and_target_in_bar_stop_assumed", -100)

    def test_only_stop_touched(self):
        self.check(bar(2, 100, 110, 89, 95), 90, "stop_intrabar", -100)

    def test_only_target_touched(self):
        self.check(bar(2, 100, 121, 95, 110), 120, "target_intrabar", 200)

    def test_level_exactly_touched_counts(self):
        self.check(bar(2, 100, 120, 95, 110), 120, "target_intrabar", 200)
        self.check(bar(2, 100, 110, 90, 95), 90, "stop_intrabar", -100)

    def test_bracket_active_within_entry_bar(self):
        t = one_trade([flat(0, 100), bar(1, 100, 101, 89, 95)], self.ORDER)
        self.assertEqual((t.entry_time, t.exit_time, t.exit_price, t.exit_reason),
                         (T(1), T(1), 90, "stop_intrabar"))
        t = one_trade([flat(0, 100), bar(1, 100, 125, 85, 95)], self.ORDER)
        self.assertEqual((t.exit_time, t.exit_price, t.exit_reason),
                         (T(1), 90, "stop_and_target_in_bar_stop_assumed"))

    def test_no_level_touched_continues(self):
        v = run_a(self.START + [bar(2, 100, 119, 91, 110)], self.ORDER)
        self.assertEqual(v.trades, ())
        self.assertEqual(v.equity_curve[-1][1], 10100)    # 10000 + 10 * (110 - 100)


class TestFillsShort(unittest.TestCase):
    # Entered SHORT at h1 open 100 (10 units), stop 110, target 80.
    START = [flat(0, 100), flat(1, 100)]
    ORDER = {0: Entry(SHORT, 110, 80)}

    def check(self, b2, price, reason, pnl):
        t = one_trade(self.START + [b2], self.ORDER)
        self.assertEqual((t.side, t.exit_price, t.exit_reason, t.net_pnl),
                         (SHORT, price, reason, pnl))

    def test_open_past_stop_exits_at_open(self):
        self.check(bar(2, 115, 120, 112, 118), 115, "stop_at_open", -150)  # 10 * (100 - 115)

    def test_open_past_target_exits_at_target_not_open(self):
        self.check(bar(2, 70, 72, 65, 71), 80, "target_at_open", 200)       # not 300

    def test_both_levels_inside_bar_assumes_stop_first(self):
        self.check(bar(2, 100, 115, 75, 90), 110, "stop_and_target_in_bar_stop_assumed", -100)

    def test_only_stop_touched(self):
        self.check(bar(2, 100, 111, 95, 105), 110, "stop_intrabar", -100)

    def test_only_target_touched(self):
        self.check(bar(2, 100, 105, 79, 85), 80, "target_intrabar", 200)


# ------------------------------------------------------ position size ----

class TestSizing(unittest.TestCase):

    def test_quantity_is_notional_over_fill_for_both_sides(self):
        for side, stop, target, close, pnl in ((LONG, 100, 150, 130, 40), (SHORT, 150, 100, 130, -40)):
            with self.subTest(side=side):
                # fill 125 -> 1000 / 125 = 8 units; h2 opens 130 -> market exit at 130.
                # LONG 8 * (130 - 125) = +40 ; SHORT 8 * (125 - 130) = -40
                bars = [flat(0, 125), flat(1, 125), flat(2, close)]
                t = one_trade(bars, {0: Entry(side, stop, target), 1: Exit()})
                self.assertEqual((t.quantity, t.entry_value, t.net_pnl), (8, 1000, pnl))
                self.assertEqual(t.trade_return, pnl / 1000)

    def test_no_compounding_after_a_loss(self):
        # Trade 1: LONG at 100 (10 units), stop 50, h2 opens 50 -> -500. Equity 9500.
        # Trade 2: decided h2, fills h3 at 125 -> STILL 1000 / 125 = 8 units.
        bars = [flat(0, 100), flat(1, 100), flat(2, 50), flat(3, 125), flat(4, 130)]
        v = run_a(bars, {0: Entry(LONG, 50, 200), 2: Entry(LONG)})
        self.assertEqual(v.trades[0].net_pnl, -500)
        self.assertEqual(v.open_position_at_end["quantity"], 8)
        # Equity: h0 10000, h1 10000, h2 9500, h3 9500 + 8*(125-125), h4 9500 + 8*5 = 9540
        self.assertEqual([e for _, e in v.equity_curve], [10000, 10000, 9500, 9500, 9540])

    def test_config_rules(self):
        self.assertEqual(BacktestConfig(10_000).fixed_notional, 10_000)
        for bad in (0, -1, 10_000.01, float("nan"), float("inf"), True, "1000"):
            with self.subTest(notional=bad), self.assertRaises(EngineError):
                BacktestConfig(bad)
        with self.assertRaises(EngineError):
            BacktestConfig(1000, starting_equity=20_000)

    def test_settings_file_backtest_section(self):
        import tomllib
        with open(h.ROOT / "config" / "settings.toml", "rb") as f:
            cfg = BacktestConfig.from_settings(tomllib.load(f))
        self.assertEqual((cfg.starting_equity, cfg.fixed_notional), (10_000, 1000))
        with self.assertRaises(EngineError):
            BacktestConfig.from_settings({})


# ---------------------------------------------- refused strategy orders ----

class TestRefusedOrders(unittest.TestCase):

    def test_illegal_orders_raise(self):
        bars = [flat(0, 100), flat(1, 100), flat(2, 100)]
        cases = {
            "entry while open": {0: Entry(LONG), 1: Entry(LONG)},
            "exit while flat": {0: Exit()},
            "adjust while flat": {0: Adjust(90, 110)},
            "long stop above target": {0: Entry(LONG, 110, 90)},
            "short stop below target": {0: Entry(SHORT, 90, 110)},
            "long adjust inverted": {0: Entry(LONG), 1: Adjust(110, 90)},
            "nan stop": {0: Entry(LONG, float("nan"))},
            "negative target": {0: Entry(LONG, None, -5)},
            "bool stop": {0: Entry(LONG, True)},
            "bad side": {0: Entry("BUY")},
            "unknown order": {0: "buy"},
        }
        for name, orders in cases.items():
            with self.subTest(name), self.assertRaises(StrategyError):
                run(bars, orders)

    def test_impossible_or_unordered_bars_refused(self):
        cases = {
            "high below close": [bar(0, 100, 99, 98, 100)],
            "non-positive": [bar(0, 0, 1, 0, 1)],
            "nan": [bar(0, 100, float("nan"), 99, 100)],
            "out of order": [flat(1, 100), flat(0, 100)],
            "duplicate": [flat(0, 100), flat(0, 100)],
            "empty": [],
        }
        for name, bars in cases.items():
            with self.subTest(name), self.assertRaises(EngineError):
                run(bars)


class TestNoSignalWhileIndicatorUnavailable(unittest.TestCase):

    def test_sma_gated_strategy_is_silent_until_window_complete_again(self):
        class AboveSMA:
            def __init__(self):
                self.sma = SimpleMovingAverage(3, h.H)

            def on_bar(self, view, position):
                b = view.latest
                value = self.sma.update(int(b.open_time.timestamp()), b.close)
                if position is None and all_available(value) and b.close >= value:
                    return Entry(LONG)
                if position is not None:
                    return Exit()
        # Rising closes: every bar's close is >= the SMA whenever it is available.
        bars = [flat(0, 100), flat(1, 101), flat(2, 102), flat(4, 103), flat(5, 104),
                flat(6, 105)]
        v = run_a(bars, strategy=AboveSMA)
        # Entry signals only where 3 contiguous bars exist: h2 and h6 (h3 missing).
        self.assertEqual([t for t, o in v.decisions if isinstance(o, Entry)], [T(2), T(6)])


if __name__ == "__main__":
    unittest.main()
