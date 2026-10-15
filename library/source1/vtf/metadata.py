"""Read VTF header fields and the 7.3+ resource directory without decoding pixels.

The native VTF module only exposes the image itself, so the sprite sheet and the other resources are read here.
Layouts follow Valve's ``public/vtf/vtf.h`` and were checked against every sheet VTF in TF2:

* ``\\x10\\0\\0`` sheet: ``int32 version, uint32 sequence_count``, then per sequence ``int32 id, int32 clamp,
  uint32 frame_count, float total_time`` and per frame ``float duration`` plus one (version 0) or four (version 1)
  ``float u0, v0, u1, v1`` rectangles. Clamp is a full little-endian int that the engine tests for ``!= 0``.
* ``CRC``, ``LOD``, ``TS0`` and ``STR`` keep their four bytes in the directory entry itself.
* ``KVD`` is VTFLib's free-form KeyValues text; ``+\\0\\0`` is Strata Source's hotspot rectangle list.
"""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field
from typing import Any

from ...shared.sprite_sheet import SpriteSheetFrame, SpriteSheetImage, SpriteSheetMetadata, SpriteSheetSequence

VTF_SIGNATURE = b"VTF\0"

RESOURCE_LOW_RES_IMAGE = b"\x01\0\0"
RESOURCE_IMAGE = b"\x30\0\0"
RESOURCE_SHEET = b"\x10\0\0"
RESOURCE_CRC = b"CRC"
RESOURCE_LOD = b"LOD"
RESOURCE_SETTINGS_EX = b"TS0"
RESOURCE_STREAM = b"STR"
RESOURCE_KEYVALUES = b"KVD"
RESOURCE_HOTSPOT = b"+\0\0"

RESOURCE_NAMES = {
    RESOURCE_LOW_RES_IMAGE: "low_res_image",
    RESOURCE_IMAGE: "image",
    RESOURCE_SHEET: "sheet",
    RESOURCE_CRC: "crc",
    RESOURCE_LOD: "lod",
    RESOURCE_SETTINGS_EX: "settings_ex",
    RESOURCE_STREAM: "stream",
    RESOURCE_KEYVALUES: "keyvalues",
    RESOURCE_HOTSPOT: "hotspot",
}

#: ``RSRCF_HAS_NO_DATA_CHUNK``: the entry holds a 32-bit value instead of an offset.
RESOURCE_FLAG_NO_DATA = 0x02
#: ``MAX_RSRC_DICTIONARY_ENTRIES`` in vtf.h.
MAX_RESOURCES = 32
#: ``MAX_SEQUENCES`` in the engine's sheet reader.
MAX_SHEET_SEQUENCES = 64

_HEADER = struct.Struct("<4s2IIHHIHH4x3f4xfIBIBB")
_DEPTH = struct.Struct("<H")
_RESOURCE_COUNT = struct.Struct("<3xI8x")
_RESOURCE_ENTRY = struct.Struct("<3sBI")


@dataclass(frozen=True, slots=True)
class VTFHeader:
    version: tuple[int, int]
    header_size: int
    width: int
    height: int
    flags: int
    frame_count: int
    first_frame: int
    reflectivity: tuple[float, float, float]
    bump_scale: float
    image_format: int
    mipmap_count: int
    low_res_format: int
    low_res_width: int
    low_res_height: int
    depth: int

    @property
    def version_string(self) -> str:
        return f"{self.version[0]}.{self.version[1]}"


@dataclass(frozen=True, slots=True)
class VTFResource:
    tag: bytes
    flags: int
    #: The 32-bit value of an entry flagged ``RESOURCE_FLAG_NO_DATA``, else the offset of its data block.
    value: int
    #: The data block's contents; ``None`` for an inline value.
    data: bytes | None = field(default=None, repr=False)

    @property
    def is_inline(self) -> bool:
        return bool(self.flags & RESOURCE_FLAG_NO_DATA)

    @property
    def name(self) -> str:
        return RESOURCE_NAMES.get(self.tag, self.tag.hex())

    @property
    def inline_bytes(self) -> bytes:
        return struct.pack("<I", self.value)


@dataclass(frozen=True, slots=True)
class HotspotRect:
    flags: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int

    @property
    def random_rotation(self) -> bool:
        return bool(self.flags & 0x1)

    @property
    def random_reflection(self) -> bool:
        return bool(self.flags & 0x2)

    @property
    def is_alternate(self) -> bool:
        return bool(self.flags & 0x4)


@dataclass(frozen=True, slots=True)
class HotspotMetadata:
    version: int
    flags: int
    rects: tuple[HotspotRect, ...]


@dataclass(frozen=True, slots=True)
class VTFMetadata:
    header: VTFHeader
    resources: tuple[VTFResource, ...] = ()
    sprite_sheet: SpriteSheetMetadata | None = None
    sprite_sheet_version: int | None = None
    crc: int | None = None
    #: ``(clamp_x, clamp_y)``: the largest mip, as a power of two, used at the highest texture detail.
    lod_clamp: tuple[int, int] | None = None
    settings_ex: int | None = None
    #: ``(first_available_mip, last_available_mip)``.
    stream_mips: tuple[int, int] | None = None
    keyvalues: str | None = None
    hotspot: HotspotMetadata | None = None
    #: Resources that are present but could not be decoded. The raw bytes stay in ``resources``.
    warnings: tuple[str, ...] = ()

    def resource(self, tag: bytes) -> VTFResource | None:
        return next((resource for resource in self.resources if resource.tag == tag), None)

    def to_dict(self) -> dict[str, Any]:
        header = self.header
        result: dict[str, Any] = {
            "version": header.version_string,
            "width": header.width,
            "height": header.height,
            "depth": header.depth,
            "flags": header.flags,
            "frame_count": header.frame_count,
            "first_frame": header.first_frame,
            "mipmap_count": header.mipmap_count,
            "image_format": header.image_format,
            "low_res_format": header.low_res_format,
            "low_res_size": [header.low_res_width, header.low_res_height],
            "reflectivity": list(header.reflectivity),
            "bump_scale": header.bump_scale,
            "resources": [
                {
                    "tag": resource.tag.hex(),
                    "name": resource.name,
                    "flags": resource.flags,
                    "inline": resource.is_inline,
                    "size": None if resource.data is None else len(resource.data),
                }
                for resource in self.resources
            ],
        }
        if self.sprite_sheet is not None:
            result["sprite_sheet_version"] = self.sprite_sheet_version
            result["sprite_sheet"] = self.sprite_sheet.to_dict()
            result["sprite_sheet_resource"] = base64.b64encode(self.sprite_sheet.raw_data).decode("ascii")
        if self.crc is not None:
            result["crc"] = self.crc
        if self.lod_clamp is not None:
            result["lod_clamp"] = list(self.lod_clamp)
        if self.settings_ex is not None:
            result["settings_ex"] = self.settings_ex
        if self.stream_mips is not None:
            result["stream_mips"] = list(self.stream_mips)
        if self.keyvalues is not None:
            result["keyvalues"] = self.keyvalues
        if self.hotspot is not None:
            result["hotspot"] = {
                "version": self.hotspot.version,
                "flags": self.hotspot.flags,
                "rects": [
                    {"flags": rect.flags, "min": [rect.min_x, rect.min_y], "max": [rect.max_x, rect.max_y]}
                    for rect in self.hotspot.rects
                ],
            }
        if self.warnings:
            result["warnings"] = list(self.warnings)
        return result


def read_vtf_header(data: bytes) -> VTFHeader:
    if len(data) < _HEADER.size or data[:4] != VTF_SIGNATURE:
        raise ValueError("Not a VTF file")
    (_, major, minor, header_size, width, height, flags, frame_count, first_frame,
     ref_r, ref_g, ref_b, bump_scale, image_format, mipmap_count, low_res_format,
     low_res_width, low_res_height) = _HEADER.unpack_from(data)
    if major != 7:
        raise ValueError(f"Unsupported VTF version {major}.{minor}")
    depth = 1
    if minor >= 2:
        if len(data) < _HEADER.size + _DEPTH.size:
            raise ValueError("VTF header is truncated")
        (depth,) = _DEPTH.unpack_from(data, _HEADER.size)
    return VTFHeader(
        (major, minor), header_size, width, height, flags, frame_count, first_frame,
        (ref_r, ref_g, ref_b), bump_scale, _signed(image_format), mipmap_count, _signed(low_res_format),
        low_res_width, low_res_height, depth,
    )


def read_vtf_metadata(data: bytes) -> VTFMetadata:
    """Parse the header and every resource of a VTF.

    Raises ``ValueError`` when the header or the resource directory is unreadable. A resource whose contents
    cannot be decoded is reported in ``warnings`` instead, so one bad block does not hide the rest.
    """
    header = read_vtf_header(data)
    if header.version < (7, 3):
        return VTFMetadata(header)

    directory = _HEADER.size + _DEPTH.size
    if len(data) < directory + _RESOURCE_COUNT.size:
        raise ValueError("VTF resource directory is truncated")
    (count,) = _RESOURCE_COUNT.unpack_from(data, directory)
    if count > MAX_RESOURCES:
        raise ValueError(f"VTF declares {count} resources; at most {MAX_RESOURCES} are allowed")
    entries_offset = directory + _RESOURCE_COUNT.size
    if len(data) < entries_offset + count * _RESOURCE_ENTRY.size:
        raise ValueError("VTF resource directory is truncated")

    resources = []
    for index in range(count):
        tag, flags, value = _RESOURCE_ENTRY.unpack_from(data, entries_offset + index * _RESOURCE_ENTRY.size)
        block = None
        # The image resources point at pixel data with no size prefix; it is decoded natively.
        if not flags & RESOURCE_FLAG_NO_DATA and tag not in (RESOURCE_IMAGE, RESOURCE_LOW_RES_IMAGE):
            if value + 4 > len(data):
                raise ValueError(f"VTF resource {tag!r} points outside the file")
            (size,) = struct.unpack_from("<I", data, value)
            if value + 4 + size > len(data):
                raise ValueError(f"VTF resource {tag!r} is truncated")
            block = bytes(data[value + 4:value + 4 + size])
        resources.append(VTFResource(tag, flags, value, block))

    decoded: dict[str, Any] = {}
    warnings = []
    for resource in resources:
        try:
            _decode_resource(resource, decoded)
        except (ValueError, struct.error) as ex:
            warnings.append(f"{resource.name}: {ex}")
    return VTFMetadata(header, tuple(resources), warnings=tuple(warnings), **decoded)


def parse_sheet_resource(data: bytes) -> tuple[int, SpriteSheetMetadata]:
    """Decode a Source 1 sheet resource into ``(sheet_version, sheet)``.

    Version 1 frames always store four rectangles. Unused ones are written either as copies of the first or as
    zero rectangles, so a frame keeps only its non-empty rectangles, with consecutive repeats collapsed.
    """
    reader = _Reader(data)
    version = reader.unpack("<i")
    sequence_count = reader.unpack("<I")
    if version not in (0, 1):
        raise ValueError(f"unknown sheet version {version}")
    if sequence_count > MAX_SHEET_SEQUENCES:
        raise ValueError(f"sheet declares {sequence_count} sequences; at most {MAX_SHEET_SEQUENCES} are allowed")
    images_per_frame = 4 if version else 1

    sequences = []
    for _ in range(sequence_count):
        sequence_id = reader.unpack("<i")
        clamp = reader.unpack("<i") != 0
        frame_count = reader.unpack("<I")
        total_time = reader.unpack("<f")
        if not 0 <= sequence_id < MAX_SHEET_SEQUENCES:
            raise ValueError(f"invalid sheet sequence number {sequence_id}")
        if frame_count * (4 + 16 * images_per_frame) > reader.remaining:
            raise ValueError(f"sheet sequence {sequence_id} declares {frame_count} frames past the end of the data")
        frames = []
        for _ in range(frame_count):
            duration = reader.unpack("<f")
            rects = [reader.unpack("<4f") for _ in range(images_per_frame)]
            frames.append(SpriteSheetFrame(duration, _frame_images(rects)))
        sequences.append(SpriteSheetSequence(
            sequence_id, clamp, False, False, False, total_time, "", {}, tuple(frames),
        ))
    return version, SpriteSheetMetadata(tuple(sequences), bytes(data))


def _frame_images(rects: list[tuple[float, float, float, float]]) -> tuple[SpriteSheetImage, ...]:
    kept: list[tuple[float, float, float, float]] = []
    for rect in rects:
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        if kept and kept[-1] == rect:
            continue
        kept.append(rect)
    if not kept:
        kept = rects[:1]
    return tuple(
        SpriteSheetImage((u0, v0), (u1, v1), (u0, v0), (u1, v1))
        for u0, v0, u1, v1 in kept
    )


def _decode_resource(resource: VTFResource, decoded: dict[str, Any]):
    tag = resource.tag
    if tag == RESOURCE_SHEET:
        decoded["sprite_sheet_version"], decoded["sprite_sheet"] = parse_sheet_resource(_block(resource))
    elif tag == RESOURCE_CRC:
        decoded["crc"] = resource.value if resource.is_inline else struct.unpack_from("<I", _block(resource))[0]
    elif tag == RESOURCE_LOD:
        raw = _small_value(resource)
        decoded["lod_clamp"] = (raw[0], raw[1])
    elif tag == RESOURCE_SETTINGS_EX:
        decoded["settings_ex"] = struct.unpack("<I", _small_value(resource))[0]
    elif tag == RESOURCE_STREAM:
        raw = _small_value(resource)
        decoded["stream_mips"] = (raw[0], raw[1])
    elif tag == RESOURCE_KEYVALUES:
        decoded["keyvalues"] = _block(resource).rstrip(b"\0").decode("utf8", "replace")
    elif tag == RESOURCE_HOTSPOT:
        decoded["hotspot"] = _parse_hotspot(_block(resource))


def _parse_hotspot(data: bytes) -> HotspotMetadata:
    reader = _Reader(data)
    version = reader.unpack("<B")
    if version != 1:
        raise ValueError(f"unknown hotspot version {version}")
    flags = reader.unpack("<B")
    count = reader.unpack("<H")
    rects = tuple(HotspotRect(*reader.unpack("<B4H")) for _ in range(count))
    return HotspotMetadata(version, flags, rects)


def _block(resource: VTFResource) -> bytes:
    if resource.data is None:
        raise ValueError("expected a data block, found an inline value")
    return resource.data


def _small_value(resource: VTFResource) -> bytes:
    if resource.is_inline:
        return resource.inline_bytes
    data = _block(resource)
    if len(data) < 4:
        raise ValueError(f"needs 4 bytes, has {len(data)}")
    return data[:4]


def _signed(value: int) -> int:
    return value - (1 << 32) if value >= 1 << 31 else value


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if size > self.remaining:
            raise ValueError(f"truncated at byte {self.offset}: needs {size}, has {self.remaining}")
        values = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return values[0] if len(values) == 1 else values
