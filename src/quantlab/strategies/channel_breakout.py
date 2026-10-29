"""channel_breakout: N-bar breakout with a clock-time exit. TEST STRATEGY ONLY.

At closed bar t, while flat (LONG; SHORT is the exact mirror):
  the N (= 4) hours immediately preceding bar t must all be present, i.e. bars
  t-N .. t-1 and t are genuinely consecutive hourly observations (bar t itself
  is NOT part of the lookback). A missing hour anywhere in that span: no signal.
  LONG : close[t] strictly above the highest HIGH of bars t-N .. t-1
         -> entry; stop = lowest LOW of those same bars; no target
  SHORT: close[t] strictly below the lowest LOW of bars t-N .. t-1
         -> entry; stop = highest HIGH of those same bars; no target
  Equality is not a breakout.

Exit after hold_hours (= 3) of REAL ELAPSED UTC TIME. The entry fills at the
open of the bar after t, which is exactly bar t's close time. The exit time is
fixed at decision time as  exit_at = close_time(t) + hold_hours  and handled by
the engine: at the first available bar opening at or after exit_at the position
exits at that open. Example: decided at the 09:00 bar (closes 10:00), filled at
the 10:00 open, three hours complete at the 13:00 boundary, i.e. at the close
of the 12:00 bar, and the exit fills at the 13:00 open. If that moment falls in
missing hours, clock time still passes, nothing executes inside the gap, and the
exit follows the approved pending-exit-across-gap rule at the first available
bar (stop/target open check first).
"""

from datetime import timedelta

from quantlab.core.engine import Entry
from quantlab.core.gap_execution import LONG
from quantlab.strategies.base import (
    VerificationStrategy, check_hourly, check_positive_int, check_side,
)


class ChannelBreakout(VerificationStrategy):
    NAME = "channel_breakout"
    VERSION = "1.0.0"

    def __init__(self, side="LONG", lookback_hours=4, hold_hours=3):
        self.side = check_side(side)
        self.lookback_hours = check_positive_int("lookback_hours", lookback_hours)
        self.hold_hours = check_positive_int("hold_hours", hold_hours)
        self.settings = {"side": self.side, "lookback_hours": self.lookback_hours,
                         "hold_hours": self.hold_hours}

    def on_bar(self, view, position):
        check_hourly(view)
        n = self.lookback_hours
        if position is not None or len(view) < n + 1:
            return None
        if any(view.missing_before(-k) for k in range(1, n + 1)):
            return None              # a missing hour inside t-N .. t: no signal
        window = view[-n - 1:-1]     # bars t-N .. t-1 (bar t excluded)
        bar = view.latest
        highest = max(b.high for b in window)
        lowest = min(b.low for b in window)
        exit_at = bar.close_time + timedelta(hours=self.hold_hours)
        if self.side == LONG and bar.close > highest:
            return Entry(self.side, stop=lowest, exit_at=exit_at)
        if self.side != LONG and bar.close < lowest:
            return Entry(self.side, stop=highest, exit_at=exit_at)
        return None
