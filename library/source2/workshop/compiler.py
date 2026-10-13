from __future__ import annotations

import codecs
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Pattern, Sequence

from .target import (
    ContentRelativePath,
    UnsafeWorkshopPathError,
    WorkshopTargetProfile,
)


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


class ResourceCompilerError(RuntimeError):
    def __init__(self, message: str, result: CompilerRunResult | None = None):
        super().__init__(message)
        self.result = result


class ResourceCompilerLaunchError(ResourceCompilerError):
    pass


class ResourceCompilerTimeoutError(ResourceCompilerError):
    pass


class ResourceCompilerCaptureError(ResourceCompilerError):
    pass


class ResourceCompilerExitError(ResourceCompilerError):
    pass


class ResourceCompilerReportedError(ResourceCompilerError):
    pass


class ResourceCompilerOutputMissingError(ResourceCompilerError):
    def __init__(self, outputs: Sequence[Path], result: CompilerRunResult):
        self.outputs = tuple(outputs)
        names = ", ".join(str(path) for path in self.outputs)
        super().__init__(f"Resource Compiler did not create expected output(s): {names}", result)


class ResourceCompilerOutputStaleError(ResourceCompilerError):
    def __init__(self, outputs: Sequence[Path], result: CompilerRunResult):
        self.outputs = tuple(outputs)
        names = ", ".join(str(path) for path in self.outputs)
        super().__init__(f"Resource Compiler left stale expected output(s): {names}", result)


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


# Starts a rescanned tail that begins inside a line, so ^-anchored patterns cannot treat that point as a line start.
_MID_LINE = "\0"
_RESCAN_CHARACTERS = 1024


def _rescan_tail(text: str, limit: int = _RESCAN_CHARACTERS) -> str:
    """The end of the scanned text to prepend to the next chunk, so markers split across chunks are still found.

    It starts at a line start where it can, and with the mid-line sentinel where the last line is too long."""
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


class ResourceCompilerRunner:
    _RESERVED_ARGUMENTS = frozenset(("-game", "-i", "-filelist"))

    def __init__(
            self,
            target: WorkshopTargetProfile,
            *,
            timeout_seconds: float = 300.0,
            max_stream_bytes: int = 256 * 1024,
            max_log_bytes: int = 512 * 1024,
            freshness_tolerance_seconds: float = 2.0,
            error_patterns: Sequence[str | Pattern[str]] = DEFAULT_ERROR_PATTERNS,
            popen_factory: Callable[..., object] | None = None,
    ):
        if not isinstance(target, WorkshopTargetProfile):
            raise TypeError("target must be a WorkshopTargetProfile")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_stream_bytes <= 0 or max_log_bytes <= 0:
            raise ValueError("Compiler output bounds must be positive")
        if freshness_tolerance_seconds < 0:
            raise ValueError("freshness_tolerance_seconds cannot be negative")
        compiled_patterns = []
        for pattern in error_patterns:
            compiled_patterns.append(
                re.compile(pattern, re.IGNORECASE | re.MULTILINE)
                if isinstance(pattern, str)
                else pattern
            )
        self.target = target
        self.timeout_seconds = float(timeout_seconds)
        self.max_stream_bytes = int(max_stream_bytes)
        self.max_log_bytes = int(max_log_bytes)
        self.freshness_tolerance_ns = int(freshness_tolerance_seconds * 1_000_000_000)
        self.error_patterns = tuple(compiled_patterns)
        self._popen_factory = popen_factory

    @classmethod
    def _validate_arguments(cls, arguments: Sequence[str]) -> tuple[str, ...]:
        if isinstance(arguments, (str, bytes)):
            raise TypeError("Compiler arguments must be an array of strings")
        normalized = []
        for argument in arguments:
            if not isinstance(argument, str):
                raise TypeError("Compiler arguments must be strings")
            if not argument or "\0" in argument:
                raise ValueError("Compiler arguments cannot be empty or contain NUL")
            option = argument.split("=", 1)[0].casefold()
            if option in cls._RESERVED_ARGUMENTS:
                raise ValueError(f"Compiler argument is managed by the runner: {option}")
            normalized.append(argument)
        return tuple(normalized)

    def compile(
            self,
            source: str | os.PathLike[str] | ContentRelativePath,
            *,
            expected_outputs: Sequence[
                str | os.PathLike[str] | ContentRelativePath
            ] | None = None,
            arguments: Sequence[str] = (),
    ) -> CompilerRunResult:
        source_asset = self.target.content_relative(source)
        if source_asset.name.endswith("_c"):
            raise ValueError(f"Compiled resources cannot be compiler inputs: {source_asset}")
        source_path = self.target.content_path(source_asset)
        if not source_path.is_file():
            raise FileNotFoundError(f"Compiler input is unavailable: {source_path}")

        if expected_outputs is None:
            output_assets = (source_asset.compiled(),)
        else:
            if isinstance(expected_outputs, (str, bytes, os.PathLike, ContentRelativePath)):
                raise TypeError("expected_outputs must be an array of content-relative paths")
            output_assets = tuple(ContentRelativePath(path) for path in expected_outputs)
            if not output_assets:
                raise ValueError("At least one expected output is required")
        if any(not output.name.endswith("_c") for output in output_assets):
            raise ValueError("Expected Resource Compiler outputs must end with '_c'")
        if len(set(output_assets)) != len(output_assets):
            raise ValueError("Expected Resource Compiler outputs must be unique")
        output_paths = tuple(self.target.game_path(path) for path in output_assets)
        before = {path: _FileState.read(path) for path in output_paths}

        arguments = self._validate_arguments(arguments)
        if not self.target.resource_compiler.is_file():
            raise ResourceCompilerLaunchError(
                f"Resource Compiler is unavailable: {self.target.resource_compiler}"
            )
        if not self.target.game_directory.is_dir():
            raise ResourceCompilerLaunchError(
                f"Game directory is unavailable: {self.target.game_directory}"
            )
        if not self.target.gameinfo_path.is_file():
            raise ResourceCompilerLaunchError(
                f"gameinfo.gi is unavailable: {self.target.gameinfo_path}"
            )

        command = (
            str(self.target.resource_compiler),
            "-game",
            str(self.target.game_directory),
            "-i",
            str(source_path),
            *arguments,
        )
        start_wall_ns = time.time_ns()
        start_monotonic = time.monotonic()
        factory = self._popen_factory or subprocess.Popen
        try:
            process = factory(
                list(command),
                cwd=str(self.target.resource_compiler.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError as exc:
            raise ResourceCompilerLaunchError(
                f"Unable to launch Resource Compiler: {self.target.resource_compiler}"
            ) from exc

        stdout_stream = getattr(process, "stdout", None)
        stderr_stream = getattr(process, "stderr", None)
        if stdout_stream is None or stderr_stream is None:
            raise ResourceCompilerLaunchError(
                "Resource Compiler process did not expose stdout and stderr pipes"
            )
        stdout_capture = _BoundedBytes(self.max_stream_bytes)
        stderr_capture = _BoundedBytes(self.max_stream_bytes)
        marker_collector = _MarkerCollector(self.error_patterns)
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
            returncode = process.wait(timeout=self.timeout_seconds)
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
            self.max_log_bytes,
        )
        result = CompilerRunResult(
            command=command,
            source=source_path,
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
            raise ResourceCompilerTimeoutError(
                f"Resource Compiler exceeded {self.timeout_seconds:g} seconds",
                result,
            )
        if any(thread.is_alive() for thread in threads) or capture_errors:
            raise ResourceCompilerCaptureError(
                "Resource Compiler output capture did not finish cleanly",
                result,
            )
        if result.returncode != 0:
            raise ResourceCompilerExitError(
                f"Resource Compiler exited with status {result.returncode}",
                result,
            )
        if result.error_markers:
            raise ResourceCompilerReportedError(
                "Resource Compiler reported an error despite a zero exit status",
                result,
            )

        missing = []
        stale = []
        for output_asset, output_path in zip(output_assets, output_paths):
            verified_path = self.target.game_path(output_asset)
            if verified_path != output_path:
                raise UnsafeWorkshopPathError(
                    f"Compiled output changed resolution during compilation: {output_asset}"
                )
            state = _FileState.read(verified_path)
            if state is None:
                missing.append(verified_path)
                continue
            previous = before[output_path]
            if previous == state:
                stale.append(verified_path)
                continue
            if state.mtime_ns + self.freshness_tolerance_ns < start_wall_ns:
                stale.append(verified_path)
        if missing:
            raise ResourceCompilerOutputMissingError(missing, result)
        if stale:
            raise ResourceCompilerOutputStaleError(stale, result)
        return result
