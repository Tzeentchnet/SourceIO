"""Run inside Blender with SourceIO registered: unittest ...test_skybox.

CS2 ships a map's 3D skybox as a map of its own, named by the map's ``skybox_reference``, built at
1/``scale`` of its ``sky_camera``. Also covers the per-map entity collections the skybox needs, and
*Load Entity* on an aggregate whose draw calls repeat (it placed only the first fragment).
"""
import math
import unittest
from types import SimpleNamespace
from unittest import mock

import bpy
from mathutils import Matrix, Vector

from SourceIO.blender_bindings.operators import shared_operators
from SourceIO.blender_bindings.shared.model_container import ModelContainer
from SourceIO.blender_bindings.source2.vwrld import loader
from SourceIO.blender_bindings.utils.bpy_utils import get_or_create_child_collection

SCALE = 0.0254
CAMERA = (-8.0, -308.0, 202.0)  # de_dust2_skybox's sky_camera


def assert_vectors_equal(test, a, b):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=4)


class FakeLump:
    def __init__(self, entities, children=()):
        self.entities = entities
        self.children = list(children)

    def get_entities(self):
        return iter(self.entities)

    def get_child_lumps(self, cm):
        return iter(self.children)


class FakeWorld:
    def __init__(self, *lumps):
        self.lumps = dict(enumerate(lumps))
        self.data_block = {"m_entityLumps": list(self.lumps)}

    def get_child_resource(self, key, cm, resource_class):
        return self.lumps[key]


class RecordingHandler:
    """Creates an empty per entity at its origin, like the real handlers' simple cases."""

    def __init__(self, entities, collection, cm, scale):
        self.entities, self.collection, self.scale = entities, collection, scale

    def load_entities(self):
        for entity in self.entities:
            values = loader.entity_values(entity)
            obj = bpy.data.objects.new(values["classname"], None)
            obj.location = Vector(loader.get_origin(values)) * self.scale
            get_or_create_child_collection(values["classname"], self.collection).objects.link(obj)


class SkyboxMatrixTests(unittest.TestCase):
    def test_sky_camera_maps_to_reference(self):
        reference = {"origin": "0 0 0", "angles": "0 0 0", "scales": "1 1 1"}
        matrix = loader.skybox_matrix(reference, {"scale": 16, "origin": "-8 -308 202"}, SCALE)
        assert_vectors_equal(self, matrix @ (Vector(CAMERA) * SCALE), (0, 0, 0))
        # One skybox unit is 16 map units
        assert_vectors_equal(self, matrix @ ((Vector(CAMERA) + Vector((1, 2, 3))) * SCALE),
                             Vector((16, 32, 48)) * SCALE)

    def test_reference_transform_applies_after_scale(self):
        reference = {"origin": "100 0 0", "angles": "0 90 0", "scales": "1 1 1"}
        matrix = loader.skybox_matrix(reference, {"scale": "16", "origin": "0 0 0"}, SCALE)
        # A yaw of 90 degrees turns +X to +Y
        assert_vectors_equal(self, matrix @ Vector((SCALE, 0, 0)), Vector((100, 16, 0)) * SCALE)

    def test_without_sky_camera_the_map_is_placed_as_is(self):
        matrix = loader.skybox_matrix({"origin": "10 0 0"}, None, SCALE)
        assert_vectors_equal(self, matrix @ Vector((1, 1, 1)), Vector((1 + 10 * SCALE, 1, 1)))


class ChildCollectionTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def test_each_parent_gets_its_own_collection(self):
        first = bpy.data.collections.new("first")
        second = bpy.data.collections.new("second")
        a = get_or_create_child_collection("func", first)
        b = get_or_create_child_collection("func", second)
        self.assertIsNot(a, b)
        self.assertEqual(b.name, "func.001")
        self.assertIs(get_or_create_child_collection("func", second), b)
        self.assertIs(get_or_create_child_collection("func", first), a)
        self.assertIsNot(get_or_create_child_collection("func_brush", first), a)


class SkyboxImportTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.map_collection = bpy.data.collections.new("de_dust2")
        bpy.context.scene.collection.children.link(self.map_collection)

    def load(self, reference_values, sky_entities):
        main_world = FakeWorld(FakeLump([{"version": 1, "values": reference_values}]))
        sky_world = FakeWorld(FakeLump(sky_entities[:1], [FakeLump(sky_entities[1:])]))

        def load_world_nodes(world, map_resource, cm, collection, scale):
            obj = bpy.data.objects.new("static_prop", None)
            obj.matrix_world = Matrix.Translation((Vector(CAMERA) + Vector((0, 0, 10))) * scale)
            get_or_create_child_collection("static_props_node000", collection).objects.link(obj)

        with mock.patch.object(loader, "open_skybox_map", return_value=object()) as open_map, \
                mock.patch.object(loader, "find_world", return_value=sky_world), \
                mock.patch.object(loader, "load_world_nodes", load_world_nodes), \
                mock.patch.object(loader, "get_entity_handler", return_value=RecordingHandler):
            loader.load_skyboxes(main_world, self.map_collection, SCALE, cm=None)
        return open_map

    def test_skybox_is_placed_around_the_map(self):
        open_map = self.load(
            {"classname": "skybox_reference", "targetMapName": "maps/prefabs/de_dust2/de_dust2_skybox.vmap",
             "origin": "0 0 0", "angles": "0 0 0", "scales": "1 1 1"},
            [{"classname": "sky_camera", "scale": 16, "origin": "-8 -308 202"},
             {"classname": "func_brush", "origin": "-7 -308 202"},
             {"classname": "light_environment", "origin": "0 0 0"},
             {"classname": "env_sky", "origin": "0 0 0"}])
        self.assertEqual(open_map.call_args.args[0], "maps/prefabs/de_dust2/de_dust2_skybox.vmap")

        sky = self.map_collection.children["de_dust2_skybox"]
        names = sorted(obj.name for obj in sky.all_objects)
        # The skybox's sun and sky duplicate the map's own; the sky camera only places it
        self.assertEqual(names, ["func_brush", "static_prop"])
        bpy.context.view_layer.update()
        assert_vectors_equal(self, bpy.data.objects["func_brush"].matrix_world.translation,
                             Vector((16 * SCALE, 0, 0)))
        prop = bpy.data.objects["static_prop"].matrix_world
        assert_vectors_equal(self, prop.translation, Vector((0, 0, 160 * SCALE)))
        assert_vectors_equal(self, prop.to_scale(), (16, 16, 16))

    def test_maps_without_a_target_are_skipped(self):
        open_map = self.load({"classname": "skybox_reference", "targetMapName": ""}, [])
        open_map.assert_not_called()
        self.assertEqual(len(self.map_collection.children), 0)


class FakeModelResource:
    name = "aggregate"


class AggregateLoadTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.use_instances = False
        bpy.context.scene.replace_entity = True
        bpy.context.scene.import_materials = False
        bpy.context.scene.import_physics = False
        self.loaded = []

    def load_model(self, cm, resource, import_context):
        mesh = bpy.data.meshes.new(f"draw_call_{import_context.draw_call_index}")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new(mesh.name, mesh)
        self.loaded.append(import_context.draw_call_index)
        return ModelContainer([obj], {"group": [obj]})

    def test_every_fragment_is_placed(self):
        map_collection = bpy.data.collections.new("map")
        bpy.context.scene.collection.children.link(map_collection)
        placeholder = bpy.data.objects.new("aggregate", None)
        placeholder.location = (10, 0, 0)
        map_collection.objects.link(placeholder)
        fragments = [{"draw_call": 0, "tint_color": [255, 255, 255], "matrix": list(Matrix.Translation((1, 0, 0)))},
                     {"draw_call": 0, "tint_color": [255, 255, 255], "matrix": list(Matrix.Translation((2, 0, 0)))},
                     {"draw_call": 1, "tint_color": [255, 255, 255], "matrix": list(Matrix.Translation((0, 5, 0)))}]
        placeholder["entity_data"] = {"prop_path": "maps/x/worldnodes/agg.vmdl_c", "type": "aggregate_static_prop",
                                      "scale": SCALE, "entity": {}, "fragments": fragments, "skin": "default"}

        operator = SimpleNamespace(report=lambda *args: None)
        cm = SimpleNamespace(find_file=lambda path: b"vmdl")
        with mock.patch.object(shared_operators, "load_model", self.load_model), \
                mock.patch.object(shared_operators.CompiledModelResource, "from_buffer",
                                  return_value=FakeModelResource()):
            shared_operators.SourceIO_OT_LoadEntity.load_vmdl(operator, cm, bpy.context, placeholder)
        bpy.context.view_layer.update()

        self.assertNotIn("aggregate", [obj.name for obj in bpy.data.objects if obj.type == 'EMPTY'
                                       and obj.instance_type != 'COLLECTION'])
        instances = sorted((obj for obj in bpy.data.objects if obj.instance_type == 'COLLECTION'),
                           key=lambda obj: obj.matrix_world.translation.x)
        self.assertEqual(len(instances), 2)
        assert_vectors_equal(self, instances[0].matrix_world.translation, (11, 0, 0))
        assert_vectors_equal(self, instances[1].matrix_world.translation, (12, 0, 0))
        self.assertEqual(instances[0].instance_collection, instances[1].instance_collection)
        self.assertNotIn("fragments", instances[0]["entity_data"])
        self.assertTrue(instances[0]["entity_data"]["imported"])

        single = [obj for obj in bpy.data.objects if obj.type == 'MESH' and obj.data.name == "draw_call_1"]
        self.assertEqual(len(single), 1)
        assert_vectors_equal(self, single[0].matrix_world.translation, (10, 5, 0))
        self.assertEqual(single[0].name, "aggregate")
        self.assertEqual(sorted(self.loaded), [0, 1])


if __name__ == '__main__':
    unittest.main()
