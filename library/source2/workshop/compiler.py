from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Callable, Pattern

from ...shared.compiler import (
    DEFAULT_ERROR_PATTERNS,
    CompilerCaptureError,
    CompilerError,
    CompilerExitError,
    CompilerLaunchError,
    CompilerOutputMissingError,
    CompilerOutputStaleError,
    CompilerReportedError,
    CompilerRunResult,
    CompilerTimeoutError,
    _BoundedBytes,
    _MarkerCollector,
    _drain_stream,
    run_compiler,
)
from .target import (
    ContentRelativePath,
    UnsafeWorkshopPathError,
    WorkshopTargetProfile,
)


ResourceCompilerError = CompilerError
ResourceCompilerLaunchError = CompilerLaunchError
ResourceCompilerTimeoutError = CompilerTimeoutError
ResourceCompilerCaptureError = CompilerCaptureError
ResourceCompilerExitError = CompilerExitError
ResourceCompilerReportedError = CompilerReportedError
ResourceCompilerOutputMissingError = CompilerOutputMissingError
ResourceCompilerOutputStaleError = CompilerOutputStaleError


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
        self.target = target
        self.timeout_seconds = float(timeout_seconds)
        self.max_stream_bytes = int(max_stream_bytes)
        self.max_log_bytes = int(max_log_bytes)
        self.freshness_tolerance_seconds = float(freshness_tolerance_seconds)
        self.error_patterns = tuple(
            re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            if isinstance(pattern, str)
            else pattern
            for pattern in error_patterns
        )
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
            if isinstance(
                    expected_outputs,
                    (str, bytes, os.PathLike, ContentRelativePath),
            ):
                raise TypeError(
                    "expected_outputs must be an array of content-relative paths"
                )
            output_assets = tuple(
                ContentRelativePath(path) for path in expected_outputs
            )
            if not output_assets:
                raise ValueError("At least one expected output is required")
        if any(not output.name.endswith("_c") for output in output_assets):
            raise ValueError(
                "Expected Resource Compiler outputs must end with '_c'"
            )
        if len(set(output_assets)) != len(output_assets):
            raise ValueError("Expected Resource Compiler outputs must be unique")
        output_paths = tuple(
            self.target.game_path(path) for path in output_assets
        )

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
        result = run_compiler(
            command,
            source_path,
            output_paths,
            tool_name="Resource Compiler",
            cwd=self.target.resource_compiler.parent,
            timeout_seconds=self.timeout_seconds,
            max_stream_bytes=self.max_stream_bytes,
            max_log_bytes=self.max_log_bytes,
            freshness_tolerance_seconds=self.freshness_tolerance_seconds,
            error_patterns=self.error_patterns,
            popen_factory=self._popen_factory,
        )
        for output_asset, output_path in zip(output_assets, output_paths):
            if self.target.game_path(output_asset) != output_path:
                raise UnsafeWorkshopPathError(
                    "Compiled output changed resolution during compilation: "
                    f"{output_asset}"
                )
        return result
