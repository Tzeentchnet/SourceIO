import os

os.environ["NO_BPY"] = "1"

import struct
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage

from SourceIO.library.source1.vtf import load_texture
from SourceIO.library.source1.vtf.dds import vtf_to_dds
from SourceIO.library.utils import MemoryBuffer
from SourceIO.tests.texture_tests.vtf_fixtures import (
    ATI2N,
    DXT1,
    DXT1_ONEBITALPHA,
    DXT5,
    bc1_solid,
    bc1_transparent,
    bc3_solid,
    build_compressed_vtf,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source1" / "textures"

_DDSD_MIPMAPCOUNT = 0x00020000
_DDPF_FOURCC = 0x00000004
_DDSCAPS_COMPLEX = 0x00000008
_DDSCAPS_MIPMAP = 0x00400000


def _dds_fields(data):
    magic, *fields = struct.unpack("<4s31I", data[:128])
    return magic, fields


def test_dxt1_mips_are_reordered_largest_first():
    mips = (b"\x10" * 32, b"\x20" * 8, b"\x30" * 8, b"\x40" * 8)

    dds = vtf_to_dds(build_compressed_vtf(8, 8, DXT1, mips))

    magic, fields = _dds_fields(dds)
    assert magic == b"DDS "
    assert fields[0] == 124
    assert (fields[3], fields[2]) == (8, 8)
    assert fields[4] == 32
    assert fields[6] == 4
    assert fields[1] & _DDSD_MIPMAPCOUNT
    assert fields[18] == 32
    assert fields[20].to_bytes(4, "little") == b"DXT1"
    assert fields[26] & (_DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP)
    assert dds[128:] == b"".join(mips)


@pytest.mark.parametrize(
    ("image_format", "fourcc", "size"),
    [
        (DXT1, b"DXT1", 8),
        (DXT1_ONEBITALPHA, b"DXT1", 8),
        (DXT5, b"DXT5", 16),
    ],
)
def test_supported_formats_get_the_expected_dds_header(image_format, fourcc, size):
    dds = vtf_to_dds(build_compressed_vtf(4, 4, image_format, (b"\x55" * size,)))

    _, fields = _dds_fields(dds)
    assert fields[4] == size
    assert fields[20].to_bytes(4, "little") == fourcc
    assert fields[19] == _DDPF_FOURCC


def test_legacy_vtf_skips_its_low_resolution_thumbnail():
    high_res = b"\x22" * 32
    data = build_compressed_vtf(
        8,
        8,
        DXT1,
        (high_res,),
        version=(7, 2),
        low_res_format=DXT1,
        low_res_size=(4, 4),
        low_res_data=b"\x11" * 8,
    )

    assert vtf_to_dds(data)[128:] == high_res


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_format": ATI2N},
        {"frame_count": 2},
        {"depth": 2},
        {"flags": 0x00004000},
    ],
)
def test_incompatible_vtf_layouts_are_left_for_the_decoder(kwargs):
    kwargs = dict(kwargs)
    image_format = kwargs.pop("image_format", DXT1)
    data = build_compressed_vtf(4, 4, image_format, (b"\0" * 8,), **kwargs)

    assert vtf_to_dds(data) is None


def test_truncated_compatible_payload_is_reported():
    data = build_compressed_vtf(8, 8, DXT5, (b"\0" * 64,))

    with pytest.raises(ValueError, match="truncated"):
        vtf_to_dds(data[:-1])


def test_impossible_mipmap_count_is_reported():
    data = bytearray(build_compressed_vtf(4, 4, DXT1, (b"\0" * 8,)))
    data[56] = 4

    with pytest.raises(ValueError, match="4 mip levels"):
        vtf_to_dds(bytes(data))


@pytest.mark.parametrize(
    ("image_format", "blocks"),
    [
        (
            DXT1,
            (
                bc1_solid(255, 0, 0),
                bc1_solid(0, 255, 0),
                bc1_solid(0, 0, 255),
                bc1_solid(255, 255, 255),
            ),
        ),
        (
            DXT1_ONEBITALPHA,
            (
                bc1_solid(255, 0, 0),
                bc1_transparent(),
                bc1_solid(0, 0, 255),
                bc1_solid(255, 255, 255),
            ),
        ),
        (
            DXT5,
            (
                bc3_solid(255, 0, 0, 0),
                bc3_solid(0, 255, 0, 85),
                bc3_solid(0, 0, 255, 170),
                bc3_solid(255, 255, 255, 255),
            ),
        ),
    ],
)
def test_independent_dds_decoder_preserves_vtf_orientation_and_alpha(image_format, blocks):
    vtf = build_compressed_vtf(8, 8, image_format, (b"".join(blocks),))
    expected, _, _ = load_texture(MemoryBuffer(vtf), normalized=False)

    with PILImage.open(BytesIO(vtf_to_dds(vtf))) as image:
        actual = np.asarray(image.convert("RGBA"))

    np.testing.assert_allclose(actual, expected, atol=7)
    np.testing.assert_array_equal(actual[..., 3], expected[..., 3])


@pytest.mark.parametrize(
    "name",
    ["sample_dxt1.vtf", "sample_dxt1_onebitalpha.vtf", "sample_dxt5.vtf"],
)
def test_public_multimip_samples_match_an_independent_dds_decoder(name):
    path = SAMPLES / name
    if not path.is_file():
        pytest.skip(f"{name} not fetched (tests/fetch_samples.py)")
    vtf = path.read_bytes()
    expected, _, _ = load_texture(MemoryBuffer(vtf), normalized=False)

    with PILImage.open(BytesIO(vtf_to_dds(vtf))) as image:
        actual = np.asarray(image.convert("RGBA"))

    np.testing.assert_allclose(actual, expected, atol=7)
