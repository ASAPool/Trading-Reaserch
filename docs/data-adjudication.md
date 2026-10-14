# Cross-provider adjudication, explained

## What it does

For every **missing bar** and every **suspicious or flat candle** found by
the Phase 4 validator, it looks at what an **independent second provider
(Bitstamp)** recorded for the same hour, and labels the evidence.

**It is read-only.** It never fills, replaces, smooths or merges anything.
The Coinbase dataset is fingerprinted before and after, and the run stops
with an error if anything in it changed. Bitstamp data lives in its own
folder and is never mixed into the Coinbase data. **No backtesting decision
is made here.**

## How to run it

```
.venv\Scripts\python.exe -m quantlab.data.adjudicate
```

Outputs:

```
data\bitstamp-exchange\BTC-USD\1h_windows\<window>\   raw Bitstamp replies, manifest, CSV (separate)
data\adjudication\coinbase-exchange_BTC-USD_1h_...\
    adjudication_report.txt     for people
    adjudication_report.json    every detail
    adjudication.log            what happened during the run
```

## Steps

1. Re-validate the Coinbase dataset (read-only). If it FAILED, stop.
2. Download Bitstamp data for each event hour **plus 24 hours on each side**
   (overlapping windows are merged), using the same safe downloader as Coinbase.
3. **Alignment check**: on normal, unflagged hours, compare closing prices
   with Bitstamp shifted by -1, 0 and +1 hour. Comparison is only trusted if
   **no shift fits best**, the median difference is **at most 0.5%**, and at
   least **100** hours are compared. Otherwise **every event is UNRESOLVED**.
4. Label each event using the fixed rules below.

## Rules (fixed in `config/settings.toml` before any real results were seen)

### Missing Coinbase hour

| Bitstamp shows | Label |
|---|---|
| A candle with volume > 0 | **PRIMARY-SPECIFIC GAP** (Bitstamp traded, Coinbase recorded nothing) |
| No candle, or zero volume | **CROSS-VENUE MISSING / UNRESOLVED** (two missing venues are *not* proof of a market-wide halt) |
| No usable data, or alignment failed | **UNRESOLVED: CANNOT BE INDEPENDENTLY VERIFIED** |

### Suspicious candle

The question is: **was the general market move independently observed?**

- **Large move**: compare the close-to-close move on each venue.
- **Open gap**: compare the previous-close-to-open move on each venue.
- **Wide bar**: compare the bar's range (high - low) on each venue.

| Second-source move compared with Coinbase's | Open and close agree within 1%? | Label |
|---|---|---|
| Same direction, at least 50% as large | yes | **MARKET MOVE INDEPENDENTLY OBSERVED** (or **WIDE RANGE ... OBSERVED**) |
| Same direction, at least 50% as large | no | **PARTIALLY OBSERVED / UNRESOLVED** |
| Between 20% and 50% as large | either | **PARTIALLY OBSERVED / UNRESOLVED** |
| 20% or less, or opposite direction | either | **MOVE NOT OBSERVED ON SECOND SOURCE** (possible Coinbase-specific event) |

High and low differences are **reported separately**. If either differs by
more than 3%, the event gets the extra tag **EXTREME WICK DISAGREEMENT**.
This tag never invalidates the candle and never changes the main label,
because different exchanges legitimately have different intra-hour wicks.

### Flat candle (open = high = low = close)

| Bitstamp in that hour | Label |
|---|---|
| Traded normally (range and volume) | **PRIMARY-SPECIFIC FLAT BAR** |
| Also flat, or zero volume | **CROSS-VENUE QUIET / UNRESOLVED** |

## Honest limits

- Agreement between two venues is **corroboration, not proof**.
- "Not observed on the second source" does **not** mean the Coinbase candle
  is wrong. A wick seen only on Coinbase may still be real Coinbase trading.
- Bitstamp is smaller than Coinbase; small price differences are normal.
- Bitstamp's reply format could only be checked against its documentation,
  not live, while building this (the build computer cannot reach Bitstamp).
  The strict parser and the alignment check stop or mark events UNRESOLVED
  if Bitstamp's real data does not match expectations.
