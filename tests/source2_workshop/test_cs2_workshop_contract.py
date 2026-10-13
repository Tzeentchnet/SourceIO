from __future__ import annotations

import io
import json
import os
import shutil
import struct
import uuid
from pathlib import Path, PurePosixPath

import pytest

from SourceIO.library.source2 import (
    CompiledMaterialResource,
    CompiledModelResource,
    CompiledTextureResource,
    ContentRelativePath,
    ResourceCompilerError,
    ResourceCompilerRunner,
    WorkshopTargetError,
    WorkshopTargetProfile,
    stage_text_outputs,
)
from SourceIO.library.source2.workshop import CS2_WORKSHOP_LAYOUT
from SourceIO.library.utils.s2_keyvalues import KeyValues


_CS2_ROOT_VARIABLE = "SOURCEIO_CS2_WORKSHOP_ROOT"
_FIXTURE_PREFIX = "sourceio_cs2_m9_"
_FIXTURE_DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "source2_workshop"
    / "cs2_static_authoring"
)


def _contract() -> dict:
    return json.loads(
        (_FIXTURE_DIRECTORY / "contract.json").read_text(encoding="utf-8")
    )


def _child(node: dict, class_name: str) -> dict:
    return next(
        child for child in node["children"]
        if child["_class"] == class_name
    )


def _fixture_tga() -> bytes:
    width = height = 4
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0,
        0,
        2,
        0,
        0,
        0,
        0,
        0,
        width,
        height,
        32,
        0x28,
    )
    pixels = bytearray()
    colors = ((36, 91, 214, 255), (230, 126, 34, 255))
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = colors[(x + y) % 2]
            pixels.extend((blue, green, red, alpha))
    return header + bytes(pixels)


def _remove_exact_fixture_root(
        fixture_root: Path,
        addons_root: Path,
        fixture_name: str,
):
    resolved_parent = addons_root.resolve(strict=True)
    resolved_fixture = fixture_root.resolve(strict=False)
    if (
            resolved_fixture.parent != resolved_parent
            or resolved_fixture.name != fixture_name
            or not fixture_name.startswith(_FIXTURE_PREFIX)
    ):
        raise RuntimeError(f"Refusing unsafe fixture cleanup: {resolved_fixture}")
    if os.path.lexists(fixture_root):
        if fixture_root.is_symlink():
            raise RuntimeError(
                f"Refusing to recursively clean a fixture symlink: {fixture_root}"
            )
        shutil.rmtree(resolved_fixture)


def _compile_or_fail(
        runner: ResourceCompilerRunner,
        source: ContentRelativePath,
        arguments: tuple[str, ...],
):
    try:
        return runner.compile(source, arguments=arguments)
    except ResourceCompilerError as exc:
        log = f"\n{exc.result.log}" if exc.result is not None else ""
        pytest.fail(f"{exc}{log}")


def test_cs2_static_authoring_profile_contract():
    contract = CS2_WORKSHOP_LAYOUT.static_authoring_contract

    assert contract is not None
    assert CS2_WORKSHOP_LAYOUT.content_addons_path == ContentRelativePath(
        "content/csgo_addons"
    )
    assert CS2_WORKSHOP_LAYOUT.game_addons_path == ContentRelativePath(
        "game/csgo_addons"
    )
    assert CS2_WORKSHOP_LAYOUT.game_directory_path == ContentRelativePath(
        "game/csgo"
    )
    assert (
        contract.modeldoc_version,
        contract.model_archetype,
        contract.render_mesh_class,
        contract.render_mesh_suffix,
        contract.render_mesh_uses_import_filter,
        contract.compiled_render_mesh_is_embedded,
    ) == (
        36,
        "static_prop_model",
        "RenderMeshFile",
        ".fbx",
        True,
        True,
    )
    assert (
        contract.material_shader,
        contract.source_texture_suffix,
        contract.source_texture_is_compiler_input,
        contract.compiled_texture_suffix,
    ) == ("csgo_complex.vfx", ".tga", False, ".vtex_c")
    assert contract.default_material_group_class == "DefaultMaterialGroup"
    assert contract.compiler_arguments == ("-fshallow2", "-nop4")


def test_cs2_static_authoring_golden_contract():
    contract = _contract()
    paths = {
        name: ContentRelativePath(path)
        for name, path in contract["paths"].items()
    }

    assert contract["schema"] == "sourceio.cs2-static-authoring-contract"
    assert contract["schema_version"] == 1
    assert contract["addon_layout"] == {
        "content": "content/csgo_addons/{addon}",
        "game": "game/csgo_addons/{addon}",
    }
    assert paths["render_mesh"].suffix == ".fbx"
    assert paths["source_texture"].suffix == ".tga"
    assert contract["compile_inputs"] == [
        paths["material"].as_posix(),
        paths["modeldoc"].as_posix(),
    ]
    assert contract["expected_primary_outputs"] == [
        paths["material"].compiled().as_posix(),
        paths["modeldoc"].compiled().as_posix(),
    ]

    modeldoc_text = (_FIXTURE_DIRECTORY / "sourceio.vmdl").read_text(
        encoding="utf-8"
    )
    assert "format:modeldoc36:" in modeldoc_text
    _, modeldoc = KeyValues.read_data(io.StringIO(modeldoc_text))
    root = modeldoc["rootNode"]
    render_mesh = _child(root, "RenderMeshList")["children"][0]
    assert render_mesh == {
        "_class": "RenderMeshFile",
        "name": "sourceio_mesh",
        "filename": paths["render_mesh"].as_posix(),
        "import_scale": 1.0,
        "import_filter": {
            "exclude_by_default": True,
            "exception_list": [contract["fbx_mesh"]],
        },
    }
    assert root["model_archetype"] == "static_prop_model"
    material_group = _child(root, "MaterialGroupList")["children"][0]
    assert material_group["_class"] == "DefaultMaterialGroup"
    assert material_group["remaps"] == [{
        "from": contract["fbx_material"],
        "to": paths["material"].as_posix(),
    }]

    material_text = (_FIXTURE_DIRECTORY / "sourceio.vmat").read_text(
        encoding="utf-8"
    )
    assert 'shader "csgo_complex.vfx"' in material_text
    assert (
        f'TextureColor "{paths["source_texture"].as_posix()}"'
        in material_text
    )
    render_mesh_text = (_FIXTURE_DIRECTORY / "sourceio.fbx").read_text(
        encoding="ascii"
    )
    assert "FBXVersion: 7400" in render_mesh_text
    assert f'Geometry::{contract["fbx_mesh"]}", "Mesh"' in render_mesh_text
    assert f'Material::{contract["fbx_material"]}", ""' in render_mesh_text
    assert "PolygonVertexIndex: *3 {" in render_mesh_text
    assert "a: 0,1,-3" in render_mesh_text


def test_cs2_workshop_static_contract_roundtrip():
    configured_root = os.environ.get(_CS2_ROOT_VARIABLE)
    if not configured_root:
        pytest.skip(
            f"CS2 Workshop contract round-trip is opt-in; set "
            f"{_CS2_ROOT_VARIABLE} to the CS2 installation containing "
            "content/ and game/"
        )

    fixture_name = f"{_FIXTURE_PREFIX}{uuid.uuid4().hex}"
    try:
        target = WorkshopTargetProfile.cs2(configured_root, fixture_name)
    except WorkshopTargetError as exc:
        pytest.skip(f"CS2 Workshop tools are unavailable: {exc}")

    authoring = target.static_authoring_contract
    assert authoring is not None
    fixture_contract = _contract()
    paths = {
        name: ContentRelativePath(path)
        for name, path in fixture_contract["paths"].items()
    }
    compile_inputs = tuple(
        ContentRelativePath(path)
        for path in fixture_contract["compile_inputs"]
    )
    roots = (target.content_addon_root, target.game_addon_root)
    for root in roots:
        if os.path.lexists(root):
            pytest.fail(
                f"Refusing to reuse an existing disposable fixture root: {root}"
            )

    try:
        for root in roots:
            root.mkdir()

        stage_text_outputs(target.content_addon_root, {
            paths["modeldoc"]: (
                _FIXTURE_DIRECTORY / "sourceio.vmdl"
            ).read_text(encoding="utf-8"),
            paths["render_mesh"]: (
                _FIXTURE_DIRECTORY / "sourceio.fbx"
            ).read_text(encoding="ascii"),
            paths["material"]: (
                _FIXTURE_DIRECTORY / "sourceio.vmat"
            ).read_text(encoding="utf-8"),
        })
        target.content_path(paths["source_texture"]).write_bytes(_fixture_tga())

        runner = ResourceCompilerRunner(
            target,
            timeout_seconds=180.0,
            max_stream_bytes=1024 * 1024,
            max_log_bytes=1024 * 1024,
        )
        results = tuple(
            _compile_or_fail(runner, source, authoring.compiler_arguments)
            for source in compile_inputs
        )
        assert all(result.returncode == 0 for result in results)
        assert all(not result.error_markers for result in results)
        for source, result in zip(compile_inputs, results):
            assert result.command == (
                str(target.resource_compiler),
                "-game",
                str(target.game_directory),
                "-i",
                str(target.content_path(source)),
                *authoring.compiler_arguments,
            )

        for expected in fixture_contract["expected_primary_outputs"]:
            assert target.game_path(ContentRelativePath(expected)).is_file()

        material_path = paths["material"].compiled()
        material = CompiledMaterialResource.from_buffer(
            target.game_path(material_path).read_bytes(),
            material_path.as_posix(),
        )
        used_textures = material.get_used_textures()
        texture_reference = str(
            used_textures[fixture_contract["associated_texture_parameter"]]
        )
        referenced_texture = PurePosixPath(texture_reference)
        source_texture = paths["source_texture"].path
        assert referenced_texture.parent == source_texture.parent
        assert referenced_texture.name.startswith(
            f"{source_texture.stem}_tga_"
        )
        assert referenced_texture.suffix == ".vtex"

        compiled_texture_path = ContentRelativePath(f"{texture_reference}_c")
        assert compiled_texture_path.suffix == authoring.compiled_texture_suffix
        texture = CompiledTextureResource.from_buffer(
            target.game_path(compiled_texture_path).read_bytes(),
            compiled_texture_path.as_posix(),
        )
        pixels, resolution = texture.get_texture_data(0)
        assert resolution == (4, 4)
        assert tuple(pixels.shape) == (4, 4, 4)

        model_path = paths["modeldoc"].compiled()
        model = CompiledModelResource.from_buffer(
            target.game_path(model_path).read_bytes(),
            model_path.as_posix(),
        )
        assert not any(
            str(reference).endswith(".vmesh")
            for reference in model.get_child_resources()
        )
        reconstructed = model.to_model_document()
        assert len(reconstructed.meshes) == 1
        mesh = reconstructed.meshes[0]
        assert len(mesh.vertices) == 3
        assert len(mesh.faces) == 1
        assert all(vertex.corner.normal is not None for vertex in mesh.vertices)
        assert all(len(vertex.corner.texcoords) == 1 for vertex in mesh.vertices)
        assert mesh.faces[0].material == paths["material"].as_posix()
    finally:
        _remove_exact_fixture_root(
            target.content_addon_root,
            target.content_addons_root,
            fixture_name,
        )
        _remove_exact_fixture_root(
            target.game_addon_root,
            target.game_addons_root,
            fixture_name,
        )
