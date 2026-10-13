from __future__ import annotations

import io
from pathlib import Path

from SourceIO.library.source2.authoring import (
    AuthoringMaterial,
    CS2AuthoringBackend,
    ExtractedAuthoringModel,
    Source2AuthoringRequest,
    solid_color_tga,
)
from SourceIO.library.source2.export import (
    CornerData,
    MeshFace,
    MeshVertex,
    ModelDocument,
    StaticMesh,
)
from SourceIO.library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    ContentRelativePath,
    Source2WorkshopLayout,
    WorkshopGame,
    WorkshopTargetProfile,
)
from SourceIO.library.utils.s2_keyvalues import KeyValues


def _target(tmp_path: Path) -> WorkshopTargetProfile:
    for path in (
        "content/csgo_addons",
        "game/csgo_addons",
        "game/csgo",
        "game/bin/win64",
    ):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "game/csgo/gameinfo.gi").write_text("GameInfo {}", encoding="utf-8")
    (tmp_path / "game/bin/win64/resourcecompiler.exe").write_bytes(b"compiler")
    return WorkshopTargetProfile(
        CS2_WORKSHOP_LAYOUT,
        tmp_path,
        "sourceio_test",
    )


def _model(material: AuthoringMaterial) -> ExtractedAuthoringModel:
    mesh = StaticMesh(
        "crate",
        (
            MeshVertex(
                (0.0, 0.0, 0.0),
                CornerData((0.0, 0.0, 1.0), texcoords=((0.0, 0.0),)),
            ),
            MeshVertex(
                (1.0, 0.0, 0.0),
                CornerData((0.0, 0.0, 1.0), texcoords=((1.0, 0.0),)),
            ),
            MeshVertex(
                (0.0, 1.0, 0.0),
                CornerData((0.0, 0.0, 1.0), texcoords=((0.0, 1.0),)),
            ),
        ),
        (MeshFace((0, 1, 2), material.name),),
    )
    return ExtractedAuthoringModel(
        "crate",
        ModelDocument("crate", (mesh,)),
        b"Kaydara FBX Binary  \x00",
        (material,),
    )


def test_cs2_backend_emits_fbx_modeldoc_vmat_and_source_texture(tmp_path):
    target = _target(tmp_path)
    material = AuthoringMaterial(
        "painted",
        base_color=(0.25, 0.5, 0.75, 1.0),
    )

    output = CS2AuthoringBackend().build(
        Source2AuthoringRequest(target, "crate"),
        (_model(material),),
    )
    files = {artifact.path.as_posix(): artifact.payload for artifact in output.artifacts}

    assert set(files) == {
        "materials/sourceio/painted.vmat",
        "materials/sourceio/textures/painted_base_color.tga",
        "models/sourceio/crate.fbx",
        "models/sourceio/crate.vmdl",
    }
    assert files["models/sourceio/crate.fbx"].startswith(b"Kaydara FBX Binary")
    _, modeldoc = KeyValues.read_data(
        io.StringIO(files["models/sourceio/crate.vmdl"])
    )
    render_mesh = next(
        child for child in modeldoc["rootNode"]["children"]
        if child["_class"] == "RenderMeshList"
    )["children"][0]
    assert render_mesh["filename"] == "models/sourceio/crate.fbx"
    assert render_mesh["import_filter"] == {
        "exclude_by_default": True,
        "exception_list": ["crate"],
    }
    assert modeldoc["rootNode"]["model_archetype"] == "static_prop_model"
    material_group = next(
        child for child in modeldoc["rootNode"]["children"]
        if child["_class"] == "MaterialGroupList"
    )["children"][0]
    assert material_group["remaps"] == [{
        "from": "painted",
        "to": "materials/sourceio/painted.vmat",
    }]
    vmat = files["materials/sourceio/painted.vmat"]
    assert 'shader "csgo_complex.vfx"' in vmat
    assert (
        'TextureColor "materials/sourceio/textures/painted_base_color.tga"'
        in vmat
    )
    assert 'g_vColorTint "[0.250000 0.500000 0.750000 0.000000]"' in vmat
    assert files["materials/sourceio/textures/painted_base_color.tga"][2] == 2
    assert [step.source.as_posix() for step in output.compile_steps] == [
        "materials/sourceio/painted.vmat",
        "models/sourceio/crate.vmdl",
    ]


def test_existing_material_is_remapped_without_reauthoring(tmp_path):
    target = _target(tmp_path)
    material = AuthoringMaterial(
        "existing",
        existing_resource=ContentRelativePath("materials/props/existing.vmat"),
    )

    output = CS2AuthoringBackend().build(
        Source2AuthoringRequest(target, "crate"),
        (_model(material),),
    )
    paths = {artifact.path.as_posix() for artifact in output.artifacts}

    assert paths == {
        "models/sourceio/crate.fbx",
        "models/sourceio/crate.vmdl",
    }
    modeldoc = next(
        artifact.payload for artifact in output.artifacts
        if artifact.path.suffix == ".vmdl"
    )
    assert "materials/props/existing.vmat" in modeldoc


def test_solid_color_tga_is_deterministic():
    first = solid_color_tga((0.0, 0.5, 1.0, 1.0))
    second = solid_color_tga((0.0, 0.5, 1.0, 1.0))

    assert first == second
    assert first[2] == 2
    assert len(first) == 18 + 4 * 4 * 4


def test_backend_rejects_non_cs2_target(tmp_path):
    target = _target(tmp_path)
    dota_layout = Source2WorkshopLayout(
        WorkshopGame.DOTA_2,
        "Test Dota",
        CS2_WORKSHOP_LAYOUT.app_id,
        target.layout.content_addons_path,
        target.layout.game_addons_path,
        target.layout.game_directory_path,
        target.layout.resource_compiler_path,
    )
    dota_target = WorkshopTargetProfile(
        dota_layout,
        target.install_root,
        target.addon_name,
    )

    output = CS2AuthoringBackend().build(
        Source2AuthoringRequest(dota_target, "crate"),
        (_model(AuthoringMaterial("painted")),),
    )

    assert output.report.has_errors
    assert "authoring.cs2.target.invalid" in {
        diagnostic.code for diagnostic in output.report
    }
