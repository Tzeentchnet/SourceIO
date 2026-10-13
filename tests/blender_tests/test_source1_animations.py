"""Run inside Blender with SourceIO registered: unittest ...test_source1_animations.

Default-option Source 1 animation import, with the HL2 dog from samples/: its animations live in include models.
"""
import unittest
from pathlib import Path

import bpy

from SourceIO.blender_bindings.models.mdl44 import import_mdl44
from SourceIO.blender_bindings.models.mdl49 import import_mdl49
from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.utils import FileBuffer, TinyPath

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@unittest.skipUnless((SAMPLES / "dog_animations.ani").exists(), "dog samples absent; run tests/fetch_samples.py")
class Source1AnimationDefaultsTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.content = ContentManager()
        self.content.clean()
        self.content.add_child(LooseFilesContentProvider(TinyPath(SAMPLES)))
        self.options = ModelOptions.default()
        self.options.import_animations = True
        self.options.import_textures = False

    def tearDown(self):
        self.content.clean()

    def import_model(self, name, importer=import_mdl44):
        with FileBuffer(SAMPLES / name) as buffer:
            return importer(TinyPath(name), buffer, self.content, self.options)

    def test_defaults_import_include_model_animations_as_separate_actions(self):
        armature = self.import_model("dog.mdl").armature

        names = {action.name for action in bpy.data.actions}
        self.assertIn("@ragdoll", names)  # dog.mdl's own
        self.assertIn("@pound", names)  # dog_animations.mdl
        self.assertGreater(len(names), 100)
        self.assertTrue(all(len(action.slots) == 1 for action in bpy.data.actions))
        # Left in rest pose rather than on whichever animation came last.
        self.assertIsNone(armature.animation_data.action)

    def test_compact_actions_are_not_left_assigned(self):
        self.options.compact_animations = True
        armature = self.import_model("dog.mdl").armature

        self.assertEqual({action.name for action in bpy.data.actions},
                         {"dog.mdl", "dog_gestures.mdl", "dog_postures.mdl", "dog_animations.mdl"})
        self.assertIsNone(armature.animation_data.action)

    def test_animation_only_model_imports_its_own_animations(self):
        # Without meshes the importer builds just the armature; the include setting must not skip it.
        container = self.import_model("dog_animations.mdl", import_mdl49)  # v48

        self.assertIsNotNone(container.armature)
        self.assertIn("@pound", {action.name for action in bpy.data.actions})


if __name__ == '__main__':
    unittest.main()
