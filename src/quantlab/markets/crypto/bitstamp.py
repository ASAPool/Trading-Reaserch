"""Bitstamp public candle data: the independent SECOND source (no account or key).

Used only to cross-check Coinbase events (adjudication). Bitstamp data is
stored separately and is never merged into the Coinbase dataset.

Endpoint:
  GET https://www.bitstamp.net/api/v2/ohlc/{pair}/?step=<seconds>&limit=<n>&start=<unix>&end=<unix>

Facts this module relies on (checked again on the first real run by the
adjudicator's alignment test, which refuses to compare if timing differs):
- Response: {"data": {"pair": "BTC/USD", "ohlc": [{"timestamp", "open",
  "high", "low", "close", "volume"}, ...]}} with values usually as text.
- "timestamp" is believed to be the candle's START time (Unix, UTC).
- At most 1000 candles per request.
- Volume is Bitstamp's volume only. Bitstamp is a smaller venue than
  Coinbase, so small price differences between the two are normal.
"""

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from quantlab.data.chunking import Chunk
from quantlab.data.storage import CandleRow

BASE_URL = "https://www.bitstamp.net"
SUPPORTED_GRANULARITIES = (60, 180, 300, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400, 259200)
MAX_CANDLES_PER_REQUEST = 1000

# Project symbol -> Bitstamp pair code.
PAIRS = {
    "BTC": "btcusd",
    "ETH": "ethusd",
    "SOL": "solusd",
    "XRP": "xrpusd",
}


class BitstampCandleSource:
    """Builds request URLs for one pair and parses the responses."""

    name = "bitstamp-exchange"
    base_url = BASE_URL
    max_candles_per_request = MAX_CANDLES_PER_REQUEST
    # One less than the limit: leaves room for one boundary candle (see build_url).
    candles_per_chunk = MAX_CANDLES_PER_REQUEST - 1

    def __init__(self, symbol: str, granularity: int):
        if symbol not in PAIRS:
            raise ValueError(f"Unknown symbol {symbol!r}. Known: {sorted(PAIRS)}")
        if granularity not in SUPPORTED_GRANULARITIES:
            raise ValueError(f"Bitstamp does not offer {granularity}-second candles. "
                             f"Supported: {SUPPORTED_GRANULARITIES}")
        self.symbol = symbol
        self.pair = PAIRS[symbol]
        self.product_id = f"{symbol}-USD"  # project-wide name, same as Coinbase's
        self.granularity = granularity

    def describe(self) -> dict:
        return {
            "source": self.name,
            "base_url": self.base_url,
            "symbol": self.symbol,
            "product_id": self.product_id,
            "pair": self.pair,
            "granularity_seconds": self.granularity,
            "candles_per_chunk": self.candles_per_chunk,
            "max_candles_per_request": self.max_candles_per_request,
        }

    def build_url(self, chunk: Chunk) -> str:
        # Ask for every bar in the chunk plus one more, and set "end" one
        # interval past the last bar, so the chunk is fully covered whether
        # Bitstamp honours "start+limit" or "start..end". Any extra candle is
        # filtered out by the downloader and counted, never kept.
        bars = int((chunk.end_inclusive - chunk.start).total_seconds()) // self.granularity + 1
        end = chunk.end_inclusive + timedelta(seconds=self.granularity)
        query = urlencode({
            "step": self.granularity,
            "limit": min(bars + 1, self.max_candles_per_request),
            "start": int(chunk.start.timestamp()),
            "end": int(end.timestamp()),
        })
        return f"{self.base_url}/api/v2/ohlc/{self.pair}/?{query}"

    def parse(self, body: bytes) -> list[CandleRow]:
        """Turn raw response bytes into rows. Raises ValueError if malformed
        or if any value is not a finite number."""
        data = json.loads(body, parse_float=Decimal, parse_int=int,
                          parse_constant=_reject_non_finite)
        try:
            candles = data["data"]["ohlc"]
        except (TypeError, KeyError):
            raise ValueError(f"unexpected response shape: {str(data)[:200]}") from None
        if not isinstance(candles, list):
            raise ValueError("'ohlc' is not a list")
        rows = []
        for i, c in enumerate(candles):
            if not isinstance(c, dict):
                raise ValueError(f"candle #{i} is not an object: {c!r}")
            try:
                t = int(str(c["timestamp"]))
                values = {k: _finite_text(c[k], k, i)
                          for k in ("open", "high", "low", "close", "volume")}
            except KeyError as e:
                raise ValueError(f"candle #{i} is missing {e}") from None
            rows.append(CandleRow(open_time_unix=t, **values))
        return rows


def _finite_text(value, name, i) -> str:
    if isinstance(value, bool):
        raise ValueError(f"candle #{i} {name} is not a number: {value!r}")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"candle #{i} {name} is not a number: {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"candle #{i} {name} is not a finite number: {value!r}")
    return str(value)


def _reject_non_finite(token: str):
    raise ValueError(f"non-finite number {token!r} in response")
