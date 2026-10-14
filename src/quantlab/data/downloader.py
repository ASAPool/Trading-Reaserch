"""Safe, resumable, chunked downloader for historical bars.

This module is market-neutral. It works with any "source" object that
provides:  describe(), build_url(chunk), parse(body_bytes),
granularity, candles_per_chunk.

Safety rules it enforces:
1. Requests are small (one chunk each) and paced with a pause between them.
2. Temporary failures (network errors, HTTP 429 "too many requests",
   HTTP 5xx server errors, garbled responses) are retried with growing
   waits. Other errors (e.g. HTTP 400 "bad request") stop immediately.
3. If a chunk still fails after all retries, the download STOPS with a
   clear error. Nothing is guessed or filled in.
4. Every raw response is saved byte-for-byte, unchanged, with a SHA-256
   fingerprint recorded in manifest.json. Files are written atomically,
   so an interruption never leaves a half-written file.
5. Re-running resumes: chunks already saved (and whose fingerprint still
   matches) are not downloaded again. A raw file that was changed or
   deleted after download stops the run with an error.
6. Missing bars are never invented. They are counted and reported.
7. The last requested bar must have fully closed, so no incomplete
   (still-forming) bar can ever be saved.
"""

import json
import logging
import platform
import subprocess
import sys
import time
import http.client
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import quantlab
from quantlab.data.chunking import Chunk, expected_bar_count, plan_chunks
from quantlab.data.storage import (
    atomic_write_bytes,
    atomic_write_json,
    candle_rows_to_csv_bytes,
    sha256_bytes,
    sha256_file,
)

log = logging.getLogger("quantlab.data.download")

MANIFEST_VERSION = 1
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


class DownloadError(Exception):
    """A chunk could not be retrieved, or the request is not allowed."""


class DataIntegrityError(Exception):
    """Previously saved raw data is missing or has been altered."""


class TransientNetworkError(Exception):
    """A network-level problem worth retrying (timeout, dropped connection)."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5                        # per chunk
    base_delay_seconds: float = 2.0              # wait after 1st failure; doubles each time
    max_delay_seconds: float = 60.0
    pause_between_requests_seconds: float = 0.4  # about 2.5 requests/second at most
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DownloadResult:
    out_dir: Path
    csv_path: Path
    manifest_path: Path
    expected_bars: int
    received_bars: int
    unique_bar_times: int
    missing_bars: int
    duplicate_bar_times: int
    outside_window_rows: int
    chunks_total: int
    chunks_downloaded_this_run: int
    http_requests_this_run: int


def urllib_fetch(url: str, timeout: float) -> tuple[int, dict, bytes]:
    """Perform one HTTP GET. Returns (status, headers, body)."""
    request = Request(url, headers={
        "User-Agent": f"quantlab/{quantlab.__version__} (historical research)",
        "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as e:  # the server answered with an error status
        body = e.read() if e.fp is not None else b""
        return e.code, dict(e.headers or {}), body
    except (URLError, TimeoutError, socket.timeout, ConnectionError,
            http.client.HTTPException) as e:
        raise TransientNetworkError(f"{type(e).__name__}: {e}") from e


def download_bars(source, start: datetime, end_inclusive: datetime, out_dir: Path, *,
                  fetch=urllib_fetch, sleep=time.sleep,
                  now=lambda: datetime.now(timezone.utc),
                  policy: RetryPolicy = RetryPolicy()) -> DownloadResult:
    """Download every bar from start to end_inclusive (bar open times, UTC)."""
    out_dir = Path(out_dir)
    interval = source.granularity
    last_bar_close = end_inclusive + timedelta(seconds=interval)
    if last_bar_close > now():
        raise DownloadError(
            f"The last requested bar ({end_inclusive:%Y-%m-%d %H:%M} UTC) has not "
            f"closed yet. Only completed bars may be downloaded."
        )

    chunks = plan_chunks(start, end_inclusive, interval, source.candles_per_chunk)
    params = dict(source.describe(), start_utc=_iso(start), end_inclusive_utc=_iso(end_inclusive))

    manifest_path = out_dir / "manifest.json"
    manifest = _load_or_create_manifest(manifest_path, params, policy, now)
    raw_dir = out_dir / "raw"

    log.info("Planned %d chunks of up to %d bars for %s (%s to %s).",
             len(chunks), source.candles_per_chunk, params.get("product_id", "?"),
             _iso(start), _iso(end_inclusive))

    downloaded = 0
    requests_made = 0
    for chunk in chunks:
        raw_path = raw_dir / f"chunk_{chunk.index:04d}.json"
        record = manifest["chunks"].get(str(chunk.index))
        if record is not None:
            _verify_saved_chunk(raw_path, record)
            continue

        if requests_made > 0:
            sleep(policy.pause_between_requests_seconds)
        url = source.build_url(chunk)
        try:
            body, rows, attempts, failures = _fetch_with_retry(url, source.parse, fetch, sleep, policy)
        except DownloadError as e:
            manifest["status"] = "failed"
            manifest["error"] = {"chunk_index": chunk.index, "start_utc": _iso(chunk.start),
                                 "end_inclusive_utc": _iso(chunk.end_inclusive), "message": str(e)}
            manifest["updated_utc"] = _iso(now())
            atomic_write_json(manifest_path, manifest)
            raise DownloadError(
                f"Chunk {chunk.index + 1} of {len(chunks)} "
                f"({_iso(chunk.start)} to {_iso(chunk.end_inclusive)}) could not be downloaded. {e}"
            ) from None
        requests_made += attempts

        in_window = sum(1 for r in rows if _in_chunk(chunk, r.open_time_unix))
        atomic_write_bytes(raw_path, body)
        manifest["chunks"][str(chunk.index)] = {
            "index": chunk.index,
            "start_utc": _iso(chunk.start),
            "end_inclusive_utc": _iso(chunk.end_inclusive),
            "url": url,
            "file": f"raw/{raw_path.name}",
            "sha256": sha256_bytes(body),
            "bytes": len(body),
            "rows_received": len(rows),
            "rows_in_window": in_window,
            "rows_outside_window": len(rows) - in_window,
            "attempts": attempts,
            "failed_attempts": failures,
            "downloaded_utc": _iso(now()),
        }
        manifest["status"] = "in_progress"
        manifest.pop("error", None)
        manifest["updated_utc"] = _iso(now())
        atomic_write_json(manifest_path, manifest)
        downloaded += 1
        log.debug("chunk %d: %d rows (%d in window), %d attempt(s)",
                  chunk.index, len(rows), in_window, attempts)
        if downloaded % 20 == 0:
            log.info("Progress: %d of %d chunks saved.", len(manifest["chunks"]), len(chunks))

    return _build_csv(source, chunks, start, end_inclusive, out_dir, manifest,
                      manifest_path, now, downloaded, requests_made)


def _fetch_with_retry(url, parse, fetch, sleep, policy):
    """Return (body, rows, attempts, failure_reasons) or raise DownloadError."""
    failures = []
    for attempt in range(1, policy.max_attempts + 1):
        retry_after = None
        try:
            status, headers, body = fetch(url, policy.timeout_seconds)
        except TransientNetworkError as e:
            reason = f"network error: {e}"
        else:
            if status == 200:
                try:
                    return body, parse(body), attempt, failures
                except ValueError as e:
                    reason = f"malformed response: {e}"
            elif status in RETRYABLE_HTTP_STATUS:
                reason = f"HTTP {status}"
                retry_after = _retry_after_seconds(headers)
            else:
                raise DownloadError(
                    f"Server answered HTTP {status}, which is not a temporary error, "
                    f"so it was not retried. Response: {body[:300]!r}"
                )
        failures.append(reason)
        if attempt == policy.max_attempts:
            break
        delay = min(policy.max_delay_seconds, policy.base_delay_seconds * 2 ** (attempt - 1))
        if retry_after is not None:
            delay = max(delay, retry_after)
        log.warning("Attempt %d/%d failed (%s). Waiting %.1f s before retrying.",
                    attempt, policy.max_attempts, reason, delay)
        sleep(delay)
    raise DownloadError(f"Gave up after {policy.max_attempts} attempts: " + "; ".join(failures))


def _build_csv(source, chunks, start, end_inclusive, out_dir, manifest, manifest_path,
               now, downloaded, requests_made) -> DownloadResult:
    """Combine all saved raw chunks into one time-ordered CSV."""
    rows = []
    outside = 0
    for chunk in chunks:
        record = manifest["chunks"][str(chunk.index)]
        raw_path = out_dir / record["file"]
        _verify_saved_chunk(raw_path, record)
        for row in source.parse(raw_path.read_bytes()):
            if _in_chunk(chunk, row.open_time_unix):
                rows.append(row)
            else:
                outside += 1  # belongs to a neighbouring chunk, which has its own copy
    rows.sort(key=lambda r: r.open_time_unix)  # stable: duplicates keep their order

    csv_bytes = candle_rows_to_csv_bytes(rows)
    csv_path = out_dir / "candles.csv"
    atomic_write_bytes(csv_path, csv_bytes)

    expected = expected_bar_count(start, end_inclusive, source.granularity)
    unique_times = len({r.open_time_unix for r in rows})
    result = DownloadResult(
        out_dir=out_dir, csv_path=csv_path, manifest_path=manifest_path,
        expected_bars=expected, received_bars=len(rows), unique_bar_times=unique_times,
        missing_bars=expected - unique_times, duplicate_bar_times=len(rows) - unique_times,
        outside_window_rows=outside, chunks_total=len(chunks),
        chunks_downloaded_this_run=downloaded, http_requests_this_run=requests_made,
    )
    manifest["status"] = "complete"
    manifest["updated_utc"] = _iso(now())
    manifest["result"] = {
        "csv_file": csv_path.name,
        "csv_sha256": sha256_bytes(csv_bytes),
        "expected_bars": expected,
        "received_bars": len(rows),
        "unique_bar_times": unique_times,
        "missing_bars_not_filled": expected - unique_times,
        "duplicate_bar_times_not_removed": len(rows) - unique_times,
        "rows_outside_chunk_windows_excluded": outside,
        "note": "Missing bars are reported, never invented. Raw responses in raw/ are unchanged.",
    }
    atomic_write_json(manifest_path, manifest)
    return result


def _load_or_create_manifest(path: Path, params: dict, policy: RetryPolicy, now) -> dict:
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("params") != params:
            raise DownloadError(
                f"{path.parent} already holds a download made with different settings. "
                f"Existing: {manifest.get('params')}  Requested: {params}"
            )
        log.info("Resuming: %d chunk(s) already saved in %s.", len(manifest["chunks"]), path.parent)
        return manifest
    return {
        "manifest_version": MANIFEST_VERSION,
        "description": "Record of a historical bar download. Enough to reproduce it exactly.",
        "params": params,
        "status": "in_progress",
        "created_utc": _iso(now()),
        "updated_utc": _iso(now()),
        "software": {
            "quantlab_version": quantlab.__version__,
            "git_commit": _git_commit(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "retry_policy": asdict(policy),
        "chunks": {},
    }


def _verify_saved_chunk(raw_path: Path, record: dict) -> None:
    if not raw_path.exists():
        raise DataIntegrityError(
            f"Raw file {raw_path} is listed in manifest.json but is missing. "
            f"Raw data must never be edited or deleted by hand. To start over, "
            f"delete the whole folder {raw_path.parent.parent} and run the download again."
        )
    if sha256_file(raw_path) != record["sha256"]:
        raise DataIntegrityError(
            f"Raw file {raw_path} has changed since it was downloaded (fingerprint mismatch). "
            f"Raw data must never be edited. To start over, delete the whole folder "
            f"{raw_path.parent.parent} and run the download again."
        )


def _in_chunk(chunk: Chunk, open_time_unix: int) -> bool:
    return chunk.start.timestamp() <= open_time_unix <= chunk.end_inclusive.timestamp()


def _retry_after_seconds(headers: dict):
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None
    return None


def _git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
