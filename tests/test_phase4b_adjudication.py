"""Cross-provider adjudication checks. All data is MADE UP; no internet.

A fake primary (Coinbase-style) dataset is built with planted events, and a
fake second source (Bitstamp-style) answers with chosen behaviour, so each
label can be proven to appear exactly when it should.
"""

import functools
import io
import json
import logging
import sys
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_bitstamp import FakeBitstamp  # noqa: E402
from fake_coinbase import FakeCoinbase  # noqa: E402
from quantlab.data import adjudication as adj  # noqa: E402
from quantlab.data.adjudication import AdjudicationError, AdjudicationRules, adjudicate  # noqa: E402
from quantlab.data.downloader import download_bars  # noqa: E402
from quantlab.data.storage import read_bars_csv  # noqa: E402
from quantlab.markets.crypto.bitstamp import BitstampCandleSource  # noqa: E402
from quantlab.markets.crypto.coinbase import CoinbaseCandleSource  # noqa: E402

logging.getLogger("quantlab").addHandler(logging.NullHandler())

UTC = timezone.utc
H = 3600
START = datetime(2019, 1, 1, tzinfo=UTC)
T0 = int(START.timestamp())
NOW = datetime(2026, 10, 14, tzinfo=UTC)
N = 2000
PRIMARY = CoinbaseCandleSource("BTC", H)
SECOND = BitstampCandleSource("BTC", H)
SETTINGS = Path(__file__).resolve().parent.parent / "config" / "settings.toml"


def t_(i):
    return T0 + i * H


def base(t):
    return 1000 + (t // H) % 10


def smooth(t):
    b = base(t)
    return {"open": b, "high": b + 2, "low": b - 1, "close": b + 1, "volume": 2.0}


def prev_close(i):
    return smooth(t_(i - 1))["close"]


# ---- the planted scenario (hour index -> candle) --------------------------------
def spike(i, close=1150, high=1180):
    b = base(t_(i))
    return {"open": b, "high": high, "low": b - 1, "close": close, "volume": 2.0}


def scaled_move(i, ratio):
    """A second-source candle whose close-to-close move is `ratio` x the primary spike's."""
    pc = prev_close(i)
    r_primary = 1150 / pc - 1
    c = round(pc * (1 + ratio * r_primary), 2)
    b = base(t_(i))
    return {"open": b, "high": round(max(b, c) + 1, 2), "low": b - 1, "close": c, "volume": 2.0}


def wick(i, extra):
    b = base(t_(i))
    return {"open": b, "high": b + extra, "low": b - 1, "close": b + 1, "volume": 2.0}


def flat(i, volume=2.0):
    b = base(t_(i))
    return {"open": b, "high": b, "low": b, "close": b, "volume": volume}


PRIMARY_MISSING = {200, 201, 400, 600}
def open_gap_candle(i, close_offset):
    b = base(t_(i))
    return {"open": b + 50, "high": b + 52, "low": b - 1, "close": b + close_offset, "volume": 2.0}


PRIMARY_OVERRIDES = {800: spike(800), 1000: spike(1000), 1200: spike(1200), 1400: spike(1400),
                     1600: wick(1600, 200), 1650: wick(1650, 200), 1700: wick(1700, 200),
                     1800: flat(1800), 1850: flat(1850), 1950: open_gap_candle(1950, +1)}
SECOND_MISSING = {400}
SECOND_OVERRIDES = {600: dict(smooth(t_(600)), volume=0), 800: spike(800),
                    1200: scaled_move(1200, 0.3), 1400: scaled_move(1400, 0.8),
                    1650: wick(1650, 150), 1700: wick(1700, 60), 1850: flat(1850, volume=0),
                    1950: open_gap_candle(1950, -5)}


def primary_candle(t):
    i = (t - T0) // H
    c = PRIMARY_OVERRIDES.get(i, smooth(t))
    return [t, c["low"], c["high"], c["open"], c["close"], c["volume"]]


def second_candle(t, shift=0):
    i = (t - T0) // H - shift
    if i in SECOND_MISSING:
        return None
    return SECOND_OVERRIDES.get(i, smooth(t_(i)))


def build_primary(out_dir, n=N, missing=PRIMARY_MISSING, candle=primary_candle):
    available = set(range(T0, T0 + (n + 1) * H, H)) - {t_(i) for i in missing}
    download_bars(PRIMARY, START, START + timedelta(hours=n - 1), out_dir,
                  fetch=FakeCoinbase(available, candle=candle), sleep=lambda s: None,
                  now=lambda: NOW)
    return out_dir


def fingerprints(folder):
    return adj._fingerprint_folder(Path(folder))


class AdjudicationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.primary_dir = build_primary(self.root / "coinbase")
        self.second_dir = self.root / "second"

    def tearDown(self):
        self._tmp.cleanup()

    def run_adj(self, fake=None, rules=AdjudicationRules()):
        fake = fake or FakeBitstamp(second_candle)
        return adjudicate(self.primary_dir, PRIMARY, SECOND, self.second_dir, "1h", rules,
                          fetch=fake, sleep=lambda s: None, now=lambda: NOW)

    def event(self, report, kind, i):
        matches = [e for e in report.events if e.kind == kind and e.time_unix == t_(i)]
        self.assertEqual(len(matches), 1, f"{kind} at hour {i}: {[(e.kind, e.time_utc) for e in report.events]}")
        return matches[0]


class TestBitstampSource(unittest.TestCase):

    def test_parse_values_kept_exactly(self):
        body = b'{"data": {"pair": "BTC/USD", "ohlc": [{"timestamp": "1546300800", "open": "3746.71", ' \
               b'"high": "3754.0", "low": "3740.02", "close": "3750.1", "volume": "12.5"}]}}'
        (row,) = SECOND.parse(body)
        self.assertEqual((row.open_time_unix, row.open, row.high, row.low, row.close, row.volume),
                         (1546300800, "3746.71", "3754.0", "3740.02", "3750.1", "12.5"))

    def test_parse_rejects_malformed_and_non_finite(self):
        good = {"timestamp": "1546300800", "open": "1", "high": "2", "low": "1", "close": "1", "volume": "1"}
        bad_bodies = [b'{"error": "x"}', b'{"data": {"ohlc": "x"}}', b'not json',
                      json.dumps({"data": {"ohlc": [dict(good, close="NaN")]}}).encode(),
                      json.dumps({"data": {"ohlc": [dict(good, high="Infinity")]}}).encode(),
                      json.dumps({"data": {"ohlc": [dict(good, volume="abc")]}}).encode(),
                      b'{"data": {"ohlc": [{"timestamp": "1", "open": NaN}]}}',
                      json.dumps({"data": {"ohlc": [{k: v for k, v in good.items() if k != "low"}]}}).encode()]
        for body in bad_bodies:
            with self.subTest(body=body[:60]), self.assertRaises(ValueError):
                SECOND.parse(body)

    def test_request_respects_1000_candle_limit(self):
        from quantlab.data.chunking import plan_chunks
        chunk = plan_chunks(START, START + timedelta(hours=5000), H, SECOND.candles_per_chunk)[0]
        url = SECOND.build_url(chunk)
        self.assertIn("/api/v2/ohlc/btcusd/", url)
        self.assertIn("step=3600", url)
        self.assertIn("limit=1000", url)
        self.assertIn(f"start={T0}", url)

    def test_generic_downloader_works_with_second_provider(self):
        """Provider independence: the same downloader, unchanged, fetches Bitstamp data."""
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeBitstamp(smooth)
            result = download_bars(SECOND, START, START + timedelta(hours=2499), Path(tmp),
                                   fetch=fake, sleep=lambda s: None, now=lambda: NOW)
            self.assertEqual((result.received_bars, result.missing_bars, len(fake.calls)), (2500, 0, 3))
            bars = read_bars_csv(result.csv_path, H)
            self.assertEqual(int(bars[-1].open_time.timestamp()), t_(2499))


class TestMissingBars(AdjudicationCase):

    def test_missing_bar_labels(self):
        report = self.run_adj()
        self.assertTrue(report.alignment["passed"], report.alignment)
        for i in (200, 201):
            self.assertEqual(self.event(report, "missing_bar", i).label, adj.PRIMARY_SPECIFIC_GAP)
        both_missing = self.event(report, "missing_bar", 400)
        self.assertEqual(both_missing.label, adj.CROSS_VENUE_MISSING)   # NOT "broader market gap"
        self.assertIn("also has no candle", both_missing.reason)
        zero_volume = self.event(report, "missing_bar", 600)
        self.assertEqual(zero_volume.label, adj.CROSS_VENUE_MISSING)
        self.assertIn("zero volume", zero_volume.reason)
        self.assertFalse(any("BROADER" in e.label.upper() for e in report.events))


class TestSuspiciousCandles(AdjudicationCase):

    def setUp(self):
        super().setUp()
        self.report = self.run_adj()

    def test_move_observed_on_both_venues(self):
        e = self.event(self.report, "large_move", 800)
        self.assertEqual(e.label, adj.MOVE_OBSERVED)
        self.assertTrue(e.metrics["open_close_agree"])
        self.assertEqual(self.event(self.report, "open_gap", 801).label, adj.MOVE_OBSERVED)
        self.assertEqual(self.event(self.report, "wide_bar", 800).label, adj.RANGE_OBSERVED)

    def test_move_not_observed(self):
        e = self.event(self.report, "large_move", 1000)
        self.assertEqual(e.label, adj.MOVE_NOT_OBSERVED)
        self.assertIn(adj.TAG_LEVELS, e.tags)
        self.assertEqual(self.event(self.report, "open_gap", 1001).label, adj.MOVE_NOT_OBSERVED)

    def test_partial_move_is_unresolved(self):
        e = self.event(self.report, "large_move", 1200)
        self.assertEqual(e.label, adj.MOVE_PARTIAL)
        self.assertAlmostEqual(e.metrics["second/primary ratio"], 0.3, places=2)

    def test_move_observed_but_open_close_disagree_is_partial(self):
        e = self.event(self.report, "large_move", 1400)
        self.assertEqual(e.label, adj.MOVE_PARTIAL)
        self.assertIn("open/close differ", e.reason)
        self.assertGreaterEqual(e.metrics["second/primary ratio"], 0.5)

    def test_wick_only_on_primary_is_labelled_separately_not_invalidated(self):
        e = self.event(self.report, "wide_bar", 1600)
        self.assertEqual(e.label, adj.RANGE_NOT_OBSERVED)
        self.assertIn(adj.TAG_WICK, e.tags)
        self.assertTrue(e.metrics["open_close_agree"])      # open/close still match
        self.assertNotIn(adj.TAG_LEVELS, e.tags)
        self.assertNotIn("INVALID", e.label.upper())

    def test_wick_observed_with_different_extreme_still_observed(self):
        e = self.event(self.report, "wide_bar", 1650)
        self.assertEqual(e.label, adj.RANGE_OBSERVED)
        self.assertIn(adj.TAG_WICK, e.tags)                 # high differs >3%, reported separately
        self.assertIn("high_diff (reported separately)", e.metrics)

    def test_partial_range_with_matching_open_close_is_still_partial(self):
        e = self.event(self.report, "wide_bar", 1700)
        self.assertTrue(e.metrics["open_close_agree"])
        self.assertLess(e.metrics["second/primary ratio"], 0.5)
        self.assertEqual(e.label, adj.MOVE_PARTIAL)

    def test_open_gap_is_measured_from_the_open(self):
        # Both venues gap up ~5% at the open; their closes then move in opposite
        # directions. The OPEN gap was observed on both, so it must be OBSERVED.
        e = self.event(self.report, "open_gap", 1950)
        self.assertEqual(e.label, adj.MOVE_OBSERVED)
        self.assertGreater(e.metrics["second/primary ratio"], 0.9)

    def test_flat_bars(self):
        self.assertEqual(self.event(self.report, "flat_bar", 1800).label, adj.FLAT_PRIMARY_SPECIFIC)
        self.assertEqual(self.event(self.report, "flat_bar", 1850).label, adj.FLAT_CROSS_VENUE)


class TestSafetyAndAlignment(AdjudicationCase):

    def test_misaligned_second_source_makes_everything_unresolved(self):
        shifted = FakeBitstamp(functools.partial(second_candle, shift=1))  # labels one hour late
        report = self.run_adj(shifted)
        self.assertFalse(report.alignment["passed"])
        self.assertIn("time shift matches better", report.alignment["explanation"])
        self.assertTrue(report.events)
        self.assertTrue(all(e.label == adj.UNVERIFIED for e in report.events))

    def test_consistent_price_offset_fails_alignment(self):
        def one_percent_higher(t):
            c = second_candle(t)
            return None if c is None else {k: (v * 1.01 if k != "volume" else v) for k, v in c.items()}
        report = self.run_adj(FakeBitstamp(one_percent_higher))
        self.assertFalse(report.alignment["passed"])
        self.assertIn("exceeds", report.alignment["explanation"])
        self.assertTrue(all(e.label == adj.UNVERIFIED for e in report.events))

    def test_too_few_alignment_hours(self):
        report = self.run_adj(rules=AdjudicationRules(alignment_min_hours=100_000))
        self.assertFalse(report.alignment["passed"])
        self.assertTrue(all(e.label == adj.UNVERIFIED for e in report.events))

    def test_failed_window_only_affects_its_own_events(self):
        failing = FakeBitstamp(second_candle, fail_when=lambda url: f"start={t_(976)}" in url)
        report = self.run_adj(failing)
        self.assertEqual(self.event(report, "large_move", 1000).label, adj.UNVERIFIED)
        self.assertEqual(self.event(report, "large_move", 800).label, adj.MOVE_OBSERVED)
        failed = [w for w in report.windows if w["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("Gave up after 5 attempts", failed[0]["error"])

    def test_primary_dataset_is_never_modified(self):
        before = fingerprints(self.primary_dir)
        report = self.run_adj()
        self.assertEqual(fingerprints(self.primary_dir), before)
        self.assertEqual(report.primary_fingerprints["candles.csv"], before["candles.csv"])

    def test_safety_stop_if_primary_changes_during_the_run(self):
        csv = self.primary_dir / "candles.csv"
        inner = FakeBitstamp(second_candle)

        def tampering_fetch(url, timeout):  # simulates something editing the primary mid-run
            if len(inner.calls) == 0:
                csv.write_bytes(csv.read_bytes() + b"")
                (self.primary_dir / "unexpected.txt").write_text("x")
            return inner(url, timeout)
        with self.assertRaises(AdjudicationError) as ctx:
            self.run_adj(tampering_fetch)
        self.assertIn("primary dataset changed", str(ctx.exception))

    def test_second_source_stored_separately_and_not_merged(self):
        csv_before = (self.primary_dir / "candles.csv").read_bytes()
        report = self.run_adj()
        self.assertEqual((self.primary_dir / "candles.csv").read_bytes(), csv_before)
        primary_times = {int(b.open_time.timestamp()) for b in read_bars_csv(self.primary_dir / "candles.csv", H)}
        self.assertTrue({t_(i) for i in (200, 201, 400, 600)}.isdisjoint(primary_times))  # still missing
        for w in report.windows:
            folder = Path(w["folder"])
            self.assertTrue(str(folder).startswith(str(self.second_dir)))
            self.assertTrue((folder / "manifest.json").exists())
            self.assertTrue(list((folder / "raw").glob("chunk_*.json")))  # raw responses preserved
            self.assertIn("csv_sha256", w)

    def test_windows_are_merged_and_small(self):
        report = self.run_adj()
        spans = [(w["start_utc"], w["end_utc"]) for w in report.windows]
        # 13 windows: events 50 h apart with +/-24 h windows do NOT touch (2 h gap between them).
        self.assertEqual(len(spans), 13)
        self.assertIn(("2019-01-08T08:00:00Z", "2019-01-10T09:00:00Z"), spans)  # hours 176..225: 200+201 merged
        for w in report.windows:
            self.assertLessEqual(w["bars"], 50)

    def test_overlapping_windows_merge(self):
        start, end = START, START + timedelta(hours=N - 1)
        merged = adj._plan_windows([t_(100), t_(130), t_(300)], H, 24, start, end)
        self.assertEqual([(int(a.timestamp()), int(b.timestamp())) for a, b in merged],
                         [(t_(76), t_(154)), (t_(276), t_(324))])
        touching = adj._plan_windows([t_(100), t_(149)], H, 24, start, end)  # 124 and 125 are adjacent
        self.assertEqual(len(touching), 1)
        clamped = adj._plan_windows([t_(3)], H, 24, start, end)               # never before the period
        self.assertEqual(clamped[0][0], start)

    def test_refuses_when_primary_fails_validation(self):
        raw = self.primary_dir / "raw" / "chunk_0001.json"
        raw.write_bytes(raw.read_bytes().replace(b"2.0", b"3.0", 1))
        with self.assertRaises(AdjudicationError):
            self.run_adj()

    def test_report_is_reproducible_and_labelled(self):
        report = self.run_adj()
        text = report.to_text()
        self.assertIn("No data was filled, replaced, merged or changed", text)
        self.assertIn("no backtesting decision has been made", text)
        data = json.loads(report.to_json_bytes())
        self.assertEqual(data["rules_fixed_in_advance"], AdjudicationRules().__dict__)
        self.assertEqual(len(data["events"]), len(report.events))


class TestRulesFixedInSettings(unittest.TestCase):

    def test_settings_hold_the_approved_rules(self):
        with open(SETTINGS, "rb") as f:
            section = tomllib.load(f)["adjudication"]
        self.assertEqual(section["second_provider"], "bitstamp")
        self.assertEqual(AdjudicationRules.from_settings({"adjudication": section}), AdjudicationRules())
        self.assertEqual(AdjudicationRules().__dict__, {
            "window_hours": 24, "alignment_min_hours": 100, "alignment_max_median_close_diff": 0.005,
            "open_close_tolerance": 0.01, "move_observed_min_ratio": 0.5,
            "move_not_observed_max_ratio": 0.2, "wick_disagreement_threshold": 0.03})


class TestAdjudicateCommand(unittest.TestCase):

    def test_command_writes_separate_report(self):
        import quantlab.data.adjudicate as cmd
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            primary = data_dir / "coinbase-exchange" / "BTC-USD" / "1h_2019-01-01_2026-09-30"
            end = datetime(2026, 9, 30, 23, tzinfo=UTC)
            n = (int(end.timestamp()) - T0) // H + 1
            build_primary(primary, n=n, missing={30_000, 40_000, 50_000},
                          candle=lambda t: [t, base(t) - 1, base(t) + 2, base(t), base(t) + 1, 1.5])
            before = fingerprints(primary)
            fake = FakeBitstamp(smooth)
            patched = functools.partial(adjudicate, fetch=fake, sleep=lambda s: None)
            with mock.patch.object(cmd, "adjudicate", patched), mock.patch("sys.stdout", io.StringIO()) as out:
                code = cmd.main(["--settings", str(SETTINGS), "--data-dir", str(data_dir)])
            self.assertEqual(code, 0, out.getvalue())
            report_dir = data_dir / "adjudication" / "coinbase-exchange_BTC-USD_1h_2019-01-01_2026-09-30"
            self.assertTrue((report_dir / "adjudication_report.txt").exists())
            data = json.loads((report_dir / "adjudication_report.json").read_text(encoding="utf-8"))
            self.assertEqual(data["label_counts"], {adj.PRIMARY_SPECIFIC_GAP: 3})
            self.assertEqual(fingerprints(primary), before)               # primary untouched
            self.assertTrue((data_dir / "bitstamp-exchange" / "BTC-USD" / "1h_windows").is_dir())
            self.assertIn("ALIGNMENT CHECK: PASSED", out.getvalue())


if __name__ == "__main__":
    unittest.main()
