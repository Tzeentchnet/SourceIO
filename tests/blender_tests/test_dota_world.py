"""Run inside Blender with SourceIO registered: unittest ...test_dota_world."""
import unittest
from types import SimpleNamespace
from unittest import mock

import bpy
from mathutils import Vector

from SourceIO.blender_bindings.source2.vwrld import loader
from SourceIO.blender_bindings.source2.vwrld.entities.base_entity_handlers import BaseEntityHandler
from SourceIO.blender_bindings.source2.vwrld.entities.cs2_entity_handlers import CS2EntityHandler
from SourceIO.blender_bindings.source2.vwrld.entities.dota2_entity_handlers import Dota2EntityHandler
from SourceIO.blender_bindings.source2.vwrld.entity_import_diagnostics import (
    ENTITY_IMPORT_REPORT_PROPERTY,
    MAX_LOGGED_ENTITY_CLASSES,
)
from SourceIO.library.shared.app_id import SteamAppId


SCALE = 0.0254


def assert_vectors_equal(test, actual, expected):
    for actual_value, expected_value in zip(actual, expected):
        test.assertAlmostEqual(actual_value, expected_value, places=4)


class DotaDispatchTests(unittest.TestCase):
    def test_dota_uses_a_dedicated_common_source2_handler(self):
        content_manager = SimpleNamespace(steam_id=SteamAppId.DOTA_2)

        self.assertIs(loader.get_entity_handler(content_manager), Dota2EntityHandler)
        self.assertTrue(issubclass(Dota2EntityHandler, BaseEntityHandler))
        self.assertFalse(issubclass(Dota2EntityHandler, CS2EntityHandler))


    def test_dota_keeps_typed_base_entity_classes(self):
        base_table = BaseEntityHandler.entity_lookup_table
        for classname in ("trigger_multiple", "info_particle_system", "info_player_start"):
            self.assertIs(Dota2EntityHandler.entity_lookup_table[classname], base_table[classname])


class DotaRepresentationTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.collection = bpy.data.collections.new("dota")
        bpy.context.scene.collection.children.link(self.collection)

    def test_unserializable_entity_is_skipped_not_fatal(self):
        entities = [
            {"classname": "info_player_start_goodguys", "hammerUniqueId": 1, "origin": [0.0, 0.0, 0.0],
             "angles": [0.0, 0.0, 0.0], "scales": [1.0, 1.0, 1.0], "parser_object": object()},
            {"classname": "info_player_start_badguys", "hammerUniqueId": 2, "origin": [0.0, 0.0, 0.0],
             "angles": [0.0, 0.0, 0.0], "scales": [1.0, 1.0, 1.0]},
        ]
        handler = Dota2EntityHandler(entities, self.collection, None, SCALE)
        handler.load_entities()

        self.assertIn("info_player_start_badguys_2", bpy.data.objects)
        report = handler.entity_import_diagnostics.to_dict()
        self.assertEqual(report["class_counts"]["unsupported"], {"info_player_start_goodguys": 1})

    def test_visual_entities_keep_transforms_grouping_and_payloads(self):
        entities = [
            {
                "classname": "ent_dota_tree",
                "hammerUniqueId": 101,
                "targetname": "",
                "origin": [100.0, 200.0, 300.0],
                "angles": [0.0, 90.0, 0.0],
                "scales": [2.0, 3.0, 4.0],
                "model": "models/props_tree/tree_pine_01.vmdl",
                "custom": {"season": "summer", "weights": [1, 2]},
            },
            {
                "classname": "trigger_shop",
                "hammerUniqueId": 102,
                "origin": [4.0, 5.0, 6.0],
                "angles": [0.0, 0.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
                "model": "maps/dota/entities/shop.vmdl",
            },
            {
                "classname": "dota_world_particle_system",
                "hammerUniqueId": 103,
                "origin": [7.0, 8.0, 9.0],
                "angles": [1.0, 2.0, 3.0],
                "scales": [2.0, 2.0, 2.0],
                "effect_name": "particles/world_environmental_fx/test.vpcf",
            },
            {
                "classname": "env_deferred_light_client_only",
                "hammerUniqueId": 104,
                "origin": [10.0, 11.0, 12.0],
                "angles": [0.0, 45.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
                "brightness": 4.0,
            },
            {
                "classname": "info_world_layer",
                "hammerUniqueId": 105,
                "origin": [13.0, 14.0, 15.0],
                "angles": [0.0, 0.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
                "layerName": "world_layer_dire_base",
            },
        ]

        Dota2EntityHandler(entities, self.collection, None, SCALE).load_entities()
        bpy.context.view_layer.update()

        tree = bpy.data.objects["ent_dota_tree_101"]
        assert_vectors_equal(self, tree.location, Vector((100.0, 200.0, 300.0)) * SCALE)
        # The model is imported at the scene scale, so the placeholder only carries the entity scale.
        assert_vectors_equal(self, tree.scale, (2.0, 3.0, 4.0))
        assert_vectors_equal(
            self,
            tree.rotation_euler.to_matrix() @ Vector((1.0, 0.0, 0.0)),
            (0.0, 1.0, 0.0),
        )
        self.assertIn(tree, self.collection.children["props"].children["ent_dota_tree"].objects[:])
        self.assertEqual(tree["entity_data"]["prop_path"], "models/props_tree/tree_pine_01.vmdl_c")
        self.assertEqual(tree["entity_data"]["entity"]["custom"]["season"], "summer")
        self.assertEqual(list(tree["entity_data"]["entity"]["custom"]["weights"]), [1, 2])
        self.assertEqual(tree["sourceio_entity_behavior"], "handled")

        trigger = bpy.data.objects["trigger_shop_102"]
        self.assertIn(trigger, self.collection.children["triggers"].children["trigger_shop"].objects[:])
        self.assertEqual(trigger["sourceio_entity_behavior"], "represented")
        self.assertEqual(trigger["entity_data"]["prop_path"], "maps/dota/entities/shop.vmdl_c")

        particle = bpy.data.objects["dota_world_particle_system_103"]
        self.assertIn(
            particle,
            self.collection.children["particles"].children["dota_world_particle_system"].objects[:],
        )
        self.assertEqual(particle["sourceio_entity_behavior"], "represented")
        # The entity scale scales the marker but must not move it.
        assert_vectors_equal(self, particle.location, Vector((7.0, 8.0, 9.0)) * SCALE)
        assert_vectors_equal(self, particle.scale, (2.0 * SCALE, 2.0 * SCALE, 2.0 * SCALE))
        self.assertEqual(
            particle["entity_data"]["entity"]["effect_name"],
            "particles/world_environmental_fx/test.vpcf",
        )

        deferred_light = bpy.data.objects["env_deferred_light_client_only_104"]
        self.assertIn(
            deferred_light,
            self.collection.children["environment"].children["env_deferred_light_client_only"].objects[:],
        )
        self.assertEqual(deferred_light["sourceio_entity_behavior"], "deferred")
        self.assertEqual(deferred_light["entity_data"]["entity"]["brightness"], 4.0)

        layer_collection = self.collection.children["world_layers"].children["world_layer_dire_base"]
        self.assertEqual(layer_collection["sourceio_world_layer"], "world_layer_dire_base")
        self.assertIn(bpy.data.objects["info_world_layer_105"], layer_collection.objects[:])

        report = loader.get_entity_import_report(self.collection)
        self.assertEqual(report["totals"], {
            "handled": 1,
            "represented": 3,
            "deferred": 1,
            "unsupported": 0,
        })


class EntityImportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def _load(self, name, entities):
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
        handler = Dota2EntityHandler(entities, collection, None, SCALE)
        handler.logger = mock.Mock()
        handler.load_entities()
        return collection, handler.logger

    def test_reports_are_deterministic_and_warning_output_is_bounded(self):
        unsupported = [
            {
                "classname": f"unsupported_{index:02d}",
                "payload": f"payload that must not be logged {index}",
            }
            for index in range(MAX_LOGGED_ENTITY_CLASSES + 2)
        ]
        entities = [
            {
                "classname": "ent_dota_tree",
                "model": "models/props_tree/tree_pine_01.vmdl",
                "origin": [0.0, 0.0, 0.0],
                "angles": [0.0, 0.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
            },
            {
                "classname": "info_particle_system",
                "origin": [0.0, 0.0, 0.0],
                "angles": [0.0, 0.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
                "effect_name": "particles/test.vpcf",
            },
            {
                "classname": "env_global_light",
                "origin": [0.0, 0.0, 0.0],
                "angles": [0.0, 0.0, 0.0],
                "scales": [1.0, 1.0, 1.0],
            },
            *unsupported,
            unsupported[0],
        ]

        first, first_logger = self._load("first", entities)
        second, second_logger = self._load("second", list(reversed(entities)))

        self.assertEqual(
            first[ENTITY_IMPORT_REPORT_PROPERTY],
            second[ENTITY_IMPORT_REPORT_PROPERTY],
        )
        report = loader.get_entity_import_report(first)
        self.assertEqual(report["entity_count"], len(entities))
        self.assertEqual(report["totals"], {
            "handled": 1,
            "represented": 1,
            "deferred": 1,
            "unsupported": len(unsupported) + 1,
        })
        self.assertEqual(report["class_counts"]["unsupported"]["unsupported_00"], 2)
        self.assertEqual(len(report["class_counts"]["unsupported"]), len(unsupported))

        for logger in (first_logger, second_logger):
            self.assertEqual(logger.warn.call_count, 1)
            warning = logger.warn.call_args.args[0]
            self.assertIn("2 additional classes omitted", warning)
            self.assertNotIn("payload that must not be logged", warning)


if __name__ == "__main__":
    unittest.main()
