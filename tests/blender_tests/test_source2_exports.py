"""Run inside Blender with SourceIO importable: unittest ...test_source2_exports."""
from __future__ import annotations

import json
import math
import unittest

import bpy

from SourceIO.blender_bindings.exporting.source2 import (
    hammer_document_from_blender,
    model_document_from_blender,
)
from SourceIO.blender_bindings.shared.model_container import ModelContainer
from SourceIO.library.source2.export import LossReport


class ModelDocAdapterTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def test_quads_are_triangulated_without_mutating_the_mesh(self):
        mesh = bpy.data.meshes.new("quad")
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [],
            [(0, 1, 2, 3)],
        )
        mesh.update()
        obj = bpy.data.objects.new("quad", mesh)
        report = LossReport()

        document = model_document_from_blender(
            ModelContainer([obj], {}),
            "quad",
            report=report,
        )

        self.assertEqual(len(mesh.polygons), 1)
        self.assertEqual(len(document.meshes[0].faces), 2)
        self.assertTrue(all(len(face.vertices) == 3 for face in document.meshes[0].faces))
        self.assertIn(
            "model.mesh.triangulated.blender",
            {diagnostic.code for diagnostic in report},
        )

    def test_static_prop_payload_is_not_misclassified_as_generic_entity(self):
        obj = bpy.data.objects.new("crate", None)
        obj["entity_data"] = {
            "type": "static_prop",
            "prop_path": "models/props/crate.vmdl_c",
            "scale": 0.0254,
            "skin": "default",
            "entity": {"hammeruniqueid": "42"},
        }
        obj.location = (0.0254, 0.0508, 0.0762)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        report = LossReport()

        document = hammer_document_from_blender(
            bpy.context.scene.collection,
            report=report,
        )

        self.assertEqual(len(document.entities), 0)
        self.assertEqual(len(document.props), 1)
        self.assertEqual(document.props[0].model, "models/props/crate.vmdl_c")
        for actual, expected in zip(document.props[0].transform.origin, (1.0, 2.0, 3.0)):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertFalse(report.has_errors)

    def test_modeldoc_restores_source_units_world_transform_and_material_path(self):
        mesh = bpy.data.meshes.new("transformed")
        mesh.from_pydata(
            [(1, 0, 0), (0, 1, 0), (0, 0, 0)],
            [],
            [(0, 1, 2)],
        )
        mesh.update()
        material = bpy.data.materials.new("cached_material")
        material["full_path"] = "materials/props/crate.vmat?sourceio-texture=deadbeef"
        mesh.materials.append(material)
        obj = bpy.data.objects.new("transformed", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (2, 3, 0)
        obj.rotation_euler = (0, 0, math.pi / 2)
        obj.scale = (2, 1, 1)
        obj["sourceio_import_provenance"] = json.dumps({
            "schema": "sourceio.import-provenance",
            "schema_version": 1,
            "asset_kind": "model",
            "provenance": {
                "schema": "sourceio.resource-provenance",
                "schema_version": 1,
                "root_resource": "models/props/crate.vmdl_c",
                "resources": ["models/props/crate.vmdl_c"],
                "dependencies": [],
                "cycles": [],
                "unresolved_resources": [],
                "metadata": {"import_settings": {"scale": 0.5}},
            },
        })
        bpy.context.view_layer.update()
        report = LossReport()

        document = model_document_from_blender(
            ModelContainer([obj], {}),
            "transformed",
            report=report,
        )

        self.assertEqual(document.metadata["source_unit_scale"], 0.5)
        self.assertAlmostEqual(document.meshes[0].vertices[0].position[0], 4.0, places=5)
        self.assertAlmostEqual(document.meshes[0].vertices[0].position[1], 10.0, places=5)
        self.assertEqual(
            document.meshes[0].faces[0].material,
            "materials/props/crate.vmat",
        )
        self.assertNotIn(
            "model.material.resource_path.missing",
            {diagnostic.code for diagnostic in report},
        )


if __name__ == "__main__":
    unittest.main()
