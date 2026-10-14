"""A FAKE Coinbase server for tests. Not market data.

It produces made-up candles with simple, predictable values so tests can
check exactly what the downloader does, without using the internet.
"""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


def fake_candle(t: int) -> list:
    """A made-up candle for Unix time t, in Coinbase's order [time, low, high, open, close, volume]."""
    base = 100 + (t // 3600) % 50
    return [t, base - 1, base + 2, base, base + 1, 1.5]


def smooth_candle(t: int) -> list:
    """A made-up candle whose prices move gently (under 1% per hour), so a clean
    fake dataset raises no "suspicious move" flags."""
    base = 1000 + (t // 3600) % 10
    return [t, base - 1, base + 2, base, base + 1, 1.5]


class FakeCoinbase:
    """Callable like quantlab.data.downloader.urllib_fetch(url, timeout).

    available_times: set of Unix times for which a candle "exists".
    end_inclusive:   True  -> behaves as if Coinbase includes the end time,
                     False -> as if it excludes it. The downloader must work either way.
    script:          optional list of responses to return for the first calls, each either
                     an Exception instance (raised) or a (status, headers, body) tuple.
    """

    def __init__(self, available_times, end_inclusive=True, script=None, max_candles=300,
                 candle=fake_candle):
        self.available_times = set(available_times)
        self.end_inclusive = end_inclusive
        self.script = list(script or [])
        self.max_candles = max_candles
        self.calls = []
        self.candle = candle

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        q = parse_qs(urlparse(url).query)
        start = _parse(q["start"][0])
        end = _parse(q["end"][0])
        gran = int(q["granularity"][0])
        if (end - start) // gran > self.max_candles:
            return 400, {}, b'{"message":"granularity too small for the requested time range"}'
        times = [t for t in self.available_times
                 if start <= t and (t <= end if self.end_inclusive else t < end)]
        candles = [self.candle(t) for t in sorted(times, reverse=True)]  # newest first, like Coinbase
        assert len(candles) <= self.max_candles, "fake server: response would exceed the limit"
        return 200, {"Content-Type": "application/json"}, json.dumps(candles).encode()


def _parse(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
