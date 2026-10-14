"""A FAKE Bitstamp server for tests. Not market data.

candle(t) returns a dict with open/high/low/close/volume for Unix time t,
or None when the fake venue has no candle for that hour.
"""

import json
from urllib.parse import parse_qs, urlparse


class FakeBitstamp:
    """Callable like quantlab.data.downloader.urllib_fetch(url, timeout)."""

    def __init__(self, candle, fail_when=None, max_candles=1000):
        self.candle = candle
        self.fail_when = fail_when  # optional: url -> True to answer HTTP 503
        self.max_candles = max_candles
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.fail_when and self.fail_when(url):
            return 503, {}, b"service unavailable"
        q = parse_qs(urlparse(url).query)
        step, limit = int(q["step"][0]), int(q["limit"][0])
        start, end = int(q["start"][0]), int(q["end"][0])
        if limit > self.max_candles:
            return 400, {}, b'{"errors": [{"field": "limit", "message": "too large"}]}'
        last = min(end, start + (limit - 1) * step)
        ohlc = []
        for t in range(start, last + 1, step):
            c = self.candle(t)
            if c is not None:
                ohlc.append({"timestamp": str(t), **{k: str(v) for k, v in c.items()}})
        body = {"data": {"pair": "BTC/USD", "ohlc": ohlc}}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
