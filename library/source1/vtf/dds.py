"""Wrap compatible block-compressed VTF image data in a DDS container."""
from __future__ import annotations

import struct

from .metadata import RESOURCE_IMAGE, VTFMetadata, read_vtf_metadata

_VTF_DXT1 = 13
_VTF_DXT5 = 15
_VTF_DXT1_ONEBITALPHA = 20
_VTF_ENVMAP = 0x00004000

_DDSD_CAPS = 0x00000001
_DDSD_HEIGHT = 0x00000002
_DDSD_WIDTH = 0x00000004
_DDSD_PIXELFORMAT = 0x00001000
_DDSD_MIPMAPCOUNT = 0x00020000
_DDSD_LINEARSIZE = 0x00080000
_DDPF_FOURCC = 0x00000004
_DDSCAPS_COMPLEX = 0x00000008
_DDSCAPS_TEXTURE = 0x00001000
_DDSCAPS_MIPMAP = 0x00400000

# VTF format -> (DDS FourCC, compressed bytes per 4x4 block)
_DDS_FORMATS = {
    _VTF_DXT1: (b"DXT1", 8),
    _VTF_DXT5: (b"DXT5", 16),
    _VTF_DXT1_ONEBITALPHA: (b"DXT1", 8),
}


def vtf_to_dds(data: bytes, metadata: VTFMetadata | None = None) -> bytes | None:
    """Return a DDS containing the original VTF blocks, or ``None`` when the layout is incompatible.

    Only a single-frame, two-dimensional DXT1/DXT5 texture can be represented without changing its pixels.
    VTF stores mip levels smallest-first, so their byte ranges are reordered for DDS without decoding them.
    """
    metadata = metadata or read_vtf_metadata(data)
    header = metadata.header
    format_info = _DDS_FORMATS.get(header.image_format)
    if format_info is None:
        return None
    if header.frame_count != 1 or header.depth != 1 or header.flags & _VTF_ENVMAP:
        return None
    if header.width <= 0 or header.height <= 0:
        raise ValueError(f"VTF dimensions must be positive, got {header.width}x{header.height}")
    if not 1 <= header.mipmap_count <= max(header.width, header.height).bit_length():
        raise ValueError(
            f"VTF declares {header.mipmap_count} mip levels for {header.width}x{header.height}"
        )

    fourcc, block_size = format_info
    image_offset = _image_offset(metadata, len(data))
    if image_offset is None:
        return None

    levels = [b""] * header.mipmap_count
    cursor = image_offset
    for mip in range(header.mipmap_count - 1, -1, -1):
        width = max(1, header.width >> mip)
        height = max(1, header.height >> mip)
        size = _block_data_size(width, height, block_size)
        end = cursor + size
        if end > len(data):
            raise ValueError(
                f"VTF image data is truncated at mip {mip}: needs {size} bytes, "
                f"has {max(0, len(data) - cursor)}"
            )
        levels[mip] = bytes(data[cursor:end])
        cursor = end

    top_level_size = _block_data_size(header.width, header.height, block_size)
    dds_header = _dds_header(
        header.width,
        header.height,
        header.mipmap_count,
        top_level_size,
        fourcc,
    )
    return dds_header + b"".join(levels)


def _image_offset(metadata: VTFMetadata, data_size: int) -> int | None:
    header = metadata.header
    if header.version >= (7, 3):
        resource = metadata.resource(RESOURCE_IMAGE)
        if resource is None:
            raise ValueError("VTF resource directory has no high-resolution image")
        if resource.is_inline:
            raise ValueError("VTF high-resolution image resource is inline")
        offset = resource.value
    else:
        thumbnail_size = _legacy_thumbnail_size(metadata)
        if thumbnail_size is None:
            return None
        offset = header.header_size + thumbnail_size

    if offset < header.header_size or offset > data_size:
        raise ValueError(f"VTF high-resolution image offset {offset} is outside the file")
    return offset


def _legacy_thumbnail_size(metadata: VTFMetadata) -> int | None:
    header = metadata.header
    if header.low_res_width == 0 and header.low_res_height == 0:
        return 0
    if header.low_res_width <= 0 or header.low_res_height <= 0:
        raise ValueError(
            f"VTF low-resolution dimensions must both be zero or positive, "
            f"got {header.low_res_width}x{header.low_res_height}"
        )
    format_info = _DDS_FORMATS.get(header.low_res_format)
    if format_info is None:
        return None
    return _block_data_size(header.low_res_width, header.low_res_height, format_info[1])


def _block_data_size(width: int, height: int, block_size: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_size


def _dds_header(
        width: int,
        height: int,
        mipmap_count: int,
        linear_size: int,
        fourcc: bytes,
) -> bytes:
    flags = _DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH | _DDSD_PIXELFORMAT | _DDSD_LINEARSIZE
    caps = _DDSCAPS_TEXTURE
    if mipmap_count > 1:
        flags |= _DDSD_MIPMAPCOUNT
        caps |= _DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP
    values = [
        124,
        flags,
        height,
        width,
        linear_size,
        0,
        mipmap_count,
        *([0] * 11),
        32,
        _DDPF_FOURCC,
        int.from_bytes(fourcc, "little"),
        0,
        0,
        0,
        0,
        0,
        caps,
        0,
        0,
        0,
        0,
    ]
    return struct.pack("<4s31I", b"DDS ", *values)
