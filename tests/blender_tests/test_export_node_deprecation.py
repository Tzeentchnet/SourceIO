from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bpy


class LegacyExportNodeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="sourceio_legacy_export_nodes_"
        )

    def tearDown(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.temporary_directory.cleanup()

    def test_legacy_tree_loads_but_evaluation_is_disabled(self):
        tree = bpy.data.node_groups.new(
            "legacy_sourceio_export",
            "sourceio.model_definition",
        )
        tree.use_fake_user = True
        output = tree.nodes.new("SourceIOModelNode")
        output.model_name = "models/legacy.mdl"
        blend_path = Path(self.temporary_directory.name) / "legacy.blend"
        bpy.ops.wm.save_as_mainfile(
            filepath=str(blend_path),
            check_existing=False,
        )

        bpy.ops.wm.open_mainfile(filepath=str(blend_path))

        loaded = bpy.data.node_groups["legacy_sourceio_export"]
        self.assertEqual(
            loaded.bl_idname,
            "sourceio.model_definition",
        )
        self.assertIn("deprecated", loaded.bl_rna.name.casefold())
        loaded_output = loaded.nodes[0]
        self.assertEqual(loaded_output.bl_idname, "SourceIOModelNode")
        self.assertEqual(loaded_output.model_name, "models/legacy.mdl")
        self.assertIsNone(bpy.data.texts.get("qc"))

        result = bpy.ops.sourceio.evaluate_nodetree("EXEC_DEFAULT")

        self.assertEqual(result, {"CANCELLED"})
        self.assertIsNone(bpy.data.texts.get("qc"))
