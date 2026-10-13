"""Run inside Blender with SourceIO registered: unittest ...test_source1_entity_props.

Loading a Source 1 map's prop placeholder goes through the real model importer with options built in code, not by an
operator, so every option must still have its default (REDxEYE/SourceIO#478: MDL v44-v52 read unset options).
"""
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

from SourceIO.blender_bindings.operators import shared_operators as operators
from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.utils import TinyPath

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source1"


class Source1EntityPropTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene['INSTANCE_CACHE'] = {}
        self.content = ContentManager()
        self.content.clean()
        self.content.add_child(LooseFilesContentProvider(TinyPath(SAMPLES)))

    def tearDown(self):
        self.content.clean()

    def test_default_options_cover_every_declared_property(self):
        options = ModelOptions.default()
        for base in ModelOptions.__mro__:
            for name in inspect.get_annotations(base):
                self.assertTrue(hasattr(options, name), name)
        self.assertFalse(options.debug_stereo_balance)
        self.assertTrue(options.generate_wrinkle_map_node_group)
        self.assertTrue(options.compact_animations)

    def test_placeholder_loads_a_real_mdl(self):
        placeholder = bpy.data.objects.new("prop_static_1", None)
        bpy.context.scene.collection.objects.link(placeholder)
        placeholder["entity_data"] = {"prop_path": "models/barrel01.mdl", "skin": "0", "entity": {},
                                      "type": "prop_static", "scale": 1.0}
        context = SimpleNamespace(scene=SimpleNamespace(
            use_instances=False, import_materials=False, replace_entity=False, use_bvlg=False, import_physics=False))
        reports = []
        loader = SimpleNamespace(report=lambda level, message: reports.append((level, message)))
        loader.apply_prop_pose = lambda *args: operators.SourceIO_OT_LoadEntity.apply_prop_pose(loader, *args)

        operators.SourceIO_OT_LoadEntity.load_mdl(loader, self.content, context, placeholder)

        self.assertEqual(reports, [])
        self.assertTrue(placeholder["entity_data"]["imported"])
        self.assertTrue(any(child.type == 'MESH' for child in placeholder.children_recursive))


if __name__ == '__main__':
    unittest.main()
