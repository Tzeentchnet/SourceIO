from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
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
    run_compiler,
)
from .models import (
    Source1ArtifactRoot,
    Source1CompileStep,
    Source1CompilerKind,
)
from .target import Source1AuthoringTarget


Source1CompilerError = CompilerError
Source1CompilerLaunchError = CompilerLaunchError
Source1CompilerTimeoutError = CompilerTimeoutError
Source1CompilerCaptureError = CompilerCaptureError
Source1CompilerExitError = CompilerExitError
Source1CompilerReportedError = CompilerReportedError
Source1CompilerOutputMissingError = CompilerOutputMissingError
Source1CompilerOutputStaleError = CompilerOutputStaleError
Source1CompilerRunResult = CompilerRunResult


@dataclass(frozen=True, slots=True)
class Source1CompilerToolchain:
    root: Path
    vtex: Path
    studiomdl: Path

    @classmethod
    def discover(
            cls,
            target: Source1AuthoringTarget,
            tool_root: str | os.PathLike[str] | None = None,
    ) -> Source1CompilerToolchain:
        if not isinstance(target, Source1AuthoringTarget):
            raise TypeError("target must be a Source1AuthoringTarget")
        configured = tool_root
        if configured is None:
            configured = os.environ.get("SOURCEIO_SOURCE1_TOOL_ROOT")
        if configured is not None:
            candidates = (Path(configured),)
        else:
            candidates = (
                target.game_root.parent / "bin",
                target.game_root / "bin",
            )
        checked = []
        for candidate in candidates:
            root = candidate.expanduser().resolve(strict=False)
            checked.append(root)
            vtex = root / "vtex.exe"
            studiomdl = root / "studiomdl.exe"
            if root.is_dir() and vtex.is_file() and studiomdl.is_file():
                return cls(root, vtex, studiomdl)
        locations = ", ".join(str(path) for path in checked)
        raise Source1CompilerLaunchError(
            "Source 1 compiler tools are unavailable; expected vtex.exe and "
            f"studiomdl.exe together in: {locations}"
        )


def _target_path(
        target: Source1AuthoringTarget,
        root: Source1ArtifactRoot,
        path,
) -> Path:
    if root == Source1ArtifactRoot.CONTENT:
        return target.content_path(path)
    if root == Source1ArtifactRoot.GAME:
        return target.game_path(path)
    raise ValueError(f"Unsupported Source 1 artifact root: {root}")


class Source1CompilerRunner:
    _RESERVED_ARGUMENTS = frozenset((
        "-game",
        "-mkdir",
        "-nop4",
        "-nopause",
        "-outdir",
        "-quiet",
        "-vproject",
    ))

    def __init__(
            self,
            target: Source1AuthoringTarget,
            *,
            tool_root: str | os.PathLike[str] | None = None,
            timeout_seconds: float = 300.0,
            max_stream_bytes: int = 256 * 1024,
            max_log_bytes: int = 512 * 1024,
            freshness_tolerance_seconds: float = 2.0,
            error_patterns: Sequence[str | Pattern[str]] = DEFAULT_ERROR_PATTERNS,
            popen_factory: Callable[..., object] | None = None,
    ):
        if not isinstance(target, Source1AuthoringTarget):
            raise TypeError("target must be a Source1AuthoringTarget")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_stream_bytes <= 0 or max_log_bytes <= 0:
            raise ValueError("Compiler output bounds must be positive")
        if freshness_tolerance_seconds < 0:
            raise ValueError("freshness_tolerance_seconds cannot be negative")
        self.target = target
        self.toolchain = Source1CompilerToolchain.discover(target, tool_root)
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

    def compile(self, step: Source1CompileStep) -> CompilerRunResult:
        if not isinstance(step, Source1CompileStep):
            raise TypeError("step must be a Source1CompileStep")
        source_path = _target_path(
            self.target,
            step.source_root,
            step.source,
        )
        if not source_path.is_file():
            raise FileNotFoundError(f"Compiler input is unavailable: {source_path}")
        output_paths = tuple(
            _target_path(self.target, output.root, output.path)
            for output in step.expected_outputs
        )
        arguments = self._validate_arguments(step.arguments)
        if step.compiler == Source1CompilerKind.VTEX:
            executable = self.toolchain.vtex
            managed_arguments = (
                "-quiet",
                "-nopause",
                "-nop4",
                "-mkdir",
                "-game",
                str(self.target.game_root),
            )
            tool_name = "VTEX"
        elif step.compiler == Source1CompilerKind.STUDIOMDL:
            executable = self.toolchain.studiomdl
            managed_arguments = (
                "-nop4",
                "-game",
                str(self.target.game_root),
            )
            tool_name = "StudioMDL"
        else:
            raise ValueError(f"Unsupported Source 1 compiler: {step.compiler}")

        command = (
            str(executable),
            *managed_arguments,
            *arguments,
            str(source_path),
        )
        environment = os.environ.copy()
        environment["VPROJECT"] = str(self.target.game_root)
        result = run_compiler(
            command,
            source_path,
            output_paths,
            tool_name=tool_name,
            cwd=self.toolchain.root,
            environment=environment,
            timeout_seconds=self.timeout_seconds,
            max_stream_bytes=self.max_stream_bytes,
            max_log_bytes=self.max_log_bytes,
            freshness_tolerance_seconds=self.freshness_tolerance_seconds,
            allow_unchanged_outputs_if_up_to_date=True,
            error_patterns=self.error_patterns,
            popen_factory=self._popen_factory,
        )
        for output, output_path in zip(step.expected_outputs, output_paths):
            if _target_path(self.target, output.root, output.path) != output_path:
                raise Source1CompilerLaunchError(
                    "Compiled output changed resolution during compilation: "
                    f"{output.path}"
                )
        return result
