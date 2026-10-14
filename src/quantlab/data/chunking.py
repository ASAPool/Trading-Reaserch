"""Split a long date range into small, non-overlapping request windows.

Data providers limit how many bars one request may return, so a long
history must be fetched in pieces ("chunks"). Chunks here are:
- contiguous: each starts exactly one interval after the previous one ends
- non-overlapping: no bar time belongs to two chunks
- complete: together they cover every bar time from start to end
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Chunk:
    index: int
    start: datetime          # open time of the first bar in this chunk
    end_inclusive: datetime  # open time of the last bar in this chunk

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end_inclusive


def plan_chunks(start: datetime, end_inclusive: datetime,
                interval_seconds: int, bars_per_chunk: int) -> list[Chunk]:
    """Return chunks covering every bar open time in [start, end_inclusive]."""
    if start.tzinfo is None or end_inclusive.tzinfo is None:
        raise ValueError("start and end must be timezone-aware (UTC)")
    if end_inclusive < start:
        raise ValueError("end must not be before start")
    if interval_seconds <= 0 or bars_per_chunk <= 0:
        raise ValueError("interval_seconds and bars_per_chunk must be positive")
    if (start.timestamp() % interval_seconds) or (end_inclusive.timestamp() % interval_seconds):
        raise ValueError("start and end must fall exactly on bar boundaries")

    step = timedelta(seconds=interval_seconds)
    chunks = []
    chunk_start = start
    while chunk_start <= end_inclusive:
        chunk_end = min(chunk_start + step * (bars_per_chunk - 1), end_inclusive)
        chunks.append(Chunk(len(chunks), chunk_start, chunk_end))
        chunk_start = chunk_end + step
    return chunks


def expected_bar_count(start: datetime, end_inclusive: datetime, interval_seconds: int) -> int:
    """How many bar slots exist in the period (if no bars were missing)."""
    return int((end_inclusive - start).total_seconds()) // interval_seconds + 1
