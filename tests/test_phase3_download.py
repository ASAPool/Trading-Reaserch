"""Phase 3 checks: chunking, Coinbase parsing, retries, resume and integrity.

All tests use FAKE data from tests/fake_coinbase.py. No internet is used.
"""

import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_coinbase import FakeCoinbase, fake_candle  # noqa: E402
from quantlab.core.bars import Bar, timeframe_to_seconds  # noqa: E402
from quantlab.data.chunking import expected_bar_count, plan_chunks  # noqa: E402
from quantlab.data.downloader import (  # noqa: E402
    DataIntegrityError, DownloadError, RetryPolicy, TransientNetworkError, download_bars,
)
from quantlab.data.storage import read_bars_csv  # noqa: E402
from quantlab.markets.crypto.coinbase import CoinbaseCandleSource  # noqa: E402

logging.getLogger("quantlab").addHandler(logging.NullHandler())  # keep test output quiet

UTC = timezone.utc
H = 3600
START = datetime(2019, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 14, tzinfo=UTC)


def hours(start, n):
    t0 = int(start.timestamp())
    return [t0 + i * H for i in range(n)]


class Recorder:
    """Stands in for time.sleep so tests run instantly and we can see the waits."""
    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


def run(fake, start, n_hours, out_dir, policy=RetryPolicy(), sleep=None):
    source = CoinbaseCandleSource("BTC", H)
    end = start + timedelta(hours=n_hours - 1)
    return download_bars(source, start, end, out_dir, fetch=fake,
                         sleep=sleep or Recorder(), now=lambda: NOW, policy=policy)


class TestChunking(unittest.TestCase):

    def test_real_dataset_plan(self):
        """The approved 2019-01-01 .. 2026-09-30 period, in 299-bar chunks."""
        end = datetime(2026, 9, 30, 23, tzinfo=UTC)
        chunks = plan_chunks(START, end, H, 299)
        total = expected_bar_count(START, end, H)
        self.assertEqual(total, 67_920)  # 2,830 days x 24 hours
        self.assertEqual(len(chunks), 228)
        self.assertEqual(chunks[0].start, START)
        self.assertEqual(chunks[-1].end_inclusive, end)
        covered = 0
        for prev, cur in zip(chunks, chunks[1:]):
            self.assertEqual(cur.start, prev.end_inclusive + timedelta(hours=1))  # contiguous, no overlap
        for c in chunks:
            n = expected_bar_count(c.start, c.end_inclusive, H)
            self.assertLessEqual(n, 299)
            covered += n
        self.assertEqual(covered, total)

    def test_rejects_misaligned_and_naive_times(self):
        with self.assertRaises(ValueError):
            plan_chunks(START + timedelta(minutes=30), START + timedelta(hours=5), H, 299)
        with self.assertRaises(ValueError):
            plan_chunks(datetime(2019, 1, 1), datetime(2019, 1, 2), H, 299)


class TestCoinbaseSource(unittest.TestCase):

    def test_request_never_exceeds_300_candles(self):
        source = CoinbaseCandleSource("BTC", H)
        self.assertEqual(source.candles_per_chunk, 299)
        chunk = plan_chunks(START, START + timedelta(hours=1000), H, source.candles_per_chunk)[0]
        url = source.build_url(chunk)
        self.assertIn("granularity=3600", url)
        self.assertIn("products/BTC-USD/candles", url)
        self.assertIn("start=2019-01-01T00%3A00%3A00Z", url)
        self.assertIn("end=2019-01-13T11%3A00%3A00Z", url)   # last bar + 1 h = 299 intervals

    def test_parse_maps_coinbase_column_order(self):
        """Coinbase order is [time, low, high, open, close, volume]."""
        body = b'[[1546300800, 3700.1, 3800.25, 3750.00, 3790.5, 12.34567891]]'
        (row,) = CoinbaseCandleSource("BTC", H).parse(body)
        self.assertEqual(row.open_time_unix, 1546300800)
        self.assertEqual((row.open, row.high, row.low, row.close, row.volume),
                         ("3750.00", "3800.25", "3700.1", "3790.5", "12.34567891"))  # exact text kept

    def test_parse_rejects_malformed(self):
        source = CoinbaseCandleSource("BTC", H)
        for bad in [b'{"message": "error"}', b'[[1, 2, 3]]', b'[["x", 1, 2, 3, 4, 5]]',
                    b'[[1546300800, 1, 2, "3", 4, 5]]', b'not json', b'[[1546300800']:
            with self.subTest(body=bad), self.assertRaises(ValueError):
                source.parse(bad)

    def test_rejects_unsupported_settings(self):
        with self.assertRaises(ValueError):
            CoinbaseCandleSource("BTC", timeframe_to_seconds("4h"))  # Coinbase has no 4h candles
        with self.assertRaises(ValueError):
            CoinbaseCandleSource("DOGE", H)


class TestDownloader(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "dl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_complete_download_either_end_semantics(self):
        for inclusive in (True, False):
            with self.subTest(end_inclusive=inclusive):
                out = self.out / str(inclusive)
                fake = FakeCoinbase(hours(START, 1000), end_inclusive=inclusive)
                result = run(fake, START, 1000, out)
                self.assertEqual(result.expected_bars, 1000)
                self.assertEqual(result.received_bars, 1000)
                self.assertEqual(result.missing_bars, 0)
                self.assertEqual(result.duplicate_bar_times, 0)
                self.assertEqual(len(fake.calls), 4)  # ceil(1000 / 299)
                bars = read_bars_csv(result.csv_path, H)
                self.assertEqual([int(b.open_time.timestamp()) for b in bars], hours(START, 1000))
                self.assertTrue(all(isinstance(b, Bar) for b in bars))

    def test_raw_responses_saved_byte_for_byte(self):
        fake = FakeCoinbase(hours(START, 600))
        served = []
        def recording_fetch(url, timeout):
            status, headers, body = fake(url, timeout)
            served.append(body)
            return status, headers, body
        run(recording_fetch, START, 600, self.out)
        manifest = json.loads((self.out / "manifest.json").read_text())
        for i, body in enumerate(served):
            self.assertEqual((self.out / "raw" / f"chunk_{i:04d}.json").read_bytes(), body)
            self.assertEqual(manifest["chunks"][str(i)]["bytes"], len(body))

    def test_missing_candles_are_reported_not_invented(self):
        available = set(hours(START, 500)) - set(hours(START + timedelta(hours=100), 7))
        result = run(FakeCoinbase(available), START, 500, self.out)
        self.assertEqual(result.received_bars, 493)
        self.assertEqual(result.missing_bars, 7)
        csv_times = [int(b.open_time.timestamp()) for b in read_bars_csv(result.csv_path, H)]
        self.assertEqual(csv_times, sorted(available))

    def test_empty_chunk_is_allowed_and_counted(self):
        available = set(hours(START, 900)) - set(hours(START + timedelta(hours=299), 299))
        result = run(FakeCoinbase(available), START, 900, self.out)
        self.assertEqual(result.missing_bars, 299)
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["chunks"]["1"]["rows_in_window"], 0)

    def test_requests_are_paced(self):
        sleep = Recorder()
        run(FakeCoinbase(hours(START, 1000)), START, 1000, self.out, sleep=sleep)
        self.assertEqual(sleep.waits, [0.4, 0.4, 0.4])  # a pause before every request after the first

    def test_temporary_failures_are_retried(self):
        script = [TransientNetworkError("timed out"),
                  (503, {}, b"unavailable"),
                  (200, {}, b"[[garbled"),
                  (429, {"Retry-After": "7"}, b"slow down")]
        sleep = Recorder()
        fake = FakeCoinbase(hours(START, 100), script=script)
        result = run(fake, START, 100, self.out, sleep=sleep)
        self.assertEqual(result.received_bars, 100)
        self.assertEqual(sleep.waits, [2.0, 4.0, 8.0, 16.0])  # backoff doubles (Retry-After 7 < 16)
        record = json.loads((self.out / "manifest.json").read_text())["chunks"]["0"]
        self.assertEqual(record["attempts"], 5)
        self.assertEqual(len(record["failed_attempts"]), 4)

    def test_retry_after_header_is_respected(self):
        sleep = Recorder()
        run(FakeCoinbase(hours(START, 10), script=[(429, {"retry-after": "30"}, b"")]),
            START, 10, self.out, sleep=sleep)
        self.assertEqual(sleep.waits, [30.0])

    def test_fails_clearly_after_max_retries_and_writes_no_csv(self):
        good = FakeCoinbase(hours(START, 700))  # first chunk succeeds, the second always fails
        calls = {"n": 0}
        def flaky(url, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return good(url, timeout)
            return 502, {}, b"bad gateway"
        with self.assertRaises(DownloadError) as ctx:
            run(flaky, START, 700, self.out)
        self.assertIn("Chunk 2 of 3", str(ctx.exception))
        self.assertIn("Gave up after 5 attempts", str(ctx.exception))
        self.assertFalse((self.out / "candles.csv").exists())
        manifest = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(list(manifest["chunks"]), ["0"])  # the good chunk is kept for resuming

    def test_non_retryable_error_stops_immediately(self):
        fake = FakeCoinbase(hours(START, 10), script=[(400, {}, b'{"message":"bad request"}')])
        sleep = Recorder()
        with self.assertRaises(DownloadError):
            run(fake, START, 10, self.out, sleep=sleep)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(sleep.waits, [])

    def test_resume_skips_saved_chunks(self):
        calls = {"n": 0}
        fake = FakeCoinbase(hours(START, 700))
        def fails_on_third_chunk(url, timeout):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise TransientNetworkError("connection lost")
            return fake(url, timeout)
        with self.assertRaises(DownloadError):
            run(fails_on_third_chunk, START, 700, self.out)
        fresh = FakeCoinbase(hours(START, 700))
        result = run(fresh, START, 700, self.out)
        self.assertEqual(len(fresh.calls), 1)       # only the missing chunk was fetched
        self.assertEqual(result.received_bars, 700)
        rerun = FakeCoinbase(hours(START, 700))
        run(rerun, START, 700, self.out)
        self.assertEqual(len(rerun.calls), 0)       # complete download: nothing re-fetched

    def test_edited_raw_file_is_detected(self):
        run(FakeCoinbase(hours(START, 400)), START, 400, self.out)
        raw = self.out / "raw" / "chunk_0000.json"
        raw.write_bytes(raw.read_bytes().replace(b"1.5", b"9.9"))
        with self.assertRaises(DataIntegrityError):
            run(FakeCoinbase(hours(START, 400)), START, 400, self.out)

    def test_deleted_raw_file_is_detected(self):
        run(FakeCoinbase(hours(START, 400)), START, 400, self.out)
        (self.out / "raw" / "chunk_0001.json").unlink()
        with self.assertRaises(DataIntegrityError):
            run(FakeCoinbase(hours(START, 400)), START, 400, self.out)

    def test_different_settings_in_same_folder_refused(self):
        run(FakeCoinbase(hours(START, 50)), START, 50, self.out)
        with self.assertRaises(DownloadError):
            run(FakeCoinbase(hours(START, 60)), START, 60, self.out)

    def test_unfinished_bar_is_refused(self):
        source = CoinbaseCandleSource("BTC", H)
        last = NOW  # this bar opens now and closes 1 hour in the future
        with self.assertRaises(DownloadError):
            download_bars(source, last - timedelta(hours=5), last, self.out,
                          fetch=FakeCoinbase([]), sleep=Recorder(), now=lambda: NOW)

    def test_manifest_has_reproducibility_metadata(self):
        run(FakeCoinbase(hours(START, 50)), START, 50, self.out)
        m = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(m["status"], "complete")
        self.assertEqual(m["params"]["product_id"], "BTC-USD")
        self.assertEqual(m["params"]["granularity_seconds"], 3600)
        self.assertEqual(m["params"]["start_utc"], "2019-01-01T00:00:00Z")
        for key in ("quantlab_version", "git_commit", "python_version", "platform"):
            self.assertIn(key, m["software"])
        self.assertIn("csv_sha256", m["result"])
        self.assertIn("url", m["chunks"]["0"])

    def test_csv_values_match_source_exactly(self):
        result = run(FakeCoinbase(hours(START, 3)), START, 3, self.out)
        lines = result.csv_path.read_text().splitlines()
        self.assertEqual(lines[0], "open_time_utc,open_time_unix,open,high,low,close,volume")
        t, low, high, open_, close, vol = fake_candle(int(START.timestamp()))
        self.assertEqual(lines[1], f"2019-01-01T00:00:00Z,{t},{open_},{high},{low},{close},{vol}")


if __name__ == "__main__":
    unittest.main()


class TestDownloadCommand(unittest.TestCase):

    def test_settings_produce_the_approved_period(self):
        """config/settings.toml -> BTC-USD, 3600 s, 2019-01-01 00:00 .. 2026-09-30 23:00 UTC."""
        from unittest import mock
        import quantlab.data.download as cmd
        captured = {}

        def fake_download(source, start, end_inclusive, out_dir):
            captured.update(source=source, start=start, end=end_inclusive, out_dir=out_dir)
            raise DownloadError("stopped by test")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cmd, "download_bars", fake_download):
            exit_code = cmd.main(["--data-dir", tmp])
            logging.getLogger("quantlab").handlers.clear()  # release download.log so it can be deleted
            logging.getLogger("quantlab").addHandler(logging.NullHandler())
        self.assertEqual(exit_code, 1)  # failures give a non-zero exit code
        self.assertEqual(captured["source"].product_id, "BTC-USD")
        self.assertEqual(captured["source"].granularity, 3600)
        self.assertEqual(captured["start"], datetime(2019, 1, 1, 0, tzinfo=UTC))
        self.assertEqual(captured["end"], datetime(2026, 9, 30, 23, tzinfo=UTC))
        self.assertEqual(captured["out_dir"].name, "1h_2019-01-01_2026-09-30")
