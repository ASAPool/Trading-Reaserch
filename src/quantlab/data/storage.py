"""Saving and loading price data as CSV, plus safe file-writing helpers.

CSV ("comma-separated values") is a plain-text table that also opens in
Excel. Columns:

    open_time_utc   bar START time, ISO 8601 UTC, e.g. 2019-01-01T00:00:00Z
    open_time_unix  the same moment as a Unix timestamp (seconds)
    open, high, low, close, volume

Prices are written exactly as the provider sent them (no rounding).
"""

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from quantlab.core.bars import Bar, utc_from_unix

CSV_COLUMNS = ["open_time_utc", "open_time_unix", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class CandleRow:
    """One candle exactly as received: time as an int, values as decimal text."""
    open_time_unix: int
    open: str
    high: str
    low: str
    close: str
    volume: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write a file so it is either fully written or not changed at all.

    Data goes to a temporary file first, which is then renamed over the
    target. An interruption (crash, power loss, Ctrl+C) therefore can never
    leave a half-written file behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=False) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))


def candle_rows_to_csv_bytes(rows: list[CandleRow]) -> bytes:
    """Render rows as CSV text (rows are written in the order given)."""
    lines = [",".join(CSV_COLUMNS)]
    for r in rows:
        iso = utc_from_unix(r.open_time_unix).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(",".join([iso, str(r.open_time_unix), r.open, r.high, r.low, r.close, r.volume]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def read_bars_csv(path: Path, interval_seconds: int) -> list[Bar]:
    """Load a candles CSV into standard Bar objects, in file order.

    Checks that both time columns agree; does not alter or drop any row.
    """
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError(f"{path}: unexpected columns {reader.fieldnames}, expected {CSV_COLUMNS}")
        for line_no, row in enumerate(reader, start=2):
            open_time = utc_from_unix(int(row["open_time_unix"]))
            if open_time.strftime("%Y-%m-%dT%H:%M:%SZ") != row["open_time_utc"]:
                raise ValueError(f"{path}:{line_no}: open_time_utc and open_time_unix disagree")
            bars.append(Bar(
                open_time=open_time,
                interval_seconds=interval_seconds,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            ))
    return bars
