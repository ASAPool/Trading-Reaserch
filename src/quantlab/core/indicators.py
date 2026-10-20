"""Reference indicators that obey DQP-1 at gaps (Phase 5).

These exist to PROVE the missing-bar rules inside the engine, not for research:

- SimpleMovingAverage: a WINDOW indicator. Its value is available only when
  its whole lookback window contains no missing bar. After a gap the window
  starts again from empty.
- ExponentialMovingAverage: a RECURSIVE indicator. Its state is RESET at a gap
  and it must warm up again for `period` bars before a value is available.

Both are fed one closed bar at a time with update(time, value) and return
None while unavailable. A strategy must not produce a signal while any value
it needs is None (see all_available()).

Times are integers (e.g. Unix seconds) on bar boundaries; `interval` is the
bar length in the same unit. A step of exactly one interval is contiguous;
anything larger is a gap.
"""

import math
from collections import deque


def _check_step(last_time, time, interval) -> bool:
    """True if `time` directly follows `last_time`; False at a gap or first bar."""
    if last_time is None:
        return False
    step = time - last_time
    if step <= 0 or step % interval:
        raise ValueError("times must be strictly increasing and on bar boundaries")
    return step == interval


def _check_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"indicator input must be a finite number, got {value!r}")


class SimpleMovingAverage:
    """Mean of the last `window` values; None unless all of them are contiguous."""

    def __init__(self, window: int, interval: int):
        if not isinstance(window, int) or window < 1:
            raise ValueError("window must be a positive integer")
        if not isinstance(interval, int) or interval <= 0:
            raise ValueError("interval must be a positive integer")
        self.window, self.interval = window, interval
        self._values = deque(maxlen=window)
        self._last_time = None

    def update(self, time: int, value: float):
        _check_value(value)
        if not _check_step(self._last_time, time, self.interval):
            self._values.clear()          # first bar or a gap: the window starts again
        self._last_time = time
        self._values.append(value)
        return self.value

    @property
    def value(self):
        if len(self._values) < self.window:
            return None
        return sum(self._values) / self.window


class ExponentialMovingAverage:
    """EMA with alpha = 2 / (period + 1), seeded with the first value of each
    segment. Reset at every gap; available only after `period` contiguous bars
    (the documented warm-up)."""

    def __init__(self, period: int, interval: int):
        if not isinstance(period, int) or period < 1:
            raise ValueError("period must be a positive integer")
        if not isinstance(interval, int) or interval <= 0:
            raise ValueError("interval must be a positive integer")
        self.period, self.interval = period, interval
        self.alpha = 2 / (period + 1)
        self._ema = None
        self._count = 0
        self._last_time = None

    def update(self, time: int, value: float):
        _check_value(value)
        if not _check_step(self._last_time, time, self.interval):
            self._ema, self._count = None, 0   # first bar or a gap: reset and re-warm
        self._last_time = time
        self._ema = value if self._ema is None else self.alpha * value + (1 - self.alpha) * self._ema
        self._count += 1
        return self.value

    @property
    def value(self):
        return self._ema if self._count >= self.period else None


def all_available(*values) -> bool:
    """True only if no needed indicator value is None (DQP-1: no signal otherwise)."""
    return all(v is not None for v in values)
