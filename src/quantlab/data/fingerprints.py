"""Print the real fingerprints of a dataset, ready to paste into its record.

Run from the project folder with:
    .venv\\Scripts\\python.exe -m quantlab.data.fingerprints

Read-only: it only computes SHA-256 of existing files. Use its output instead
of copying fingerprints by hand, so a record cannot contain a typo. It also
compares against the permanent record (if one exists) and reports MATCH or
MISMATCH for each file.
"""

import argparse
import sys
import tomllib
from pathlib import Path

from quantlab.data.download import PROJECT_ROOT, resolve_dataset
from quantlab.data.storage import sha256_file


def dataset_files(data_dir: Path, dataset) -> dict:
    name = f"{dataset.source.name}_{dataset.source.product_id}_{dataset.out_dir.name}"
    return {
        "candles_csv": dataset.out_dir / "candles.csv",
        "manifest_json": dataset.out_dir / "manifest.json",
        "adjudication_report_json": Path(data_dir) / "adjudication" / name / "adjudication_report.json",
        "quality_flags_json": Path(data_dir) / "quality" / name / "quality_flags.json",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Print dataset fingerprints (read-only).")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config" / "settings.toml")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--records-dir", type=Path, default=PROJECT_ROOT / "records" / "datasets")
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args.settings, args.data_dir)
    files = dataset_files(args.data_dir, dataset)
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("Missing files, cannot fingerprint:")
        for m in missing:
            print(f"   {m}")
        return 1
    computed = {k: sha256_file(p) for k, p in files.items()}

    record_path = Path(args.records_dir) / (
        f"{dataset.source.name}_{dataset.source.product_id}_{dataset.out_dir.name}.toml")
    recorded = {}
    if record_path.exists():
        with open(record_path, "rb") as f:
            recorded = tomllib.load(f).get("fingerprints", {})

    print("[fingerprints]")
    for key, value in computed.items():
        print(f'{key} = "{value}"')
    print()
    if not recorded:
        print(f"No permanent record found at {record_path}")
        return 0
    all_match = True
    for key, value in computed.items():
        ok = recorded.get(key) == value
        all_match &= ok
        print(f"   {'MATCH   ' if ok else 'MISMATCH'} {key}")
    print()
    print("All fingerprints match the permanent record." if all_match
          else "AT LEAST ONE FINGERPRINT DOES NOT MATCH THE PERMANENT RECORD.")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
