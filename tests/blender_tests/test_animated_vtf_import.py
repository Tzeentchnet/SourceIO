"""Animated VTF imports and their on-disk Blender image-sequence cache."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np
from PIL import Image as PILImage

from SourceIO.blender_bindings.source1.vtf import import_animated_texture
from SourceIO.blender_bindings.utils.texture_utils import CACHE_DIR_NAME
from SourceIO.library.source1.vtf import load_texture_frames
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.texture_tests.vtf_fixtures import DXT5, bc3_solid, build_compressed_vtf


class AnimatedVTFImportTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache_root = Path(directory.name) / CACHE_DIR_NAME / "materials" / "animated"
        bpy.context.scene.TextureCachePath = directory.name
        self.addCleanup(setattr, bpy.context.scene, "TextureCachePath", "")

        blocks = (
            bc3_solid(248, 128, 40, 64),
            bc3_solid(80, 196, 128, 192),
            bc3_solid(200, 32, 240, 255),
        )
        self.vtf = build_compressed_vtf(4, 4, DXT5, (b"".join(blocks),), frame_count=3)
        self.frames, height, width = load_texture_frames(MemoryBuffer(self.vtf))
        self.assertEqual((len(self.frames), height, width), (3, 4, 4))

    def _import(self, path, *, invert_y=False):
        image, count = import_animated_texture(path, MemoryBuffer(self.vtf), invert_y=invert_y)
        if image is not None:
            self.addCleanup(bpy.data.images.remove, image)
        return image, count

    def _assert_frame(self, path, frame, *, invert_y=False):
        expected = frame.copy()
        if invert_y:
            expected[:, :, 1] = 1.0 - expected[:, :, 1]
        expected = (expected * 255).astype(np.uint8)
        with PILImage.open(path) as image:
            self.assertEqual(image.mode, "RGBA")
            np.testing.assert_array_equal(np.asarray(image), expected)

    def test_uncached_import_writes_every_frame_and_preserves_normal_conversion(self):
        for invert_y in (False, True):
            with self.subTest(invert_y=invert_y):
                stem = f"water_{int(invert_y)}"
                texture_path = TinyPath(f"materials/animated/{stem}.vtf")

                image, count = self._import(texture_path, invert_y=invert_y)

                self.assertEqual(count, 3)
                self.assertEqual(image.source, "SEQUENCE")
                self.assertEqual(image.alpha_mode, "CHANNEL_PACKED")
                self.assertEqual(image["full_path"], texture_path.as_posix())
                self.assertEqual(image["frame_count"], 3)
                self.assertEqual(image["sourceio_vtf_frames"], 3)
                self.assertEqual(bool(image.get("normalmap_converted", False)), invert_y)
                self.assertEqual(
                    Path(bpy.path.abspath(image.filepath)),
                    self.cache_root / f"{stem}_0001.png",
                )
                for index, frame in enumerate(self.frames, 1):
                    self._assert_frame(self.cache_root / f"{stem}_{index:04d}.png", frame, invert_y=invert_y)

    def test_partially_cached_sequence_only_writes_missing_frames(self):
        texture_path = TinyPath("materials/animated/cached.vtf")
        self.cache_root.mkdir(parents=True)
        cached_paths = [self.cache_root / "cached_0001.png", self.cache_root / "cached_0003.png"]
        for index, path in enumerate(cached_paths):
            pixels = np.full((4, 4, 4), 20 + index, dtype=np.uint8)
            PILImage.fromarray(pixels).save(path)
        cached_bytes = [path.read_bytes() for path in cached_paths]

        image, count = self._import(texture_path)

        self.assertEqual(count, 3)
        self.assertEqual(image.source, "SEQUENCE")
        self.assertEqual([path.read_bytes() for path in cached_paths], cached_bytes)
        self._assert_frame(self.cache_root / "cached_0002.png", self.frames[1])

        with patch("SourceIO.blender_bindings.utils.texture_utils._save_png_fast") as save:
            reloaded, reloaded_count = import_animated_texture(texture_path, MemoryBuffer(self.vtf))
        save.assert_not_called()
        self.assertIs(reloaded, image)
        self.assertEqual(reloaded_count, 3)


if __name__ == "__main__":
    unittest.main()
