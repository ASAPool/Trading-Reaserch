# Trading Research Lab (quantlab)

A **research-only** system for testing crypto trading ideas on historical data,
honestly and reproducibly.

> **This project never trades real money.**
> There is no broker connection and no live order execution, by design.
> A good backtest is **not** proof of future profit.

## Current scope

| Item | Status |
|---|---|
| Markets | Crypto only: **BTC, ETH, SOL, XRP** |
| Futures | Not implemented. Planned as a later expansion. |
| Current phase | **Phase 4: data validation** (no trading logic yet) |

## Folder guide

| Folder | What lives there |
|---|---|
| `config/` | Plain-text settings (markets, timeframe). No code. |
| `src/quantlab/core/` | Market-neutral building blocks (price-bar format, backtest engine). Never mentions crypto. |
| `src/quantlab/markets/crypto/` | Everything crypto-specific (data source, fees, 24/7 trading). |
| `src/quantlab/data/` | Downloading, storing and validating price data. |
| `src/quantlab/stats/` | Performance math and train / validation / test splits. |
| `src/quantlab/experiments/` | The lab notebook: one record per experiment. |
| `src/quantlab/agents/` | The six research agents and the coordinator (Phase 10). |
| `tests/` | Automated checks that prove the code behaves correctly. |
| `data/` | Downloaded price files. Kept on your computer only, never uploaded. |
| `docs/` | Beginner-friendly explanations. |

See [`docs/project-structure.md`](docs/project-structure.md) for more detail.

## How to run the checks

From the project folder, using the project's virtual environment:

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

A successful run ends with `OK`.

## How to download the data (Phase 3)

One-time setup, which registers the project with its virtual environment:

```
.venv\Scripts\python.exe -m pip install -e .
```

Then:

```
.venv\Scripts\python.exe -m quantlab.data.download
```

See [`docs/data-pipeline.md`](docs/data-pipeline.md) for what it does and how it stays safe.

## How to validate the data (Phase 4)

```
.venv\Scripts\python.exe -m quantlab.data.validate
```

Read-only: it reports problems and never changes data. See
[`docs/data-validation.md`](docs/data-validation.md).

## How to cross-check flagged events (Phase 4, adjudication)

```
.venv\Scripts\python.exe -m quantlab.data.adjudicate
```

Compares every missing or suspicious Coinbase hour with an independent second
provider (Bitstamp). Read-only; nothing is merged or replaced. See
[`docs/data-adjudication.md`](docs/data-adjudication.md).

## Roadmap

0. Inspect environment ✅
1. Project folder + virtual environment ✅
2. Project structure ✅
3. One reliable data pipeline (BTC, 1-hour candles) ✅
4. Data validation tests ⬅ current
5. Smallest trustworthy backtester
6. Hand-verifiable test strategies
7. Realistic costs and execution assumptions
8. Train / validation / untouched test methodology
9. Experiment tracking
10. Six-agent research architecture
11. First multi-agent experiment
12. ETH, SOL, XRP
13. *(Futures: deferred)*
14. Paper trading (simulated only)
15. Dashboard
