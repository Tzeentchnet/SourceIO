"""Run inside Blender with SourceIO registered: unittest ...test_vtf_editable_export."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

from SourceIO.blender_bindings.operators.source1_operators import SOURCEIO_OT_VTFExportEditable
from SourceIO.blender_bindings.source1.vtf import import_texture, import_texture_tth
from SourceIO.library.source1.vtf.editable_export import export_vtf_for_editing
from SourceIO.library.source1.vtf.metadata import RESOURCE_SHEET
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.texture_tests.vtf_fixtures import build_tth_pair, build_vtf, cell, checker, sheet_blob


class VTFEditableExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vtf = build_vtf(checker(), [(RESOURCE_SHEET, 0, sheet_blob(1, [
            (0, 0, [(1.0, [cell(0)]), (0.5, [cell(4)])]),
            (1, 1, [(2.0, [cell(4)])]),
        ]))])

    def tearDown(self):
        for image in list(bpy.data.images):
            if image.get("full_path") in {"puff.vtf", "puff_tth.vtf"}:
                bpy.data.images.remove(image)
        self.temp.cleanup()

    def import_puff(self):
        return import_texture(TinyPath("puff.vtf"), MemoryBuffer(self.vtf))

    def test_import_records_vtf_metadata(self):
        image = self.import_puff()

        self.assertEqual(image["sourceio_vtf_version"], "7.3")
        self.assertEqual(image["sourceio_vtf_frames"], 1)
        self.assertEqual(image["sourceio_sprite_sheet_sequences"], 2)
        stored = json.loads(image["sourceio_vtf_metadata"])
        self.assertEqual(stored["sprite_sheet_version"], 1)
        self.assertEqual([sequence["clamp"] for sequence in stored["sprite_sheet"]["sequences"]], [False, True])

    def test_tth_import_records_the_reconstructed_vtf_metadata(self):
        direct = self.import_puff()
        tth, ttz = build_tth_pair(self.vtf)
        image = import_texture_tth(
            TinyPath("puff_tth.vtf"),
            MemoryBuffer(tth),
            MemoryBuffer(ttz),
        )

        self.assertEqual(image["sourceio_vtf_version"], direct["sourceio_vtf_version"])
        self.assertEqual(image["sourceio_vtf_flags"], direct["sourceio_vtf_flags"])
        self.assertEqual(image["sourceio_vtf_frames"], direct["sourceio_vtf_frames"])
        self.assertEqual(
            json.loads(image["sourceio_vtf_metadata"]),
            json.loads(direct["sourceio_vtf_metadata"]),
        )

    def test_operator_matches_the_direct_vtf_export(self):
        image = self.import_puff()
        reports = []
        operator = SimpleNamespace(
            directory=str(self.root / "blender"),
            image_format="tga",
            overwrite=False,
            report=lambda kind, message: reports.append((kind, message)),
        )
        context = SimpleNamespace(space_data=SimpleNamespace(image=image))

        self.assertEqual(SOURCEIO_OT_VTFExportEditable.execute(operator, context), {'FINISHED'})
        export_vtf_for_editing(self.vtf, self.root / "vtf", "puff")

        written = sorted(path.name for path in (self.root / "blender").iterdir())
        self.assertEqual(written, sorted(path.name for path in (self.root / "vtf").iterdir()))
        self.assertIn("puff.sht", written)
        for name in written:
            if name.endswith(".json"):
                continue
            self.assertEqual((self.root / "blender" / name).read_bytes(), (self.root / "vtf" / name).read_bytes(),
                             name)
        self.assertTrue(any("mksheet puff.mks" in message for _, message in reports))

    def test_operator_needs_vtf_metadata(self):
        image = bpy.data.images.new("not_a_vtf", 4, 4)
        try:
            context = SimpleNamespace(space_data=SimpleNamespace(image=image))
            self.assertFalse(SOURCEIO_OT_VTFExportEditable.poll(context))
        finally:
            bpy.data.images.remove(image)
