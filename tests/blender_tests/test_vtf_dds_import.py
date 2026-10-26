"""Run inside Blender with SourceIO registered: unittest ...test_vtf_dds_import."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np

from SourceIO.blender_bindings.preferences import ADDON_PACKAGE, SourceIOPreferences, source1_dds_enabled
from SourceIO.blender_bindings.source1.vtf import import_texture
from SourceIO.blender_bindings.utils.texture_utils import (
    check_texture_cache,
    create_and_cache_texture,
)
from SourceIO.library.source1.vtf import load_texture
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.texture_tests.vtf_fixtures import (
    DXT1,
    DXT1_ONEBITALPHA,
    DXT5,
    bc1_solid,
    bc1_transparent,
    bc3_solid,
    build_compressed_vtf,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source1" / "textures"


def _pixels(image):
    pixels = np.zeros(image.size[0] * image.size[1] * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(image.size[1], image.size[0], 4)


def _quadrants(block):
    return block(255, 0, 0), block(0, 255, 0), block(0, 0, 255), block(255, 255, 255)


class VTFDDSImportTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.TextureCachePath = ""

    def _assert_matches_decoded_png(self, vtf, name, *, tolerance=8 / 255):
        fast = import_texture(TinyPath(f"{name}.vtf"), MemoryBuffer(vtf))
        decoded, _, _ = load_texture(MemoryBuffer(vtf), normalized=False)
        reference = create_and_cache_texture(TinyPath(f"{name}_reference.vtf"), decoded)

        self.assertEqual(tuple(fast.size), (decoded.shape[1], decoded.shape[0]))
        self.assertIsNotNone(fast.packed_file)
        self.assertEqual(bytes(fast.packed_file.data[:4]), b"DDS ")
        np.testing.assert_allclose(_pixels(fast), _pixels(reference), atol=tolerance)
        return fast

    def test_dxt1_preserves_existing_orientation_and_colors(self):
        vtf = build_compressed_vtf(8, 8, DXT1, (b"".join(_quadrants(bc1_solid)),))

        image = self._assert_matches_decoded_png(vtf, "dxt1")

        self.assertEqual(image["sourceio_vtf_version"], "7.3")
        self.assertEqual(image["sourceio_vtf_frames"], 1)

    def test_dxt5_preserves_existing_orientation_colors_and_alpha(self):
        blocks = (
            bc3_solid(255, 0, 0, 0),
            bc3_solid(0, 255, 0, 85),
            bc3_solid(0, 0, 255, 170),
            bc3_solid(255, 255, 255, 255),
        )
        vtf = build_compressed_vtf(8, 8, DXT5, (b"".join(blocks),))

        self._assert_matches_decoded_png(vtf, "dxt5")

    def test_dxt1_one_bit_alpha_remains_transparent(self):
        blocks = (
            bc1_solid(255, 0, 0),
            bc1_transparent(),
            bc1_solid(0, 0, 255),
            bc1_solid(255, 255, 255),
        )
        vtf = build_compressed_vtf(8, 8, DXT1_ONEBITALPHA, (b"".join(blocks),))

        self._assert_matches_decoded_png(vtf, "dxt1_alpha")

    def test_pixel_transform_keeps_the_decoded_png_path(self):
        vtf = build_compressed_vtf(8, 8, DXT1, (b"".join(_quadrants(bc1_solid)),))

        image = import_texture(TinyPath("normal.vtf"), MemoryBuffer(vtf), invert_y=True)

        self.assertEqual(image.file_format, "PNG")
        self.assertTrue(image["normalmap_converted"])

    def test_dds_preference_defaults_on_and_honors_an_explicit_disabled_value(self):
        self.assertTrue(SourceIOPreferences.bl_rna.properties["use_source1_dds"].default)
        self.assertTrue(source1_dds_enabled(SimpleNamespace(preferences=None)))
        context = SimpleNamespace(preferences=SimpleNamespace(addons={
            ADDON_PACKAGE: SimpleNamespace(preferences=SimpleNamespace(use_source1_dds=False)),
        }))
        self.assertFalse(source1_dds_enabled(context))

    def test_disabled_preference_keeps_the_decoded_png_path(self):
        vtf = build_compressed_vtf(8, 8, DXT1, (b"".join(_quadrants(bc1_solid)),))

        with patch("SourceIO.blender_bindings.source1.vtf.source1_dds_enabled", return_value=False):
            image = import_texture(TinyPath("dds_disabled.vtf"), MemoryBuffer(vtf))

        self.assertEqual(image.file_format, "PNG")
        self.assertEqual(bytes(image.packed_file.data[:8]), b"\x89PNG\r\n\x1a\n")

    def test_public_multimip_samples_stay_within_bc_decoder_rounding(self):
        for name in ("sample_dxt1.vtf", "sample_dxt1_onebitalpha.vtf", "sample_dxt5.vtf"):
            with self.subTest(name=name):
                path = SAMPLES / name
                if not path.is_file():
                    self.skipTest(f"{name} not fetched (tests/fetch_samples.py)")
                vtf = path.read_bytes()
                fast = self._assert_matches_decoded_png(vtf, path.stem)
                decoded, _, _ = load_texture(MemoryBuffer(vtf), normalized=False)
                reference = create_and_cache_texture(TinyPath(f"{path.stem}_alpha_reference.vtf"), decoded)
                alpha_difference = np.abs(_pixels(fast)[..., 3] - _pixels(reference)[..., 3]).max()
                self.assertLessEqual(alpha_difference, 1 / 255)

    def test_disk_cache_reloads_dds(self):
        vtf = build_compressed_vtf(8, 8, DXT1, (b"".join(_quadrants(bc1_solid)),))
        path = TinyPath("materials/cached.vtf")
        with tempfile.TemporaryDirectory() as directory:
            bpy.context.scene.TextureCachePath = directory
            image = import_texture(path, MemoryBuffer(vtf))
            cached_path = Path(directory) / "materials" / "cached.dds"
            self.assertTrue(cached_path.is_file())
            bpy.data.images.remove(image)

            reloaded = check_texture_cache(path)

            self.assertIsNotNone(reloaded)
            self.assertEqual(Path(bpy.path.abspath(reloaded.filepath)).suffix.lower(), ".dds")
            bpy.data.images.remove(reloaded)
            bpy.context.scene.TextureCachePath = ""
