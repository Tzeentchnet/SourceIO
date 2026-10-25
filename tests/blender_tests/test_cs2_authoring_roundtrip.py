from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import bpy
import numpy as np
import pytest

from SourceIO.blender_bindings.exporting.source2.cs2_authoring import (
    execute_cs2_authoring,
    recompile_cs2_manifest,
)
from SourceIO.library.source2.authoring import Source2AuthoringRequest
from SourceIO.library.source2.resource_types.compiled_material_resource import (
    CompiledMaterialResource,
)
from SourceIO.library.source2.resource_types.compiled_model_resource import (
    CompiledModelResource,
)
from SourceIO.library.source2.resource_types.compiled_texture_resource import (
    CompiledTextureResource,
)
from SourceIO.library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    ContentRelativePath,
    ResourceCompilerError,
    WorkshopTargetProfile,
)
from SourceIO.library.utils import FileBuffer


def _configured_root() -> Path | None:
    value = os.environ.get("SOURCEIO_CS2_WORKSHOP_ROOT")
    return Path(value) if value else None


def _remove_fixture(path: Path, parent: Path, addon_name: str):
    resolved_parent = parent.resolve()
    resolved = path.resolve(strict=False)
    if resolved.parent != resolved_parent or resolved.name != addon_name:
        raise AssertionError(f"Refusing unsafe fixture cleanup: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _compiled_resource(resource_type, path: Path):
    with FileBuffer(path) as buffer:
        return resource_type.from_buffer(buffer, path)


@pytest.mark.skipif(
    _configured_root() is None,
    reason="set SOURCEIO_CS2_WORKSHOP_ROOT to run the installed CS2 authoring gate",
)
def test_blender_authored_cs2_static_bundle_compiles_and_reimports():
    addon_name = f"sourceio_cs2_authoring_{uuid.uuid4().hex}"
    target = WorkshopTargetProfile(
        CS2_WORKSHOP_LAYOUT,
        _configured_root(),
        addon_name,
    )
    try:
        bpy.ops.wm.read_homefile(use_empty=True)
        mesh = bpy.data.meshes.new("sourceio_mesh")
        mesh.from_pydata(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            [],
            [(0, 1, 2)],
        )
        mesh.update()
        uv = mesh.uv_layers.new(name="UVMap")
        uv.data[0].uv = (0.0, 0.0)
        uv.data[1].uv = (1.0, 0.0)
        uv.data[2].uv = (0.0, 1.0)
        material = bpy.data.materials.new("sourceio_slot")
        material.use_nodes = True
        principled = material.node_tree.nodes["Principled BSDF"]
        principled.inputs["Base Color"].default_value = (0.25, 0.5, 0.75, 1.0)
        principled.inputs["Metallic"].default_value = 0.0
        principled.inputs["Roughness"].default_value = 0.5
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

        request = Source2AuthoringRequest(
            target,
            "sourceio",
            model_directory=ContentRelativePath("models/sourceio_authoring"),
            material_directory=ContentRelativePath("materials/sourceio_authoring"),
            texture_directory=ContentRelativePath(
                "materials/sourceio_authoring/textures"
            ),
            compile_after_export=True,
        )
        try:
            result = execute_cs2_authoring(bpy.context, request)
        except ResourceCompilerError as exc:
            log = f"\n{exc.result.log}" if exc.result is not None else ""
            pytest.fail(f"{exc}{log}")

        assert len(result.execution.compiler_results) == 2
        model_path = target.game_path(
            ContentRelativePath("models/sourceio_authoring/sourceio.vmdl_c")
        )
        material_path = target.game_path(
            ContentRelativePath("materials/sourceio_authoring/sourceio_slot.vmat_c")
        )
        assert model_path.is_file()
        assert material_path.is_file()

        compiled_material = _compiled_resource(
            CompiledMaterialResource,
            material_path,
        )
        assert compiled_material.data_block["m_shaderName"] == "csgo_complex.vfx"
        assert np.allclose(
            compiled_material.get_vector_property("g_vColorTint"),
            (0.25, 0.5, 0.75, 0.0),
            atol=1e-6,
        )
        source_normal = np.array((0.25, 0.8, 1.0))
        signed_normal = source_normal * 2.0 - 1.0
        compiled_normal = (
            signed_normal / np.linalg.norm(signed_normal) + 1.0
        ) / 2.0
        for parameter, expected_rgb in (
            ("g_tColor", (1.0, 1.0, 1.0)),
            ("g_tNormal", compiled_normal),
        ):
            texture_reference = compiled_material.get_texture_property(
                parameter,
                None,
            )
            assert texture_reference
            texture_asset = ContentRelativePath(str(texture_reference))
            if not texture_asset.name.endswith("_c"):
                texture_asset = texture_asset.compiled()
            texture_path = target.game_path(texture_asset)
            assert texture_path.is_file()
            compiled_texture = _compiled_resource(
                CompiledTextureResource,
                texture_path,
            )
            pixels, size = compiled_texture.get_texture_data()
            assert size == (4, 4)
            assert np.allclose(pixels[..., :3], expected_rgb, atol=0.03)

        compiled_model = _compiled_resource(CompiledModelResource, model_path)
        assert compiled_model.get_name().endswith(
            "models/sourceio_authoring/sourceio.vmdl"
        )
        recompile_results = recompile_cs2_manifest(
            target.install_root,
            addon_name,
            result.execution.manifest_path,
        )
        assert len(recompile_results) == 2

        bpy.ops.wm.read_homefile(use_empty=True)
        operator_result = bpy.ops.sourceio.vmdl(
            "EXEC_DEFAULT",
            filepath=str(model_path),
            directory=str(model_path.parent) + os.sep,
            files=[{"name": model_path.name}],
            import_animations=False,
            import_materials=True,
            discover_resources=True,
        )
        assert "FINISHED" in operator_result
        imported_meshes = [item for item in bpy.data.objects if item.type == "MESH"]
        if len(imported_meshes) != 1:
            compiler_logs = "\n".join(
                item.log for item in result.execution.compiler_results
            )
            pytest.fail(
                "Compiled model did not re-import one mesh.\n"
                f"{compiler_logs}"
            )
        imported_mesh = imported_meshes[0]
        assert len(imported_mesh.data.polygons) == 1
        assert imported_mesh.dimensions.x == pytest.approx(1.0, abs=0.02)
        assert imported_mesh.dimensions.y == pytest.approx(1.0, abs=0.02)
        assert len(imported_mesh.data.uv_layers) == 1
        assert all(
            normal.vector.length == pytest.approx(1.0, abs=1e-5)
            for normal in imported_mesh.data.corner_normals
        )
        assert any(
            material.get("source2_shader") == "csgo_complex.vfx"
            and material.get("source2_shader_supported") is True
            for material in bpy.data.materials
        )
    finally:
        _remove_fixture(
            target.content_addon_root,
            target.content_addons_root,
            addon_name,
        )
        _remove_fixture(
            target.game_addon_root,
            target.game_addons_root,
            addon_name,
        )
