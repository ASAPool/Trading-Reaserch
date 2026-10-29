# Trading Research Lab (quantlab)

> **New session? Read [`docs/PROJECT_HANDOFF.md`](docs/PROJECT_HANDOFF.md) first.**

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
| Current phase | **Phase 6 complete** (test strategies, synthetic data only; real data LOCKED). Next: Phase 7 (plan first). |

## Folder guide

| Folder | What lives there |
|---|---|
| `config/` | Plain-text settings (markets, timeframe). No code. |
| `src/quantlab/core/` | Market-neutral building blocks (price-bar format, backtest engine). Never mentions crypto. |
| `src/quantlab/markets/crypto/` | Everything crypto-specific (data source, fees, 24/7 trading). |
| `src/quantlab/data/` | Downloading, storing and validating price data. |
| `src/quantlab/stats/` | Performance math and train / validation / test splits. |
| `src/quantlab/strategies/` | Test strategies used only to verify the engine on made-up data (Phase 6). |
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

## Data quality policy (DQP-1)

How backtests must use the data (missing bars, flagged candles, gap
execution, sensitivity tests) is fixed in `config/data_quality_policy.toml`
before any strategy exists. See [`docs/data-quality-policy.md`](docs/data-quality-policy.md).

```
.venv\Scripts\python.exe -m quantlab.data.quality_flags
```

builds the separate `quality_flags.json` metadata file (raw data unchanged).

```
.venv\Scripts\python.exe -m quantlab.data.fingerprints
```

prints the dataset's real fingerprints and checks them against the permanent
record in `records/datasets/` (read-only).

## The backtest engine (Phase 5)

Replays made-up price bars one hour at a time and provably cannot use future
data. **COSTS NOT MODELLED (Phase 7).** It refuses to run on the real BTC
history until the train / validation / final-test split is frozen (Phase 8).
There is no command to run: it is exercised by the tests. See
[`docs/backtest-engine.md`](docs/backtest-engine.md).

## Test strategies (Phase 6)

Four deliberately simple strategies (`null`, `buy_and_hold`, `ma_cross`,
`channel_breakout`) that exist **only to verify the engine on made-up data**.
They are not research strategies and their results are never evidence of
profitability. See [`docs/strategies.md`](docs/strategies.md).

## Roadmap

0. Inspect environment ✅
1. Project folder + virtual environment ✅
2. Project structure ✅
3. One reliable data pipeline (BTC, 1-hour candles) ✅
4. Data validation tests ✅ (plus Bitstamp adjudication, DQP-1, dataset record)
5. Smallest trustworthy backtester ✅ (synthetic data only; see [`docs/backtest-engine.md`](docs/backtest-engine.md))
6. Hand-verifiable test strategies ✅ (synthetic data only; see [`docs/strategies.md`](docs/strategies.md))
7. Costs and execution assumptions (synthetic data only) ⬅ next
8. Split freeze: train / validation / final-test periods and leakage rules frozen before any real-data strategy result
9. Experiment tracking
10. First real-data evaluations (train/validation only)
11. Six-agent research architecture
12. First multi-agent experiment
13. ETH, SOL, XRP
14. Paper trading (simulated only)
15. Dashboard

The final test period stays untouched until its proper evaluation stage. *(Futures: deferred.)*
