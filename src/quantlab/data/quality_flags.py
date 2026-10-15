"""Build quality_flags.json: data-quality metadata kept SEPARATE from raw OHLCV.

Run from the project folder with:
    .venv\\Scripts\\python.exe -m quantlab.data.quality_flags

Reads (never writes) the primary dataset, re-runs validation read-only, reads
the adjudication report, and writes ONE new file:
    data/quality/<dataset>/quality_flags.json

The output is deterministic: the same inputs always produce byte-identical
output (no timestamps inside), so its SHA-256 fingerprint is reproducible.

Refuses to run if:
- the policy file does not match the registered DQP-1 fingerprint
- the dataset FAILED validation
- the adjudication report was made for a different candles.csv
- the adjudication report does not cover exactly the current validation events
- an adjudication label is not one this policy knows how to categorise
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from quantlab.core.bars import utc_from_unix
from quantlab.core.policy import PolicyError, PolicyIdentity, verify_registered
from quantlab.data import adjudication as adj
from quantlab.data.storage import atomic_write_bytes, sha256_bytes, sha256_file
from quantlab.data.validation import Thresholds, validate_dataset

FILE_FORMAT = "quantlab.quality_flags/1"

LABEL_TO_CATEGORY = {
    adj.MOVE_OBSERVED: "corroborated_extreme",
    adj.RANGE_OBSERVED: "corroborated_extreme",
    adj.MOVE_PARTIAL: "partially_corroborated",
    adj.MOVE_NOT_OBSERVED: "not_corroborated",
    adj.RANGE_NOT_OBSERVED: "not_corroborated",
    adj.UNVERIFIED: "unverified",
    adj.FLAT_PRIMARY_SPECIFIC: "primary_specific_flat",
    adj.FLAT_CROSS_VENUE: "cross_venue_quiet",
}
MISSING_LABELS = {adj.PRIMARY_SPECIFIC_GAP, adj.CROSS_VENUE_MISSING, adj.UNVERIFIED}


class QualityFlagsError(Exception):
    """The inputs are inconsistent; no flags file is written."""


def build_quality_flags(dataset_dir: Path, source, adjudication_report_path: Path,
                        policy_identity: PolicyIdentity, masked_categories,
                        thresholds: Thresholds = Thresholds()) -> bytes:
    """Return the quality_flags.json bytes. Reads inputs only; writes nothing."""
    dataset_dir = Path(dataset_dir)
    candles_sha = sha256_file(dataset_dir / "candles.csv")

    validation = validate_dataset(dataset_dir, source, thresholds)
    if validation.verdict == "FAILED":
        raise QualityFlagsError("the dataset FAILED validation; flags are only built for a "
                                "dataset that passes (with or without warnings)")

    report_bytes = Path(adjudication_report_path).read_bytes()
    report = json.loads(report_bytes)
    fp = report.get("primary_fingerprints_verified_unchanged", {})
    if fp.get("candles.csv") != candles_sha:
        raise QualityFlagsError("the adjudication report was made for a different candles.csv "
                                "(fingerprint mismatch); re-run the adjudication first")

    # The adjudication must cover exactly the events validation finds now.
    missing_events, flag_events = adj._collect_events(validation)
    expected = {("missing_bar", t) for t, _ in missing_events} | \
               {(e["kind"], e["time_unix"]) for e in flag_events}
    adjudicated = {(e["kind"], e["time_unix"]): e for e in report.get("events", [])}
    if set(adjudicated) != expected:
        raise QualityFlagsError(
            f"the adjudication report covers {len(adjudicated)} events but validation finds "
            f"{len(expected)}; they must match exactly (re-run the adjudication)")

    missing, hours = [], {}
    for (kind, t), e in sorted(adjudicated.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        label = e["label"]
        if kind == "missing_bar":
            if label not in MISSING_LABELS:
                raise QualityFlagsError(f"unknown label for a missing bar: {label!r}")
            missing.append({"time_unix": t, "time_utc": _iso(t), "adjudication_label": label})
            continue
        if label not in LABEL_TO_CATEGORY:
            raise QualityFlagsError(f"unknown adjudication label {label!r}; the policy does not "
                                    f"say how to treat it")
        hours.setdefault(t, []).append({
            "category": LABEL_TO_CATEGORY[label], "kind": kind,
            "adjudication_label": label, "tags": e.get("tags", []),
            "validation_message": e.get("validation_message", ""),
        })

    masked = set(masked_categories)
    flagged = []
    for t in sorted(hours):
        flags = sorted(hours[t], key=lambda f: f["kind"])
        flagged.append({
            "time_unix": t, "time_utc": _iso(t), "flags": flags,
            "variant_b_masked": any(f["category"] in masked for f in flags),
        })

    counts = {}
    for h in flagged:
        for f in h["flags"]:
            counts[f["category"]] = counts.get(f["category"], 0) + 1
    params = validation.params
    doc = {
        "format": FILE_FORMAT,
        "note": ("Data-quality metadata only. The raw OHLCV data is unchanged; nothing was "
                 "filled, removed, replaced or merged. Missing bars stay missing."),
        "policy": policy_identity.as_dict(),
        "dataset": {k: params.get(k) for k in ("source", "product_id", "granularity_seconds",
                                                "start_utc", "end_inclusive_utc")},
        "inputs": {
            "candles_csv_sha256": candles_sha,
            "manifest_sha256": sha256_file(dataset_dir / "manifest.json"),
            "adjudication_report_sha256": sha256_bytes(report_bytes),
            "adjudication_alignment_passed": report.get("alignment", {}).get("passed"),
            "validation_thresholds": thresholds.__dict__,
        },
        "summary": {
            "missing_bars": len(missing),
            "flagged_hours": len(flagged),
            "flag_counts_by_category": dict(sorted(counts.items())),
            "variant_b_masked_hours": sum(h["variant_b_masked"] for h in flagged),
            "variant_b_masked_categories": sorted(masked),
        },
        "missing_bars": missing,
        "flagged_hours": flagged,
    }
    return (json.dumps(doc, indent=2, sort_keys=False) + "\n").encode("utf-8")


def load_quality_flags(flags_path: Path, candles_csv_path: Path, policy_identity: PolicyIdentity):
    """Load flags for use by the engine. Refuses if they do not belong to this exact
    candles.csv or this exact policy. Returns (document, flags_sha256)."""
    data = Path(flags_path).read_bytes()
    doc = json.loads(data)
    if doc.get("format") != FILE_FORMAT:
        raise QualityFlagsError(f"unexpected flags format {doc.get('format')!r}")
    if doc["inputs"]["candles_csv_sha256"] != sha256_file(candles_csv_path):
        raise QualityFlagsError("quality_flags.json was built for a different candles.csv")
    if doc["policy"] != policy_identity.as_dict():
        raise QualityFlagsError("quality_flags.json was built under a different policy")
    return doc, sha256_bytes(data)


def experiment_data_identity(policy_identity: PolicyIdentity, flags_sha256: str,
                             code_commit) -> dict:
    """The identifiers every future experiment must store."""
    return policy_identity.as_dict() | {"quality_flags_sha256": flags_sha256,
                                        "code_commit": code_commit}


def main(argv=None) -> int:
    from quantlab.data.download import PROJECT_ROOT, resolve_dataset
    parser = argparse.ArgumentParser(description="Build quality_flags.json (read-only on data).")
    parser.add_argument("--settings", type=Path, default=PROJECT_ROOT / "config" / "settings.toml")
    parser.add_argument("--policy", type=Path, default=PROJECT_ROOT / "config" / "data_quality_policy.toml")
    parser.add_argument("--registry", type=Path, default=PROJECT_ROOT / "config" / "policy_registry.toml")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args.settings, args.data_dir)
    name = f"{dataset.source.name}_{dataset.source.product_id}_{dataset.out_dir.name}"
    adjudication_path = Path(args.data_dir) / "adjudication" / name / "adjudication_report.json"
    out_path = Path(args.data_dir) / "quality" / name / "quality_flags.json"
    with open(args.settings, "rb") as f:
        thresholds = Thresholds.from_settings(tomllib.load(f))
    try:
        identity = verify_registered(args.policy, args.registry)
        with open(args.policy, "rb") as f:
            masked = tomllib.load(f)["variants"]["variant_b_masked_categories"]
        if not adjudication_path.exists():
            raise QualityFlagsError(f"no adjudication report at {adjudication_path}")
        data = build_quality_flags(dataset.out_dir, dataset.source, adjudication_path,
                                   identity, masked, thresholds)
    except (PolicyError, QualityFlagsError) as e:
        print(f"QUALITY FLAGS NOT BUILT: {e}")
        return 1
    atomic_write_bytes(out_path, data)
    doc = json.loads(data)
    s = doc["summary"]
    print(f"Policy           : {identity.version} (commit {identity.commit[:12]}, "
          f"sha256 {identity.sha256[:12]}...)")
    print(f"Missing bars     : {s['missing_bars']}")
    print(f"Flagged hours    : {s['flagged_hours']}")
    for cat, n in s["flag_counts_by_category"].items():
        print(f"   {n:>3} flag(s): {cat}")
    print(f"Variant B masks  : {s['variant_b_masked_hours']} hour(s)")
    print(f"Written          : {out_path}")
    print(f"quality_flags.json sha256: {sha256_bytes(data)}")
    return 0


def _iso(t):
    return utc_from_unix(t).strftime("%Y-%m-%d %H:%M UTC")


if __name__ == "__main__":
    sys.exit(main())
