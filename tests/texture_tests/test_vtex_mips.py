"""Decoding Source 2 texture mips other than the first. Uses the samples from ``tests/fetch_samples.py``."""
import os

os.environ['NO_BPY'] = '1'

from pathlib import Path

import numpy as np
import pytest

from SourceIO.library.source2.resource_types.compiled_texture_resource import CompiledTextureResource
from SourceIO.library.utils import FileBuffer, TinyPath

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source2" / "textures"


def load(name):
    path = SAMPLES / name
    if not path.is_file():
        pytest.skip(f"{name} not fetched (tests/fetch_samples.py)")
    return CompiledTextureResource.from_buffer(FileBuffer(path), TinyPath(name))


@pytest.mark.parametrize("name", ["BC7_testgrid_color_tga_2d6cc34.vtex_c",
                                  "ETC2_banner_s0_lvl0_color_psd_953a8d49.vtex_c", "sheet_clamp_decal.vtex_c",
                                  "ATI2N_hotel_tarp_001_freedom_psd_993397bd.vtex_c"])
def test_mip_is_downsampled_first_mip(name):
    texture = load(name)
    full, (width, height) = texture.get_texture_data(0)
    full = np.asarray(full, np.float32).reshape(height, width, 4)
    pixels, (mip_width, mip_height) = texture.get_texture_data(1)
    assert (mip_width, mip_height) == (width // 2, height // 2)
    pixels = np.asarray(pixels, np.float32).reshape(mip_height, mip_width, 4)
    box_filtered = full.reshape(mip_height, 2, mip_width, 2, 4).mean(axis=(1, 3))
    assert np.abs(pixels - box_filtered).mean() < 0.02


def test_missing_mip_raises():
    texture = load("DXT5_announcer_axe_png.vtex_c")  # a single mip
    with pytest.raises(ValueError):
        texture.get_texture_data(1)
