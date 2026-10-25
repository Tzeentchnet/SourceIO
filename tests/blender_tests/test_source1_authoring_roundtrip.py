from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import bpy
import numpy as np
import pytest
from mathutils import Vector

from SourceIO.blender_bindings.exporting.source1 import (
    execute_source1_authoring,
    recompile_source1_manifest,
)
from SourceIO.blender_bindings.models import import_model
from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.source1.authoring import (
    Source1AuthoringRequest,
    Source1AuthoringTarget,
    Source1CompilerError,
)
from SourceIO.library.source1.vtf import load_texture
from SourceIO.library.utils.tiny_path import TinyPath


def _configured_root() -> Path | None:
    value = os.environ.get("SOURCEIO_SOURCE1_TOOLCHAIN_ROOT")
    return Path(value) if value else None


def _remove_asset_tree(path: Path, asset_name: str):
    resolved = path.resolve(strict=False)
    if resolved.name != asset_name or not asset_name.startswith("s1b_"):
        raise AssertionError(f"Refusing unsafe fixture cleanup: {resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)


def _mesh_positions(obj) -> np.ndarray:
    return np.asarray(
        [tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices],
        dtype=np.float64,
    )


@pytest.mark.skipif(
    _configured_root() is None,
    reason="set SOURCEIO_SOURCE1_TOOLCHAIN_ROOT to run the installed Source 1 authoring gate",
)
def test_blender_authored_source1_bundle_compiles_and_reimports():
    install_root = _configured_root().resolve()
    target = Source1AuthoringTarget(install_root / "tf", install_root)
    tool_root = install_root / "bin"
    asset_name = f"s1b_{uuid.uuid4().hex[:12]}"
    model_directory = target.game_path(f"models/sourceio/{asset_name}")
    material_directory = target.game_path(
        f"materials/models/sourceio/{asset_name}"
    )
    model_source_directory = target.content_path(
        f"modelsrc/sourceio/{asset_name}"
    )
    material_source_directory = target.content_path(
        f"materialsrc/models/sourceio/{asset_name}"
    )
    manifest_path = TinyPath(
        f"sourceio/manifests/{asset_name}.sourceio.json"
    )
    metadata_paths = (
        target.content_path(manifest_path),
        target.content_path(
            f"sourceio/reports/{asset_name}.preflight.json"
        ),
        target.content_path(
            f"sourceio/logs/{asset_name}.compile.log"
        ),
    )
    content_manager = None
    model_buffer = None
    try:
        bpy.ops.wm.read_homefile(use_empty=True)
        expected_positions = np.asarray(
            ((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (0.0, 0.25, 0.0)),
            dtype=np.float64,
        )
        mesh = bpy.data.meshes.new("sourceio_mesh")
        mesh.from_pydata(expected_positions, [], ((0, 1, 2),))
        mesh.update()
        uv = mesh.uv_layers.new(name="UVMap")
        expected_uvs = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
        for loop, value in zip(uv.data, expected_uvs):
            loop.uv = value

        material = bpy.data.materials.new("sourceio_slot")
        material.use_nodes = True
        material.surface_render_method = "DITHERED"
        material.alpha_threshold = 0.4
        principled = material.node_tree.nodes["Principled BSDF"]
        color_image = bpy.data.images.new(
            "sourceio_color",
            width=4,
            height=4,
            alpha=True,
        )
        color_image.pixels[:] = (0.25, 0.5, 0.75, 0.5) * 16
        color_texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        color_texture.image = color_image
        material.node_tree.links.new(
            color_texture.outputs["Color"],
            principled.inputs["Base Color"],
        )
        material.node_tree.links.new(
            color_texture.outputs["Alpha"],
            principled.inputs["Alpha"],
        )
        normal_image = bpy.data.images.new(
            "sourceio_normal",
            width=4,
            height=4,
            alpha=True,
        )
        normal_image.colorspace_settings.is_data = True
        normal_image.colorspace_settings.name = "Non-Color"
        normal_image.pixels[:] = (0.25, 0.2, 1.0, 1.0) * 16
        normal_texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        normal_texture.image = normal_image
        normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(
            normal_texture.outputs["Color"],
            normal_map.inputs["Color"],
        )
        material.node_tree.links.new(
            normal_map.outputs["Normal"],
            principled.inputs["Normal"],
        )
        mesh.materials.append(material)
        obj = bpy.data.objects.new("sourceio_mesh", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        request = Source1AuthoringRequest(
            target,
            asset_name,
            compile_after_export=True,
        )
        try:
            result = execute_source1_authoring(
                bpy.context,
                request,
                tool_root=tool_root,
            )
        except Source1CompilerError as exc:
            log = f"\n{exc.result.log}" if exc.result is not None else ""
            pytest.fail(f"{exc}{log}")

        assert len(result.execution.compiler_results) == 3
        for texture_name, expected_alpha in (
            ("sourceio_slot_base_color", 0.5),
            ("sourceio_slot_normal", 1.0),
        ):
            with (material_directory / f"{texture_name}.vtf").open("rb") as stream:
                pixels, height, width = load_texture(stream, normalized=False)
            assert (width, height) == (4, 4)
            assert np.allclose(
                pixels[..., 3],
                round(expected_alpha * 255),
                atol=1,
            )

        recompiled = recompile_source1_manifest(
            target.game_root,
            target.content_root,
            result.execution.manifest_path,
            tool_root=tool_root,
        )
        assert len(recompiled) == 3

        bpy.ops.wm.read_homefile(use_empty=True)
        content_manager = ContentManager()
        content_manager.clean()
        content_manager.scan_for_content(
            TinyPath(target.game_root / "gameinfo.txt")
        )
        model_path = TinyPath(
            f"models/sourceio/{asset_name}/{asset_name}.mdl"
        )
        model_buffer = content_manager.find_file(model_path)
        assert model_buffer is not None
        container = import_model(
            model_path,
            model_buffer,
            content_manager,
            ModelOptions.default(),
        )
        model_buffer.close()
        model_buffer = None
        assert container is not None
        imported_meshes = [
            imported for imported in container.objects
            if imported.type == "MESH"
        ]
        assert len(imported_meshes) == 1
        imported = imported_meshes[0]
        actual_positions = _mesh_positions(imported)
        assert actual_positions.shape == expected_positions.shape
        for expected in expected_positions:
            distances = np.linalg.norm(actual_positions - expected, axis=1)
            assert distances.min() == pytest.approx(0.0, abs=1e-5)
        imported_uvs = {
            tuple(round(component, 5) for component in loop.uv)
            for loop in imported.data.uv_layers.active.data
        }
        assert imported_uvs == set(expected_uvs)
        assert len(imported.data.materials) == 1
        imported_material = imported.data.materials[0]
        assert str(imported_material.get("full_path", "")).split("?", 1)[0] == (
            f"models/sourceio/{asset_name}/sourceio_slot"
        )
        assert Vector(imported.data.polygons[0].normal).dot(Vector((0, 0, 1))) > 0.99
    finally:
        if model_buffer is not None and not model_buffer.closed:
            model_buffer.close()
        if content_manager is not None:
            content_manager.clean()
        _remove_asset_tree(model_directory, asset_name)
        _remove_asset_tree(material_directory, asset_name)
        _remove_asset_tree(model_source_directory, asset_name)
        _remove_asset_tree(material_source_directory, asset_name)
        for path in metadata_paths:
            if path.is_file():
                path.unlink()
