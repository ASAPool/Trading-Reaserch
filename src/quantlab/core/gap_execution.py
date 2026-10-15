"""Policy primitives for missing primary-source bars (DQP-1).

Not a backtester. These small, separately tested functions encode the
approved rules so the Phase 5 engine can use them unchanged:

- segments():            where the bar history is broken by missing bars
- one_bar_returns():     returns that are UNDEFINED across a gap
- first_bar_after_gap(): the symmetric LONG/SHORT exit rule at the first
                         available primary bar after a gap
- is_gap_exposed():      was a trade open during any missing primary hour?
"""

import math
from dataclasses import dataclass

LONG, SHORT = "LONG", "SHORT"


def segments(times: list[int], interval: int) -> list[dict]:
    """Split sorted bar open times into runs of consecutive bars.

    Returns one dict per bar: {"segment": index, "missing_before": bars missing
    immediately before this bar}. The first bar has missing_before = 0.
    """
    out, seg = [], 0
    for i, t in enumerate(times):
        if i == 0:
            out.append({"segment": 0, "missing_before": 0})
            continue
        step = t - times[i - 1]
        if step <= 0 or step % interval:
            raise ValueError("times must be strictly increasing and on bar boundaries")
        missing = step // interval - 1
        if missing:
            seg += 1
        out.append({"segment": seg, "missing_before": missing})
    return out


def one_bar_returns(times: list[int], closes: list[float], interval: int) -> list:
    """close[i] / close[i-1] - 1 when bar i-1 is exactly one interval earlier;
    None (undefined) for the first bar and for any bar right after a gap."""
    out = [None]
    for i in range(1, len(times)):
        out.append(closes[i] / closes[i - 1] - 1 if times[i] - times[i - 1] == interval else None)
    return out


@dataclass(frozen=True)
class GapDecision:
    action: str          # "exit_stop", "exit_target" or "continue"
    price: float = None  # fill price for exits


def first_bar_after_gap(side: str, first_open: float, stop: float = None,
                        target: float = None) -> GapDecision:
    """Apply the DQP-1 rule at the first available primary bar after a gap.

    LONG : open <= stop   -> exit at the OPEN   (adverse gap: loss is realised)
           open >= target -> exit at the TARGET (favourable gap: no windfall)
    SHORT: open >= stop   -> exit at the OPEN
           open <= target -> exit at the TARGET
    Otherwise continue; ordinary intrabar rules then apply inside the bar.
    Nothing is assumed about what happened during the missing hours.
    """
    if side not in (LONG, SHORT):
        raise ValueError(f"side must be {LONG!r} or {SHORT!r}")
    for name, v in (("first_open", first_open), ("stop", stop), ("target", target)):
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError(f"{name} must be a finite positive price, got {v!r}")
    if stop is not None and target is not None:
        if side == LONG and not stop < target:
            raise ValueError("for a LONG position the stop must be below the target")
        if side == SHORT and not stop > target:
            raise ValueError("for a SHORT position the stop must be above the target")

    if side == LONG:
        stop_hit = stop is not None and first_open <= stop
        target_hit = target is not None and first_open >= target
    else:
        stop_hit = stop is not None and first_open >= stop
        target_hit = target is not None and first_open <= target
    if stop_hit:
        return GapDecision("exit_stop", first_open)
    if target_hit:
        return GapDecision("exit_target", target)
    return GapDecision("continue")


def is_gap_exposed(entry_time: float, exit_time: float, missing_bar_times, interval: int) -> bool:
    """True if the position was open at any moment of any missing bar.

    A missing bar starting at t covers [t, t + interval). The trade is open
    over [entry_time, exit_time). They overlap if entry < t + interval and
    exit > t.
    """
    if exit_time < entry_time:
        raise ValueError("exit_time is before entry_time")
    return any(entry_time < t + interval and exit_time > t for t in missing_bar_times)
