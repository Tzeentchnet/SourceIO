from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.library.shared.app_id import SteamAppId
from SourceIO.library.source2.authoring import (
    AuthoringArtifact,
    AuthoringArtifactKind,
    AuthoringBackendOutput,
    AuthoringManifestError,
    CompileStep,
    Source2AuthoringManifest,
    Source2AuthoringRequest,
    Source2AuthoringService,
    stage_authoring_artifacts,
    validate_request,
)
from SourceIO.library.source2.export import ExportBlockedError
from SourceIO.library.source2.workshop import (
    CompilerRunResult,
    ContentRelativePath,
    ResourceCompilerReportedError,
    Source2WorkshopLayout,
    WorkshopGame,
    WorkshopTargetProfile,
)


def _target(tmp_path: Path, addon: str = "sourceio_test") -> WorkshopTargetProfile:
    layout = Source2WorkshopLayout(
        WorkshopGame.COUNTER_STRIKE_2,
        "Test CS2",
        SteamAppId.COUNTER_STRIKE_GO,
        ContentRelativePath("content/csgo_addons"),
        ContentRelativePath("game/csgo_addons"),
        ContentRelativePath("game/csgo"),
    )
    for path in (
        "content/csgo_addons",
        "game/csgo_addons",
        "game/csgo",
        "game/bin/win64",
    ):
        (tmp_path / Path(path)).mkdir(parents=True, exist_ok=True)
    (tmp_path / "game/csgo/gameinfo.gi").write_text("GameInfo {}", encoding="utf-8")
    (tmp_path / "game/bin/win64/resourcecompiler.exe").write_bytes(b"compiler")
    return WorkshopTargetProfile(layout, tmp_path, addon)


def _backend() -> AuthoringBackendOutput:
    model = ContentRelativePath("models/sourceio/crate.vmdl")
    material = ContentRelativePath("materials/sourceio/crate.vmat")
    return AuthoringBackendOutput.create(
        (
            AuthoringArtifact(model, AuthoringArtifactKind.MODEL, "model"),
            AuthoringArtifact(material, AuthoringArtifactKind.MATERIAL, "material"),
        ),
        (
            CompileStep(material, (material.compiled(),)),
            CompileStep(model, (model.compiled(),)),
        ),
        (model,),
    )


def test_manifest_is_deterministic_and_contains_no_install_path(tmp_path):
    target = _target(tmp_path)
    request = Source2AuthoringRequest(target, "crate")
    plan = Source2AuthoringService().plan(request, _backend())

    first = plan.manifest.to_json()
    second = plan.manifest.to_json()

    assert first == second
    assert str(tmp_path) not in first
    data = json.loads(first)
    assert data["target"] == {
        "addon": "sourceio_test",
        "app_id": 730,
        "game": "cs2",
    }
    assert [item["path"] for item in data["artifacts"]] == [
        "materials/sourceio/crate.vmat",
        "models/sourceio/crate.vmdl",
        "sourceio/reports/crate.preflight.json",
    ]


def test_preflight_rejects_invalid_name_case_collisions_and_existing_files(tmp_path):
    target = _target(tmp_path)
    request = Source2AuthoringRequest(target, "Bad Name")
    duplicate_backend = AuthoringBackendOutput.create(
        (
            AuthoringArtifact(
                "models/sourceio/Crate.vmdl",
                AuthoringArtifactKind.MODEL,
                "one",
            ),
            AuthoringArtifact(
                "models/sourceio/crate.vmdl",
                AuthoringArtifactKind.MODEL,
                "two",
            ),
        ),
        (),
        (),
    )

    plan = Source2AuthoringService().plan(request, duplicate_backend)

    codes = {diagnostic.code for diagnostic in plan.report}
    assert "authoring.asset_name.invalid" in codes
    assert "authoring.path.collision" in codes
    name_diagnostic = next(
        item for item in plan.report
        if item.code == "authoring.asset_name.invalid"
    )
    assert name_diagnostic.details["suggestion"] == "bad_name"

    existing = target.content_path(ContentRelativePath("models/sourceio/crate.vmdl"))
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("authored", encoding="utf-8")
    protected = Source2AuthoringService().plan(
        Source2AuthoringRequest(target, "crate"),
        _backend(),
    )
    assert "authoring.output.exists" in {
        diagnostic.code for diagnostic in protected.report
    }


def test_preflight_errors_prevent_every_write(tmp_path):
    target = _target(tmp_path)
    plan = Source2AuthoringService().plan(
        Source2AuthoringRequest(target, "Bad Name"),
        _backend(),
    )

    with pytest.raises(ExportBlockedError):
        plan.write()

    assert not target.content_path(
        ContentRelativePath("models/sourceio/crate.vmdl")
    ).exists()
    assert not target.content_path(
        ContentRelativePath("sourceio/manifests/Bad Name.sourceio.json")
    ).exists()


def test_plan_writes_text_and_binary_atomically_and_protects_existing(tmp_path):
    target = _target(tmp_path)
    artifacts = (
        AuthoringArtifact("models/sourceio/crate.vmdl", AuthoringArtifactKind.MODEL, "model"),
        AuthoringArtifact(
            "materials/sourceio/textures/crate.png",
            AuthoringArtifactKind.TEXTURE_SOURCE,
            b"\x89PNG\r\n",
        ),
    )

    written = stage_authoring_artifacts(target, artifacts)

    assert len(written) == 2
    assert target.content_path(artifacts[0].path).read_text(encoding="utf-8") == "model"
    assert target.content_path(artifacts[1].path).read_bytes() == b"\x89PNG\r\n"
    with pytest.raises(FileExistsError):
        stage_authoring_artifacts(target, artifacts)


def test_recompile_rejects_changed_artifact_before_launch(tmp_path):
    target = _target(tmp_path)
    request = Source2AuthoringRequest(target, "crate")
    service = Source2AuthoringService()
    plan = service.plan(request, _backend())
    plan.write()
    model_path = target.content_path(ContentRelativePath("models/sourceio/crate.vmdl"))
    model_path.write_text("changed", encoding="utf-8")

    with pytest.raises(AuthoringManifestError, match="changed since export"):
        service.recompile(target, plan.manifest_path)


def test_manifest_target_must_match_configured_addon(tmp_path):
    first_target = _target(tmp_path, "sourceio_first")
    manifest = Source2AuthoringManifest.build(
        Source2AuthoringRequest(first_target, "crate"),
        _backend().artifacts,
        _backend().compile_steps,
        _backend().model_paths,
    )
    second_target = WorkshopTargetProfile(
        first_target.layout,
        tmp_path,
        "sourceio_second",
    )

    with pytest.raises(AuthoringManifestError, match="does not match"):
        manifest.validate_target(second_target)


def test_compile_failure_preserves_sources_and_writes_bounded_log(tmp_path, monkeypatch):
    target = _target(tmp_path)
    request = Source2AuthoringRequest(
        target,
        "crate",
        compile_after_export=True,
    )
    plan = Source2AuthoringService().plan(request, _backend())
    compiler_result = CompilerRunResult(
        command=("resourcecompiler.exe",),
        source=target.content_path(ContentRelativePath("materials/sourceio/crate.vmat")),
        expected_outputs=(
            target.game_path(ContentRelativePath("materials/sourceio/crate.vmat_c")),
        ),
        returncode=0,
        stdout="[FAIL] synthetic",
        stderr="",
        stdout_bytes=16,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        log="[stdout]\n[FAIL] synthetic\n[stderr]\n",
        log_truncated=False,
        error_markers=("stdout: [FAIL] synthetic",),
        duration_seconds=0.01,
    )

    class FailingRunner:
        def __init__(self, _target):
            pass

        def compile(self, *_args, **_kwargs):
            raise ResourceCompilerReportedError("synthetic failure", compiler_result)

    monkeypatch.setattr(
        "SourceIO.library.source2.authoring.service.ResourceCompilerRunner",
        FailingRunner,
    )

    with pytest.raises(ResourceCompilerReportedError):
        Source2AuthoringService().execute(plan)

    assert target.content_path(
        ContentRelativePath("models/sourceio/crate.vmdl")
    ).read_text(encoding="utf-8") == "model"
    assert target.content_path(
        ContentRelativePath("sourceio/logs/crate.compile.log")
    ).read_text(encoding="utf-8") == compiler_result.log


def test_request_validation_reports_directory_collisions(tmp_path):
    target = _target(tmp_path)
    report = validate_request(Source2AuthoringRequest(
        target,
        "crate",
        model_directory=ContentRelativePath("models/sourceio"),
        material_directory=ContentRelativePath("models/sourceio"),
    ))

    assert {item.code for item in report} == {"authoring.directory.collision"}
