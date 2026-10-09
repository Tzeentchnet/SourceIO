import os

os.environ["NO_BPY"] = "1"

from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from SourceIO.library.source2.blocks.texture_data import (
    CompressedMip,
    TextureData,
    TextureImportSettings,
    TextureInfo,
    VTexExtraData,
    VTexFlags,
    VTexFormat,
    VTexMipCompression,
)
from SourceIO.library.source2.resource_types.compiled_texture_resource import CompiledTextureResource
from SourceIO.library.utils import FileBuffer, MemoryBuffer, TinyPath
from SourceIO.library.utils.pylib.compression import lz4_compress


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source2" / "textures"


def load(name):
    path = SAMPLES / name
    if not path.is_file():
        pytest.skip(f"{name} not fetched (tests/fetch_samples.py)")
    return CompiledTextureResource.from_buffer(FileBuffer(path), TinyPath(name))


class SyntheticTexture(CompiledTextureResource):
    def __init__(self, data_block: TextureData, payload: bytes, edit_info=None):
        self._test_data_block = data_block
        self._test_edit_info = edit_info
        self._buffer = MemoryBuffer(payload)
        self._filepath = TinyPath("synthetic.vtex_c")
        self._header = SimpleNamespace(
            blocks=[SimpleNamespace(name="DATA", absolute_offset=0, size=0)]
        )
        self._cached_mips = {}

    def get_block(self, block_class, *, block_id=None, block_name=None):
        if block_name == "DATA":
            return self._test_data_block
        if block_name in {"REDI", "RED2"}:
            return self._test_edit_info
        return None


def texture_info(
        width,
        height,
        depth=1,
        *,
        mip_count=1,
        pixel_format=VTexFormat.RGBA8888,
        flags=VTexFlags(0),
):
    return TextureInfo(
        1,
        flags,
        (0.0, 0.0, 0.0, 0.0),
        width,
        height,
        depth,
        pixel_format,
        mip_count,
        0,
    )


def rgba_mip(width, height, subresources, value):
    return bytes([value, value, value, 255]) * width * height * subresources


def test_mip_offsets_dimensions_and_memory_reduction():
    info = texture_info(8, 8, mip_count=3)
    mip_data = [
        rgba_mip(8, 8, 1, 10),
        rgba_mip(4, 4, 1, 20),
        rgba_mip(2, 2, 1, 30),
    ]
    resource = SyntheticTexture(TextureData(info, {}, {}), b"".join(reversed(mip_data)))

    layouts = resource.get_mip_layouts()
    assert [(layout.width, layout.height, layout.offset, layout.stored_size) for layout in layouts] == [
        (8, 8, 80, 256),
        (4, 4, 16, 64),
        (2, 2, 0, 16),
    ]

    full = resource.get_texture_artifact(TextureImportSettings(mip_level=0))
    smallest = resource.get_texture_artifact(TextureImportSettings(mip_level=99))
    assert smallest.mip_level == 2
    assert smallest.memory_size == full.memory_size // 16
    assert smallest.pixels.shape == (1, 2, 2, 4)
    assert np.allclose(smallest.pixels[..., :3], 30 / 255)

    resource.get_texture_artifact(TextureImportSettings(mip_level=1))
    assert len(resource._cached_mips) == 2
    assert all(cache_key[0] != 0 for cache_key in resource._cached_mips)


def test_lz4_compressed_mip_offsets_and_decode():
    info = texture_info(16, 16, mip_count=3)
    raw_mips = [
        rgba_mip(16, 16, 1, 11),
        rgba_mip(8, 8, 1, 22),
        rgba_mip(4, 4, 1, 33),
    ]
    stored_mips = []
    for raw in raw_mips:
        compressed = bytes(lz4_compress(raw))
        stored_mips.append(compressed if len(compressed) < len(raw) else raw)
    compression = CompressedMip(
        VTexMipCompression.LZ4,
        8,
        len(raw_mips),
        [len(data) for data in stored_mips],
        b"",
    )
    data_block = TextureData(
        info,
        {VTexExtraData.COMPRESSED_MIP_SIZE: compression},
        {VTexExtraData.COMPRESSED_MIP_SIZE: b""},
    )
    resource = SyntheticTexture(data_block, b"".join(reversed(stored_mips)))

    layout = resource.get_mip_layout(1)
    assert layout.offset == len(stored_mips[2])
    assert layout.stored_size == len(stored_mips[1])
    artifact = resource.get_texture_artifact(TextureImportSettings(mip_level=1))
    assert artifact.pixels.shape == (1, 8, 8, 4)
    assert np.allclose(artifact.pixels[..., :3], 22 / 255)


def test_array_layers_do_not_shrink_with_mips_and_can_be_selected():
    info = texture_info(4, 2, depth=3, mip_count=2, flags=VTexFlags.TEXTURE_ARRAY)
    mip0 = b"".join(rgba_mip(4, 2, 1, value) for value in (10, 20, 30))
    mip1 = b"".join(rgba_mip(2, 1, 1, value) for value in (40, 50, 60))
    resource = SyntheticTexture(TextureData(info, {}, {}), mip1 + mip0)

    layout = resource.get_mip_layout(1)
    assert (layout.depth, layout.array_layers, layout.subresource_count) == (1, 3, 3)
    artifact = resource.get_texture_artifact(
        TextureImportSettings(mip_level=1, array_layer=99)
    )
    assert artifact.pixels.shape == (1, 1, 2, 4)
    assert artifact.subresources[0].array_layer == 2
    assert np.allclose(artifact.pixels[..., :3], 60 / 255)


def test_volume_depth_shrinks_per_mip_and_slice_selection_clamps():
    info = texture_info(4, 4, depth=4, mip_count=3, flags=VTexFlags.VOLUME_TEXTURE)
    mip0 = rgba_mip(4, 4, 4, 10)
    mip1 = b"".join(rgba_mip(2, 2, 1, value) for value in (20, 30))
    mip2 = rgba_mip(1, 1, 1, 40)
    resource = SyntheticTexture(TextureData(info, {}, {}), mip2 + mip1 + mip0)

    assert [layout.depth for layout in resource.get_mip_layouts()] == [4, 2, 1]
    artifact = resource.get_texture_artifact(
        TextureImportSettings(mip_level=1, volume_slice=50)
    )
    assert artifact.pixels.shape == (1, 2, 2, 4)
    assert artifact.subresources[0].volume_slice == 1
    assert np.allclose(artifact.pixels[..., :3], 30 / 255)


def test_hdr_precision_is_preserved():
    values = np.array(
        [[[2.5, 0.5, -1.0, 1.0], [4.0, 2.0, 1.0, 0.25]]],
        dtype=np.float16,
    )
    info = texture_info(2, 1, pixel_format=VTexFormat.RGBA16161616F)
    resource = SyntheticTexture(TextureData(info, {}, {}), values.tobytes())
    artifact = resource.get_texture_artifact()

    assert artifact.is_hdr
    assert artifact.pixels.dtype == np.float32
    assert artifact.pixels[0, 0, 0, 0] == pytest.approx(2.5)
    assert artifact.pixels[0, 0, 1, 0] == pytest.approx(4.0)


def test_cache_identity_isolates_mips_and_decode_semantics():
    info = texture_info(4, 4, mip_count=2, pixel_format=VTexFormat.DXT5)
    resource = SyntheticTexture(TextureData(info, {}, {}), bytes(16 + 16))

    mip0 = resource.get_cache_identity(TextureImportSettings(mip_level=0))
    mip1 = resource.get_cache_identity(TextureImportSettings(mip_level=1))
    clamped = resource.get_cache_identity(TextureImportSettings(mip_level=999))
    raw = resource.get_cache_identity(
        TextureImportSettings(mip_level=1, decode_packed_channels=False)
    )
    inverted = resource.get_cache_identity(
        TextureImportSettings(mip_level=1, invert_y=True)
    )

    assert len({mip0, mip1, raw, inverted}) == 4
    assert clamped == mip1


def test_blender_memory_and_disk_cache_keys_are_isolated(monkeypatch):
    fake_bpy = SimpleNamespace(types=SimpleNamespace(Image=object, Node=object))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    from SourceIO.blender_bindings.utils.texture_utils import _variant_path, texture_cache_key

    path = TinyPath("materials/example/packed.vtex")
    mip0 = TextureImportSettings(mip_level=0).cache_identity()
    mip2 = TextureImportSettings(mip_level=2).cache_identity()
    assert texture_cache_key(path, mip0) != texture_cache_key(path, mip2)
    assert _variant_path(path, "png", mip0) != _variant_path(path, "png", mip2)
    assert _variant_path(path, "png", mip0) == _variant_path(path, "png", mip0)


def test_missing_edit_metadata_has_explicit_diagnostic():
    info = texture_info(4, 4, pixel_format=VTexFormat.DXT5)
    resource = SyntheticTexture(TextureData(info, {}, {}), bytes(16))
    artifact = resource.get_texture_artifact()
    assert {diagnostic.code for diagnostic in artifact.diagnostics} == {
        "source2.texture.edit-info-missing"
    }


def test_sprite_sheet_metadata_and_raw_blob_are_exposed():
    resource = load("sheet_named_sequences.vtex_c")
    metadata = resource._data_block().metadata
    assert metadata.sprite_sheet is not None
    assert [sequence.name for sequence in metadata.sprite_sheet.sequences] == [
        "embers_loop",
        "embers_burst",
        "2",
    ]
    assert metadata.raw_extra_data[VTexExtraData.SHEET] == metadata.sprite_sheet.raw_data
    assert metadata.sprite_sheet.sequences[0].frames[0].images[0].uncropped_rect(128, 64) == (
        0,
        0,
        64,
        64,
    )


def test_display_metadata_and_raw_blob_are_exposed():
    metadata = load("DXT5_announcer_axe_png.vtex_c")._data_block().metadata
    assert metadata.display is not None
    assert (metadata.display.display_width, metadata.display.display_height) == (256, 170)
    assert len(metadata.raw_extra_data[VTexExtraData.METADATA]) == 128


def test_cubemap_radiance_and_face_selection():
    cubemap = load("cubemap.vtex_c")
    metadata = cubemap._data_block().metadata
    assert metadata.cubemap_radiance is not None
    assert len(metadata.cubemap_radiance.coefficients) == 27
    assert len(metadata.cubemap_radiance.coefficient_data) == 27 * 4
    assert metadata.cubemap_radiance.cubemap_count == 1

    face = cubemap.get_texture_artifact(
        TextureImportSettings(mip_level=2, cubemap_face=4)
    )
    assert (face.width, face.height, face.face_count) == (2, 2, 6)
    assert face.pixels.shape == (1, 2, 2, 4)
    assert face.subresources[0].cubemap_face == 4


@pytest.mark.parametrize(
    ("name", "extension", "magic"),
    [
        ("PNG_RGBA8888_dynamic_images_ti7_sentinel.vtex_c", "png", b"\x89PNG"),
        ("JPEG_DXT5_halloween_jpg.vtex_c", "jpg", b"\xff\xd8"),
        ("WEBP_RGBA8888.vtex_c", "webp", b"RIFF"),
    ],
)
def test_embedded_image_formats_are_exposed(name, extension, magic):
    embedded = load(name).get_texture_artifact()
    assert embedded.encoded_extension == extension
    assert embedded.encoded_data.startswith(magic)
    assert embedded.pixels is None
