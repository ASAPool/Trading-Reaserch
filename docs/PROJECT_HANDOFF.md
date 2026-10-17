# PROJECT HANDOFF (read this first)

Last updated: end of **Phase 4** (data validation, adjudication, DQP-1 complete).
**Phase 5 has NOT started.** No strategy code and no backtester exist yet.

A new Claude Code session must read this file, `README.md`, every file in
`docs/`, `config/`, `records/`, the Git history (`git log`), and the current
code under `src/` and `tests/` **before making any change**.

---

## 1. What this project is

A research-only, multi-agent quantitative **research** system for crypto
(BTC, ETH, SOL, XRP). Futures are a later expansion and are NOT implemented.

**Hard rules (from the owner):**
- Never connect to a broker. Never add live order execution. No real money.
- Never fabricate data or results. Never claim guaranteed profitability.
- Never hide failed tests; never silently repair data; never use future data.
- Backtest profitability is never proof of future profitability.
- Prefer boring and trustworthy over impressive and unreliable.
- If evidence is insufficient, say: "WE DO NOT YET HAVE ENOUGH EVIDENCE."

**The owner** has zero coding experience and uses a **Windows 10 laptop**
(PowerShell). Working agreement:
- Explain everything in plain language (what, why, where, how to run, what success looks like).
- Give ONE step at a time; say exactly where to paste each command and what to expect; wait for the result.
- Propose plans and wait for approval before large changes. Explain any new dependency first (none added so far).
- Tests and mutation testing ("deliberately disable each rule, confirm a test fails") before claiming anything works.

## 2. How work flows

- Code is written and tested in the cloud session, committed, and pushed to
  branch `claude/multi-agent-crypto-research-htfg15` of `ASAPool/Trading-Reaserch`.
- The owner pulls on the laptop (project at `C:\Users\mason\Projects\Trading-Reaserch`,
  virtual environment `.venv`, project installed editable with `pip install -e .`).
- The cloud cannot reach Coinbase or Bitstamp (network policy), so **all real
  data lives only on the laptop** (`data/` is git-ignored). Real-data commands
  are run by the owner on the laptop.
- Laptop commands use the form:
  `& $HOME\Projects\Trading-Reaserch\.venv\Scripts\python.exe -m <module>`
  and `git -C $HOME\Projects\Trading-Reaserch pull origin claude/multi-agent-crypto-research-htfg15`.
- Run all tests: `.venv\Scripts\python.exe -m unittest discover -s tests -v`.

## 3. Architecture (Python 3.12, standard library only, no add-on packages)

```
config/settings.toml             symbol/timeframe/period, validation and adjudication thresholds
config/data_quality_policy.toml  DQP-1 (fixed; see section 6)
config/policy_registry.toml      DQP-1 commit hash + fingerprint
records/datasets/*.toml          permanent dataset record (fingerprints)
src/quantlab/core/               MARKET-NEUTRAL: bars.py, policy.py, gap_execution.py
src/quantlab/stats/              MARKET-NEUTRAL: sensitivity.py (DQP-1 comparison rules)
src/quantlab/experiments/        empty (Phase 9)
src/quantlab/agents/             empty (Phase 10)
src/quantlab/data/               chunking, storage, downloader (generic), validation,
                                 adjudication, quality_flags + commands:
                                 download, validate, adjudicate, quality_flags
src/quantlab/markets/crypto/     coinbase.py (temporary test provider), bitstamp.py (second source)
tests/                           unittest suites + fake servers (fake data only)
```

Rules enforced by tests: `core`, `stats`, `experiments`, `agents` never import
`quantlab.markets`. Providers are isolated modules; the research core sees only
standardized OHLCV bars. Coinbase is a **temporary** provider; the owner plans a
different final provider. The provider is chosen in one place
(`data/download.py: resolve_dataset`).

## 4. Completed phases

| Phase | Result |
|---|---|
| 0-1 | Windows setup: Python 3.12.10, Git, VS Code, project folder, `.venv` |
| 2 | Project structure |
| 3 | Chunked, resumable, retrying Coinbase downloader; raw responses kept byte-for-byte with SHA-256 manifest |
| 4 | Read-only validator (9 checks, NaN/inf rejected); Bitstamp cross-provider adjudication; DQP-1 policy; quality_flags.json; permanent dataset record |

## 5. The dataset (BTC-USD, 1 hour, Coinbase)

See `records/datasets/coinbase-exchange_BTC-USD_1h_2019-01-01_2026-09-30.toml`.

- Period 2019-01-01 00:00 to 2026-09-30 23:00 UTC: 67,920 slots, 67,901 bars, 19 missing (9 gaps), 0 duplicates.
- Validation: PASSED WITH WARNINGS (0 impossible candles, all fingerprints OK, CSV rebuilds byte-for-byte).
- Bitstamp adjudication: alignment passed (973 hours, median close diff 0.033%, zero shift best).
  All 19 missing bars: PRIMARY-SPECIFIC GAP. 27 warnings: 4 move observed, 11 range observed,
  11 partially observed/unresolved, 1 primary-specific flat bar.
- quality_flags.json: 21 flagged hours, 9 masked in Variant B; reproducible.

| File | SHA-256 |
|---|---|
| candles.csv | 35f39de2fdf3ce5c3c246d772e170338b7ac6e54c1d3e49e41b9c3ec4d478251 |
| manifest.json | 37c19a6ff88c38d5ffe9963004816c1d68ab290ada9ac370b50cd311af7f5055 |
| adjudication_report.json | ba112b74b0e34248f1d77099fd498fb36f651bcaebb999eaef036d65ac8b09a0 |
| quality_flags.json | 1e6694aa41b20557bafb53c81287f5fadc0bd2c6f05bd0e77d083dca7ebf9df4 |

**The raw dataset must never be modified, filled, or merged with other providers.**

Never copy fingerprints by hand. Print them with
`.venv\Scripts\python.exe -m quantlab.data.fingerprints`, which also reports
MATCH/MISMATCH against this record. (A hand-copied one-character typo in the
quality_flags.json fingerprint was caught by `tests/test_dataset_record.py` and
corrected; the real file was never changed.)

## 6. DQP-1 (data-usage policy), fixed before any strategy existed

Full text: `config/data_quality_policy.toml`; explanation: `docs/data-quality-policy.md`.
Commit `ad7d65005d899a361da66fb89846100458517886`, policy SHA-256 (LF-normalised)
`f0e1624e3f2aa57442bea929d9857b070f6de1f0f421346669689fd4c61f7196`.

Key points the Phase 5 engine must implement:
- Missing bars never filled; one-bar returns undefined across gaps; window indicators need
  complete windows; recursive indicators reset and re-warm; no signal while invalid.
- During missing primary hours: hold, execute nothing, infer nothing, never use Bitstamp prices.
  First bar after a gap (symmetric): LONG open<=stop -> exit at open; open>=target -> exit at
  target; SHORT mirrored. Implemented and tested in `core/gap_execution.py`.
- Trades open during any missing hour are `gap_exposed`.
- Every experiment runs Variant A (official) and Variant B (non-corroborated hours treated as
  missing). SENSITIVE TO DATA QUALITY rules in `stats/sensitivity.py: compare_variants`.
- Gap-exposure DIAGNOSTIC CONTRIBUTION ANALYSIS (never a counterfactual backtest):
  `stats/sensitivity.py: gap_exposure_diagnostic`, rules 1-4.
- Zero tolerance 1e-12 for sign classification of dimensionless values.
- Every experiment records policy version, policy commit, policy SHA-256, quality_flags SHA-256,
  and its own code commit (`data/quality_flags.py: experiment_data_identity`).
- Policy changes require a new version (DQP-2) with a written reason; thresholds are never tuned
  on profitability.

## 7. Test status

All tests pass on Python 3.12 and 3.13 (cloud) and on the owner's Windows laptop.
`tests/test_dataset_record.py` re-checks the real files' fingerprints wherever the
real data exists (the laptop) and skips elsewhere. Mutation testing was done for
the validator, adjudicator and DQP-1 rules (all sabotaged rules caught).

## 8. Known limitations

- Coinbase is a temporary source; volume is Coinbase-only.
- Bitstamp's real response format was verified only by the laptop run (cloud blocked).
- XRP on Coinbase was suspended Jan 2021 to Jul 2023 (matters in Phase 12).
- No trading costs defined yet (Phase 7). No intrabar execution rules yet (Phase 5).

## 9. Current stopping point and exact next step

Phase 4 is complete. **Next: Phase 5, the smallest trustworthy backtesting engine.**
Do NOT start building it directly. First propose a written Phase 5 plan to the
owner in plain language (scope, intrabar fill rules, how DQP-1 is enforced,
what is deliberately excluded until Phases 6-9, how it will be tested with
hand-verifiable examples) and **wait for approval**.
