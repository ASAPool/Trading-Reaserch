# The data pipeline (Phase 3), explained

## What it does

Downloads **BTC-USD 1-hour candles** from Coinbase's free public price
history, for **2019-01-01 00:00 UTC through 2026-09-30 23:00 UTC**
(67,920 hourly slots), and saves them on your computer.

## How to run it

From the project folder:

```
.venv\Scripts\python.exe -m quantlab.data.download
```

It takes a few minutes. If it stops for any reason, run the same command
again: it continues from where it stopped.

## Where the results go

```
data\coinbase-exchange\BTC-USD\1h_2019-01-01_2026-09-30\
    raw\chunk_0000.json ... chunk_0227.json   exact Coinbase responses, never edited
    manifest.json                             what was downloaded, when, how
    candles.csv                               all candles in one table, oldest first
    download.log                              everything that happened
```

## How it stays safe

| Rule | How |
|---|---|
| Respect the 300-candle limit | Requests are split into 228 chunks of at most 299 candles. The request end is set one hour past each chunk, so no candle is lost whatever Coinbase's end-time rule is. The one possible extra candle belongs to the next chunk and is excluded from the CSV (counted as a "boundary row"). |
| Be polite to the server | A 0.4 second pause between requests. |
| Survive temporary problems | Network errors, "too many requests" (429), server errors (5xx) and garbled responses are retried up to 5 times, waiting 2, 4, 8, 16 seconds (longer if the server asks). |
| Fail clearly | If a chunk still fails, the download stops, explains which chunk failed and why, and creates no CSV. |
| Never invent data | Hours with no trades have no candle. They are **counted and reported** as missing, never filled in. |
| Keep raw data unchanged | Each response is saved byte-for-byte with a SHA-256 fingerprint. If a raw file is later changed or deleted, the program refuses to continue. |
| No half-written files | Every file is written to a temporary name first and then renamed in one step. |
| No unfinished candles | The program refuses to download any candle that has not fully closed yet. |
| Reproducible | `manifest.json` records the exact settings, every request URL, every fingerprint, the software version and Git commit, and the Python version. |

## Known limitations (stated honestly)

- This is **Coinbase's** trading only, not the whole crypto market.
- Coinbase gives no candle for an hour without trades, so gaps can be real.
- Full data checks (duplicates, gaps, bad prices, time consistency) come
  in **Phase 4**. Phase 3 only counts missing and duplicate times.
- XRP trading on Coinbase was suspended from January 2021 to July 2023.
  That will matter in Phase 12, when XRP is added.

## Words you'll see

- **API**: a web address that programs (not people) use to ask a service for data.
- **Chunk**: one small piece of a big download.
- **Retry / backoff**: trying again after a failure, waiting longer each time.
- **SHA-256 fingerprint**: a long code computed from a file's exact contents.
  Change even one character and the fingerprint changes completely.
- **Manifest**: a written record of what was downloaded and how.
