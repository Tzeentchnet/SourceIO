from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np
from PIL import Image as PILImage

from SourceIO.blender_bindings.utils.texture_utils import (
    PNG_COMPRESSION_LEVEL,
    _encode_png_fast,
    _save_png_fast,
)
from SourceIO.library.utils.pylib.image import encode_png


class FastPngEncodingTests(TestCase):
    def test_round_trips_every_supported_channel_layout(self):
        modes = {1: "L", 2: "LA", 3: "RGB", 4: "RGBA"}
        for channels, mode in modes.items():
            with self.subTest(channels=channels):
                pixels = np.arange(5 * 3 * channels, dtype=np.uint8).reshape(3, 5, channels)
                encoded = _encode_png_fast(pixels.tobytes(), 5, 3, channels)
                with PILImage.open(BytesIO(encoded)) as image:
                    self.assertEqual(image.mode, mode)
                    self.assertEqual(image.size, (5, 3))
                    self.assertEqual(image.tobytes(), pixels.tobytes())

    def test_fast_compression_has_bounded_size_and_saves_the_same_pixels(self):
        y, x = np.indices((128, 256), dtype=np.uint16)
        pixels = np.stack((
            x % 256,
            y % 256,
            (x + y) % 256,
            np.full_like(x, 255),
        ), axis=-1).astype(np.uint8)
        raw = pixels.tobytes()
        fast = _encode_png_fast(raw, 256, 128, 4)
        default = encode_png(raw, 256, 128, 4)
        self.assertEqual(PNG_COMPRESSION_LEVEL, 2)
        self.assertLessEqual(len(fast), len(default) * 1.10)

        with TemporaryDirectory() as directory:
            output = Path(directory) / "fast.png"
            _save_png_fast(raw, 256, 128, 4, output)
            with PILImage.open(output) as image:
                self.assertEqual(image.tobytes(), raw)
