from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bpy

from SourceIO.blender_bindings.exporting.source2.authoring import (
    extract_authoring_models,
    resolve_authoring_objects,
)
from SourceIO.blender_bindings.exporting.source2.cs2_authoring import (
    execute_cs2_authoring,
)
from SourceIO.blender_bindings import cli
from SourceIO.library.source2.authoring import (
    AuthoringGrouping,
    AuthoringScope,
    MaterialAlphaMode,
    Source2AuthoringRequest,
    TextureRole,
)
from SourceIO.library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    WorkshopTargetProfile,
)


class Source2AuthoringAdapterTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="sourceio_authoring_test_"
        )
        root = Path(self.temporary_directory.name)
        for path in (
            "content/csgo_addons",
            "game/csgo_addons",
            "game/csgo",
            "game/bin/win64",
        ):
            (root / path).mkdir(parents=True, exist_ok=True)
        (root / "game/csgo/gameinfo.gi").write_text("GameInfo {}", encoding="utf-8")
        (root / "game/bin/win64/resourcecompiler.exe").write_bytes(b"compiler")
        self.target = WorkshopTargetProfile(
            CS2_WORKSHOP_LAYOUT,
            root,
            "sourceio_test",
        )

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
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    @staticmethod
    def principled_material(name="crate_material"):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        return material

    def request(self, **kwargs):
        return Source2AuthoringRequest(self.target, "crate", **kwargs)

    def test_evaluated_export_restores_context_and_scene_data(self):
        obj = self.mesh_object()
        material = self.principled_material()
        obj.data.materials.append(material)
        modifier = obj.modifiers.new("mirror", "MIRROR")
        modifier.use_axis[0] = True
        obj.location.x = 2.0
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        exported_vertex_counts = []

        def exporter(path):
            exported_vertex_counts.extend(
                len(item.data.vertices)
                for item in bpy.context.selected_objects
                if item.type == "MESH"
            )
            exported = bpy.context.selected_objects[0]
            self.assertEqual(exported.name, "crate")
            self.assertEqual(exported.data.name, "crate_mesh")
            self.assertEqual(exported.data.materials[0].name, "crate_material")
            path.write_bytes(b"synthetic-fbx")

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=exporter,
        )

        self.assertFalse(report.has_errors)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].fbx_payload, b"synthetic-fbx")
        self.assertGreater(exported_vertex_counts[0], len(obj.data.vertices))
        self.assertEqual(len(obj.data.vertices), 3)
        self.assertEqual(obj.name, "crate")
        self.assertEqual(obj.data.name, "crate")
        self.assertEqual(material.name, "crate_material")
        self.assertEqual(
            models[0].document.meshes[0].metadata["blender_mesh"],
            "crate_mesh",
        )
        self.assertEqual(tuple(bpy.context.selected_objects), (obj,))
        self.assertIs(bpy.context.view_layer.objects.active, obj)
        self.assertNotIn("__SOURCEIO_AUTHORING__", bpy.data.collections)

    def test_default_exporter_produces_an_fbx_payload(self):
        obj = self.mesh_object()
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
        )

        self.assertFalse(report.has_errors)
        self.assertTrue(models[0].fbx_payload.startswith(b"Kaydara FBX Binary"))

    def test_default_fbx_payload_is_deterministic(self):
        obj = self.mesh_object()
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        first, first_report = extract_authoring_models(
            bpy.context,
            self.request(),
        )
        second, second_report = extract_authoring_models(
            bpy.context,
            self.request(),
        )

        self.assertFalse(first_report.has_errors)
        self.assertFalse(second_report.has_errors)
        self.assertEqual(first[0].fbx_payload, second[0].fbx_payload)

    def test_fbx_failure_restores_context_and_temporary_data(self):
        obj = self.mesh_object()
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        def fail(_path):
            raise RuntimeError("synthetic FBX failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic FBX failure"):
            extract_authoring_models(
                bpy.context,
                self.request(),
                fbx_exporter=fail,
            )

        self.assertEqual(tuple(bpy.context.selected_objects), (obj,))
        self.assertIs(bpy.context.view_layer.objects.active, obj)
        self.assertNotIn("__SOURCEIO_AUTHORING__", bpy.data.collections)

    def test_principled_material_and_generated_texture_are_extracted(self):
        obj = self.mesh_object()
        material = self.principled_material("Crate Painted")
        principled = material.node_tree.nodes.get("Principled BSDF")
        principled.inputs["Metallic"].default_value = 0.25
        principled.inputs["Roughness"].default_value = 0.75
        image = bpy.data.images.new("paint", width=1, height=1)
        image.pixels[:] = (0.2, 0.4, 0.6, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(
            texture.outputs["Color"],
            principled.inputs["Base Color"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        authored = models[0].materials[0]
        self.assertEqual(authored.name, "crate_painted")
        self.assertEqual(authored.metallic, 0.25)
        self.assertEqual(authored.roughness, 0.75)
        self.assertEqual(len(authored.textures), 1)
        self.assertEqual(authored.textures[0].extension, ".tga")
        self.assertEqual(authored.textures[0].payload[2], 2)
        self.assertEqual(authored.textures[0].payload[18:22], bytes((203, 170, 124, 255)))
        self.assertIn(
            "authoring.material.name.normalized",
            {item.code for item in report},
        )

    def test_imported_material_path_takes_precedence_over_principled(self):
        obj = self.mesh_object()
        material = self.principled_material()
        material["full_path"] = "materials/props/existing.vmat?sourceio-scale=1"
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        self.assertEqual(
            models[0].materials[0].existing_resource.as_posix(),
            "materials/props/existing.vmat",
        )
        self.assertEqual(models[0].materials[0].textures, ())

    def test_principled_normal_map_is_extracted_with_source_handedness(self):
        obj = self.mesh_object()
        material = self.principled_material()
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("normal", width=1, height=1, alpha=True)
        image.colorspace_settings.is_data = True
        image.colorspace_settings.name = "Non-Color"
        image.pixels[:] = (0.25, 0.2, 1.0, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        normal_map = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
        material.node_tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        textures = models[0].materials[0].textures
        self.assertEqual(len(textures), 1)
        self.assertEqual(textures[0].role, TextureRole.NORMAL)
        self.assertFalse(textures[0].srgb)
        self.assertEqual(textures[0].payload[18:22], bytes((255, 204, 64, 255)))
        self.assertAlmostEqual(image.pixels[1], 0.2)

    def test_nondefault_normal_map_strength_is_blocked(self):
        obj = self.mesh_object()
        material = self.principled_material()
        nodes = material.node_tree.nodes
        image = bpy.data.images.new("normal", width=1, height=1)
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = 0.5
        material.node_tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
        material.node_tree.links.new(
            normal_map.outputs["Normal"],
            nodes["Principled BSDF"].inputs["Normal"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertEqual(models, ())
        self.assertIn(
            "authoring.material.normal.unsupported",
            {item.code for item in report},
        )

    def test_principled_roughness_map_is_extracted_as_data(self):
        obj = self.mesh_object()
        material = self.principled_material()
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("roughness", width=1, height=1)
        image.pixels[:] = (0.2, 0.2, 0.2, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(
            texture.outputs["Color"],
            principled.inputs["Roughness"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        textures = models[0].materials[0].textures
        self.assertEqual(len(textures), 1)
        self.assertEqual(textures[0].role, TextureRole.ROUGHNESS)
        self.assertFalse(textures[0].srgb)
        self.assertEqual(textures[0].payload[18:22], bytes((51, 51, 51, 255)))

    def test_principled_metallic_map_is_extracted_as_data(self):
        obj = self.mesh_object()
        material = self.principled_material()
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("metallic", width=1, height=1)
        image.pixels[:] = (0.75, 0.75, 0.75, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(
            texture.outputs["Color"],
            principled.inputs["Metallic"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        textures = models[0].materials[0].textures
        self.assertEqual(len(textures), 1)
        self.assertEqual(textures[0].role, TextureRole.METALLIC)
        self.assertFalse(textures[0].srgb)
        self.assertEqual(textures[0].payload[18:22], bytes((191, 191, 191, 255)))

    def test_colored_scalar_map_is_blocked(self):
        obj = self.mesh_object()
        material = self.principled_material()
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("colored_metallic", width=1, height=1)
        image.pixels[:] = (0.25, 0.5, 0.75, 1.0)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(
            texture.outputs["Color"],
            principled.inputs["Metallic"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertEqual(models, ())
        self.assertIn(
            "authoring.material.scalar_texture.color_unsupported",
            {item.code for item in report},
        )

    def test_principled_alpha_test_extracts_base_image_alpha(self):
        obj = self.mesh_object()
        material = self.principled_material()
        material.surface_render_method = "DITHERED"
        material.alpha_threshold = 0.4
        principled = material.node_tree.nodes["Principled BSDF"]
        image = bpy.data.images.new("paint", width=1, height=1, alpha=True)
        image.pixels[:] = (0.2, 0.4, 0.6, 0.25)
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        material.node_tree.links.new(
            texture.outputs["Color"],
            principled.inputs["Base Color"],
        )
        material.node_tree.links.new(
            texture.outputs["Alpha"],
            principled.inputs["Alpha"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        authored = models[0].materials[0]
        self.assertEqual(authored.alpha_mode, MaterialAlphaMode.MASK)
        self.assertAlmostEqual(authored.alpha_cutoff, 0.4)
        alpha = next(
            texture for texture in authored.textures
            if texture.role == TextureRole.ALPHA
        )
        self.assertFalse(alpha.srgb)
        self.assertEqual(alpha.payload[18:22], bytes((64, 64, 64, 255)))

    def test_principled_blended_alpha_remains_blend_mode(self):
        obj = self.mesh_object()
        material = self.principled_material()
        material.surface_render_method = "BLENDED"
        principled = material.node_tree.nodes["Principled BSDF"]
        principled.inputs["Alpha"].default_value = 0.5
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )

        self.assertFalse(report.has_errors)
        self.assertEqual(
            models[0].materials[0].alpha_mode,
            MaterialAlphaMode.BLEND,
        )

    def test_unsupported_procedural_graph_blocks_before_fbx_export(self):
        obj = self.mesh_object()
        material = self.principled_material()
        nodes = material.node_tree.nodes
        noise = nodes.new("ShaderNodeTexNoise")
        material.node_tree.links.new(
            noise.outputs["Fac"],
            nodes["Principled BSDF"].inputs["Base Color"],
        )
        obj.data.materials.append(material)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        called = False

        def exporter(_path):
            nonlocal called
            called = True

        models, report = extract_authoring_models(
            bpy.context,
            self.request(),
            fbx_exporter=exporter,
        )

        self.assertEqual(models, ())
        self.assertTrue(report.has_errors)
        self.assertFalse(called)
        self.assertIn(
            "authoring.material.input.unsupported",
            {item.code for item in report},
        )

    def test_scope_resolution_and_batch_collision(self):
        first = self.mesh_object("Crate")
        second = self.mesh_object("crate")
        nested = bpy.data.collections.new("nested")
        bpy.context.scene.collection.children.link(nested)
        bpy.context.scene.collection.objects.unlink(second)
        nested.objects.link(second)
        first.select_set(True)
        bpy.context.view_layer.objects.active = first

        self.assertEqual(
            resolve_authoring_objects(bpy.context, AuthoringScope.SELECTED),
            (first,),
        )
        self.assertEqual(
            set(resolve_authoring_objects(bpy.context, AuthoringScope.SCENE)),
            {first, second},
        )
        models, report = extract_authoring_models(
            bpy.context,
            self.request(
                scope=AuthoringScope.SCENE,
                grouping=AuthoringGrouping.BATCH,
            ),
            fbx_exporter=lambda path: path.write_bytes(b"fbx"),
        )
        self.assertEqual(models, ())
        self.assertIn(
            "authoring.model.name.collision",
            {item.code for item in report},
        )

    def test_cli_and_shared_service_write_identical_manifests_and_artifacts(self):
        obj = self.mesh_object()
        obj.data.materials.append(self.principled_material())
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        request = self.request()

        first = execute_cs2_authoring(bpy.context, request)
        manifest_file = self.target.content_path(first.execution.manifest_path)
        first_manifest = manifest_file.read_bytes()
        first_outputs = {
            path.relative_to(self.target.content_addon_root).as_posix(): path.read_bytes()
            for path in first.execution.written_paths
        }

        result = cli.execute([
            "export-cs2",
            "--install-root",
            str(self.target.install_root),
            "--addon",
            self.target.addon_name,
            "--asset",
            "crate",
            "--overwrite",
        ])

        self.assertEqual(result, 0)
        self.assertEqual(manifest_file.read_bytes(), first_manifest)
        self.assertEqual(
            {
                path.relative_to(self.target.content_addon_root).as_posix(): path.read_bytes()
                for path in first.execution.written_paths
            },
            first_outputs,
        )


if __name__ == "__main__":
    unittest.main()
