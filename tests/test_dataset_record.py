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

    def test_documentation_fingerprints_match_record(self):
        """No document may carry a fingerprint that disagrees with the record."""
        import re
        known = set(REC["fingerprints"].values()) | {REC["policy"]["sha256"]}
        handoff = (ROOT / "docs" / "PROJECT_HANDOFF.md").read_text(encoding="utf-8")
        found = set(re.findall(r"\b[0-9a-f]{64}\b", handoff))
        self.assertTrue(found, "handoff lists no fingerprints")
        self.assertEqual(found - known, set(), "handoff contains fingerprints not in the record")
        for name in ("candles.csv", "manifest.json", "adjudication_report.json", "quality_flags.json"):
            row = next(line for line in handoff.splitlines() if line.startswith(f"| {name} |"))
            key = name.replace(".", "_")
            self.assertIn(REC["fingerprints"][key], row, name)
        self.assertIn(REC["policy"]["commit"], handoff)

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

    def test_quality_flags_inputs_match_record(self):
        """Second, independent path: the fingerprints quality_flags.json recorded
        for its own inputs must equal the permanent record."""
        flags = REAL_FILES["quality_flags_json"]
        if not flags.exists():
            self.skipTest("real data not present on this computer")
        import json
        doc = json.loads(flags.read_bytes())
        self.assertEqual(doc["inputs"]["candles_csv_sha256"], REC["fingerprints"]["candles_csv"])
        self.assertEqual(doc["inputs"]["manifest_sha256"], REC["fingerprints"]["manifest_json"])
        self.assertEqual(doc["inputs"]["adjudication_report_sha256"],
                         REC["fingerprints"]["adjudication_report_json"])
        self.assertEqual(doc["policy"]["policy_commit"], REC["policy"]["commit"])
        self.assertEqual(doc["policy"]["policy_sha256"], REC["policy"]["sha256"])


class TestFingerprintsCommand(unittest.TestCase):

    def test_prints_computed_values_and_detects_mismatch(self):
        import io
        import tempfile
        from unittest import mock
        import quantlab.data.fingerprints as cmd
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            files = {
                "candles_csv": data / "coinbase-exchange/BTC-USD/1h_2019-01-01_2026-09-30/candles.csv",
                "manifest_json": data / "coinbase-exchange/BTC-USD/1h_2019-01-01_2026-09-30/manifest.json",
                "adjudication_report_json": data / "adjudication" / NAME / "adjudication_report.json",
                "quality_flags_json": data / "quality" / NAME / "quality_flags.json",
            }
            for key, path in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(key.encode())
            records = Path(tmp) / "records"
            records.mkdir()
            good = "".join(f'{k} = "{sha256(p)}"\n' for k, p in files.items())
            (records / f"{NAME}.toml").write_text("[fingerprints]\n" + good)
            args = ["--settings", str(ROOT / "config" / "settings.toml"), "--data-dir", str(data),
                    "--records-dir", str(records)]
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(cmd.main(args), 0)
            for key, path in files.items():
                self.assertIn(f'{key} = "{sha256(path)}"', out.getvalue())   # paste-ready line
            self.assertIn("All fingerprints match", out.getvalue())
            typo = good.replace(sha256(files["quality_flags_json"])[20], "#", 1)  # one-character typo
            (records / f"{NAME}.toml").write_text("[fingerprints]\n" + typo)
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(cmd.main(args), 1)
            self.assertIn("MISMATCH", out.getvalue())


if __name__ == "__main__":
    unittest.main()
