from __future__ import annotations

import os
import shutil
import struct
import uuid
from pathlib import Path

import pytest

from SourceIO.library.source2 import (
    CompiledMaterialResource,
    CompiledModelResource,
    CompiledTextureResource,
    CornerData,
    MeshFace,
    MeshVertex,
    ModelDocExporter,
    ModelDocument,
    ResourceCompilerError,
    ResourceCompilerRunner,
    StaticMesh,
    WorkshopTargetError,
    WorkshopTargetProfile,
    stage_text_outputs,
)
from SourceIO.library.source2.workshop import ContentRelativePath


_DOTA_ROOT_VARIABLE = "SOURCEIO_DOTA2_WORKSHOP_ROOT"
_FIXTURE_PREFIX = "sourceio_roundtrip_"


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
    colors = ((32, 64, 255, 255), (255, 64, 32, 255))
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = colors[(x + y) % 2]
            pixels.extend((blue, green, red, alpha))
    return header + bytes(pixels)


def _fixture_vtex(texture_path: ContentRelativePath) -> str:
    return f"""<!-- dmx encoding keyvalues2_noids 1 format vtex 1 -->
"CDmeVtex"
{{
    "m_inputTextureArray" "element_array"
    [
        "CDmeInputTexture"
        {{
            "m_name" "string" "0"
            "m_fileName" "string" "{texture_path.as_posix()}"
            "m_colorSpace" "string" "srgb"
            "m_typeString" "string" "2D"
        }}
    ]
    "m_outputTypeString" "string" "2D"
    "m_outputFormat" "string" "DXT5"
    "m_textureOutputChannelArray" "element_array"
    [
        "CDmeTextureOutputChannel"
        {{
            "m_inputTextureArray" "string_array"
            [
                "0"
            ]
            "m_srcChannels" "string" "rgba"
            "m_dstChannels" "string" "rgba"
            "m_mipAlgorithm" "CDmeImageProcessor"
            {{
                "m_algorithm" "string" ""
                "m_stringArg" "string" ""
                "m_vFloat4Arg" "vector4" "0 0 0 0"
            }}
            "m_outputColorSpace" "string" "srgb"
        }}
    ]
}}
"""


def _fixture_vmat(texture_path: ContentRelativePath) -> str:
    return f"""Layer0
{{
    shader "global_lit_simple.vfx"
    g_vColorTint "[1.000000 1.000000 1.000000 0.000000]"
    g_vTexCoordOffset "[0.000 0.000]"
    g_vTexCoordScale "[1.000 1.000]"
    TextureColor "{texture_path.as_posix()}"
}}
"""


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
            raise RuntimeError(f"Refusing to recursively clean a fixture symlink: {fixture_root}")
        shutil.rmtree(resolved_fixture)


def _compile_or_fail(
        runner: ResourceCompilerRunner,
        source: ContentRelativePath,
):
    try:
        return runner.compile(source, arguments=("-fshallow2", "-nop4"))
    except ResourceCompilerError as exc:
        log = f"\n{exc.result.log}" if exc.result is not None else ""
        pytest.fail(f"{exc}{log}")


def test_dota_workshop_static_fixture_roundtrip():
    configured_root = os.environ.get(_DOTA_ROOT_VARIABLE)
    if not configured_root:
        pytest.skip(
            f"Dota Workshop round-trip is opt-in; set {_DOTA_ROOT_VARIABLE} "
            "to the Dota 2 installation containing content/ and game/"
        )

    fixture_name = f"{_FIXTURE_PREFIX}{uuid.uuid4().hex}"
    try:
        target = WorkshopTargetProfile.dota2(configured_root, fixture_name)
    except WorkshopTargetError as exc:
        pytest.skip(f"Dota Workshop tools are unavailable: {exc}")

    roots = (target.content_addon_root, target.game_addon_root)
    for root in roots:
        if os.path.lexists(root):
            pytest.fail(f"Refusing to reuse an existing disposable fixture root: {root}")

    asset_directory = fixture_name
    texture_tga = ContentRelativePath(
        f"materials/{asset_directory}/sourceio_probe_color.tga"
    )
    texture_vtex = ContentRelativePath(
        f"materials/{asset_directory}/sourceio_probe_color.vtex"
    )
    material = ContentRelativePath(
        f"materials/{asset_directory}/sourceio_probe.vmat"
    )
    model = ContentRelativePath(
        f"models/{asset_directory}/sourceio_probe.vmdl"
    )

    try:
        for root in roots:
            root.mkdir()

        stage_text_outputs(target.content_addon_root, {
            texture_vtex: _fixture_vtex(texture_tga),
            material: _fixture_vmat(texture_tga),
        })
        texture_path = target.content_path(texture_tga)
        texture_path.write_bytes(_fixture_tga())

        document = ModelDocument(
            "sourceio_probe",
            (StaticMesh(
                "sourceio_probe",
                (
                    MeshVertex(
                        (0.0, 0.0, 0.0),
                        CornerData(
                            normal=(0.0, 0.0, 1.0),
                            texcoords=((0.0, 0.0),),
                        ),
                    ),
                    MeshVertex(
                        (16.0, 0.0, 0.0),
                        CornerData(
                            normal=(0.0, 0.0, 1.0),
                            texcoords=((1.0, 0.0),),
                        ),
                    ),
                    MeshVertex(
                        (0.0, 16.0, 0.0),
                        CornerData(
                            normal=(0.0, 0.0, 1.0),
                            texcoords=((0.0, 1.0),),
                        ),
                    ),
                ),
                (MeshFace((0, 1, 2), material.as_posix()),),
            ),),
        )
        model_directory = target.content_path(
            ContentRelativePath(model.parent)
        )
        model_directory.mkdir(parents=True)
        ModelDocExporter().export(
            document,
            model_directory,
            target_profile=target,
        )

        runner = ResourceCompilerRunner(
            target,
            timeout_seconds=180.0,
            max_stream_bytes=1024 * 1024,
            max_log_bytes=1024 * 1024,
        )
        results = tuple(
            _compile_or_fail(runner, source)
            for source in (texture_vtex, material, model)
        )
        assert all(result.returncode == 0 for result in results)
        assert all(not result.error_markers for result in results)

        expected_types = (
            (texture_vtex, CompiledTextureResource),
            (material, CompiledMaterialResource),
            (model, CompiledModelResource),
        )
        for source, expected_type in expected_types:
            compiled = source.compiled()
            output = target.game_path(compiled)
            resource = expected_type.from_buffer(
                output.read_bytes(),
                compiled.as_posix(),
            )
            assert isinstance(resource, expected_type)
            assert resource.header.blocks
            assert resource.parse_blocks()
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
