"""The validation command (Phase 4).

Run from the project folder with:
    .venv\\Scripts\\python.exe -m quantlab.data.validate

It finds the dataset described by config/settings.toml, checks it, and
writes two report files next to it:
    validation_report.txt    for people to read
    validation_report.json   full detail, for programs

It NEVER changes market data. Raw files and candles.csv are only read.
Exit code: 0 = PASSED or PASSED WITH WARNINGS, 1 = FAILED.
"""

import argparse
import sys
import tomllib
from pathlib import Path

from quantlab.data.download import PROJECT_ROOT, resolve_dataset
from quantlab.data.storage import atomic_write_bytes
from quantlab.data.validation import Thresholds, validate_dataset


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a downloaded dataset (read-only).")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config" / "settings.toml")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args.settings, args.data_dir)
    with open(args.settings, "rb") as f:
        thresholds = Thresholds.from_settings(tomllib.load(f))

    if not dataset.out_dir.exists():
        print(f"No dataset found at {dataset.out_dir}")
        print("Run the download first: python -m quantlab.data.download")
        return 1

    report = validate_dataset(dataset.out_dir, dataset.source, thresholds)
    text_path = dataset.out_dir / "validation_report.txt"
    json_path = dataset.out_dir / "validation_report.json"
    atomic_write_bytes(text_path, report.to_text().encode("utf-8"))
    atomic_write_bytes(json_path, report.to_json_bytes())

    print(f"VERDICT: {report.verdict}")
    print()
    for i, check in enumerate(report.checks, 1):
        print(f"  {i}. [{check.status}] {check.title}")
    print()
    print(f"Full report : {text_path}")
    print(f"Details     : {json_path}")
    return 1 if report.verdict == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
