"""Normal-map Z reconstruction for Source 2 textures."""
import os

os.environ['NO_BPY'] = '1'

import numpy as np

from SourceIO.library.source2.blocks.texture_data.enums import VTexFormat
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


class _Spec:
    def __init__(self, string):
        self.string = string


class _EditInfo:
    def __init__(self, *steps):
        self.special_deps = [_Spec("Texture Compiler Version")] + [_Spec(f"Texture Compiler Version {step}")
                                                                    for step in steps]


class _Texture(CompiledTextureResource):
    """Enough of a CompiledTextureResource for _decompress_texture: only the edit info block."""

    def __init__(self, edit_info):
        self._edit_info = edit_info

    def get_block(self, block_class, *, block_name=None):
        return self._edit_info


# The steps CS2 lists on its normal maps (ATI2N ones have no Image Inverse) and on g_tAnisoGloss.
NORMAL_STEPS = ("Image Inverse", "Image NormalizeNormals", "Mip HemiOctAnisoRoughness", "Mip HemiOctIsoRoughness_RG_B")
ANISO_GLOSS_STEPS = ("Image Inverse", "Image NormalizeNormals", "Mip AnisoRoughness_RG", "Mip HemiOctAnisoRoughness")


def _ati2n_block(red, green):
    # One 4x4 BC5 block: per channel two endpoints and 16 three-bit indices, all 0 (the first endpoint).
    return bytes([red, 0, 0, 0, 0, 0, 0, 0, green, 0, 0, 0, 0, 0, 0, 0])


def test_aniso_gloss_keeps_raw_roughness():
    texture = _Texture(_EditInfo(*ANISO_GLOSS_STEPS))
    pixels = CompiledTextureResource._decompress_texture(texture, _ati2n_block(170, 40), 4, VTexFormat.ATI2N, 4)
    assert np.allclose(pixels[..., 0], 170 / 255)
    assert np.allclose(pixels[..., 1], 40 / 255)


def test_ati2n_normal_map_is_still_decoded():
    texture = _Texture(_EditInfo(*NORMAL_STEPS))
    pixels = CompiledTextureResource._decompress_texture(texture, _ati2n_block(170, 40), 4, VTexFormat.ATI2N, 4)
    assert not np.allclose(pixels[..., 0], 170 / 255)
