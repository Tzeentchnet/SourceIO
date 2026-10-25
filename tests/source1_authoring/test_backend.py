from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.library.source1.authoring import (
    Source1ArtifactKind,
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringBackend,
    Source1AuthoringManifest,
    Source1AuthoringManifestError,
    Source1AuthoringRequest,
    Source1AuthoringService,
    Source1AuthoringTarget,
    Source1AuthoringTargetError,
    serialize_qc,
    serialize_smd,
    serialize_vmt,
    serialize_vtex_settings,
    stage_source1_artifacts,
)
from SourceIO.library.source2.authoring import (
    AuthoringMaterial,
    AuthoringTexture,
    ExtractedAuthoringModel,
    MaterialAlphaMode,
    TextureRole,
    solid_color_tga,
)
from SourceIO.library.source2.export import (
    CornerData,
    ExportBlockedError,
    MeshFace,
    MeshVertex,
    ModelDocument,
    StaticMesh,
)
from SourceIO.library.source2.workshop import ContentRelativePath


FIXTURES = Path(__file__).parents[1] / "fixtures" / "source1_authoring"


def _target(tmp_path: Path, name: str = "tf") -> Source1AuthoringTarget:
    game_root = tmp_path / name
    content_root = tmp_path / f"{name}_content"
    game_root.mkdir()
    content_root.mkdir()
    (game_root / "gameinfo.txt").write_text("GameInfo {}\n", encoding="utf-8")
    return Source1AuthoringTarget(game_root, content_root)


def _document(*, material: str = "crate", uv: bool = True) -> ModelDocument:
    texcoords = (
        ((0.0, 1.0),),
        ((1.0, 1.0),),
        ((0.0, 0.0),),
    )
    vertices = tuple(
        MeshVertex(
            position,
            CornerData(
                normal=(0.0, 0.0, 1.0),
                texcoords=texcoords[index] if uv else (),
            ),
        )
        for index, position in enumerate((
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        ))
    )
    return ModelDocument(
        "crate",
        (StaticMesh(
            "crate_mesh",
            vertices,
            (MeshFace((0, 1, 2), material),),
        ),),
    )


def _material(
        *,
        metallic: float = 0.0,
        roughness: float = 0.5,
        alpha_mode: MaterialAlphaMode = MaterialAlphaMode.MASK,
) -> AuthoringMaterial:
    return AuthoringMaterial(
        "crate",
        base_color=(0.25, 0.5, 0.75, 1.0),
        metallic=metallic,
        roughness=roughness,
        alpha_mode=alpha_mode,
        alpha_cutoff=0.4,
        double_sided=True,
        textures=(
            AuthoringTexture(
                "crate_base_color",
                TextureRole.BASE_COLOR,
                solid_color_tga((0.2, 0.4, 0.6, 0.5)),
                extension=".tga",
                srgb=True,
            ),
            AuthoringTexture(
                "crate_alpha",
                TextureRole.ALPHA,
                solid_color_tga((0.5, 0.5, 0.5, 1.0)),
                extension=".tga",
            ),
            AuthoringTexture(
                "crate_normal",
                TextureRole.NORMAL,
                solid_color_tga((0.5, 0.5, 1.0, 1.0)),
                extension=".tga",
            ),
        ),
    )


def _model(
        *,
        material: AuthoringMaterial | None = None,
        document: ModelDocument | None = None,
) -> ExtractedAuthoringModel:
    value = material or _material()
    return ExtractedAuthoringModel(
        "crate",
        document or _document(),
        b"",
        (value,),
    )


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_serializers_match_source1_golden_contract():
    material = _material()
    color_path = ContentRelativePath(
        "materialsrc/models/sourceio/crate/crate_base_color.tga"
    )
    normal_path = ContentRelativePath(
        "materialsrc/models/sourceio/crate/crate_normal.tga"
    )

    assert serialize_smd(_document(), {"crate"}) == _fixture("sourceio.smd")
    assert serialize_qc(
        ContentRelativePath("models/sourceio/crate/crate.mdl"),
        "crate.smd",
        ContentRelativePath("materials/models/sourceio/crate"),
    ) == _fixture("sourceio.qc")
    assert serialize_vmt(
        material,
        color_path,
        normal_texture=normal_path,
    ) == _fixture("sourceio.vmt")
    assert serialize_vtex_settings(
        TextureRole.BASE_COLOR,
        alpha_mode=MaterialAlphaMode.MASK,
    ) == _fixture("sourceio_base_color.txt")
    assert serialize_vtex_settings(
        TextureRole.NORMAL,
    ) == _fixture("sourceio_normal.txt")


def test_backend_plans_dual_root_editable_bundle(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(target, "crate")

    output = Source1AuthoringBackend().build(request, (_model(),))

    assert not output.report.has_errors
    assert output.model_paths == (
        ContentRelativePath("models/sourceio/crate/crate.mdl"),
    )
    assert [
        (artifact.root.value, artifact.path.as_posix())
        for artifact in output.artifacts
    ] == [
        ("content", "materialsrc/models/sourceio/crate/crate_base_color.tga"),
        ("content", "materialsrc/models/sourceio/crate/crate_base_color.txt"),
        ("content", "materialsrc/models/sourceio/crate/crate_normal.tga"),
        ("content", "materialsrc/models/sourceio/crate/crate_normal.txt"),
        ("content", "modelsrc/sourceio/crate/crate.qc"),
        ("content", "modelsrc/sourceio/crate/crate.smd"),
        ("game", "materials/models/sourceio/crate/crate.vmt"),
    ]
    assert all(
        "crate_alpha" not in artifact.path.as_posix()
        for artifact in output.artifacts
    )


def test_service_writes_manifest_report_and_artifacts(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(target, "crate")
    backend = Source1AuthoringBackend().build(request, (_model(),))
    service = Source1AuthoringService()

    plan = service.plan(request, backend)
    result = service.execute(plan)

    assert len(result.written_paths) == 9
    assert target.content_path(
        "modelsrc/sourceio/crate/crate.smd"
    ).read_text(encoding="utf-8") == _fixture("sourceio.smd")
    assert target.game_path(
        "materials/models/sourceio/crate/crate.vmt"
    ).read_text(encoding="utf-8") == _fixture("sourceio.vmt")
    manifest_text = target.content_path(result.manifest_path).read_text(
        encoding="utf-8"
    )
    assert str(tmp_path) not in manifest_text
    manifest = Source1AuthoringManifest.from_json(manifest_text)
    manifest.validate_target(target)
    assert manifest.to_json() == plan.manifest.to_json()
    data = json.loads(manifest_text)
    assert data["target"] == {
        "content_directory": "tf_content",
        "game_directory": "tf",
    }
    assert len(data["artifacts"]) == 8


def test_preflight_errors_block_every_write(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(target, "Bad Name")
    backend = Source1AuthoringBackend().build(request, (_model(),))
    plan = Source1AuthoringService().plan(request, backend)

    with pytest.raises(ExportBlockedError):
        plan.write()

    assert not target.content_path(
        "modelsrc/sourceio/Bad Name/crate.smd"
    ).exists()
    assert not target.content_path(
        "sourceio/manifests/Bad Name.sourceio.json"
    ).exists()


def test_existing_artifacts_are_protected_without_overwrite(tmp_path):
    target = _target(tmp_path)
    existing = target.game_path("materials/models/sourceio/crate/crate.vmt")
    existing.parent.mkdir(parents=True)
    existing.write_text("existing", encoding="utf-8")
    request = Source1AuthoringRequest(target, "crate")
    backend = Source1AuthoringBackend().build(request, (_model(),))

    plan = Source1AuthoringService().plan(request, backend)

    assert "authoring.output.exists" in {
        diagnostic.code for diagnostic in plan.report
    }
    with pytest.raises(ExportBlockedError):
        plan.write()
    assert existing.read_text(encoding="utf-8") == "existing"


def test_pbr_conversion_and_missing_uv_are_blocked(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(target, "crate")
    model = _model(
        material=_material(metallic=0.25, roughness=0.75),
        document=_document(uv=False),
    )

    output = Source1AuthoringBackend().build(request, (model,))

    assert {
        diagnostic.code for diagnostic in output.report
    } >= {
        "authoring.source1.material.pbr_conversion_unsupported",
        "authoring.source1.smd.invalid",
    }


def test_opaque_material_cannot_carry_effective_alpha(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(target, "crate")
    material = AuthoringMaterial(
        "crate",
        base_color=(1.0, 1.0, 1.0, 0.5),
        alpha_mode=MaterialAlphaMode.OPAQUE,
        textures=(
            AuthoringTexture(
                "crate_base_color",
                TextureRole.BASE_COLOR,
                solid_color_tga((1.0, 1.0, 1.0, 1.0)),
                extension=".tga",
            ),
        ),
    )

    output = Source1AuthoringBackend().build(
        request,
        (_model(material=material),),
    )

    assert "authoring.source1.material.alpha_mode_mismatch" in {
        diagnostic.code for diagnostic in output.report
    }


def test_blended_material_uses_translucent_not_alpha_test():
    material = _material(alpha_mode=MaterialAlphaMode.BLEND)

    value = serialize_vmt(
        material,
        ContentRelativePath(
            "materialsrc/models/sourceio/crate/crate_base_color.tga"
        ),
    )

    assert '"$translucent" "1"' in value
    assert "$alphatest" not in value


def test_material_source_and_runtime_directories_must_match(tmp_path):
    target = _target(tmp_path)
    request = Source1AuthoringRequest(
        target,
        "crate",
        material_source_directory=ContentRelativePath("materialsrc/sourceio"),
        material_directory=ContentRelativePath("materials/models/sourceio"),
    )
    plan = Source1AuthoringService().plan(
        request,
        Source1AuthoringBackend().build(request, (_model(),)),
    )

    assert "authoring.source1.material_directories.mismatch" in {
        diagnostic.code for diagnostic in plan.report
    }


def test_target_requires_existing_roots_and_gameinfo(tmp_path):
    game_root = tmp_path / "tf"
    content_root = tmp_path / "tf_content"
    game_root.mkdir()
    content_root.mkdir()

    with pytest.raises(Source1AuthoringTargetError, match="gameinfo.txt"):
        Source1AuthoringTarget(game_root, content_root)
    with pytest.raises(Source1AuthoringTargetError, match="unavailable"):
        Source1AuthoringTarget(tmp_path / "missing", content_root)


def test_manifest_target_identity_is_validated(tmp_path):
    first = _target(tmp_path, "tf")
    request = Source1AuthoringRequest(first, "crate")
    backend = Source1AuthoringBackend().build(request, (_model(),))
    manifest = Source1AuthoringManifest.build(
        request,
        backend.artifacts,
        backend.model_paths,
    )
    second = _target(tmp_path, "hl2")

    with pytest.raises(Source1AuthoringManifestError, match="does not match"):
        manifest.validate_target(second)


def test_artifact_root_is_typed():
    artifact = Source1AuthoringArtifact(
        "content",
        "sourceio/reports/crate.json",
        "report",
        "",
    )

    assert artifact.root is Source1ArtifactRoot.CONTENT
    assert artifact.kind is Source1ArtifactKind.REPORT


def test_staging_restores_overwritten_files_after_partial_failure(
        tmp_path,
        monkeypatch,
):
    target = _target(tmp_path)
    existing = target.game_path("materials/sourceio/existing.vmt")
    existing.parent.mkdir(parents=True)
    existing.write_text("original", encoding="utf-8")
    artifacts = (
        Source1AuthoringArtifact(
            Source1ArtifactRoot.GAME,
            "materials/sourceio/existing.vmt",
            Source1ArtifactKind.MATERIAL,
            "replacement",
        ),
        Source1AuthoringArtifact(
            Source1ArtifactRoot.CONTENT,
            "modelsrc/sourceio/new.smd",
            Source1ArtifactKind.MODEL_SOURCE,
            "new",
        ),
    )
    real_replace = __import__("os").replace

    def fail_new_source(source, destination):
        if Path(destination).name == "new.smd":
            raise OSError("synthetic commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        "SourceIO.library.shared.authoring.staging.os.replace",
        fail_new_source,
    )

    with pytest.raises(OSError, match="synthetic"):
        stage_source1_artifacts(target, artifacts, overwrite=True)

    assert existing.read_text(encoding="utf-8") == "original"
    assert not target.content_path("modelsrc/sourceio/new.smd").exists()
    assert not list(tmp_path.rglob("*.sourceio-stage"))
    assert not list(tmp_path.rglob("*.sourceio-backup"))
