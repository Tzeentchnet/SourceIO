from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from SourceIO.library.shared.app_id import SteamAppId
from SourceIO.library.source2.export import (
    MeshFace,
    MeshVertex,
    ModelDocExporter,
    ModelDocument,
    StaticMesh,
)
from SourceIO.library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    DOTA2_WORKSHOP_LAYOUT,
    ContentRelativePath,
    ResourceCompilerExitError,
    ResourceCompilerOutputMissingError,
    ResourceCompilerOutputStaleError,
    ResourceCompilerReportedError,
    ResourceCompilerRunner,
    UnsafeWorkshopPathError,
    WorkshopTargetError,
    WorkshopTargetProfile,
)
from SourceIO.library.utils.s2_keyvalues import KeyValues


def _create_install(root: Path, layout):
    for relative in (
            layout.content_addons_path,
            layout.game_addons_path,
            layout.game_directory_path,
    ):
        root.joinpath(*relative.parts).mkdir(parents=True, exist_ok=True)
    gameinfo = root.joinpath(*layout.gameinfo_path.parts)
    gameinfo.write_text('"GameInfo"\n{\n}\n', encoding="utf-8")
    compiler = root.joinpath(*layout.resource_compiler_path.parts)
    compiler.parent.mkdir(parents=True, exist_ok=True)
    compiler.write_bytes(b"MZ")


def _target(tmp_path: Path, *, cs2: bool = False) -> WorkshopTargetProfile:
    layout = CS2_WORKSHOP_LAYOUT if cs2 else DOTA2_WORKSHOP_LAYOUT
    _create_install(tmp_path, layout)
    constructor = WorkshopTargetProfile.cs2 if cs2 else WorkshopTargetProfile.dota2
    return constructor(tmp_path, "sourceio_test")


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


def _compiler_source(target: WorkshopTargetProfile) -> ContentRelativePath:
    source = ContentRelativePath("models/sourceio/probe.vmdl")
    path = target.content_path(source)
    path.parent.mkdir(parents=True)
    path.write_text("source", encoding="utf-8")
    return source


def test_dota_and_cs2_profiles_validate_installed_layouts(tmp_path):
    dota_root = tmp_path / "dota"
    cs2_root = tmp_path / "cs2"
    dota = _target(dota_root)
    cs2 = _target(cs2_root, cs2=True)

    assert dota.app_id == SteamAppId.DOTA_2
    assert dota.content_addon_root == (
        dota_root / "content/dota_addons/sourceio_test"
    ).resolve()
    assert dota.game_addon_root == (
        dota_root / "game/dota_addons/sourceio_test"
    ).resolve()
    assert dota.game_directory == (dota_root / "game/dota").resolve()
    assert dota.gameinfo_path == (dota_root / "game/dota/gameinfo.gi").resolve()
    assert cs2.app_id == SteamAppId.COUNTER_STRIKE_GO
    assert cs2.content_addon_root == (
        cs2_root / "content/csgo_addons/sourceio_test"
    ).resolve()
    assert cs2.game_addon_root == (
        cs2_root / "game/csgo_addons/sourceio_test"
    ).resolve()


def test_profile_requires_tools_and_safe_normalized_paths(tmp_path):
    _create_install(tmp_path, DOTA2_WORKSHOP_LAYOUT)
    (tmp_path / "game/bin/win64/resourcecompiler.exe").unlink()
    with pytest.raises(WorkshopTargetError, match="resourcecompiler.exe"):
        WorkshopTargetProfile.dota2(tmp_path, "sourceio_test")

    (tmp_path / "game/bin/win64/resourcecompiler.exe").write_bytes(b"MZ")
    with pytest.raises(WorkshopTargetError, match="Addon name"):
        WorkshopTargetProfile.dota2(tmp_path, "../escape")

    target = WorkshopTargetProfile.dota2(tmp_path, "sourceio_test")
    assert ContentRelativePath(r"models\props\crate.vmdl").as_posix() == (
        "models/props/crate.vmdl"
    )
    absolute = target.content_path("models/props/crate.vmdl")
    assert target.content_relative(absolute) == ContentRelativePath(
        "models/props/crate.vmdl"
    )
    with pytest.raises(UnsafeWorkshopPathError):
        ContentRelativePath("../crate.vmdl")
    with pytest.raises(UnsafeWorkshopPathError):
        target.content_relative(tmp_path / "outside.vmdl")


def test_profile_drives_nested_modeldoc_mesh_reference(tmp_path):
    target = _target(tmp_path)
    output_directory = target.content_path("models/sourceio/nested")
    output_directory.mkdir(parents=True)
    document = ModelDocument(
        "crate",
        (StaticMesh(
            "crate",
            (
                MeshVertex((0.0, 0.0, 0.0)),
                MeshVertex((1.0, 0.0, 0.0)),
                MeshVertex((0.0, 1.0, 0.0)),
            ),
            (MeshFace((0, 1, 2), "materials/sourceio/crate.vmat"),),
        ),),
    )

    ModelDocExporter().export(
        document,
        output_directory,
        target_profile=target,
    )

    _, modeldoc = KeyValues.read_data(io.StringIO(
        (output_directory / "crate.vmdl").read_text(encoding="utf-8")
    ))
    render_mesh_list = next(
        child for child in modeldoc["rootNode"]["children"]
        if child["_class"] == "RenderMeshList"
    )
    assert render_mesh_list["children"][0]["filename"] == (
        "models/sourceio/nested/crate.dmx"
    )


def test_compiler_uses_argument_array_bounded_logs_and_fresh_output(tmp_path):
    target = _target(tmp_path)
    source = _compiler_source(target)
    output = target.expected_output(source)
    output.parent.mkdir(parents=True)
    process = _FakeProcess(
        stdout=b"x" * 256 + b"\nOK: 1 compiled, 0 failed, 0 skipped\n",
        stderr=b"diagnostic\n",
        on_wait=lambda: output.write_bytes(b"compiled"),
    )
    factory = _FakePopenFactory(process)
    runner = ResourceCompilerRunner(
        target,
        max_stream_bytes=64,
        max_log_bytes=80,
        popen_factory=factory,
    )

    result = runner.compile(source, arguments=("-fshallow2", "-nop4"))

    command, options = factory.calls[0]
    assert command == [
        str(target.resource_compiler),
        "-game",
        str(target.game_directory),
        "-i",
        str(target.content_path(source)),
        "-fshallow2",
        "-nop4",
    ]
    assert options["shell"] is False
    assert result.returncode == 0
    assert result.stdout_truncated
    assert result.stdout_bytes > 64
    assert "0 failed" in result.stdout
    assert not result.error_markers
    assert len(result.log.encode("utf-8")) <= 80
    assert result.log_truncated


def test_compiler_rejects_nonzero_and_error_markers(tmp_path):
    target = _target(tmp_path)
    source = _compiler_source(target)

    nonzero = ResourceCompilerRunner(
        target,
        popen_factory=_FakePopenFactory(_FakeProcess(returncode=9)),
    )
    with pytest.raises(ResourceCompilerExitError) as exit_error:
        nonzero.compile(source)
    assert exit_error.value.result.returncode == 9

    marker = ResourceCompilerRunner(
        target,
        max_stream_bytes=32,
        popen_factory=_FakePopenFactory(_FakeProcess(
            stdout=b"ERROR: synthetic failure\n" + b"x" * 256,
        )),
    )
    with pytest.raises(ResourceCompilerReportedError) as marker_error:
        marker.compile(source)
    assert marker_error.value.result.stdout_truncated
    assert marker_error.value.result.error_markers == (
        "stdout: ERROR: synthetic failure",
    )

    empty_mesh = ResourceCompilerRunner(
        target,
        popen_factory=_FakePopenFactory(_FakeProcess(
            stdout=(
                b"RESOURCE COMPILE WARNING: RenderMeshFile: prop : "
                b"No meshes selected\n"
            ),
        )),
    )
    with pytest.raises(ResourceCompilerReportedError) as empty_mesh_error:
        empty_mesh.compile(source)
    assert empty_mesh_error.value.result.error_markers == (
        "stdout: RESOURCE COMPILE WARNING: "
        "RenderMeshFile: prop : No meshes selected",
    )


def test_compiler_rejects_missing_and_stale_outputs(tmp_path):
    target = _target(tmp_path)
    source = _compiler_source(target)
    missing_runner = ResourceCompilerRunner(
        target,
        popen_factory=_FakePopenFactory(_FakeProcess(
            stdout=b"OK: 0 compiled, 0 failed, 0 skipped\n"
        )),
    )
    with pytest.raises(ResourceCompilerOutputMissingError):
        missing_runner.compile(source)

    output = target.expected_output(source)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"old")
    os.utime(output, (1, 1))
    stale_runner = ResourceCompilerRunner(
        target,
        popen_factory=_FakePopenFactory(_FakeProcess(
            stdout=b"OK: 0 compiled, 0 failed, 1 skipped\n"
        )),
    )
    with pytest.raises(ResourceCompilerOutputStaleError):
        stale_runner.compile(source)


def test_compiler_rejects_path_escapes_and_managed_argument_overrides(tmp_path):
    target = _target(tmp_path)
    source = _compiler_source(target)
    process = _FakeProcess()
    factory = _FakePopenFactory(process)
    runner = ResourceCompilerRunner(target, popen_factory=factory)
    outside = tmp_path / "outside.vmdl"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(UnsafeWorkshopPathError):
        runner.compile(outside)
    with pytest.raises(UnsafeWorkshopPathError):
        runner.compile(source, expected_outputs=("../escape.vmdl_c",))
    with pytest.raises(ValueError, match="managed"):
        runner.compile(source, arguments=("-game", "other"))
    assert not factory.calls


class _ChunkedStream:
    def __init__(self, *chunks: bytes):
        self._chunks = list(chunks)

    def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        pass


def test_compiler_marker_scan_does_not_treat_chunk_overlap_as_line_start():
    from SourceIO.library.source2.workshop.compiler import (
        DEFAULT_ERROR_PATTERNS,
        _BoundedBytes,
        _MarkerCollector,
        _drain_stream,
    )

    # One long line whose last 1024 characters begin at " failed to", which only matches at a line start.
    tail = " failed to load " + "y" * (1024 - len(" failed to load "))
    mid_line = _MarkerCollector(DEFAULT_ERROR_PATTERNS)
    errors = []
    _drain_stream(
        _ChunkedStream(("a" * 100 + tail).encode(), b"\nok\n"),
        _BoundedBytes(4096), mid_line, "stdout", errors,
    )
    assert errors == []
    assert mid_line.markers == []

    # A real marker split across chunks is still found, without the sentinel in its snippet.
    split = _MarkerCollector(DEFAULT_ERROR_PATTERNS)
    _drain_stream(
        _ChunkedStream(b"compiling\nERR", b"OR: bad mesh\n"),
        _BoundedBytes(4096), split, "stdout", errors,
    )
    assert split.markers == ["stdout: ERROR: bad mesh"]
