"""The permanent dataset record must be self-consistent, must match the DQP-1
registry, and, wherever the real data is present (e.g. on the research
laptop), must match the real files fingerprint for fingerprint."""

import hashlib
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RECORD = ROOT / "records" / "datasets" / "coinbase-exchange_BTC-USD_1h_2019-01-01_2026-09-30.toml"
with open(RECORD, "rb") as f:
    REC = tomllib.load(f)
DATASET = ROOT / REC["dataset"]["folder"]
NAME = REC["dataset"]["id"]
REAL_FILES = {
    "candles_csv": DATASET / "candles.csv",
    "manifest_json": DATASET / "manifest.json",
    "adjudication_report_json": ROOT / "data" / "adjudication" / NAME / "adjudication_report.json",
    "quality_flags_json": ROOT / "data" / "quality" / NAME / "quality_flags.json",
}


def gap_hours(text):
    """'YYYY-MM-DD HH:MM' -> 1, or 'A to B' -> number of hourly bars from A to B inclusive."""
    from datetime import datetime
    parts = [datetime.strptime(x.strip(), "%Y-%m-%d %H:%M") for x in text.split(" to ")]
    return 1 if len(parts) == 1 else int((parts[1] - parts[0]).total_seconds()) // 3600 + 1


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TestDatasetRecord(unittest.TestCase):

    def test_fingerprints_are_well_formed(self):
        for name, value in REC["fingerprints"].items():
            with self.subTest(name):
                self.assertEqual(len(value), 64)
                self.assertTrue(all(c in "0123456789abcdef" for c in value))

    def test_counts_are_consistent(self):
        c = REC["counts"]
        self.assertEqual(c["bars_present"] + c["bars_missing"], c["bar_slots_in_period"])
        self.assertEqual(c["bar_slots_in_period"], 2830 * 24)
        self.assertEqual(len(c["missing_gaps_utc"]), c["missing_gaps"])
        self.assertEqual(sum(gap_hours(g) for g in c["missing_gaps_utc"]), c["bars_missing"])
        a = REC["adjudication"]
        self.assertEqual(a["missing_bars_primary_specific_gap"], c["bars_missing"])
        labelled = (a["warnings_market_move_independently_observed"]
                    + a["warnings_wide_range_independently_observed"]
                    + a["warnings_partially_observed_unresolved"]
                    + a["warnings_primary_specific_flat_bar"])
        self.assertEqual(labelled, REC["validation"]["suspicious_move_warnings"])
        q = REC["quality_flags"]
        self.assertEqual(q["flags_corroborated_extreme"] + q["flags_partially_corroborated"]
                         + q["flags_primary_specific_flat"], labelled)
        self.assertLessEqual(q["variant_b_masked_hours"], q["flagged_hours"])

    def test_policy_matches_registry(self):
        with open(ROOT / "config" / "policy_registry.toml", "rb") as f:
            entry = tomllib.load(f)[REC["policy"]["version"]]
        self.assertEqual(REC["policy"]["commit"], entry["commit"])
        self.assertEqual(REC["policy"]["sha256"], entry["sha256"])

    def test_validation_thresholds_match_settings(self):
        with open(ROOT / "config" / "settings.toml", "rb") as f:
            settings = tomllib.load(f)
        for key, value in settings["validation"].items():
            self.assertEqual(REC["validation"][key], value, key)
        self.assertEqual(settings["pipeline"]["start_date"] + "T00:00:00Z", REC["dataset"]["start_utc"])

    def test_real_files_match_record(self):
        """Runs wherever the real data exists (the research laptop)."""
        present = {k: p for k, p in REAL_FILES.items() if p.exists()}
        if not present:
            self.skipTest("real data not present on this computer")
        self.assertEqual(set(present), set(REAL_FILES), "only part of the real data is present")
        for name, path in present.items():
            with self.subTest(name):
                self.assertEqual(sha256(path), REC["fingerprints"][name],
                                 f"{path} does not match the permanent dataset record")


if __name__ == "__main__":
    unittest.main()
