"""Run inside Blender with SourceIO registered: unittest ...test_source2_entity_props.

A Source 2 entity prop goes from its map placeholder through Load Entity: the model is imported at the scene scale
and instanced under the placeholder, so the placeholder must not carry the scene scale as well.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

from SourceIO.blender_bindings.operators import shared_operators as operators
from SourceIO.blender_bindings.source2.vwrld.entities.base_entity_handlers import BaseEntityHandler
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.utils import TinyPath

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source2"
SCALE = 0.0254


def instance_size(placeholder):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = [instance.matrix_world @ Vector(corner)
              for instance in depsgraph.object_instances
              if instance.is_instance and instance.parent and instance.parent.original == placeholder
              and instance.object.type == 'MESH'
              for corner in instance.object.bound_box]
    return Vector(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3))


class Source2EntityPropTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene['INSTANCE_CACHE'] = {}
        self.content = ContentManager()
        self.content.clean()
        self.content.add_child(LooseFilesContentProvider(TinyPath(SAMPLES)))
        self.collection = bpy.data.collections.new("map")
        bpy.context.scene.collection.children.link(self.collection)

    def tearDown(self):
        self.content.clean()

    def test_loaded_entity_prop_is_scaled_once(self):
        entity = {"classname": "prop_dynamic", "targetname": "creature", "model": "models/box_creature_model.vmdl",
                  "origin": "100 200 0", "angles": "0 0 0"}
        BaseEntityHandler([entity], self.collection, self.content, SCALE).load_entities()
        placeholder = bpy.data.objects["creature"]
        self.assertEqual(tuple(placeholder.scale), (1.0, 1.0, 1.0))
        self.assertAlmostEqual(placeholder.location.x, 100 * SCALE, places=5)

        context = SimpleNamespace(scene=SimpleNamespace(
            use_instances=True, import_materials=False, replace_entity=False, import_physics=False))
        reports = []
        loader = SimpleNamespace(report=lambda level, message: reports.append((level, message)))
        operators.SourceIO_OT_LoadEntity.load_vmdl(loader, self.content, context, placeholder)
        bpy.context.view_layer.update()

        self.assertEqual(reports, [])
        self.assertEqual(placeholder.instance_type, 'COLLECTION')
        model_points = [obj.matrix_world @ Vector(corner) for obj in placeholder.instance_collection.all_objects
                        if obj.type == 'MESH' for corner in obj.bound_box]
        model_size = Vector(max(p[i] for p in model_points) - min(p[i] for p in model_points) for i in range(3))
        self.assertGreater(model_size.length, 0.0)
        # The instance in the scene is the size of the imported model, not that size times the scene scale again.
        for placed, imported in zip(instance_size(placeholder), model_size):
            self.assertAlmostEqual(placed, imported, places=4)


if __name__ == '__main__':
    unittest.main()
