"""Read-only validation of a downloaded bar dataset (Phase 4).

GOLDEN RULE: this module only READS market data and REPORTS what it finds.
It never fills, removes, smooths, interpolates, re-orders or otherwise
changes any data. The only files it writes are its own two report files.

Severity levels:
  ERROR    the data is broken or cannot be trusted -> verdict FAILED
  WARNING  real but noteworthy, needs a human decision
  INFO     plain facts

Checks:
  1 integrity         fingerprints, manifest, CSV rebuilt from raw files
  2 structure         columns, field counts, numbers readable and FINITE
  3 timestamps        UTC text matches Unix time, on bar boundaries,
                      strictly increasing, inside the dataset period
  4 duplicates        the same bar time appearing more than once
  5 missing_bars      every gap listed (never filled)
  6 price_logic       high >= open/close >= low, all prices > 0
  7 volume            never negative; zero-volume bars counted
  8 overlap           boundary candles agree between neighbouring requests
  9 suspicious_moves  large moves, wide bars, flat bars, open-vs-prev-close jumps
                      (flagged for review, never removed)
"""

import csv
import io
import json
import platform
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import quantlab
from quantlab.core.bars import utc_from_unix
from quantlab.data.chunking import plan_chunks
from quantlab.data.downloader import DataIntegrityError, combine_raw_chunks
from quantlab.data.storage import CSV_COLUMNS, candle_rows_to_csv_bytes, sha256_bytes, sha256_file

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"
PRICE_FIELDS = ("open", "high", "low", "close")
NUMERIC_FIELDS = PRICE_FIELDS + ("volume",)


@dataclass(frozen=True)
class Thresholds:
    """Limits for FLAGGING suspicious bars. They never cause data to be removed."""
    max_abs_close_to_close_return: float = 0.10   # |close / previous close - 1|
    max_bar_range: float = 0.15                   # (high - low) / low
    max_open_vs_prev_close_gap: float = 0.02      # |open / previous close - 1|

    @classmethod
    def from_settings(cls, settings: dict) -> "Thresholds":
        section = settings.get("validation", {})
        return cls(**{k: float(v) for k, v in section.items() if k in cls.__dataclass_fields__})


@dataclass
class Finding:
    severity: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    key: str
    title: str
    findings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, severity, message, **details):
        self.findings.append(Finding(severity, message, details))

    def count(self, severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def status(self) -> str:
        if self.count(ERROR):
            return "FAIL"
        if self.count(WARNING):
            return "WARN"
        return "PASS"


@dataclass
class ValidationReport:
    dataset_dir: Path
    params: dict
    thresholds: Thresholds
    checks: list
    generated_utc: str
    software: dict

    @property
    def verdict(self) -> str:
        if any(c.count(ERROR) for c in self.checks):
            return "FAILED"
        if any(c.count(WARNING) for c in self.checks):
            return "PASSED WITH WARNINGS"
        return "PASSED"

    def check(self, key) -> CheckResult:
        return next(c for c in self.checks if c.key == key)

    def to_dict(self) -> dict:
        return {
            "report": "Phase 4 dataset validation (read-only: no data was changed)",
            "verdict": self.verdict,
            "dataset_dir": str(self.dataset_dir),
            "generated_utc": self.generated_utc,
            "software": self.software,
            "dataset_params": self.params,
            "thresholds": self.thresholds.__dict__,
            "checks": [{
                "key": c.key, "title": c.title, "status": c.status,
                "errors": c.count(ERROR), "warnings": c.count(WARNING), "infos": c.count(INFO),
                "stats": c.stats,
                "findings": [{"severity": f.severity, "message": f.message, "details": f.details}
                             for f in c.findings],
            } for c in self.checks],
        }

    def to_json_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), indent=2, default=str) + "\n").encode("utf-8")

    def to_text(self, max_findings_per_check: int = 25) -> str:
        out = io.StringIO()
        w = out.write
        w("=" * 72 + "\n")
        w("DATASET VALIDATION REPORT (Phase 4)\n")
        w("This report only READS the data. Nothing was filled, removed or changed.\n")
        w("=" * 72 + "\n\n")
        w(f"VERDICT: {self.verdict}\n\n")
        w(f"Dataset   : {self.dataset_dir}\n")
        for key in ("source", "product_id", "granularity_seconds", "start_utc", "end_inclusive_utc"):
            if key in self.params:
                w(f"{key:<10}: {self.params[key]}\n")
        w(f"Generated : {self.generated_utc}\n\n")
        w("SUMMARY\n")
        for i, c in enumerate(self.checks, 1):
            w(f"  {i}. [{c.status}] {c.title}  "
              f"(errors {c.count(ERROR)}, warnings {c.count(WARNING)})\n")
        for i, c in enumerate(self.checks, 1):
            w("\n" + "-" * 72 + "\n")
            w(f"{i}. {c.title}  [{c.status}]\n")
            for k, v in c.stats.items():
                w(f"   {k}: {v}\n")
            shown = [f for f in c.findings if f.severity in (ERROR, WARNING)]
            infos = [f for f in c.findings if f.severity == INFO]
            for f in (shown + infos)[:max_findings_per_check]:
                w(f"   {f.severity:<7} {f.message}\n")
            hidden = len(shown) + len(infos) - max_findings_per_check
            if hidden > 0:
                w(f"   ... and {hidden} more (all are listed in validation_report.json)\n")
        w("\n" + "=" * 72 + "\n")
        w("Thresholds used for flagging (they never remove data):\n")
        for k, v in self.thresholds.__dict__.items():
            w(f"   {k} = {v}\n")
        return out.getvalue()


@dataclass(frozen=True)
class _Row:
    line: int
    t: int
    iso: str
    values: dict  # field name -> Decimal (finite)


def validate_dataset(dataset_dir: Path, source, thresholds: Thresholds = Thresholds(),
                     now=lambda: datetime.now(timezone.utc)) -> ValidationReport:
    """Run every check. Reads files only; returns a report (does not save it)."""
    dataset_dir = Path(dataset_dir)
    manifest = _load_manifest(dataset_dir)
    params = (manifest or {}).get("params", {})

    integrity = CheckResult("integrity", "Integrity: fingerprints, manifest, CSV rebuilt from raw")
    outside_rows = _check_integrity(integrity, dataset_dir, manifest, source)

    structure = CheckResult("structure", "Structure: columns, field counts, finite numbers")
    rows = _check_structure(structure, dataset_dir / "candles.csv")

    interval = int(params.get("granularity_seconds") or source.granularity)
    start, end = _period(params)

    timestamps = CheckResult("timestamps", "Timestamps: UTC, on bar boundaries, ordered, in period")
    _check_timestamps(timestamps, rows, interval, start, end)

    duplicates = CheckResult("duplicates", "Duplicates: each bar time appears once")
    unique = _check_duplicates(duplicates, rows)

    missing = CheckResult("missing_bars", "Missing bars (listed, never filled)")
    _check_missing(missing, unique, interval, start, end)

    prices = CheckResult("price_logic", "Price logic: high >= open/close >= low, prices > 0")
    _check_prices(prices, rows)

    volume = CheckResult("volume", "Volume: never negative, zero-volume bars counted")
    _check_volume(volume, rows)

    overlap = CheckResult("overlap", "Overlap: boundary candles agree between neighbouring requests")
    _check_overlap(overlap, outside_rows, unique, end)

    suspicious = CheckResult("suspicious_moves", "Suspicious moves (flagged for review, never removed)")
    _check_suspicious(suspicious, unique, interval, thresholds)

    return ValidationReport(
        dataset_dir=dataset_dir, params=params, thresholds=thresholds,
        checks=[integrity, structure, timestamps, duplicates, missing, prices, volume,
                overlap, suspicious],
        generated_utc=_iso(now()),
        software={"quantlab_version": quantlab.__version__,
                  "python_version": sys.version.split()[0], "platform": platform.platform()},
    )


# ----------------------------------------------------------------------------- checks

def _check_integrity(check, dataset_dir, manifest, source):
    """Returns boundary rows [(chunk_index, CandleRow)] if raw files are usable, else None."""
    csv_path = dataset_dir / "candles.csv"
    if manifest is None:
        check.add(ERROR, "manifest.json is missing or unreadable")
        return None
    if manifest.get("status") != "complete":
        check.add(ERROR, f"manifest status is {manifest.get('status')!r}, not 'complete'")
    params = manifest.get("params", {})
    for key, value in source.describe().items():
        if params.get(key) != value:
            check.add(ERROR, f"manifest setting {key}={params.get(key)!r} does not match "
                             f"the expected {value!r}")
    start, end = _period(params)
    if start is None:
        check.add(ERROR, "manifest does not record the dataset period")
        return None

    chunks = plan_chunks(start, end, source.granularity, source.candles_per_chunk)
    records = manifest.get("chunks", {})
    check.stats["chunks_expected"] = len(chunks)
    check.stats["chunks_in_manifest"] = len(records)
    if len(records) != len(chunks) or any(str(c.index) not in records for c in chunks):
        check.add(ERROR, f"manifest lists {len(records)} chunks, expected {len(chunks)}")
        return None

    bad_raw = 0
    for chunk in chunks:
        record = records[str(chunk.index)]
        raw_path = dataset_dir / record["file"]
        if not raw_path.exists():
            bad_raw += 1
            check.add(ERROR, f"raw file {record['file']} is missing")
        elif sha256_file(raw_path) != record["sha256"]:
            bad_raw += 1
            check.add(ERROR, f"raw file {record['file']} has changed since download "
                             f"(fingerprint mismatch)")
    check.stats["raw_files_verified"] = len(chunks) - bad_raw

    if not csv_path.exists():
        check.add(ERROR, "candles.csv is missing")
        return None
    csv_bytes = csv_path.read_bytes()
    recorded = manifest.get("result", {}).get("csv_sha256")
    if sha256_bytes(csv_bytes) != recorded:
        check.add(ERROR, "candles.csv has changed since download (fingerprint mismatch)")
    else:
        check.stats["csv_fingerprint"] = "matches manifest"
    if bad_raw:
        return None

    try:
        rows, outside = combine_raw_chunks(source, chunks, dataset_dir, manifest)
    except (DataIntegrityError, ValueError) as e:
        check.add(ERROR, f"raw files could not be re-read: {e}")
        return None
    if candle_rows_to_csv_bytes(rows) == csv_bytes:
        check.stats["csv_rebuilt_from_raw"] = "identical, byte for byte"
    else:
        check.add(ERROR, "candles.csv is NOT identical to a fresh rebuild from the raw files")
    return outside


def _check_structure(check, csv_path):
    """Return the rows that are structurally valid. Invalid rows are reported, not fixed."""
    rows = []
    if not csv_path.exists():
        check.add(ERROR, "candles.csv is missing")
        return rows
    bad = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != CSV_COLUMNS:
            check.add(ERROR, f"unexpected columns {header}, expected {CSV_COLUMNS}")
            return rows
        for line_no, fields in enumerate(reader, start=2):
            problem, row = _parse_row(line_no, fields)
            if problem:
                bad += 1
                check.add(ERROR, f"line {line_no}: {problem}", line=line_no, raw=",".join(fields))
            else:
                rows.append(row)
    check.stats["rows_read"] = len(rows) + bad
    check.stats["rows_structurally_valid"] = len(rows)
    return rows


def _parse_row(line_no, fields):
    if len(fields) != len(CSV_COLUMNS):
        return f"expected {len(CSV_COLUMNS)} fields, found {len(fields)}", None
    iso, unix_text, *numbers = fields
    if not unix_text.isdigit():
        return f"open_time_unix {unix_text!r} is not a whole number", None
    values = {}
    for name, text in zip(NUMERIC_FIELDS, numbers):
        if text == "" or text != text.strip():
            return f"{name} {text!r} is empty or has stray spaces", None
        try:
            value = Decimal(text)
        except InvalidOperation:
            return f"{name} {text!r} is not a number", None
        if not value.is_finite():
            return f"{name} {text!r} is not a finite number (NaN or infinity)", None
        values[name] = value
    return None, _Row(line_no, int(unix_text), iso, values)


def _check_timestamps(check, rows, interval, start, end):
    previous = None
    for row in rows:
        expected_iso = utc_from_unix(row.t).strftime("%Y-%m-%dT%H:%M:%SZ")
        if row.iso != expected_iso:
            check.add(ERROR, f"line {row.line}: open_time_utc {row.iso!r} does not match "
                             f"open_time_unix {row.t} ({expected_iso})", line=row.line)
        if row.t % interval:
            check.add(ERROR, f"line {row.line}: {expected_iso} is not on a {interval}-second "
                             f"bar boundary", line=row.line)
        if start is not None and not (start.timestamp() <= row.t <= end.timestamp()):
            check.add(ERROR, f"line {row.line}: {expected_iso} is outside the dataset period",
                      line=row.line)
        if previous is not None and row.t < previous:
            check.add(ERROR, f"line {row.line}: {expected_iso} is earlier than the row before it "
                             f"(out of order)", line=row.line)
        previous = row.t
    check.stats["rows_checked"] = len(rows)
    if rows:
        check.stats["first_bar_utc"] = _iso_unix(rows[0].t)
        check.stats["last_bar_utc"] = _iso_unix(rows[-1].t)


def _check_duplicates(check, rows):
    """Report repeated bar times. Returns {time: row} using the FIRST occurrence."""
    by_time = defaultdict(list)
    for row in rows:
        by_time[row.t].append(row)
    dup_times = 0
    for t, group in sorted(by_time.items()):
        if len(group) > 1:
            dup_times += 1
            identical = len({tuple(r.values.values()) for r in group}) == 1
            check.add(ERROR, f"{_iso_unix(t)} appears {len(group)} times "
                             f"(lines {[r.line for r in group]}; "
                             f"{'identical' if identical else 'CONFLICTING'} values)")
    check.stats["duplicate_bar_times"] = dup_times
    return {t: group[0] for t, group in by_time.items()}


def _check_missing(check, unique, interval, start, end):
    if start is None:
        check.add(ERROR, "dataset period unknown, cannot check for missing bars")
        return
    first, last = int(start.timestamp()), int(end.timestamp())
    expected = range(first, last + 1, interval)
    missing = [t for t in expected if t not in unique]
    gaps = []
    for t in missing:
        if gaps and t == gaps[-1][1] + interval:
            gaps[-1][1] = t
        else:
            gaps.append([t, t])
    per_year = Counter(utc_from_unix(t).year for t in missing)
    check.stats["bar_slots_in_period"] = len(expected)
    check.stats["bars_present"] = len(expected) - len(missing)
    check.stats["bars_missing"] = len(missing)
    check.stats["gaps"] = len(gaps)
    check.stats["longest_gap_bars"] = max((((b - a) // interval) + 1 for a, b in gaps), default=0)
    check.stats["missing_by_year"] = dict(sorted(per_year.items()))
    for a, b in gaps:
        n = (b - a) // interval + 1
        span = _iso_unix(a) if n == 1 else f"{_iso_unix(a)} to {_iso_unix(b)}"
        check.add(WARNING, f"missing {n} bar{'s' if n > 1 else ''}: {span}",
                  first_missing_utc=_iso_unix(a), last_missing_utc=_iso_unix(b), bars=n)


def _check_prices(check, rows):
    bad = 0
    for row in rows:
        o, h, l, c = (row.values[k] for k in PRICE_FIELDS)
        problems = []
        if min(o, h, l, c) <= 0:
            problems.append("a price is zero or negative")
        if h < max(o, c):
            problems.append("high is below open or close")
        if l > min(o, c):
            problems.append("low is above open or close")
        if h < l:
            problems.append("high is below low")
        if problems:
            bad += 1
            check.add(ERROR, f"{_iso_unix(row.t)} (line {row.line}): {'; '.join(problems)} "
                             f"[O={o} H={h} L={l} C={c}]", line=row.line)
    check.stats["rows_checked"] = len(rows)
    check.stats["impossible_candles"] = bad


def _check_volume(check, rows):
    zero = 0
    for row in rows:
        v = row.values["volume"]
        if v < 0:
            check.add(ERROR, f"{_iso_unix(row.t)} (line {row.line}): negative volume {v}",
                      line=row.line)
        elif v == 0:
            zero += 1
            check.add(WARNING, f"{_iso_unix(row.t)} (line {row.line}): zero volume", line=row.line)
    check.stats["zero_volume_bars"] = zero


def _check_overlap(check, outside_rows, unique, end):
    if outside_rows is None:
        check.add(WARNING, "not checked: raw files were not usable (see Integrity)")
        return
    identical = mismatched = absent = beyond_end = 0
    for chunk_index, raw in outside_rows:
        t = raw.open_time_unix
        if end is not None and t > end.timestamp():
            beyond_end += 1
            check.add(INFO, f"chunk {chunk_index}: boundary candle {_iso_unix(t)} is after the "
                            f"dataset period (expected for the last chunk; not used)")
            continue
        kept = unique.get(t)
        if kept is None:
            absent += 1
            check.add(WARNING, f"chunk {chunk_index} returned a candle for {_iso_unix(t)}, but the "
                               f"neighbouring request did not (the bar is missing from the CSV)")
            continue
        raw_values = {k: Decimal(getattr(raw, k)) for k in NUMERIC_FIELDS}
        if raw_values == kept.values:
            identical += 1
        else:
            mismatched += 1
            check.add(WARNING, f"{_iso_unix(t)}: values differ between two requests "
                               f"(chunk {chunk_index}: {_fmt(raw_values)}; CSV: {_fmt(kept.values)})")
    check.stats["boundary_candles"] = len(outside_rows)
    check.stats["identical"] = identical
    check.stats["mismatched"] = mismatched
    check.stats["absent_from_neighbour"] = absent
    check.stats["beyond_period_end"] = beyond_end


def _check_suspicious(check, unique, interval, th):
    counts = Counter()
    times = sorted(unique)
    for i, t in enumerate(times):
        v = unique[t].values
        o, h, l, c = (v[k] for k in PRICE_FIELDS)
        when = _iso_unix(t)
        if o == h == l == c:
            counts["flat_bars"] += 1
            check.add(WARNING, f"{when}: flat bar (open = high = low = close = {c}, volume {v['volume']})")
        if l > 0 and float((h - l) / l) > th.max_bar_range:
            counts["wide_bars"] += 1
            check.add(WARNING, f"{when}: bar range {float((h - l) / l):.1%} exceeds "
                               f"{th.max_bar_range:.0%} [H={h} L={l}]")
        if i == 0 or times[i - 1] != t - interval:
            continue  # only compare with the previous bar if it is directly adjacent
        prev_close = unique[times[i - 1]].values["close"]
        if prev_close <= 0:
            continue
        ret = float(c / prev_close - 1)
        if abs(ret) > th.max_abs_close_to_close_return:
            counts["large_moves"] += 1
            check.add(WARNING, f"{when}: close moved {ret:+.1%} in one bar "
                               f"({prev_close} -> {c})")
        gap = float(o / prev_close - 1)
        if abs(gap) > th.max_open_vs_prev_close_gap:
            counts["open_gaps"] += 1
            check.add(WARNING, f"{when}: open {o} is {gap:+.1%} away from the previous close "
                               f"{prev_close}")
    for key in ("large_moves", "wide_bars", "open_gaps", "flat_bars"):
        check.stats[key] = counts[key]


# ----------------------------------------------------------------------------- helpers

def _load_manifest(dataset_dir):
    try:
        return json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _period(params):
    try:
        start = datetime.strptime(params["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        end = datetime.strptime(params["end_inclusive_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return start, end
    except (KeyError, TypeError, ValueError):
        return None, None


def _fmt(values):
    return " ".join(f"{k[0].upper()}={values[k]}" for k in NUMERIC_FIELDS)


def _iso_unix(t):
    return utc_from_unix(t).strftime("%Y-%m-%d %H:%M UTC")


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
