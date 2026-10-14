"""Coinbase Exchange public candle data (no account or API key needed).

Endpoint documentation:
  GET https://api.exchange.coinbase.com/products/{product_id}/candles
      ?granularity=<seconds>&start=<ISO 8601>&end=<ISO 8601>

Facts this module relies on:
- At most 300 candles are returned per request.
- Each candle is a list in this order: [time, low, high, open, close, volume].
  NOTE the unusual order: low comes BEFORE high, and open is fourth.
- ``time`` is the candle's START time as a Unix timestamp (UTC).
- Candles are returned newest first.
- If no trades happened in an interval, Coinbase returns NO candle for it.
  Those gaps are real and are never filled in by this project.
- Volume is Coinbase's volume only, not the whole crypto market.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

from quantlab.data.chunking import Chunk
from quantlab.data.storage import CandleRow

BASE_URL = "https://api.exchange.coinbase.com"

# Coinbase only offers these candle sizes (seconds). There is no 4-hour size.
SUPPORTED_GRANULARITIES = (60, 300, 900, 3600, 21600, 86400)

MAX_CANDLES_PER_REQUEST = 300

# Project symbol -> Coinbase product id.
PRODUCTS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}


class CoinbaseCandleSource:
    """Builds request URLs for one product and parses the responses."""

    name = "coinbase-exchange"
    base_url = BASE_URL
    max_candles_per_request = MAX_CANDLES_PER_REQUEST
    # One less than the limit: leaves room for the one extra boundary candle
    # that build_url() may cause Coinbase to return.
    candles_per_chunk = MAX_CANDLES_PER_REQUEST - 1

    def __init__(self, symbol: str, granularity: int):
        if symbol not in PRODUCTS:
            raise ValueError(f"Unknown symbol {symbol!r}. Known: {sorted(PRODUCTS)}")
        if granularity not in SUPPORTED_GRANULARITIES:
            raise ValueError(
                f"Coinbase does not offer {granularity}-second candles. "
                f"Supported: {SUPPORTED_GRANULARITIES}"
            )
        self.symbol = symbol
        self.product_id = PRODUCTS[symbol]
        self.granularity = granularity

    def describe(self) -> dict:
        """Everything that defines WHAT is downloaded (stored in the manifest)."""
        return {
            "source": self.name,
            "base_url": self.base_url,
            "symbol": self.symbol,
            "product_id": self.product_id,
            "granularity_seconds": self.granularity,
            "candles_per_chunk": self.candles_per_chunk,
            "max_candles_per_request": self.max_candles_per_request,
        }

    def build_url(self, chunk: Chunk) -> str:
        # The request end is set one interval PAST the chunk's last candle.
        # This guarantees the last candle is included whether Coinbase treats
        # "end" as inclusive or exclusive. Any extra candle returned (which
        # belongs to the next chunk) is filtered out and counted, not kept.
        # Chunks hold at most 299 candles, so a response never exceeds 300.
        request_end = chunk.end_inclusive + timedelta(seconds=self.granularity)
        query = urlencode({
            "granularity": self.granularity,
            "start": _iso(chunk.start),
            "end": _iso(request_end),
        })
        return f"{self.base_url}/products/{self.product_id}/candles?{query}"

    def parse(self, body: bytes) -> list[CandleRow]:
        """Turn raw response bytes into rows. Raises ValueError if malformed.

        Numbers are kept as exact decimal text (no float rounding), so the
        CSV contains the same digits Coinbase sent.
        """
        data = json.loads(body, parse_float=Decimal, parse_int=int)
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list of candles, got {type(data).__name__}: {str(data)[:200]}")
        rows = []
        for i, item in enumerate(data):
            if not (isinstance(item, list) and len(item) == 6):
                raise ValueError(f"candle #{i} is not a list of 6 values: {item!r}")
            time_s, low, high, open_, close, volume = item
            if not isinstance(time_s, int) or isinstance(time_s, bool):
                raise ValueError(f"candle #{i} has a non-integer time: {time_s!r}")
            for value in (low, high, open_, close, volume):
                if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
                    raise ValueError(f"candle #{i} has a non-numeric value: {item!r}")
            rows.append(CandleRow(
                open_time_unix=time_s,
                open=str(open_),
                high=str(high),
                low=str(low),
                close=str(close),
                volume=str(volume),
            ))
        return rows


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
