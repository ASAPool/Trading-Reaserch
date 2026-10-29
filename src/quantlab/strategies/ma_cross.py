"""ma_cross: moving-average crossover. TEST STRATEGY ONLY.

fast = 3-hour simple moving average of closes, slow = 5-hour (DQP-1 window
indicators: unavailable until the whole window is consecutive again after a gap).

At closed bar t (LONG; SHORT is the exact mirror):
  entry cross:  fast[t-1] <= slow[t-1]  and  fast[t] > slow[t]
  exit cross:   fast[t-1] >= slow[t-1]  and  fast[t] < slow[t]
Equality alone is not a cross. Both t-1 and t must have valid, fully warmed-up
values and t must directly follow t-1: a cross is never inferred across a gap
or warm-up. So the first evaluable bar is the 6th consecutive bar of a segment.

While flat, an entry cross requests a market entry with
  LONG : stop = close[t] * (1 - stop_fraction),  target = close[t] * (1 + target_fraction)
  SHORT: stop = close[t] * (1 + stop_fraction),  target = close[t] * (1 - target_fraction)
fixed from the DECISION bar's close, never recalculated from the fill (the
engine cancels the entry, recorded, if the next open makes them invalid).
While in a position, an exit cross requests a market exit.
"""

from quantlab.core.engine import Entry, Exit
from quantlab.core.gap_execution import LONG
from quantlab.core.indicators import SimpleMovingAverage, all_available
from quantlab.strategies.base import (
    HOUR, VerificationStrategy, check_fraction, check_hourly, check_positive_int, check_side,
)


class MACross(VerificationStrategy):
    NAME = "ma_cross"
    VERSION = "1.0.0"

    def __init__(self, side="LONG", fast_hours=3, slow_hours=5, stop_fraction=0.02,
                 target_fraction=0.04):
        self.side = check_side(side)
        self.fast_hours = check_positive_int("fast_hours", fast_hours)
        self.slow_hours = check_positive_int("slow_hours", slow_hours)
        if not self.fast_hours < self.slow_hours:
            raise ValueError("fast_hours must be smaller than slow_hours")
        self.stop_fraction = check_fraction("stop_fraction", stop_fraction)
        self.target_fraction = check_fraction("target_fraction", target_fraction)
        self.settings = {"side": self.side, "fast_hours": self.fast_hours,
                         "slow_hours": self.slow_hours, "stop_fraction": self.stop_fraction,
                         "target_fraction": self.target_fraction}
        self._fast = SimpleMovingAverage(self.fast_hours, HOUR)
        self._slow = SimpleMovingAverage(self.slow_hours, HOUR)
        self._prev = None            # (fast, slow) of the previous visible bar

    def on_bar(self, view, position):
        check_hourly(view)
        bar = view.latest
        t = int(bar.open_time.timestamp())
        fast, slow = self._fast.update(t, bar.close), self._slow.update(t, bar.close)
        prev = self._prev if view.missing_before(-1) == 0 else None
        self._prev = (fast, slow)
        if prev is None or not all_available(fast, slow, *prev):
            return None
        pf, ps = prev
        up = pf <= ps and fast > slow
        down = pf >= ps and fast < slow
        enter, leave = (up, down) if self.side == LONG else (down, up)
        if position is None and enter:
            c = bar.close
            if self.side == LONG:
                return Entry(self.side, c * (1 - self.stop_fraction), c * (1 + self.target_fraction))
            return Entry(self.side, c * (1 + self.stop_fraction), c * (1 - self.target_fraction))
        if position is not None and leave:
            return Exit()
        return None
