from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bpy

from SourceIO.blender_bindings import cli
from SourceIO.blender_bindings.exporting.source1 import (
    execute_source1_authoring,
    extract_source1_authoring_models,
)
from SourceIO.library.source1.authoring import (
    Source1ArtifactRoot,
    Source1AuthoringMaterial,
    Source1AuthoringRequest,
    Source1AuthoringTarget,
)
from SourceIO.library.source2.authoring import TextureRole
from SourceIO.library.utils.math_utilities import (
    SOURCE1_HAMMER_UNIT_TO_METERS,
)


class Source1AuthoringAdapterTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="sourceio_source1_authoring_test_"
        )
        root = Path(self.temporary_directory.name)
        game_root = root / "tf"
        content_root = root / "sdk_content"
        game_root.mkdir()
        content_root.mkdir()
        (game_root / "gameinfo.txt").write_text(
            "GameInfo {}\n",
            encoding="utf-8",
        )
        self.target = Source1AuthoringTarget(game_root, content_root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def mesh_object(name="crate"):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [],
            [(0, 1, 2)],
        )
        mesh.update()
        uv = mesh.uv_layers.new(name="UVMap")
        uv.data[0].uv = (0.0, 0.0)
        uv.data[1].uv = (1.0, 0.0)
        uv.data[2].uv = (0.0, 1.0)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    @staticmethod
    def principled_material(name="crate_material"):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        return material

    def request(self, **kwargs):
        return Source1AuthoringRequest(self.target, "crate", **kwargs)

    @staticmethod
    def _outputs(result, target):
        values = {}
        for path in result.execution.written_paths:
            try:
                relative = path.relative_to(target.content_root)
                key = f"content:{relative.as_posix()}"
            except ValueError:
                relative = path.relative_to(target.game_root)
                key = f"game:{relative.as_posix()}"
            values[key] = path.read_bytes()
        return values

    def test_evaluated_geometry_uses_source1_units_without_fbx(self):
        obj = self.mesh_object()
        material = self.principled_material()
        obj.data.materials.append(material)
        modifier = obj.modifiers.new("mirror", "MIRROR")
        modifier.use_axis[0] = True
        obj.location.x = 2.0
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_source1_authoring_models(
            bpy.context,
            self.request(),
        )

        self.assertFalse(report.has_errors)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].fbx_payload, b"")
        self.assertGreater(
            len(models[0].document.meshes[0].vertices),
            len(obj.data.vertices),
        )
        positions = {
            tuple(round(component, 5) for component in vertex.position)
            for vertex in models[0].document.meshes[0].vertices
        }
        self.assertIn(
            (round(2.0 / SOURCE1_HAMMER_UNIT_TO_METERS, 5), 0.0, 0.0),
            positions,
        )
        self.assertEqual(
            models[0].document.metadata["source_unit_scale"],
            SOURCE1_HAMMER_UNIT_TO_METERS,
        )
        self.assertEqual(tuple(bpy.context.selected_objects), (obj,))
        self.assertIs(bpy.context.view_layer.objects.active, obj)
        self.assertEqual(obj.name, "crate")
        self.assertEqual(obj.data.name, "crate")
        self.assertNotIn("__SOURCEIO_AUTHORING__", bpy.data.collections)

    def test_normal_map_and_explicit_phong_are_authored(self):
        obj = self.mesh_object()
        material = self.principled_material()
        material["sourceio_source1_phong"] = True
        material["sourceio_source1_phong_exponent"] = 16.0
        material["sourceio_source1_phong_boost"] = 2.0
        material["sourceio_source1_phong_tint"] = (0.25, 0.5, 0.75)
        material["sourceio_source1_phong_fresnel_ranges"] = (0.0, 0.5, 1.0)
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("normal", width=1, height=1, alpha=True)
        image.colorspace_settings.is_data = True
        image.colorspace_settings.name = "Non-Color"
        image.pixels[:] = (0.25, 0.2, 1.0, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(
            texture.outputs["Color"],
            normal_map.inputs["Color"],
        )
        material.node_tree.links.new(
            normal_map.outputs["Normal"],
            principled.inputs["Normal"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_source1_authoring_models(
            bpy.context,
            self.request(),
        )

        self.assertFalse(report.has_errors)
        authored = models[0].materials[0]
        self.assertIsInstance(authored, Source1AuthoringMaterial)
        self.assertEqual(authored.phong.exponent, 16.0)
        normal = next(
            texture for texture in authored.textures
            if texture.role == TextureRole.NORMAL
        )
        self.assertEqual(normal.payload[18:22], bytes((255, 204, 64, 255)))
        self.assertAlmostEqual(image.pixels[1], 0.2)

        result = execute_source1_authoring(
            bpy.context,
            self.request(),
        )
        vmt = self.target.game_path(
            "materials/models/sourceio/crate/crate_material.vmt"
        ).read_text(encoding="utf-8")
        self.assertIn('"$phong" "1"', vmt)
        self.assertIn('"$phongexponent" "16"', vmt)
        self.assertEqual(len(result.execution.compiler_results), 0)

    def test_imported_vmt_provenance_writes_a_local_patch(self):
        obj = self.mesh_object()
        material = self.principled_material()
        material["full_path"] = (
            "materials/models/props/imported.vmt?sourceio-shader=bvlg"
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        result = execute_source1_authoring(
            bpy.context,
            self.request(),
        )

        vmt = self.target.game_path(
            "materials/models/sourceio/crate/crate_material.vmt"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            vmt,
            '"Patch"\n'
            "{\n"
            '    "include" "models/props/imported.vmt"\n'
            "}\n",
        )
        self.assertEqual(
            sum(
                artifact.root == Source1ArtifactRoot.CONTENT
                and artifact.kind.value == "texture_source"
                for artifact in result.plan.artifacts
            ),
            0,
        )

    def test_operator_cli_and_service_write_identical_outputs(self):
        obj = self.mesh_object()
        obj.data.materials.append(self.principled_material())
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        first = execute_source1_authoring(
            bpy.context,
            self.request(),
        )
        expected = self._outputs(first, self.target)

        operator_result = bpy.ops.sourceio.source1_authoring_export(
            "EXEC_DEFAULT",
            game_root=str(self.target.game_root),
            content_root=str(self.target.content_root),
            asset_name="crate",
            overwrite=True,
        )
        self.assertEqual(operator_result, {"FINISHED"})
        self.assertEqual(self._outputs(first, self.target), expected)

        cli_result = cli.execute([
            "export-source1",
            "--game-root",
            str(self.target.game_root),
            "--content-root",
            str(self.target.content_root),
            "--asset",
            "crate",
            "--overwrite",
        ])
        self.assertEqual(cli_result, 0)
        self.assertEqual(self._outputs(first, self.target), expected)


if __name__ == "__main__":
    unittest.main()
