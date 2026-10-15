"""Synthetic VTF files for the VTF metadata and editable-export tests."""
import struct

import numpy as np

from SourceIO.library.source1.vtf.metadata import RESOURCE_IMAGE

RGBA8888 = 0
NO_DATA = 0x02


def sheet_blob(version, sequences):
    """``sequences``: ``[(id, clamp, [(duration, [(u0, v0, u1, v1), ...]), ...]), ...]``."""
    images_per_frame = 4 if version else 1
    out = struct.pack("<iI", version, len(sequences))
    for sequence_id, clamp, frames in sequences:
        total = sum(duration for duration, _ in frames)
        out += struct.pack("<iiIf", sequence_id, clamp, len(frames), total)
        for duration, rects in frames:
            rects = list(rects) + [(0.0, 0.0, 0.0, 0.0)] * (images_per_frame - len(rects))
            out += struct.pack("<f", duration)
            for rect in rects[:images_per_frame]:
                out += struct.pack("<4f", *rect)
    return out


def build_vtf(pixels, resources=(), version=(7, 3), flags=0):
    """An uncompressed RGBA8888 VTF with one mip; ``resources`` are ``(tag, flags, int | bytes)``."""
    height, width = pixels.shape[:2]
    resources = list(resources)
    entries = len(resources) + 1
    header_size = 80 + 8 * entries if version >= (7, 3) else 80
    header_size = (header_size + 15) & ~15
    header = struct.pack(
        "<4s2IIHHIHH4x3f4xfIBIBB", b"VTF\0", version[0], version[1], header_size, width, height,
        flags, 1, 0, 0.5, 0.5, 0.5, 1.0, RGBA8888, 1, 0xFFFFFFFF, 0, 0,
    ) + struct.pack("<H", 1)
    if version < (7, 3):
        return (header.ljust(header_size, b"\0")) + pixels.tobytes()

    header += struct.pack("<3xI8x", entries)
    blocks = b""
    directory = []
    offset = header_size
    for tag, resource_flags, value in resources:
        if resource_flags & NO_DATA:
            directory.append((tag, resource_flags, value))
        else:
            directory.append((tag, resource_flags, offset))
            block = struct.pack("<I", len(value)) + value
            blocks += block
            offset += len(block)
    directory.append((RESOURCE_IMAGE, 0, offset))
    header += b"".join(struct.pack("<3sBI", *entry) for entry in directory)
    return header.ljust(header_size, b"\0") + blocks + pixels.tobytes()


def checker(width=8, height=4):
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[..., 0] = np.arange(width, dtype=np.uint8)[None, :] * 30
    pixels[..., 1] = np.arange(height, dtype=np.uint8)[:, None] * 60
    pixels[..., 2] = 7
    pixels[..., 3] = 255
    return pixels


def cell(x, width=8, height=4, size=4):
    """Valve's half-texel-inset UVs for the ``size``-pixel cell starting at column ``x``."""
    return (x + 0.5) / width, 0.5 / height, (x + size - 0.5) / width, (size - 0.5) / height


def read_tga(path):
    data = path.read_bytes()
    width, height = struct.unpack_from("<HH", data, 12)
    assert data[2] == 2 and data[16] == 32
    pixels = np.frombuffer(data[18:], dtype=np.uint8).reshape(height, width, 4)
    if not data[17] & 0x20:
        pixels = pixels[::-1]
    return pixels[..., [2, 1, 0, 3]]
