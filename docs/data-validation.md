# Data validation (Phase 4), explained

## What it does

Checks the downloaded dataset for problems and writes a report.
**It only reads the data.** It never fills, removes, smooths, interpolates,
re-orders or otherwise changes any market data. The only files it creates
are its two reports.

## How to run it

From the project folder:

```
.venv\Scripts\python.exe -m quantlab.data.validate
```

It writes, next to the data:

- `validation_report.txt`: for people to read
- `validation_report.json`: every detail, for programs

## The verdict

| Verdict | Meaning |
|---|---|
| **PASSED** | No problems found. |
| **PASSED WITH WARNINGS** | Usable, but something needs a human decision (for example, missing hours). |
| **FAILED** | At least one ERROR. Do not use this dataset until the cause is understood. |

## The nine checks

| # | Check | ERROR (dataset fails) | WARNING (human review) |
|---|---|---|---|
| 1 | Integrity | A raw file or the CSV changed or is missing; the manifest is incomplete or for different settings; the CSV is not byte-identical to a fresh rebuild from the raw files | |
| 2 | Structure | Wrong columns or field count; a value that is not a number; **NaN or infinity**; empty values or stray spaces | |
| 3 | Timestamps | Text time and Unix time disagree; not on an hour boundary; out of order; outside the dataset period | |
| 4 | Duplicates | The same hour appears more than once | |
| 5 | Missing bars | | Every gap, with start, end and length. **Never filled.** |
| 6 | Price logic | high below open/close; low above open/close; high below low; a price of zero or less | |
| 7 | Volume | Negative volume | Zero volume |
| 8 | Overlap | | A boundary candle has different values in two requests, or one request returned it and the other did not |
| 9 | Suspicious moves | | A close more than 10% from the previous close; a bar wider than 15%; an open more than 2% from the previous close; a flat bar (open = high = low = close) |

The thresholds for check 9 live in `config/settings.toml` under `[validation]`.
They only decide what is **flagged**; nothing is ever removed.

Moves are only compared between directly adjacent hours. If an hour is
missing, the bars on either side of the gap are not compared.

## How we know the checks work

`tests/test_phase4_validation.py` builds clean fake datasets, breaks one
thing at a time (a NaN, a tampered file, a duplicate, an impossible
candle, a gap, an inconsistent boundary candle, ...) and confirms the
validator reports exactly that problem with the right severity.

The tests were also checked by **mutation testing**: each validation rule
was deliberately disabled, one at a time, to confirm that at least one
test fails every time. All 22 sabotaged versions were caught.
