"""The download command.

Run from the project folder with:
    .venv\\Scripts\\python.exe -m quantlab.data.download

It reads config/settings.toml (symbol, timeframe, start and end dates),
downloads the data in safe chunks, and saves into:
    data/<source>/<product>/<timeframe>_<start>_<end>/
        raw/chunk_0000.json ...  exact server responses (never edited)
        manifest.json            how, when and what was downloaded
        candles.csv              all bars combined, oldest first
        download.log             a log of everything that happened

If it is interrupted or fails, simply run it again: it resumes from
the chunks already saved.
"""

import argparse
import logging
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantlab.core.bars import timeframe_to_seconds
from quantlab.data.downloader import DataIntegrityError, DownloadError, download_bars
from quantlab.markets.crypto.coinbase import CoinbaseCandleSource

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Download historical bars (research only).")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config" / "settings.toml")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)

    with open(args.settings, "rb") as f:
        settings = tomllib.load(f)
    pipeline = settings["pipeline"]
    symbol = pipeline["first_symbol"]
    timeframe = pipeline["first_timeframe"]
    interval = timeframe_to_seconds(timeframe)

    # Dates are inclusive: the first bar opens at 00:00 UTC on start_date and
    # the last bar opens one interval before midnight at the end of end_date.
    start = _utc_date(pipeline["start_date"])
    end_inclusive = _utc_date(pipeline["end_date"]) + timedelta(days=1) - timedelta(seconds=interval)

    source = CoinbaseCandleSource(symbol, interval)
    out_dir = (args.data_dir / source.name / source.product_id
               / f"{timeframe}_{pipeline['start_date']}_{pipeline['end_date']}")
    handlers, previous_level = _setup_logging(out_dir / "download.log")
    try:
        return _run(source, timeframe, start, end_inclusive, out_dir)
    finally:
        # Always close download.log, even after a failure or crash, so no file
        # is left open (on Windows an open file cannot be moved or deleted).
        _teardown_logging(handlers, previous_level)


def _run(source, timeframe, start, end_inclusive, out_dir) -> int:
    log = logging.getLogger("quantlab.data.download")
    log.info("Downloading %s %s bars from %s into %s", source.product_id, timeframe, source.name, out_dir)

    try:
        result = download_bars(source, start, end_inclusive, out_dir)
    except (DownloadError, DataIntegrityError) as e:
        log.error("DOWNLOAD FAILED: %s", e)
        log.error("Chunks saved so far are kept. Running the command again resumes from them.")
        return 1

    log.info("DOWNLOAD COMPLETE")
    log.info("  Bar slots in period : %d", result.expected_bars)
    log.info("  Bars received       : %d", result.received_bars)
    log.info("  Missing bars        : %d  (reported only, never filled in)", result.missing_bars)
    log.info("  Duplicate bar times : %d  (reported only, never removed)", result.duplicate_bar_times)
    log.info("  Boundary rows excluded (expected, about 1 per chunk): %d", result.outside_window_rows)
    log.info("  Chunks: %d total, %d downloaded this run, %d HTTP requests",
             result.chunks_total, result.chunks_downloaded_this_run, result.http_requests_this_run)
    log.info("  CSV file : %s", result.csv_path)
    log.info("  Manifest : %s", result.manifest_path)
    return 0


def _utc_date(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _setup_logging(log_path: Path):
    """Attach console + file log handlers. Returns what is needed to undo it."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("quantlab")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logfile = logging.FileHandler(log_path, encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handlers = [console, logfile]
    for handler in handlers:
        logger.addHandler(handler)
    return handlers, previous_level


def _teardown_logging(handlers, previous_level) -> None:
    """Detach and close exactly the handlers _setup_logging added."""
    logger = logging.getLogger("quantlab")
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()  # closes download.log (closing the console handler leaves stdout open)
    logger.setLevel(previous_level)

if __name__ == "__main__":
    sys.exit(main())
