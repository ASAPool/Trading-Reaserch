"""The standard price-bar format shared by every market.

A "bar" (or "candle") summarises trading over one fixed time interval:
the first price (open), highest (high), lowest (low), last (close) and
the amount traded (volume).

TIMING RULE (prevents look-ahead bias):
``open_time`` is the START of the interval. None of the bar's values are
known until ``close_time`` (= open_time + interval). A strategy may only
use a bar's values for decisions made at or after its close_time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Timeframe names used in settings files, mapped to their length in seconds.
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_to_seconds(name: str) -> int:
    """Convert a timeframe name such as "1h" into seconds (3600)."""
    try:
        return TIMEFRAME_SECONDS[name]
    except KeyError:
        raise ValueError(
            f"Unknown timeframe {name!r}. Known: {sorted(TIMEFRAME_SECONDS)}"
        ) from None


@dataclass(frozen=True)
class Bar:
    """One price bar. All times are timezone-aware UTC."""

    open_time: datetime
    interval_seconds: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        # Only structural checks here. Price sanity (e.g. high < low) is
        # reported by data validation instead, so bad data can be inspected
        # rather than silently rejected.
        if self.open_time.tzinfo is None or self.open_time.utcoffset() != timedelta(0):
            raise ValueError(f"open_time must be timezone-aware UTC, got {self.open_time!r}")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    @property
    def close_time(self) -> datetime:
        """The moment this bar's values become known."""
        return self.open_time + timedelta(seconds=self.interval_seconds)


def utc_from_unix(seconds: int) -> datetime:
    """Convert a Unix timestamp (seconds since 1970-01-01 UTC) to a UTC datetime."""
    return datetime.fromtimestamp(seconds, tz=timezone.utc)
