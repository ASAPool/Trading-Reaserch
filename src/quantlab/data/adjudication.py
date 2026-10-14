"""Read-only cross-provider adjudication of validation events.

For every missing bar and every suspicious/flat candle reported by the
Phase 4 validator, compare against an independent SECOND provider and label
what the second source shows.

GOLDEN RULES
- The primary (Coinbase) dataset is never modified. Its files are
  fingerprinted before and after; any change aborts with an error.
- Second-provider data is stored in its own folder and is NEVER merged
  into, used to fill, or used to replace primary data.
- Labels describe evidence only. They make no backtesting decisions.
- The comparison rules come from config/settings.toml [adjudication] and
  were fixed before any real results were seen.

Market-neutral: works with any two provider "sources" that follow the
downloader's interface.
"""

import io
import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantlab.core.bars import utc_from_unix
from quantlab.data.downloader import DataIntegrityError, DownloadError, download_bars
from quantlab.data.storage import read_bars_csv, sha256_file
from quantlab.data.validation import validate_dataset

log = logging.getLogger("quantlab.data.adjudication")

# ---- labels -----------------------------------------------------------------
PRIMARY_SPECIFIC_GAP = "PRIMARY-SPECIFIC GAP (second source traded)"
CROSS_VENUE_MISSING = "CROSS-VENUE MISSING / UNRESOLVED"
UNVERIFIED = "UNRESOLVED: CANNOT BE INDEPENDENTLY VERIFIED"
MOVE_OBSERVED = "MARKET MOVE INDEPENDENTLY OBSERVED"
MOVE_NOT_OBSERVED = "MOVE NOT OBSERVED ON SECOND SOURCE (possible primary-specific event)"
MOVE_PARTIAL = "PARTIALLY OBSERVED / UNRESOLVED"
RANGE_OBSERVED = "WIDE RANGE (VOLATILITY) INDEPENDENTLY OBSERVED"
RANGE_NOT_OBSERVED = "WIDE RANGE NOT OBSERVED ON SECOND SOURCE (possible primary-specific wick)"
FLAT_PRIMARY_SPECIFIC = "PRIMARY-SPECIFIC FLAT BAR (second source traded normally)"
FLAT_CROSS_VENUE = "CROSS-VENUE QUIET / UNRESOLVED"
TAG_WICK = "EXTREME WICK DISAGREEMENT (high/low differ; candle not invalidated)"
TAG_LEVELS = "OPEN/CLOSE DIFFER BEYOND TOLERANCE"


class AdjudicationError(Exception):
    """A precondition failed (e.g. primary dataset did not pass validation)."""


@dataclass(frozen=True)
class AdjudicationRules:
    """Fixed comparison rules (from settings.toml [adjudication])."""
    window_hours: int = 24                         # second-source data fetched around each event
    alignment_min_hours: int = 100                 # normal hours needed to trust the timing match
    alignment_max_median_close_diff: float = 0.005 # median |close difference| allowed (0.5%)
    open_close_tolerance: float = 0.01             # open and close "agree" within 1%
    move_observed_min_ratio: float = 0.5           # second-source move >= 50% of primary, same direction
    move_not_observed_max_ratio: float = 0.2       # <= 20% (or opposite direction): not observed
    wick_disagreement_threshold: float = 0.03      # high or low differ by more than 3%

    @classmethod
    def from_settings(cls, settings: dict) -> "AdjudicationRules":
        section = settings.get("adjudication", {})
        kwargs = {}
        for name, f in cls.__dataclass_fields__.items():
            if name in section:
                kwargs[name] = int(section[name]) if f.type in (int, "int") else float(section[name])
        return cls(**kwargs)


@dataclass
class EventResult:
    kind: str                 # missing_bar, large_move, open_gap, wide_bar, flat_bar
    time_unix: int
    label: str
    reason: str
    tags: list = field(default_factory=list)
    primary: dict = None      # primary candle values (None if missing)
    second: dict = None       # second-source candle values (None if missing)
    metrics: dict = field(default_factory=dict)
    validation_message: str = ""

    @property
    def time_utc(self):
        return _iso_unix(self.time_unix)


@dataclass
class AdjudicationReport:
    primary_params: dict
    second_params: dict
    rules: AdjudicationRules
    alignment: dict
    windows: list
    events: list
    primary_fingerprints: dict
    generated_utc: str

    def counts(self) -> dict:
        out = {}
        for e in self.events:
            out[e.label] = out.get(e.label, 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict:
        return {
            "report": "Cross-provider adjudication (read-only: no data was changed, filled or merged)",
            "generated_utc": self.generated_utc,
            "primary_dataset": self.primary_params,
            "second_source": self.second_params,
            "rules_fixed_in_advance": self.rules.__dict__,
            "alignment": self.alignment,
            "label_counts": self.counts(),
            "second_source_windows": self.windows,
            "primary_fingerprints_verified_unchanged": self.primary_fingerprints,
            "events": [{
                "kind": e.kind, "time_utc": e.time_utc, "time_unix": e.time_unix,
                "label": e.label, "reason": e.reason, "tags": e.tags,
                "primary_candle": e.primary, "second_source_candle": e.second,
                "metrics": e.metrics, "validation_message": e.validation_message,
            } for e in self.events],
        }

    def to_json_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), indent=2, default=str) + "\n").encode("utf-8")

    def to_text(self) -> str:
        out = io.StringIO()
        w = out.write
        w("=" * 76 + "\n")
        w("CROSS-PROVIDER ADJUDICATION REPORT\n")
        w("Read-only. No data was filled, replaced, merged or changed.\n")
        w("Labels describe evidence only; no backtesting decision has been made.\n")
        w("=" * 76 + "\n\n")
        w(f"Primary source : {self.primary_params.get('source')} {self.primary_params.get('product_id')}\n")
        w(f"Second source  : {self.second_params.get('source')} {self.second_params.get('product_id')}\n")
        w(f"Generated      : {self.generated_utc}\n\n")
        a = self.alignment
        w(f"ALIGNMENT CHECK: {'PASSED' if a['passed'] else 'FAILED'}\n")
        w(f"   {a['explanation']}\n")
        for k, v in a.get("median_abs_close_diff_by_offset_hours", {}).items():
            w(f"   offset {k:>+3} h: median |close diff| = {v}\n")
        w("\nLABEL COUNTS\n")
        for label, n in self.counts().items():
            w(f"   {n:>3}  {label}\n")
        failed = [x for x in self.windows if x["status"] != "downloaded"]
        w(f"\nSECOND-SOURCE WINDOWS: {len(self.windows)} ({len(failed)} failed)\n")
        for x in failed:
            w(f"   FAILED {x['start_utc']} to {x['end_utc']}: {x['error']}\n")
        w("\n" + "-" * 76 + "\nEVENTS\n")
        for e in self.events:
            w("\n" + f"[{e.kind}] {e.time_utc}\n")
            w(f"   LABEL : {e.label}\n")
            w(f"   WHY   : {e.reason}\n")
            for t in e.tags:
                w(f"   TAG   : {t}\n")
            w(f"   PRIMARY: {_fmt(e.primary)}\n")
            w(f"   SECOND : {_fmt(e.second)}\n")
            for k, v in e.metrics.items():
                w(f"   {k}: {v}\n")
        w("\n" + "=" * 76 + "\nRules used (fixed in settings.toml before seeing results):\n")
        for k, v in self.rules.__dict__.items():
            w(f"   {k} = {v}\n")
        w("\nPrimary dataset fingerprints (verified unchanged before and after):\n")
        for k, v in self.primary_fingerprints.items():
            w(f"   {k}: {v}\n")
        return out.getvalue()


def adjudicate(primary_dir: Path, primary_source, second_source, second_data_dir: Path,
               timeframe: str, rules: AdjudicationRules = AdjudicationRules(), *,
               fetch=None, sleep=None, now=lambda: datetime.now(timezone.utc)) -> AdjudicationReport:
    """Run the full read-only adjudication and return the report (not saved)."""
    primary_dir = Path(primary_dir)
    before = _fingerprint_folder(primary_dir)

    validation = validate_dataset(primary_dir, primary_source, now=now)
    if validation.verdict == "FAILED":
        raise AdjudicationError("The primary dataset FAILED validation; it must pass (with or "
                                "without warnings) before events can be adjudicated.")
    params = validation.params
    interval = int(params["granularity_seconds"])
    start = _parse_iso(params["start_utc"])
    end = _parse_iso(params["end_inclusive_utc"])

    missing_events, flag_events = _collect_events(validation)
    event_times = sorted({t for t, _ in missing_events} | {e["time_unix"] for e in flag_events})

    # Primary candles, read-only.
    primary = {int(b.open_time.timestamp()): b for b in read_bars_csv(primary_dir / "candles.csv", interval)}

    # Second source: download small windows around events into its OWN folder.
    windows = _plan_windows(event_times, interval, rules.window_hours, start, end)
    second, window_records, covered = {}, [], set()
    for w_start, w_end in windows:
        out_dir = (Path(second_data_dir) / second_source.name / second_source.product_id
                   / f"{timeframe}_windows" / f"{w_start:%Y%m%dT%H%M}_{w_end:%Y%m%dT%H%M}")
        record = {"start_utc": _iso(w_start), "end_utc": _iso(w_end), "folder": str(out_dir)}
        kwargs = {k: v for k, v in (("fetch", fetch), ("sleep", sleep)) if v is not None}
        try:
            result = download_bars(second_source, w_start, w_end, out_dir, now=now, **kwargs)
        except (DownloadError, DataIntegrityError) as e:
            record.update(status="failed", error=str(e))
            log.warning("Second-source window %s to %s failed: %s", _iso(w_start), _iso(w_end), e)
        else:
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            record.update(status="downloaded", csv_sha256=manifest["result"]["csv_sha256"],
                          raw_chunks=len(manifest["chunks"]), bars=result.received_bars)
            for b in read_bars_csv(result.csv_path, interval):
                second[int(b.open_time.timestamp())] = b
            covered.update(range(int(w_start.timestamp()), int(w_end.timestamp()) + 1, interval))
        window_records.append(record)

    excluded = set(event_times)
    alignment = _check_alignment(primary, second, covered, excluded, interval, rules)

    results = []
    for t, message in missing_events:
        results.append(_judge_missing(t, message, second, covered, alignment, interval))
    for ev in flag_events:
        results.append(_judge_flag(ev, primary, second, covered, alignment, interval, rules))
    results.sort(key=lambda r: (r.time_unix, r.kind))

    after = _fingerprint_folder(primary_dir)
    if after != before:
        raise AdjudicationError("SAFETY STOP: the primary dataset changed during adjudication.")

    return AdjudicationReport(
        primary_params=params, second_params=second_source.describe(), rules=rules,
        alignment=alignment, windows=window_records, events=results,
        primary_fingerprints={k: before[k] for k in ("candles.csv", "manifest.json") if k in before}
        | {"files_checked": len(before)},
        generated_utc=_iso(now()),
    )


# ---- steps --------------------------------------------------------------------

def _collect_events(validation):
    missing, flags = [], []
    for f in validation.check("missing_bars").findings:
        d = f.details
        if "first_missing_unix" in d:
            for t in range(d["first_missing_unix"], d["last_missing_unix"] + 1,
                           int(validation.params["granularity_seconds"])):
                missing.append((t, f.message))
    for f in validation.check("suspicious_moves").findings:
        if "kind" in f.details:
            flags.append({"kind": f.details["kind"], "time_unix": f.details["time_unix"],
                          "message": f.message})
    return missing, flags


def _plan_windows(event_times, interval, window_hours, start, end):
    """Merge [t - w, t + w] around every event into non-overlapping windows."""
    w = timedelta(hours=window_hours)
    spans = []
    for t in event_times:
        moment = utc_from_unix(t)
        lo, hi = max(moment - w, start), min(moment + w, end)
        if spans and lo <= spans[-1][1] + timedelta(seconds=interval):
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return [tuple(s) for s in spans]


def _check_alignment(primary, second, covered, excluded, interval, rules):
    """Confirm both providers label candles the same way before comparing anything."""
    hours = [t for t in sorted(covered) if t in primary and t not in excluded]
    medians, counts = {}, {}
    for k in (-1, 0, 1):
        diffs = [abs(primary[t].close / second[t + k * interval].close - 1)
                 for t in hours if (t + k * interval) in second and second[t + k * interval].close > 0]
        counts[k] = len(diffs)
        medians[k] = statistics.median(diffs) if diffs else None
    m0, n0 = medians[0], counts[0]
    others = [medians[k] for k in (-1, 1) if medians[k] is not None]
    problems = []
    if n0 < rules.alignment_min_hours:
        problems.append(f"only {n0} comparable normal hours (need {rules.alignment_min_hours})")
    if m0 is None or m0 > rules.alignment_max_median_close_diff:
        problems.append(f"median close difference {_pct(m0)} exceeds "
                        f"{_pct(rules.alignment_max_median_close_diff)}")
    if m0 is not None and any(o <= m0 for o in others):
        problems.append("a 1-hour time shift matches better than no shift (timestamps may "
                        "mean different things)")
    passed = not problems
    explanation = (f"{n0} normal hours compared; median close difference {_pct(m0)} at no shift; "
                   f"no shift fits best." if passed else "NOT TRUSTED: " + "; ".join(problems) +
                   ". All events are therefore UNRESOLVED.")
    return {"passed": passed, "explanation": explanation, "comparable_hours": n0,
            "median_abs_close_diff_by_offset_hours": {k: _pct(v) for k, v in medians.items()}}


def _judge_missing(t, message, second, covered, alignment, interval):
    s = second.get(t)
    base = dict(kind="missing_bar", time_unix=t, primary=None, second=_values(s),
                validation_message=message)
    if t not in covered:
        return EventResult(label=UNVERIFIED, reason="second-source data for this hour could not be "
                           "downloaded", **base)
    if not alignment["passed"]:
        return EventResult(label=UNVERIFIED, reason="the alignment check failed", **base)
    if s is not None and s.volume > 0:
        return EventResult(label=PRIMARY_SPECIFIC_GAP,
                           reason=f"the second source has a candle with volume {s.volume} for this "
                                  f"hour, so the market traded while the primary source recorded "
                                  f"nothing", **base)
    why = ("the second source also has no candle" if s is None
           else "the second source's candle has zero volume")
    return EventResult(label=CROSS_VENUE_MISSING,
                       reason=f"{why}; two missing venues are not enough evidence of a market-wide "
                              f"halt, so this stays unresolved", **base)


def _judge_flag(ev, primary, second, covered, alignment, interval, rules):
    t, kind = ev["time_unix"], ev["kind"]
    p, s = primary.get(t), second.get(t)
    pp, sp = primary.get(t - interval), second.get(t - interval)
    base = dict(kind=kind, time_unix=t, primary=_values(p), second=_values(s),
                validation_message=ev["message"])

    if t not in covered:
        return EventResult(label=UNVERIFIED, reason="second-source data for this hour could not be "
                           "downloaded", **base)
    if not alignment["passed"]:
        return EventResult(label=UNVERIFIED, reason="the alignment check failed", **base)
    if p is None or s is None:
        return EventResult(label=UNVERIFIED, reason="the second source has no candle for this hour",
                           **base)

    metrics = {
        "open_diff": _pct(p.open / s.open - 1),
        "close_diff": _pct(p.close / s.close - 1),
        "high_diff (reported separately)": _pct(p.high / s.high - 1),
        "low_diff (reported separately)": _pct(p.low / s.low - 1),
    }
    levels_agree = (abs(p.open / s.open - 1) <= rules.open_close_tolerance
                    and abs(p.close / s.close - 1) <= rules.open_close_tolerance)
    tags = []
    if max(abs(p.high / s.high - 1), abs(p.low / s.low - 1)) > rules.wick_disagreement_threshold:
        tags.append(TAG_WICK)
    if not levels_agree:
        tags.append(TAG_LEVELS)
    metrics["open_close_agree"] = levels_agree
    base.update(tags=tags, metrics=metrics)

    if kind == "flat_bar":
        if s.volume > 0 and s.high > s.low:
            return EventResult(label=FLAT_PRIMARY_SPECIFIC,
                               reason=f"the second source traded normally in this hour (range "
                                      f"{_pct((s.high - s.low) / s.low)}, volume {s.volume})", **base)
        return EventResult(label=FLAT_CROSS_VENUE,
                           reason="the second source was also flat or had zero volume", **base)

    if kind == "wide_bar":
        p_metric, s_metric = (p.high - p.low) / p.low, (s.high - s.low) / s.low
        what, observed_label, not_label = "bar range", RANGE_OBSERVED, RANGE_NOT_OBSERVED
    else:
        if pp is None or sp is None:
            return EventResult(label=UNVERIFIED, reason="the previous hour is missing on one source, "
                               "so the move cannot be compared", **base)
        if kind == "large_move":
            p_metric, s_metric = p.close / pp.close - 1, s.close / sp.close - 1
            what = "close-to-close move"
        else:  # open_gap
            p_metric, s_metric = p.open / pp.close - 1, s.open / sp.close - 1
            what = "previous-close-to-open move"
        observed_label, not_label = MOVE_OBSERVED, MOVE_NOT_OBSERVED

    ratio = s_metric / p_metric if p_metric else 0.0
    metrics[f"primary {what}"] = _pct(p_metric)
    metrics[f"second-source {what}"] = _pct(s_metric)
    metrics["second/primary ratio"] = round(ratio, 3)
    if ratio <= rules.move_not_observed_max_ratio:
        return EventResult(label=not_label,
                           reason=f"the second source's {what} was {_pct(s_metric)} versus "
                                  f"{_pct(p_metric)} on the primary (ratio {ratio:.2f}; "
                                  f"not observed means <= {rules.move_not_observed_max_ratio})", **base)
    if ratio >= rules.move_observed_min_ratio and levels_agree:
        return EventResult(label=observed_label,
                           reason=f"the second source shows a {what} of {_pct(s_metric)} versus "
                                  f"{_pct(p_metric)} (ratio {ratio:.2f}) and open/close agree within "
                                  f"{_pct(rules.open_close_tolerance)}", **base)
    if ratio >= rules.move_observed_min_ratio:
        reason = (f"the {what} was observed (ratio {ratio:.2f}) but open/close differ by more than "
                  f"{_pct(rules.open_close_tolerance)}")
    else:
        reason = f"the second source shows only part of the {what} (ratio {ratio:.2f})"
    return EventResult(label=MOVE_PARTIAL, reason=reason, **base)


# ---- helpers ------------------------------------------------------------------

def _fingerprint_folder(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): sha256_file(p)
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}


def _values(bar):
    if bar is None:
        return None
    return {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
            "volume": bar.volume}


def _fmt(values):
    if values is None:
        return "no candle"
    return "O={open} H={high} L={low} C={close} V={volume}".format(**values)


def _pct(x):
    return None if x is None else f"{x:+.3%}"


def _parse_iso(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _iso_unix(t):
    return utc_from_unix(t).strftime("%Y-%m-%d %H:%M UTC")


def _iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
