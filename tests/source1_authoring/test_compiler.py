from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from SourceIO.library.source1.authoring import (
    Source1ArtifactKind,
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringManifestError,
    Source1AuthoringRequest,
    Source1AuthoringService,
    Source1AuthoringTarget,
    Source1BackendOutput,
    Source1CompileOutput,
    Source1CompileStep,
    Source1CompilerKind,
    Source1CompilerLaunchError,
    Source1CompilerOutputStaleError,
    Source1CompilerReportedError,
    Source1CompilerRunResult,
    Source1CompilerRunner,
    Source1CompilerToolchain,
)


class _FakeProcess:
    def __init__(
            self,
            *,
            stdout: bytes = b"",
            stderr: bytes = b"",
            returncode: int = 0,
            on_wait=None,
    ):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.on_wait = on_wait
        self.killed = False

    def wait(self, timeout=None):
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback()
        return self.returncode

    def kill(self):
        self.killed = True


class _FakePopenFactory:
    def __init__(self, process: _FakeProcess):
        self.process = process
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.process


def _target(
        tmp_path: Path,
        *,
        tools: bool = True,
) -> tuple[Source1AuthoringTarget, Path]:
    game_root = tmp_path / "tf"
    content_root = tmp_path / "tf_content"
    tool_root = tmp_path / "bin"
    game_root.mkdir()
    content_root.mkdir()
    (game_root / "gameinfo.txt").write_text("GameInfo {}\n", encoding="utf-8")
    if tools:
        tool_root.mkdir()
        (tool_root / "vtex.exe").write_bytes(b"MZ")
        (tool_root / "studiomdl.exe").write_bytes(b"MZ")
    return Source1AuthoringTarget(game_root, content_root), tool_root


def _step(
        compiler: Source1CompilerKind,
        source: str,
        outputs: tuple[str, ...],
        *,
        arguments: tuple[str, ...] = (),
) -> Source1CompileStep:
    return Source1CompileStep(
        compiler,
        Source1ArtifactRoot.CONTENT,
        source,
        tuple(
            Source1CompileOutput(Source1ArtifactRoot.GAME, output)
            for output in outputs
        ),
        arguments,
    )


def _write_source(target: Source1AuthoringTarget, relative: str) -> Path:
    path = target.content_path(relative)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source")
    return path


def test_vtex_runner_sets_vproject_and_managed_arguments(tmp_path):
    target, tool_root = _target(tmp_path)
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/models/sourceio/crate.tga",
        ("materials/models/sourceio/crate.vtf",),
    )
    source = _write_source(target, step.source.as_posix())
    output = target.game_path(step.expected_outputs[0].path)
    output.parent.mkdir(parents=True)
    process = _FakeProcess(
        stdout=b"SUCCESS: Vtf file created\n",
        on_wait=lambda: output.write_bytes(b"vtf"),
    )
    factory = _FakePopenFactory(process)
    runner = Source1CompilerRunner(
        target,
        tool_root=tool_root,
        popen_factory=factory,
    )

    result = runner.compile(step)

    command, options = factory.calls[0]
    assert command == [
        str(tool_root / "vtex.exe"),
        "-quiet",
        "-nopause",
        "-nop4",
        "-mkdir",
        "-game",
        str(target.game_root),
        str(source),
    ]
    assert options["cwd"] == str(tool_root)
    assert options["env"]["VPROJECT"] == str(target.game_root)
    assert options["shell"] is False
    assert result.expected_outputs == (output,)


def test_vtex_runner_accepts_only_up_to_date_unchanged_output(tmp_path):
    target, tool_root = _target(tmp_path)
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/models/sourceio/crate.tga",
        ("materials/models/sourceio/crate.vtf",),
    )
    source = _write_source(target, step.source.as_posix())
    os.utime(source, ns=(100_000_000_000, 100_000_000_000))
    output = target.game_path(step.expected_outputs[0].path)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"current")
    os.utime(output, ns=(200_000_000_000, 200_000_000_000))
    runner = Source1CompilerRunner(
        target,
        tool_root=tool_root,
        freshness_tolerance_seconds=0,
        popen_factory=_FakePopenFactory(_FakeProcess()),
    )

    runner.compile(step)

    os.utime(source, ns=(300_000_000_000, 300_000_000_000))
    stale_runner = Source1CompilerRunner(
        target,
        tool_root=tool_root,
        freshness_tolerance_seconds=0,
        popen_factory=_FakePopenFactory(_FakeProcess()),
    )
    with pytest.raises(Source1CompilerOutputStaleError):
        stale_runner.compile(step)


def test_studiomdl_runner_verifies_all_required_outputs(tmp_path):
    target, tool_root = _target(tmp_path)
    step = _step(
        Source1CompilerKind.STUDIOMDL,
        "modelsrc/sourceio/crate.qc",
        (
            "models/sourceio/crate.mdl",
            "models/sourceio/crate.vvd",
            "models/sourceio/crate.dx90.vtx",
        ),
    )
    source = _write_source(target, step.source.as_posix())
    outputs = tuple(
        target.game_path(output.path)
        for output in step.expected_outputs
    )

    def create_outputs():
        for output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(output.suffix.encode("ascii"))

    factory = _FakePopenFactory(_FakeProcess(on_wait=create_outputs))
    runner = Source1CompilerRunner(
        target,
        tool_root=tool_root,
        popen_factory=factory,
    )

    result = runner.compile(step)

    command, _ = factory.calls[0]
    assert command == [
        str(tool_root / "studiomdl.exe"),
        "-nop4",
        "-game",
        str(target.game_root),
        str(source),
    ]
    assert result.expected_outputs == outputs


def test_tool_discovery_and_managed_arguments_fail_explicitly(tmp_path):
    target, missing_root = _target(tmp_path, tools=False)

    with pytest.raises(Source1CompilerLaunchError, match="vtex.exe"):
        Source1CompilerToolchain.discover(target, missing_root)

    missing_root.mkdir()
    (missing_root / "vtex.exe").write_bytes(b"MZ")
    (missing_root / "studiomdl.exe").write_bytes(b"MZ")
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/sourceio/crate.tga",
        ("materials/sourceio/crate.vtf",),
        arguments=("-game", "other"),
    )
    _write_source(target, step.source.as_posix())
    runner = Source1CompilerRunner(
        target,
        tool_root=missing_root,
        popen_factory=_FakePopenFactory(_FakeProcess()),
    )

    with pytest.raises(ValueError, match="managed"):
        runner.compile(step)

    redirected = _step(
        Source1CompilerKind.VTEX,
        step.source.as_posix(),
        ("materials/sourceio/crate.vtf",),
        arguments=("-outdir", str(tmp_path / "elsewhere")),
    )
    with pytest.raises(ValueError, match="managed"):
        runner.compile(redirected)


def _backend(step: Source1CompileStep) -> Source1BackendOutput:
    return Source1BackendOutput.create(
        (
            Source1AuthoringArtifact(
                step.source_root,
                step.source,
                Source1ArtifactKind.TEXTURE_SOURCE,
                b"source",
            ),
        ),
        (step,),
        ("models/sourceio/crate.mdl",),
    )


def test_service_compile_and_recompile_validate_manifest_hashes(
        tmp_path,
        monkeypatch,
):
    target, _ = _target(tmp_path)
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/sourceio/crate.tga",
        ("materials/sourceio/crate.vtf",),
    )
    request = Source1AuthoringRequest(
        target,
        "crate",
        compile_after_export=True,
    )
    calls = []

    class RecordingRunner:
        def __init__(self, configured_target, *, tool_root=None):
            assert configured_target is target
            calls.append(("init", tool_root))

        def compile(self, configured_step):
            calls.append(("compile", configured_step))
            return configured_step.compiler.value

    monkeypatch.setattr(
        "SourceIO.library.source1.authoring.service.Source1CompilerRunner",
        RecordingRunner,
    )
    service = Source1AuthoringService()
    plan = service.plan(request, _backend(step))

    result = service.execute(plan, tool_root=tmp_path / "tools")

    assert result.compiler_results == ("vtex",)
    assert calls[-1] == ("compile", step)
    assert service.recompile(
        target,
        result.manifest_path,
        tool_root=tmp_path / "tools",
    ) == ("vtex",)

    target.content_path(step.source).write_bytes(b"changed")
    with pytest.raises(
            Source1AuthoringManifestError,
            match="changed since export",
    ):
        service.recompile(target, result.manifest_path)


def test_compile_failure_keeps_sources_and_writes_bounded_log(
        tmp_path,
        monkeypatch,
):
    target, _ = _target(tmp_path)
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/sourceio/crate.tga",
        ("materials/sourceio/crate.vtf",),
    )
    request = Source1AuthoringRequest(
        target,
        "crate",
        compile_after_export=True,
    )
    result = Source1CompilerRunResult(
        command=("vtex.exe",),
        source=target.content_path(step.source),
        expected_outputs=(target.game_path(step.expected_outputs[0].path),),
        returncode=0,
        stdout="ERROR: synthetic\n",
        stderr="",
        stdout_bytes=17,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        log="[stdout]\nERROR: synthetic\n[stderr]\n",
        log_truncated=False,
        error_markers=("stdout: ERROR: synthetic",),
        duration_seconds=0.01,
    )

    class FailingRunner:
        def __init__(self, _target, *, tool_root=None):
            pass

        def compile(self, _step):
            raise Source1CompilerReportedError("synthetic failure", result)

    monkeypatch.setattr(
        "SourceIO.library.source1.authoring.service.Source1CompilerRunner",
        FailingRunner,
    )
    service = Source1AuthoringService()
    plan = service.plan(request, _backend(step))

    with pytest.raises(Source1CompilerReportedError):
        service.execute(plan)

    assert target.content_path(step.source).read_bytes() == b"source"
    assert target.content_path(
        "sourceio/logs/crate.compile.log"
    ).read_text(encoding="utf-8") == result.log


def test_tool_discovery_failure_writes_diagnostic_log(tmp_path):
    target, missing_root = _target(tmp_path, tools=False)
    step = _step(
        Source1CompilerKind.VTEX,
        "materialsrc/sourceio/crate.tga",
        ("materials/sourceio/crate.vtf",),
    )
    request = Source1AuthoringRequest(
        target,
        "crate",
        compile_after_export=True,
    )
    plan = Source1AuthoringService().plan(request, _backend(step))

    with pytest.raises(Source1CompilerLaunchError):
        Source1AuthoringService().execute(plan, tool_root=missing_root)

    log = target.content_path(
        "sourceio/logs/crate.compile.log"
    ).read_text(encoding="utf-8")
    assert log.startswith("CompilerLaunchError: ")
    assert "vtex.exe" in log
