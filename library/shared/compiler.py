from __future__ import annotations

import codecs
import os
import re
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Pattern


DEFAULT_ERROR_PATTERNS = (
    re.compile(r"(?im)^\s*(?:fatal(?:\s+error)?|error)\s*[:!]"),
    re.compile(r"(?im)^\s*\[(?:fail(?:ed)?|error)\]"),
    re.compile(r"(?im)^\s*failed(?:\s+to\b|:)"),
    re.compile(r"(?im)\b[1-9][0-9]*\s+failed\b"),
    re.compile(r"(?im)^RESOURCE COMPILE WARNING:.*\bNo meshes selected\b"),
)


@dataclass(frozen=True, slots=True)
class CompilerRunResult:
    command: tuple[str, ...]
    source: Path
    expected_outputs: tuple[Path, ...]
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    log: str
    log_truncated: bool
    error_markers: tuple[str, ...]
    duration_seconds: float


class CompilerError(RuntimeError):
    def __init__(self, message: str, result: CompilerRunResult | None = None):
        super().__init__(message)
        self.result = result


class CompilerLaunchError(CompilerError):
    pass


class CompilerTimeoutError(CompilerError):
    pass


class CompilerCaptureError(CompilerError):
    pass


class CompilerExitError(CompilerError):
    pass


class CompilerReportedError(CompilerError):
    pass


class CompilerOutputMissingError(CompilerError):
    def __init__(self, outputs: Sequence[Path], result: CompilerRunResult):
        self.outputs = tuple(outputs)
        names = ", ".join(str(path) for path in self.outputs)
        super().__init__(f"Compiler did not create expected output(s): {names}", result)


class CompilerOutputStaleError(CompilerError):
    def __init__(self, outputs: Sequence[Path], result: CompilerRunResult):
        self.outputs = tuple(outputs)
        names = ", ".join(str(path) for path in self.outputs)
        super().__init__(f"Compiler left stale expected output(s): {names}", result)


@dataclass(frozen=True, slots=True)
class _FileState:
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def read(cls, path: Path) -> _FileState | None:
        try:
            status = path.stat()
        except FileNotFoundError:
            return None
        if not path.is_file():
            return None
        return cls(status.st_size, status.st_mtime_ns, status.st_ctime_ns)


class _BoundedBytes:
    def __init__(self, limit: int):
        self.limit = limit
        self.total = 0
        self.data = bytearray()
        self._lock = threading.Lock()

    def append(self, value: bytes):
        with self._lock:
            self.total += len(value)
            if len(value) >= self.limit:
                self.data[:] = value[-self.limit:]
                return
            overflow = len(self.data) + len(value) - self.limit
            if overflow > 0:
                del self.data[:overflow]
            self.data.extend(value)

    @property
    def truncated(self) -> bool:
        return self.total > self.limit

    def text(self) -> str:
        with self._lock:
            return bytes(self.data).decode("utf-8", errors="replace")


class _MarkerCollector:
    def __init__(self, patterns: Sequence[Pattern[str]], *, limit: int = 32):
        self.patterns = tuple(patterns)
        self.limit = limit
        self.markers: list[str] = []
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def scan(self, channel: str, text: str):
        with self._lock:
            if len(self.markers) >= self.limit:
                return
            for pattern in self.patterns:
                for match in pattern.finditer(text):
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line_end = text.find("\n", match.end())
                    if line_end < 0:
                        line_end = len(text)
                    snippet = text[line_start:line_end].replace(_MID_LINE, "").strip()
                    if len(snippet) > 512:
                        snippet = snippet[:509] + "..."
                    marker = f"{channel}: {snippet}"
                    if marker in self._seen:
                        continue
                    self._seen.add(marker)
                    self.markers.append(marker)
                    if len(self.markers) >= self.limit:
                        return


_MID_LINE = "\0"
_RESCAN_CHARACTERS = 1024


def _rescan_tail(text: str, limit: int = _RESCAN_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    newline = tail.find("\n")
    if newline >= 0:
        return tail[newline + 1:]
    return _MID_LINE + tail


def _drain_stream(
        stream,
        capture: _BoundedBytes,
        markers: _MarkerCollector,
        channel: str,
        errors: list[BaseException],
):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    overlap = ""
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            capture.append(chunk)
            text = overlap + decoder.decode(chunk)
            markers.scan(channel, text)
            overlap = _rescan_tail(text)
        remainder = decoder.decode(b"", final=True)
        if remainder:
            markers.scan(channel, overlap + remainder)
    except BaseException as exc:
        errors.append(exc)
    finally:
        stream.close()


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[-limit:].decode("utf-8", errors="replace"), True


def run_compiler(
        command: Sequence[str],
        source: Path,
        expected_outputs: Sequence[Path],
        *,
        tool_name: str = "Compiler",
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
        max_stream_bytes: int = 256 * 1024,
        max_log_bytes: int = 512 * 1024,
        freshness_tolerance_seconds: float = 2.0,
        allow_unchanged_outputs_if_up_to_date: bool = False,
        error_patterns: Sequence[str | Pattern[str]] = DEFAULT_ERROR_PATTERNS,
        popen_factory: Callable[..., object] | None = None,
) -> CompilerRunResult:
    if isinstance(command, (str, bytes)) or not command:
        raise TypeError("Compiler command must be a non-empty array of strings")
    normalized_command = tuple(command)
    if any(not isinstance(argument, str) or not argument for argument in normalized_command):
        raise ValueError("Compiler command arguments must be non-empty strings")
    source = Path(source)
    output_paths = tuple(Path(path) for path in expected_outputs)
    if not output_paths:
        raise ValueError("At least one expected compiler output is required")
    if len(set(output_paths)) != len(output_paths):
        raise ValueError("Expected compiler outputs must be unique")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_stream_bytes <= 0 or max_log_bytes <= 0:
        raise ValueError("Compiler output bounds must be positive")
    if freshness_tolerance_seconds < 0:
        raise ValueError("freshness_tolerance_seconds cannot be negative")
    compiled_patterns = tuple(
        re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        if isinstance(pattern, str)
        else pattern
        for pattern in error_patterns
    )
    before = {path: _FileState.read(path) for path in output_paths}
    source_state = _FileState.read(source)
    start_wall_ns = time.time_ns()
    start_monotonic = time.monotonic()
    factory = popen_factory or subprocess.Popen
    options = {
        "cwd": str(cwd) if cwd is not None else None,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if environment is not None:
        options["env"] = dict(environment)
    try:
        process = factory(list(normalized_command), **options)
    except OSError as exc:
        raise CompilerLaunchError(
            f"Unable to launch {tool_name}: {normalized_command[0]}"
        ) from exc

    stdout_stream = getattr(process, "stdout", None)
    stderr_stream = getattr(process, "stderr", None)
    if stdout_stream is None or stderr_stream is None:
        raise CompilerLaunchError(
            f"{tool_name} process did not expose stdout and stderr pipes"
        )
    stdout_capture = _BoundedBytes(max_stream_bytes)
    stderr_capture = _BoundedBytes(max_stream_bytes)
    marker_collector = _MarkerCollector(compiled_patterns)
    capture_errors: list[BaseException] = []
    threads = (
        threading.Thread(
            target=_drain_stream,
            args=(
                stdout_stream,
                stdout_capture,
                marker_collector,
                "stdout",
                capture_errors,
            ),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_stream,
            args=(
                stderr_stream,
                stderr_capture,
                marker_collector,
                "stderr",
                capture_errors,
            ),
            daemon=True,
        ),
    )
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait()

    for thread in threads:
        thread.join(timeout=5.0)
    hanging_threads = [thread for thread in threads if thread.is_alive()]
    if hanging_threads:
        for stream in (stdout_stream, stderr_stream):
            stream.close()
        for thread in hanging_threads:
            thread.join(timeout=1.0)

    stdout = stdout_capture.text()
    stderr = stderr_capture.text()
    combined_log, combined_truncated = _bounded_text(
        f"[stdout]\n{stdout}\n[stderr]\n{stderr}",
        max_log_bytes,
    )
    result = CompilerRunResult(
        command=normalized_command,
        source=source,
        expected_outputs=output_paths,
        returncode=int(returncode),
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_capture.total,
        stderr_bytes=stderr_capture.total,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        log=combined_log,
        log_truncated=(
            combined_truncated
            or stdout_capture.truncated
            or stderr_capture.truncated
        ),
        error_markers=tuple(marker_collector.markers),
        duration_seconds=time.monotonic() - start_monotonic,
    )
    if timed_out:
        raise CompilerTimeoutError(
            f"{tool_name} exceeded {timeout_seconds:g} seconds",
            result,
        )
    if any(thread.is_alive() for thread in threads) or capture_errors:
        raise CompilerCaptureError(
            f"{tool_name} output capture did not finish cleanly",
            result,
        )
    if result.returncode != 0:
        raise CompilerExitError(
            f"{tool_name} exited with status {result.returncode}",
            result,
        )
    if result.error_markers:
        raise CompilerReportedError(
            f"{tool_name} reported an error despite a zero exit status",
            result,
        )

    missing = []
    stale = []
    freshness_tolerance_ns = int(
        freshness_tolerance_seconds * 1_000_000_000
    )
    for output_path in output_paths:
        state = _FileState.read(output_path)
        if state is None:
            missing.append(output_path)
            continue
        if before[output_path] == state:
            if (
                    allow_unchanged_outputs_if_up_to_date
                    and source_state is not None
                    and state.mtime_ns + freshness_tolerance_ns
                    >= source_state.mtime_ns
            ):
                continue
            stale.append(output_path)
            continue
        if state.mtime_ns + freshness_tolerance_ns < start_wall_ns:
            stale.append(output_path)
    if missing:
        raise CompilerOutputMissingError(missing, result)
    if stale:
        raise CompilerOutputStaleError(stale, result)
    return result
