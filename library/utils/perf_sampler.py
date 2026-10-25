import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from threading import Lock
from typing import Callable, Iterator, TextIO

CAPTURING_ENABLED = os.environ.get("SOURCEIO_PERF_TRACE", "0").lower() in ("1", "true", "yes", "on")
MAX_TIMER_NAMES = 256
MAX_REPORT_ROWS = 100
_OVERFLOW_NAME = "<other timers>"


@dataclass
class TimingSummary:
    count: int = 0
    total_seconds: float = 0.0
    min_seconds: float = float("inf")
    max_seconds: float = 0.0

    def record(self, duration: float):
        self.count += 1
        self.total_seconds += duration
        self.min_seconds = min(self.min_seconds, duration)
        self.max_seconds = max(self.max_seconds, duration)

    def to_dict(self, name: str) -> dict:
        return {
            "name": name,
            "count": self.count,
            "total_seconds": self.total_seconds,
            "average_seconds": self.total_seconds / self.count,
            "min_seconds": self.min_seconds,
            "max_seconds": self.max_seconds,
        }


FUNCTION_TIMES: dict[str, TimingSummary] = {}
_LOCK = Lock()
_overflow_samples = 0


def record_timing(name: str, duration: float):
    if not isinstance(name, str) or not name:
        raise ValueError("Timing name must be a non-empty string")
    if duration < 0:
        raise ValueError("Timing duration cannot be negative")

    global _overflow_samples
    with _LOCK:
        summary = FUNCTION_TIMES.get(name)
        if summary is None:
            named_timer_count = len(FUNCTION_TIMES) - int(_OVERFLOW_NAME in FUNCTION_TIMES)
            if named_timer_count >= MAX_TIMER_NAMES - 1:
                name = _OVERFLOW_NAME
                _overflow_samples += 1
                summary = FUNCTION_TIMES.get(name)
            if summary is None:
                summary = TimingSummary()
                FUNCTION_TIMES[name] = summary
        summary.record(duration)


def timed(function: Callable):
    if not CAPTURING_ENABLED:
        return function

    name = f"{function.__module__}.{function.__qualname__}"

    @wraps(function)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            record_timing(name, time.perf_counter() - start)

    return wrapper


@contextmanager
def timed_region(name: str) -> Iterator[None]:
    if not CAPTURING_ENABLED:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        record_timing(name, time.perf_counter() - start)


def reset_stats():
    global _overflow_samples
    with _LOCK:
        FUNCTION_TIMES.clear()
        _overflow_samples = 0


def snapshot_stats(limit: int = MAX_REPORT_ROWS) -> dict:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("Timing report limit must be an integer")
    if not 0 <= limit <= MAX_REPORT_ROWS:
        raise ValueError(f"Timing report limit must be between 0 and {MAX_REPORT_ROWS}")

    with _LOCK:
        rows = [summary.to_dict(name) for name, summary in FUNCTION_TIMES.items()]
        overflow_samples = _overflow_samples
    rows.sort(key=lambda row: (-row["total_seconds"], row["name"]))
    omitted = max(0, len(rows) - limit)
    return {
        "enabled": CAPTURING_ENABLED,
        "max_timer_names": MAX_TIMER_NAMES,
        "max_report_rows": MAX_REPORT_ROWS,
        "overflow_samples": overflow_samples,
        "omitted_timer_rows": omitted,
        "timers": rows[:limit],
    }


def print_stats(file: TextIO | None = None):
    if not CAPTURING_ENABLED:
        return
    file = file or sys.stdout
    report = snapshot_stats()
    rows = report["timers"]
    print("SourceIO import performance:", file=file)
    if not rows:
        print("No timing data collected.", file=file)
        return
    longest_name = max(len(row["name"]) for row in rows)
    print(
        f"{'Name':<{longest_name}}  {'count':>8}  {'total, sec':>12}  "
        f"{'average, sec':>12}  {'min, sec':>10}  {'max, sec':>10}",
        file=file,
    )
    for row in rows:
        print(
            f"{row['name']:<{longest_name}}  {row['count']:>8}  "
            f"{row['total_seconds']:>12.4f}  {row['average_seconds']:>12.5f}  "
            f"{row['min_seconds']:>10.5f}  {row['max_seconds']:>10.5f}",
            file=file,
        )
    if report["omitted_timer_rows"]:
        print(f"{report['omitted_timer_rows']} lower-total timer rows omitted.", file=file)
    if report["overflow_samples"]:
        print(f"{report['overflow_samples']} samples used the bounded overflow timer.", file=file)
