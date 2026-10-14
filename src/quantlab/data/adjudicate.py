"""The adjudication command (read-only cross-provider check).

Run from the project folder with:
    .venv\\Scripts\\python.exe -m quantlab.data.adjudicate

1. Re-validates the primary (Coinbase) dataset, read-only, to get its events.
2. Downloads small windows of SECOND-provider (Bitstamp) data around each
   event into its own folder: data/bitstamp-exchange/...
3. Compares, using the fixed rules in config/settings.toml [adjudication].
4. Writes the report into a separate folder: data/adjudication/...

It never changes, fills, replaces or merges any market data.
Exit code: 0 = report written, 1 = could not run (e.g. dataset failed validation).
"""

import argparse
import logging
import sys
import tomllib
from pathlib import Path

from quantlab.data.adjudication import AdjudicationError, AdjudicationRules, adjudicate
from quantlab.data.download import PROJECT_ROOT, _setup_logging, _teardown_logging, resolve_dataset
from quantlab.data.storage import atomic_write_bytes
from quantlab.markets.crypto.bitstamp import BitstampCandleSource


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cross-check validation events (read-only).")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config" / "settings.toml")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args.settings, args.data_dir)
    with open(args.settings, "rb") as f:
        settings = tomllib.load(f)
    rules = AdjudicationRules.from_settings(settings)
    provider = settings.get("adjudication", {}).get("second_provider", "bitstamp")
    if provider != "bitstamp":
        print(f"Unknown second_provider {provider!r}; only 'bitstamp' is available.")
        return 1
    second = BitstampCandleSource(dataset.source.symbol, dataset.source.granularity)

    report_dir = (Path(args.data_dir) / "adjudication"
                  / f"{dataset.source.name}_{dataset.source.product_id}_{dataset.out_dir.name}")
    if not dataset.out_dir.exists():
        print(f"No dataset found at {dataset.out_dir}")
        return 1
    handlers, level = _setup_logging(report_dir / "adjudication.log")
    log = logging.getLogger("quantlab.data.adjudication")
    try:
        log.info("Adjudicating %s against %s (read-only)...", dataset.out_dir, second.name)
        try:
            report = adjudicate(dataset.out_dir, dataset.source, second, args.data_dir,
                                dataset.timeframe, rules)
        except AdjudicationError as e:
            log.error("ADJUDICATION STOPPED: %s", e)
            return 1
        text_path = report_dir / "adjudication_report.txt"
        json_path = report_dir / "adjudication_report.json"
        atomic_write_bytes(text_path, report.to_text().encode("utf-8"))
        atomic_write_bytes(json_path, report.to_json_bytes())
        log.info("")
        log.info("ALIGNMENT CHECK: %s", "PASSED" if report.alignment["passed"] else "FAILED")
        log.info("   %s", report.alignment["explanation"])
        log.info("")
        log.info("LABEL COUNTS (%d events)", len(report.events))
        for label, n in report.counts().items():
            log.info("   %3d  %s", n, label)
        log.info("")
        log.info("Full report : %s", text_path)
        log.info("Details     : %s", json_path)
        return 0
    finally:
        _teardown_logging(handlers, level)


if __name__ == "__main__":
    sys.exit(main())
