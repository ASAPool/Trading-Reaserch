# Project structure, explained

## The big idea: a neutral core plus market "plug-ins"

```
           ┌──────────────────────────────────────────┐
           │  agents/      the research team          │
           │  experiments/ the lab notebook           │
           │  stats/       the performance math       │
           │  core/        the backtest engine        │   <- never mentions crypto
           └──────────────────────────────────────────┘
                              ▲
                  standard price bars + costs
                              │
           ┌──────────────────┴───────────────────────┐
           │  markets/crypto/   (now)                 │
           │  markets/futures/  (later, not built)    │
           └──────────────────────────────────────────┘
```

Every market is translated into the **same standard format** before the core
sees it. That means:

- the backtester, statistics, experiment records and agents are written once
- adding futures later means adding a new `markets/futures/` folder,
  not rewriting the engine

An automated test (`tests/test_project_structure.py`) enforces this rule:
it fails if `core`, `stats`, `experiments` or `agents` ever imports from `markets`.

## Where things are

| Path | Purpose | Built in |
|---|---|---|
| `config/settings.toml` | Settings in plain text | Phase 2 |
| `src/quantlab/data/` | Download and validate price data | Phases 3–4 |
| `src/quantlab/core/` | Bar format, backtest engine, cost interface | Phases 3, 5, 7 |
| `src/quantlab/markets/crypto/` | Crypto data source, fees, 24/7 calendar | Phases 3, 7 |
| `src/quantlab/stats/` | Metrics, train/validation/test splits | Phases 6–8 |
| `src/quantlab/experiments/` | Experiment records | Phase 9 |
| `src/quantlab/agents/` | Six agents + coordinator | Phase 10 |
| `tests/` | Automated checks | Every phase |
| `data/` | Downloaded data (local only) | Phase 3 |

## Words you'll see

- **Package / module**: a folder or file of Python code. Each folder with an
  `__init__.py` file is a package.
- **Test**: a small program that checks other code behaves correctly.
  If a test fails, we fix the problem before moving on.
- **`.gitignore`**: a list of files Git should never upload.
- **TOML**: a simple plain-text settings format (`name = value`).
