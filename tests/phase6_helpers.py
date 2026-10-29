"""Phase 6 test helpers: made-up price paths, a recorder, and the INDEPENDENT
test oracle. MADE-UP data only.

INDEPENDENCE RULE: the oracle functions below never import or call anything from
quantlab.strategies or quantlab.core.indicators. They recompute each strategy's
written rule from scratch, deliberately simply (no incremental state, exact
fractions for averages, time differences instead of gap bookkeeping), from the
list of bars the strategy was shown. Only basic data structures (Bar) are shared.
"""

import random
from datetime import timedelta
from fractions import Fraction

import phase5_helpers as h5
from phase5_helpers import bar
from quantlab.core.engine import Entry, Exit

HOUR = timedelta(hours=1)
LONG, SHORT = "LONG", "SHORT"
MIRROR_C = 2000.0


def random_path(seed, n=300, start_hour=0, start_price=1000, gap_prob=0.05):
    """Integer-priced made-up bars (exact in floating point) with random gaps."""
    rnd = random.Random(seed)
    bars, close, hour = [], start_price, start_hour
    while len(bars) < n:
        if rnd.random() < gap_prob:
            hour += rnd.randint(1, 4)
        o = close + rnd.randint(-5, 5)
        c = o + rnd.randint(-10, 10)
        bars.append(bar(hour, o, max(o, c) + rnd.randint(0, 8), min(o, c) - rnd.randint(0, 8), c))
        close, hour = c, hour + 1
    return bars


def random_masks(seed, bars, prob=0.03):
    rnd = random.Random(seed)
    return {int(b.open_time.timestamp()): rnd.random() < 0.6
            for b in bars if rnd.random() < prob}


class Recorder:
    """Wraps a production strategy and records, for every call, the bar shown,
    whether the strategy was flat, and the order it returned."""

    def __init__(self, inner):
        self.inner = inner
        self.identity = inner.identity
        self.log = []

    def on_bar(self, view, position):
        order = self.inner.on_bar(view, position)
        self.log.append((view.latest, position is None, order))
        return order


def run_recorded(bars, make, flagged=None):
    made = []
    result = h5.run(bars, flagged=flagged,
                    strategy=lambda: made.append(Recorder(make())) or made[-1])
    return result, made[0], made[1]          # result, Variant A recorder, Variant B recorder


def as_tuple(order):
    if order is None:
        return None
    if isinstance(order, Exit):
        return ("exit",)
    if isinstance(order, Entry):
        return ("entry", order.side, order.stop, order.target, order.exit_at)
    return ("other", order)


# ------------------------------------------------------------- the oracle ----

def oracle_ma_cross(bars, k, flat, side, fast=3, slow=5, stop_f=0.02, target_f=0.04):
    """Expected order at bar k from the written ma_cross rule."""
    if k < slow or bars[k].open_time - bars[k - slow].open_time != slow * HOUR:
        return None                     # t-1 and t not both fully warmed up in one segment
    c = [Fraction(b.close) for b in bars[k - slow:k + 1]]   # bars t-slow .. t
    f_now, s_now = sum(c[-fast:]) / fast, sum(c[-slow:]) / slow
    f_prev, s_prev = sum(c[-fast - 1:-1]) / fast, sum(c[-slow - 1:-1]) / slow
    crossed_up = f_prev <= s_prev and f_now > s_now
    crossed_down = f_prev >= s_prev and f_now < s_now
    close = bars[k].close
    if side == LONG:
        if flat and crossed_up:
            return ("entry", LONG, close * (1 - stop_f), close * (1 + target_f), None)
        if not flat and crossed_down:
            return ("exit",)
    else:
        if flat and crossed_down:
            return ("entry", SHORT, close * (1 + stop_f), close * (1 - target_f), None)
        if not flat and crossed_up:
            return ("exit",)
    return None


def oracle_channel(bars, k, flat, side, n=4, hold=3):
    """Expected order at bar k from the written channel_breakout rule."""
    if not flat or k < n or bars[k].open_time - bars[k - n].open_time != n * HOUR:
        return None
    previous = bars[k - n:k]
    hi, lo = max(b.high for b in previous), min(b.low for b in previous)
    exit_at = bars[k].open_time + HOUR + hold * HOUR
    if side == LONG and bars[k].close > hi:
        return ("entry", LONG, lo, None, exit_at)
    if side == SHORT and bars[k].close < lo:
        return ("entry", SHORT, hi, None, exit_at)
    return None


def assert_matches_oracle(test, recorder, oracle, **kw):
    bars = [b for b, _, _ in recorder.log]
    signals = 0
    for k, (b, flat, order) in enumerate(recorder.log):
        want, got = oracle(bars, k, flat, **kw), as_tuple(order)
        if want is None or got is None or want[0] != "entry" or got[0] != "entry":
            test.assertEqual(got, want, f"bar {b.open_time}")
        else:
            test.assertEqual((got[0], got[1], got[4]), (want[0], want[1], want[4]), f"bar {b.open_time}")
            for g, w in zip(got[2:4], want[2:4]):
                if w is None:
                    test.assertIsNone(g)
                else:
                    test.assertAlmostEqual(g, w, places=9)
        signals += want is not None
    return signals
