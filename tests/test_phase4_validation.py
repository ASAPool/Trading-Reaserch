"""Phase 4 checks: every validation rule is proven against deliberately BROKEN
fake datasets. All data here is made up (tests/fake_coinbase.py). No internet.

Pattern: build a clean fake dataset with the real downloader, break ONE thing,
then confirm the validator reports exactly that problem with the right severity.
"""

import hashlib
import io
import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_coinbase import FakeCoinbase, smooth_candle  # noqa: E402
from quantlab.data.downloader import RetryPolicy, download_bars  # noqa: E402
from quantlab.data.storage import sha256_bytes  # noqa: E402
from quantlab.data.validation import ERROR, INFO, WARNING, Thresholds, validate_dataset  # noqa: E402
from quantlab.markets.crypto.coinbase import CoinbaseCandleSource  # noqa: E402

logging.getLogger("quantlab").addHandler(logging.NullHandler())

UTC = timezone.utc
H = 3600
START = datetime(2019, 1, 1, tzinfo=UTC)
T0 = int(START.timestamp())
N = 700  # 3 chunks: 299 + 299 + 102 bars
NOW = datetime(2026, 10, 14, tzinfo=UTC)
SOURCE = CoinbaseCandleSource("BTC", H)


def build(out_dir, available=None, fetch=None, n=N):
    """Download a clean fake dataset into out_dir using the real downloader."""
    if available is None:
        available = range(T0, T0 + (n + 1) * H, H)  # one extra hour past the end, like real data
    fake = FakeCoinbase(available, candle=smooth_candle)
    download_bars(SOURCE, START, START + timedelta(hours=n - 1), out_dir,
                  fetch=fetch(fake) if fetch else fake, sleep=lambda s: None,
                  now=lambda: NOW, policy=RetryPolicy())
    return out_dir


def validate(out_dir, thresholds=Thresholds()):
    return validate_dataset(out_dir, SOURCE, thresholds, now=lambda: NOW)


def messages(report, key, severity=None):
    return [f.message for f in report.check(key).findings
            if severity is None or f.severity == severity]


def csv_lines(out_dir):
    return (out_dir / "candles.csv").read_text(encoding="utf-8").splitlines()


def write_csv(out_dir, lines, update_manifest_fingerprint=False):
    """Overwrite candles.csv. Optionally also 'forge' the manifest fingerprint,
    to prove the validator still catches the edit by rebuilding from raw."""
    data = ("\n".join(lines) + "\n").encode("utf-8")
    (out_dir / "candles.csv").write_bytes(data)
    if update_manifest_fingerprint:
        m = json.loads((out_dir / "manifest.json").read_text())
        m["result"]["csv_sha256"] = sha256_bytes(data)
        (out_dir / "manifest.json").write_text(json.dumps(m))


def line(t, o, h, l, c, v):
    iso = datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{iso},{t},{o},{h},{l},{c},{v}"


def replace_line(out_dir, t, new_line):
    lines = csv_lines(out_dir)
    idx = next(i for i, ln in enumerate(lines) if f",{t}," in ln)
    lines[idx] = new_line
    write_csv(out_dir, lines)


def file_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


class ValidationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "ds"

    def tearDown(self):
        self._tmp.cleanup()


class TestCleanDataset(ValidationTestCase):

    def test_clean_dataset_passes_every_check(self):
        report = validate(build(self.dir))
        self.assertEqual(report.verdict, "PASSED", report.to_text())
        for check in report.checks:
            self.assertEqual(check.status, "PASS", check.key)
        self.assertEqual(report.check("integrity").stats["csv_rebuilt_from_raw"], "identical, byte for byte")
        self.assertEqual(report.check("missing_bars").stats["bars_missing"], 0)
        ov = report.check("overlap").stats
        self.assertEqual((ov["identical"], ov["beyond_period_end"], ov["mismatched"]), (2, 1, 0))

    def test_validation_never_modifies_any_file(self):
        build(self.dir)
        before = file_hashes(self.dir)
        validate(self.dir)
        self.assertEqual(file_hashes(self.dir), before)


class TestIntegrity(ValidationTestCase):

    def test_tampered_raw_file(self):
        build(self.dir)
        raw = self.dir / "raw" / "chunk_0001.json"
        raw.write_bytes(raw.read_bytes().replace(b"1.5", b"2.5", 1))
        report = validate(self.dir)
        self.assertEqual(report.verdict, "FAILED")
        self.assertIn("raw file raw/chunk_0001.json has changed since download (fingerprint mismatch)",
                      messages(report, "integrity", ERROR))
        self.assertEqual(report.check("integrity").stats["raw_files_verified"], 2)  # of 3

    def test_deleted_raw_file(self):
        build(self.dir)
        (self.dir / "raw" / "chunk_0002.json").unlink()
        report = validate(self.dir)
        self.assertTrue(any("chunk_0002.json is missing" in m for m in messages(report, "integrity", ERROR)))

    def test_tampered_csv(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines[5] = lines[5].replace(",1.5", ",1.6")
        write_csv(self.dir, lines)
        report = validate(self.dir)
        self.assertTrue(any("candles.csv has changed" in m for m in messages(report, "integrity", ERROR)))

    def test_csv_edit_caught_even_if_fingerprint_is_forged(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines[5] = lines[5].replace(",1.5", ",1.6")
        write_csv(self.dir, lines, update_manifest_fingerprint=True)
        report = validate(self.dir)
        self.assertTrue(any("NOT identical to a fresh rebuild" in m
                            for m in messages(report, "integrity", ERROR)))

    def test_missing_manifest(self):
        build(self.dir)
        (self.dir / "manifest.json").unlink()
        report = validate(self.dir)
        self.assertEqual(report.verdict, "FAILED")
        self.assertIn("manifest.json is missing or unreadable", messages(report, "integrity", ERROR))

    def test_incomplete_download(self):
        build(self.dir)
        m = json.loads((self.dir / "manifest.json").read_text())
        m["status"] = "in_progress"
        (self.dir / "manifest.json").write_text(json.dumps(m))
        report = validate(self.dir)
        self.assertTrue(any("not 'complete'" in msg for msg in messages(report, "integrity", ERROR)))

    def test_manifest_for_a_different_product(self):
        build(self.dir)
        report = validate_dataset(self.dir, CoinbaseCandleSource("ETH", H), now=lambda: NOW)
        self.assertTrue(any("product_id" in m for m in messages(report, "integrity", ERROR)))


class TestStructure(ValidationTestCase):

    def test_non_finite_and_unreadable_values_are_errors(self):
        cases = {
            "NaN": "is not a finite number", "nan": "is not a finite number",
            "inf": "is not a finite number", "Infinity": "is not a finite number",
            "-Infinity": "is not a finite number", "sNaN": "is not a finite number",
            "abc": "is not a number", "": "is empty", " 1001": "stray spaces",
        }
        for bad, expected in cases.items():
            with self.subTest(value=bad):
                out = Path(self._tmp.name) / f"ds_{abs(hash(bad))}"
                build(out)
                t = T0 + 10 * H
                replace_line(out, t, line(t, 1000, 1003, 999, bad, 1.5))
                report = validate(out)
                self.assertEqual(report.verdict, "FAILED")
                errs = messages(report, "structure", ERROR)
                self.assertEqual(len(errs), 1)
                self.assertIn(expected, errs[0])
                self.assertIn("close", errs[0])

    def test_non_finite_volume_is_an_error(self):
        build(self.dir)
        t = T0 + 3 * H
        replace_line(self.dir, t, line(t, 1003, 1006, 1002, 1004, "NaN"))
        self.assertIn("volume", messages(validate(self.dir), "structure", ERROR)[0])

    def test_wrong_number_of_fields(self):
        build(self.dir)
        t = T0 + 4 * H
        replace_line(self.dir, t, line(t, 1004, 1007, 1003, 1005, 1.5) + ",extra")
        self.assertIn("expected 7 fields, found 8", messages(validate(self.dir), "structure", ERROR)[0])

    def test_wrong_header(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines[0] = "time,open,high,low,close,volume,x"
        write_csv(self.dir, lines)
        self.assertIn("unexpected columns", messages(validate(self.dir), "structure", ERROR)[0])

    def test_bad_rows_are_reported_but_other_rows_still_checked(self):
        build(self.dir)
        t = T0 + 20 * H
        replace_line(self.dir, t, line(t, 1000, 1003, 999, "NaN", 1.5))
        report = validate(self.dir)
        self.assertEqual(report.check("structure").stats["rows_structurally_valid"], N - 1)
        self.assertEqual(report.check("missing_bars").stats["bars_missing"], 1)  # the unreadable row


class TestTimestamps(ValidationTestCase):

    def test_text_time_disagrees_with_unix_time(self):
        build(self.dir)
        t = T0 + 5 * H
        bad = line(t, 1005, 1008, 1004, 1006, 1.5).replace("05:00:00Z", "06:00:00Z", 1)
        replace_line(self.dir, t, bad)
        self.assertIn("does not match open_time_unix", messages(validate(self.dir), "timestamps", ERROR)[0])

    def test_bar_not_on_hour_boundary(self):
        build(self.dir)
        t = T0 + 5 * H
        replace_line(self.dir, t, line(t + 1800, 1005, 1008, 1004, 1006, 1.5))
        self.assertTrue(any("not on a 3600-second bar boundary" in m
                            for m in messages(validate(self.dir), "timestamps", ERROR)))

    def test_out_of_order_rows(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines[10], lines[11] = lines[11], lines[10]
        write_csv(self.dir, lines)
        self.assertTrue(any("out of order" in m for m in messages(validate(self.dir), "timestamps", ERROR)))

    def test_bar_outside_dataset_period(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines.append(line(T0 + N * H, 1000, 1003, 999, 1001, 1.5))  # one hour past the end
        write_csv(self.dir, lines)
        self.assertTrue(any("outside the dataset period" in m
                            for m in messages(validate(self.dir), "timestamps", ERROR)))


class TestDuplicates(ValidationTestCase):

    def test_identical_duplicate(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines.insert(8, lines[7])
        write_csv(self.dir, lines)
        errs = messages(validate(self.dir), "duplicates", ERROR)
        self.assertEqual(len(errs), 1)
        self.assertIn("appears 2 times", errs[0])
        self.assertIn("identical", errs[0])

    def test_conflicting_duplicate(self):
        build(self.dir)
        lines = csv_lines(self.dir)
        lines.insert(8, lines[7].replace(",1.5", ",9.5"))
        write_csv(self.dir, lines)
        self.assertIn("CONFLICTING", messages(validate(self.dir), "duplicates", ERROR)[0])


class TestMissingBars(ValidationTestCase):

    def test_gaps_are_listed_exactly_and_never_filled(self):
        all_times = set(range(T0, T0 + (N + 1) * H, H))
        gone = {T0 + 100 * H, T0 + 101 * H, T0 + 102 * H, T0 + 400 * H}
        build(self.dir, available=all_times - gone)
        csv_before = (self.dir / "candles.csv").read_bytes()
        report = validate(self.dir)
        self.assertEqual(report.verdict, "PASSED WITH WARNINGS")
        stats = report.check("missing_bars").stats
        self.assertEqual((stats["bars_missing"], stats["gaps"], stats["longest_gap_bars"]), (4, 2, 3))
        self.assertEqual(stats["missing_by_year"], {2019: 4})
        warns = messages(report, "missing_bars", WARNING)
        self.assertEqual(warns, [
            "missing 3 bars: 2019-01-05 04:00 UTC to 2019-01-05 06:00 UTC",
            "missing 1 bar: 2019-01-17 16:00 UTC",
        ])
        self.assertEqual((self.dir / "candles.csv").read_bytes(), csv_before)  # nothing filled


class TestPriceLogic(ValidationTestCase):

    def check_bad(self, o, h, l, c, expected):
        out = Path(self._tmp.name) / f"p{abs(hash((o, h, l, c)))}"
        build(out)
        t = T0 + 30 * H
        replace_line(out, t, line(t, o, h, l, c, 1.5))
        errs = messages(validate(out), "price_logic", ERROR)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn(expected, errs[0])

    def test_high_below_low(self):
        self.check_bad(1000, 999, 1001, 1000, "high is below low")

    def test_high_below_close(self):
        self.check_bad(1000, 1001, 999, 1002, "high is below open or close")

    def test_low_above_open(self):
        self.check_bad(1000, 1003, 1001, 1002, "low is above open or close")

    def test_zero_price(self):
        self.check_bad(1000, 1003, 0, 1001, "zero or negative")

    def test_negative_price(self):
        self.check_bad(1000, 1003, -5, 1001, "zero or negative")


class TestVolume(ValidationTestCase):

    def test_negative_volume_is_error(self):
        build(self.dir)
        t = T0 + 40 * H
        replace_line(self.dir, t, line(t, 1000, 1003, 999, 1001, -1))
        self.assertIn("negative volume", messages(validate(self.dir), "volume", ERROR)[0])

    def test_zero_volume_is_warning(self):
        build(self.dir)
        t = T0 + 40 * H
        replace_line(self.dir, t, line(t, 1000, 1003, 999, 1001, 0))
        report = validate(self.dir)
        self.assertIn("zero volume", messages(report, "volume", WARNING)[0])
        self.assertEqual(report.check("volume").stats["zero_volume_bars"], 1)


class TestOverlap(ValidationTestCase):

    @staticmethod
    def tamper_first_response(change):
        """Wrap the fake server so the FIRST response is altered by change(candles)."""
        def wrap(fake):
            calls = {"n": 0}

            def fetch(url, timeout):
                status, headers, body = fake(url, timeout)
                calls["n"] += 1
                if calls["n"] == 1:
                    candles = json.loads(body)
                    change(candles)
                    body = json.dumps(candles).encode()
                return status, headers, body
            return fetch
        return wrap

    def test_boundary_candle_values_disagree(self):
        def change(candles):
            candles[0][5] = 7.25  # newest-first: [0] is the boundary candle (next chunk's first bar)
        build(self.dir, fetch=self.tamper_first_response(change))
        report = validate(self.dir)
        warns = messages(report, "overlap", WARNING)
        self.assertEqual(len(warns), 1)
        self.assertIn("values differ between two requests", warns[0])
        self.assertEqual(report.check("overlap").stats["mismatched"], 1)

    def test_boundary_candle_missing_from_neighbour(self):
        boundary_t = T0 + 299 * H  # first bar of chunk 1
        all_times = set(range(T0, T0 + (N + 1) * H, H))

        def wrap(fake):
            def fetch(url, timeout):
                status, headers, body = fake(url, timeout)
                if "start=2019-01-13T11" in url:  # the request for chunk 1 leaves the bar out
                    body = json.dumps([c for c in json.loads(body) if c[0] != boundary_t]).encode()
                return status, headers, body
            return fetch
        build(self.dir, available=all_times, fetch=wrap)
        report = validate(self.dir)
        self.assertTrue(any("did not" in m for m in messages(report, "overlap", WARNING)))
        self.assertEqual(report.check("missing_bars").stats["bars_missing"], 1)

    def test_extra_candle_after_period_is_info_only(self):
        report = validate(build(self.dir))
        infos = messages(report, "overlap", INFO)
        self.assertEqual(len(infos), 1)
        self.assertIn("after the dataset period", infos[0])
        self.assertEqual(report.check("overlap").status, "PASS")


class TestSuspiciousMoves(ValidationTestCase):

    def flags_for(self, o, h, l, c, thresholds=Thresholds(), hour=53):
        # smooth_candle: the bar before hour 53 closes at exactly 1001
        out = Path(self._tmp.name) / f"s{abs(hash((o, h, l, c, hour, str(thresholds))))}"
        build(out)
        t = T0 + hour * H
        replace_line(out, t, line(t, o, h, l, c, 1.5))
        return validate(out, thresholds)

    def test_large_move(self):
        # previous close is 1001; a close of 1200 is +19.9%. Open kept at 1001 (no open gap).
        report = self.flags_for(1001, 1210, 1000, 1200)
        self.assertTrue(any("close moved +19.9% in one bar" in m
                            for m in messages(report, "suspicious_moves", WARNING)))
        self.assertTrue(any("previous close" in m for m in messages(report, "suspicious_moves", WARNING)))
        # The NEXT bar opens far from this bar's close too: flagged, never altered.

    def test_wide_bar(self):
        report = self.flags_for(1001, 1300, 1000, 1002)
        self.assertTrue(any("bar range 30.0% exceeds 15%" in m
                            for m in messages(report, "suspicious_moves", WARNING)))

    def test_open_gap(self):
        report = self.flags_for(1050, 1052, 1000, 1001)
        self.assertTrue(any("open 1050 is +4.9% away from the previous close 1001" in m
                            for m in messages(report, "suspicious_moves", WARNING)))

    def test_flat_bar(self):
        report = self.flags_for(1001, 1001, 1001, 1001)
        self.assertTrue(any("flat bar" in m for m in messages(report, "suspicious_moves", WARNING)))

    def test_thresholds_come_from_settings(self):
        loose = Thresholds(max_abs_close_to_close_return=0.5, max_bar_range=0.5,
                           max_open_vs_prev_close_gap=0.5)
        report = self.flags_for(1001, 1210, 1000, 1200, thresholds=loose)
        self.assertEqual(report.check("suspicious_moves").status, "PASS")
        self.assertEqual(Thresholds.from_settings({"validation": {"max_bar_range": 0.3}}).max_bar_range, 0.3)

    def test_moves_across_a_gap_are_not_compared(self):
        all_times = set(range(T0, T0 + (N + 1) * H, H))
        build(self.dir, available=all_times - {T0 + 60 * H})
        t = T0 + 61 * H
        replace_line(self.dir, t, line(t, 1500, 1503, 1499, 1501, 1.5))  # far from bar 59's close
        report = validate(self.dir)
        self.assertFalse(any("previous close" in m or "close moved" in m
                             for m in messages(report, "suspicious_moves") if "2019-01-03 13:00" in m))


class TestValidateCommand(ValidationTestCase):

    def run_command(self, data_dir):
        import quantlab.data.validate as cmd
        settings = Path(__file__).resolve().parent.parent / "config" / "settings.toml"
        with mock.patch("sys.stdout", io.StringIO()) as out:
            code = cmd.main(["--settings", str(settings), "--data-dir", str(data_dir)])
        return code, out.getvalue()

    def dataset_dir(self, data_dir):
        return Path(data_dir) / "coinbase-exchange" / "BTC-USD" / "1h_2019-01-01_2026-09-30"

    def test_no_dataset(self):
        code, out = self.run_command(Path(self._tmp.name) / "empty")
        self.assertEqual(code, 1)
        self.assertIn("No dataset found", out)

    def test_command_writes_reports_and_changes_nothing_else(self):
        data_dir = Path(self._tmp.name) / "data"
        end = datetime(2026, 9, 30, 23, tzinfo=UTC)
        hours = (int(end.timestamp()) - T0) // H + 1
        ds = self.dataset_dir(data_dir)
        build(ds, n=hours)
        before = file_hashes(ds)
        code, out = self.run_command(data_dir)
        self.assertEqual(code, 0, out)
        self.assertIn("VERDICT: PASSED", out)
        after = file_hashes(ds)
        self.assertEqual(set(after) - set(before), {"validation_report.txt", "validation_report.json"})
        self.assertEqual({k: after[k] for k in before}, before)  # every data file untouched
        text = (ds / "validation_report.txt").read_text(encoding="utf-8")
        self.assertIn("Nothing was filled, removed or changed", text)
        data = json.loads((ds / "validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"], "PASSED")
        self.assertEqual(len(data["checks"]), 9)

    def test_failed_dataset_gives_exit_code_1(self):
        data_dir = Path(self._tmp.name) / "data"
        end = datetime(2026, 9, 30, 23, tzinfo=UTC)
        ds = self.dataset_dir(data_dir)
        build(ds, n=(int(end.timestamp()) - T0) // H + 1)
        (ds / "raw" / "chunk_0005.json").write_bytes(b"[]")
        code, out = self.run_command(data_dir)
        self.assertEqual(code, 1)
        self.assertIn("VERDICT: FAILED", out)


if __name__ == "__main__":
    unittest.main()
