"""quality_flags.json generator tests. Made-up data only; no internet.

Reuses the planted-event scenario from test_phase4b_adjudication: a fake
primary dataset plus a fake second source, adjudicated for real, then turned
into quality flags.
"""

import functools
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_phase4b_adjudication as scen  # noqa: E402
from fake_bitstamp import FakeBitstamp  # noqa: E402
from quantlab.core.policy import PolicyIdentity, canonical_sha256, load_policy  # noqa: E402
from quantlab.data import adjudication as adj  # noqa: E402
from quantlab.data import quality_flags as qf  # noqa: E402
from quantlab.data.storage import sha256_bytes  # noqa: E402

POLICY = ROOT / "config" / "data_quality_policy.toml"
MASKED = load_policy(POLICY).variant_b_masked_categories
IDENTITY = PolicyIdentity("DQP-1", "0123456789abcdef0123456789abcdef01234567", canonical_sha256(POLICY))


def run_adjudication(primary_dir, second_dir, report_path, fake=None):
    report = adj.adjudicate(primary_dir, scen.PRIMARY, scen.SECOND, second_dir, "1h",
                            fetch=fake or FakeBitstamp(scen.second_candle),
                            sleep=lambda s: None, now=lambda: scen.NOW)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(report.to_json_bytes())
    return report


class FlagsCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.primary = scen.build_primary(root / "cb")
        cls.report_path = root / "adj" / "adjudication_report.json"
        run_adjudication(cls.primary, root / "second", cls.report_path)
        cls.fingerprints_before = scen.fingerprints(cls.primary)
        cls.data = qf.build_quality_flags(cls.primary, scen.PRIMARY, cls.report_path, IDENTITY, MASKED)
        cls.doc = json.loads(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def hour(self, i):
        t = scen.t_(i)
        return next(h for h in self.doc["flagged_hours"] if h["time_unix"] == t)


class TestFlagContents(FlagsCase):

    def test_missing_bars_listed_and_never_flagged_as_hours(self):
        missing = {m["time_unix"]: m["adjudication_label"] for m in self.doc["missing_bars"]}
        self.assertEqual(set(missing), {scen.t_(i) for i in (200, 201, 400, 600)})
        self.assertEqual(missing[scen.t_(200)], adj.PRIMARY_SPECIFIC_GAP)
        self.assertEqual(missing[scen.t_(400)], adj.CROSS_VENUE_MISSING)
        flagged_times = {h["time_unix"] for h in self.doc["flagged_hours"]}
        self.assertTrue(flagged_times.isdisjoint(missing))

    def test_categories_follow_adjudication_labels(self):
        expect = {
            (800, "large_move"): "corroborated_extreme",
            (800, "wide_bar"): "corroborated_extreme",
            (1000, "large_move"): "not_corroborated",
            (1200, "large_move"): "partially_corroborated",
            (1600, "wide_bar"): "not_corroborated",
            (1650, "wide_bar"): "corroborated_extreme",
            (1800, "flat_bar"): "primary_specific_flat",
            (1850, "flat_bar"): "cross_venue_quiet",
        }
        for (i, kind), category in expect.items():
            with self.subTest(hour=i, kind=kind):
                flag = next(f for f in self.hour(i)["flags"] if f["kind"] == kind)
                self.assertEqual(flag["category"], category)

    def test_variant_b_masks_any_hour_not_fully_corroborated(self):
        self.assertFalse(self.hour(800)["variant_b_masked"])    # all flags corroborated: kept
        self.assertFalse(self.hour(1650)["variant_b_masked"])
        for i in (1000, 1200, 1600, 1800, 1850):
            self.assertTrue(self.hour(i)["variant_b_masked"], i)
        s = self.doc["summary"]
        self.assertEqual(s["variant_b_masked_hours"],
                         sum(h["variant_b_masked"] for h in self.doc["flagged_hours"]))

    def test_mixed_hour_is_masked_if_any_flag_is_uncorroborated(self):
        report = json.loads(self.report_path.read_bytes())
        t800 = scen.t_(800)
        for e in report["events"]:
            if e["time_unix"] == t800 and e["kind"] == "wide_bar":
                e["label"] = adj.MOVE_PARTIAL   # large_move stays corroborated
        tmp = Path(self._tmp.name) / "mixed.json"
        tmp.write_text(json.dumps(report))
        doc = json.loads(qf.build_quality_flags(self.primary, scen.PRIMARY, tmp, IDENTITY, MASKED))
        hour = next(h for h in doc["flagged_hours"] if h["time_unix"] == t800)
        self.assertEqual({f["category"] for f in hour["flags"]},
                         {"corroborated_extreme", "partially_corroborated"})
        self.assertTrue(hour["variant_b_masked"])

    def test_records_policy_and_input_fingerprints(self):
        self.assertEqual(self.doc["policy"], IDENTITY.as_dict())
        inputs = self.doc["inputs"]
        self.assertEqual(inputs["candles_csv_sha256"], self.fingerprints_before["candles.csv"])
        self.assertEqual(inputs["manifest_sha256"], self.fingerprints_before["manifest.json"])
        self.assertEqual(inputs["adjudication_report_sha256"], sha256_bytes(self.report_path.read_bytes()))
        self.assertIn("raw OHLCV data is unchanged", self.doc["note"])

    def test_output_is_deterministic(self):
        again = qf.build_quality_flags(self.primary, scen.PRIMARY, self.report_path, IDENTITY, MASKED)
        self.assertEqual(again, self.data)

    def test_primary_dataset_never_modified(self):
        qf.build_quality_flags(self.primary, scen.PRIMARY, self.report_path, IDENTITY, MASKED)
        self.assertEqual(scen.fingerprints(self.primary), self.fingerprints_before)


class TestRefusals(FlagsCase):

    def edited_report(self, edit):
        report = json.loads(self.report_path.read_bytes())
        edit(report)
        path = Path(self._tmp.name) / f"edited_{id(edit)}.json"
        path.write_text(json.dumps(report))
        return path

    def test_stale_report_for_different_candles(self):
        path = self.edited_report(lambda r: r["primary_fingerprints_verified_unchanged"]
                                  .__setitem__("candles.csv", "0" * 64))
        with self.assertRaises(qf.QualityFlagsError) as ctx:
            qf.build_quality_flags(self.primary, scen.PRIMARY, path, IDENTITY, MASKED)
        self.assertIn("different candles.csv", str(ctx.exception))

    def test_report_missing_an_event(self):
        path = self.edited_report(lambda r: r["events"].pop(0))
        with self.assertRaises(qf.QualityFlagsError) as ctx:
            qf.build_quality_flags(self.primary, scen.PRIMARY, path, IDENTITY, MASKED)
        self.assertIn("must match exactly", str(ctx.exception))

    def test_unknown_label(self):
        def edit(r):
            next(e for e in r["events"] if e["kind"] != "missing_bar")["label"] = "SOMETHING NEW"
        with self.assertRaises(qf.QualityFlagsError):
            qf.build_quality_flags(self.primary, scen.PRIMARY, self.edited_report(edit), IDENTITY, MASKED)

    def test_failed_dataset(self):
        tmp = Path(self._tmp.name) / "broken_copy"
        shutil.copytree(self.primary, tmp)
        raw = tmp / "raw" / "chunk_0001.json"
        raw.write_bytes(raw.read_bytes().replace(b"2.0", b"3.0", 1))
        with self.assertRaises(qf.QualityFlagsError):
            qf.build_quality_flags(tmp, scen.PRIMARY, self.report_path, IDENTITY, MASKED)


class TestLoadingForTheEngine(FlagsCase):

    def write_flags(self):
        path = Path(self._tmp.name) / "quality_flags.json"
        path.write_bytes(self.data)
        return path

    def test_load_and_experiment_identity(self):
        doc, sha = qf.load_quality_flags(self.write_flags(), self.primary / "candles.csv", IDENTITY)
        self.assertEqual(sha, sha256_bytes(self.data))
        record = qf.experiment_data_identity(IDENTITY, sha, "f" * 40)
        self.assertEqual(set(record), {"policy_version", "policy_commit", "policy_sha256",
                                       "quality_flags_sha256", "code_commit"})

    def test_refuses_flags_for_other_candles_or_policy(self):
        path = self.write_flags()
        other = Path(self._tmp.name) / "other.csv"
        other.write_bytes((self.primary / "candles.csv").read_bytes() + b"\n")
        with self.assertRaises(qf.QualityFlagsError):
            qf.load_quality_flags(path, other, IDENTITY)
        with self.assertRaises(qf.QualityFlagsError):
            qf.load_quality_flags(path, self.primary / "candles.csv",
                                  PolicyIdentity("DQP-1", IDENTITY.commit, "0" * 64))


class TestQualityFlagsCommand(unittest.TestCase):

    def test_command_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_dir = tmp / "data"
            name = "coinbase-exchange_BTC-USD_1h_2019-01-01_2026-09-30"
            primary = data_dir / "coinbase-exchange" / "BTC-USD" / "1h_2019-01-01_2026-09-30"
            end = datetime(2026, 9, 30, 23, tzinfo=scen.UTC)
            n = (int(end.timestamp()) - scen.T0) // scen.H + 1
            scen.build_primary(primary, n=n, missing={30_000},
                               candle=lambda t: [t, scen.base(t) - 1, scen.base(t) + 2,
                                                 scen.base(t), scen.base(t) + 1, 1.5])
            run_adjudication(primary, data_dir, data_dir / "adjudication" / name / "adjudication_report.json",
                             fake=FakeBitstamp(scen.smooth))
            before = scen.fingerprints(primary)
            registry = tmp / "registry.toml"
            args = ["--settings", str(ROOT / "config" / "settings.toml"), "--policy", str(POLICY),
                    "--registry", str(registry), "--data-dir", str(data_dir)]

            with mock.patch("sys.stdout", io.StringIO()) as out:   # not registered yet: refuse
                self.assertEqual(qf.main(args), 1)
            self.assertIn("not registered", out.getvalue())

            registry.write_text(f'[DQP-1]\ncommit = "{IDENTITY.commit}"\nsha256 = "{IDENTITY.sha256}"\n')
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertEqual(qf.main(args), 0, out.getvalue())
            flags = data_dir / "quality" / name / "quality_flags.json"
            doc = json.loads(flags.read_text())
            self.assertEqual(doc["summary"]["missing_bars"], 1)
            self.assertEqual(doc["policy"], IDENTITY.as_dict())
            self.assertIn(sha256_bytes(flags.read_bytes()), out.getvalue())
            self.assertEqual(scen.fingerprints(primary), before)


if __name__ == "__main__":
    unittest.main()
