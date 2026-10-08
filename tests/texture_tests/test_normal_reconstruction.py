"""Normal-map Z reconstruction for Source 2 textures."""
import os

os.environ['NO_BPY'] = '1'

import numpy as np

from SourceIO.library.source2.resource_types.compiled_texture_resource import CompiledTextureResource


def test_normalize_z_from_unit_xy():
    pixels = np.array([[[128, 128, 0, 255], [255, 128, 0, 255]]], np.uint8)
    out = CompiledTextureResource._normalize(pixels)
    assert out[0, 0, 2] == 255  # flat: Z = 1
    assert out[0, 1, 2] == 128  # X = 1: Z = 0


def test_normalize_xy_longer_than_one():
    # Compression can push X and Y past the unit circle; Z must clamp to 0 instead of becoming NaN.
    pixels = np.array([[[255, 255, 0, 255], [0, 0, 0, 255], [240, 30, 0, 255]]], np.uint8)
    with np.errstate(invalid='raise'):
        out = CompiledTextureResource._normalize(pixels)
    assert (out[0, :, 2] == 128).all()
    assert (out[0, :, :2] == pixels[0, :, :2]).all()
