from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

import numpy as np
import numpy.typing as npt

from ...interfaces import Diagnostic
from .enums import VTexExtraData, VTexFormat


MAX_SHEET_SEQUENCES = 4096
MAX_SHEET_FRAMES = 65536
MAX_SHEET_IMAGES = 65536
MAX_SHEET_PARAMETERS = 4096


@dataclass(frozen=True, slots=True)
class TextureImportSettings:
    """Caller-controlled Source 2 texture selection and decode behavior."""

    mip_level: int = 0
    array_layer: int | None = None
    volume_slice: int | None = None
    cubemap_face: int | None = None
    invert_y: bool = False
    decode_packed_channels: bool = True

    def with_invert_y(self, invert_y: bool) -> TextureImportSettings:
        return replace(self, invert_y=invert_y)

    def cache_identity(
            self,
            *,
            resolved_mip: int | None = None,
            decode_semantics: tuple[str, ...] = (),
            pixel_format: VTexFormat | None = None,
    ) -> str:
        mip = self.mip_level if resolved_mip is None else resolved_mip
        parts = (
            f"mip={mip}",
            f"array={self.array_layer if self.array_layer is not None else 'all'}",
            f"volume={self.volume_slice if self.volume_slice is not None else 'all'}",
            f"face={self.cubemap_face if self.cubemap_face is not None else 'all'}",
            f"invert_y={int(self.invert_y)}",
            f"decode_packed={int(self.decode_packed_channels)}",
            f"format={pixel_format.name if pixel_format is not None else 'unknown'}",
            "semantics=" + ",".join(decode_semantics),
        )
        return ";".join(parts)

    def cache_digest(self, **kwargs: Any) -> str:
        return hashlib.sha256(self.cache_identity(**kwargs).encode("utf8")).hexdigest()

    @property
    def is_default(self) -> bool:
        return self == TextureImportSettings()


@dataclass(frozen=True, slots=True)
class TextureSubresource:
    array_layer: int = 0
    volume_slice: int = 0
    cubemap_face: int | None = None


@dataclass(frozen=True, slots=True)
class TextureMipLayout:
    mip_level: int
    width: int
    height: int
    depth: int
    array_layers: int
    face_count: int
    offset: int
    stored_size: int
    decoded_size: int

    @property
    def subresource_count(self) -> int:
        return self.depth * self.array_layers * self.face_count


@dataclass(frozen=True, slots=True)
class SpriteSheetImage:
    cropped_min: tuple[float, float]
    cropped_max: tuple[float, float]
    uncropped_min: tuple[float, float]
    uncropped_max: tuple[float, float]

    @staticmethod
    def _pixel_rect(
            minimum: tuple[float, float],
            maximum: tuple[float, float],
            width: int,
            height: int,
    ) -> tuple[int, int, int, int]:
        if maximum[0] <= minimum[0] or maximum[1] <= minimum[1]:
            return 0, 0, 0, 0
        return (
            int(minimum[0] * width),
            int(minimum[1] * height),
            min(int(maximum[0] * width) + 1, width),
            min(int(maximum[1] * height) + 1, height),
        )

    def cropped_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._pixel_rect(self.cropped_min, self.cropped_max, width, height)

    def uncropped_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._pixel_rect(self.uncropped_min, self.uncropped_max, width, height)


@dataclass(frozen=True, slots=True)
class SpriteSheetFrame:
    display_time: float
    images: tuple[SpriteSheetImage, ...]


@dataclass(frozen=True, slots=True)
class SpriteSheetSequence:
    sequence_id: int
    clamp: bool
    alpha_crop: bool
    no_color: bool
    no_alpha: bool
    total_time: float
    name: str
    float_params: Mapping[str, float]
    frames: tuple[SpriteSheetFrame, ...]


@dataclass(frozen=True, slots=True)
class SpriteSheetMetadata:
    sequences: tuple[SpriteSheetSequence, ...]
    raw_data: bytes = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class TextureDisplayMetadata:
    display_width: int
    display_height: int
    motion_vectors_max_distance: int
    range_min: tuple[float, float, float, float]
    range_max: tuple[float, float, float, float]
    raw_data: bytes = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CubemapRadianceMetadata:
    coefficients: tuple[float, ...]
    raw_data: bytes = field(repr=False, compare=False)
    coefficient_data: bytes = field(repr=False, compare=False)

    @property
    def cubemap_count(self) -> int | None:
        return len(self.coefficients) // 27 if len(self.coefficients) % 27 == 0 else None


@dataclass(frozen=True, slots=True)
class TextureMetadata:
    raw_extra_data: Mapping[VTexExtraData | int, bytes]
    sprite_sheet: SpriteSheetMetadata | None = None
    display: TextureDisplayMetadata | None = None
    cubemap_radiance: CubemapRadianceMetadata | None = None


@dataclass(slots=True)
class TextureArtifact:
    """Decoded pixels or an embedded image plus exact selection metadata."""

    settings: TextureImportSettings
    requested_mip_level: int
    mip_level: int
    width: int
    height: int
    depth: int
    array_layers: int
    face_count: int
    pixel_format: VTexFormat
    is_hdr: bool
    cache_identity: str
    metadata: TextureMetadata
    subresources: tuple[TextureSubresource, ...]
    decode_semantics: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    pixels: npt.NDArray[np.float32] | None = None
    encoded_data: bytes | None = None
    encoded_extension: str | None = None

    @property
    def memory_size(self) -> int:
        if self.pixels is not None:
            return int(self.pixels.nbytes)
        return len(self.encoded_data) if self.encoded_data is not None else 0

    @property
    def subresource_count(self) -> int:
        return len(self.subresources)

    def image_pixels(self) -> npt.NDArray[np.float32] | None:
        if self.pixels is None:
            return None
        if self.pixels.ndim == 4:
            return self.pixels.reshape(self.subresource_count * self.height, self.width, self.pixels.shape[-1])
        return self.pixels


class _BlobReader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def _unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.data):
            raise ValueError(
                f"Texture metadata is truncated at {self.offset}: "
                f"needs {size} bytes, has {len(self.data) - self.offset}"
            )
        result = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return result[0] if len(result) == 1 else result

    def uint32(self) -> int:
        return self._unpack("<I")

    def int32(self) -> int:
        return self._unpack("<i")

    def float32(self) -> float:
        return self._unpack("<f")

    def boolean(self) -> bool:
        return bool(self._unpack("<B"))

    def vector2(self) -> tuple[float, float]:
        return self._unpack("<2f")

    def offset_string(self) -> str:
        field_offset = self.offset
        relative_offset = self.int32()
        if relative_offset == 0:
            return ""
        string_offset = field_offset + relative_offset
        if not 0 <= string_offset < len(self.data):
            raise ValueError(f"Sprite-sheet string offset {string_offset} is outside {len(self.data)} bytes")
        end = self.data.find(b"\0", string_offset)
        if end < 0:
            raise ValueError(f"Sprite-sheet string at {string_offset} is not null terminated")
        return self.data[string_offset:end].decode("utf8")

    def seek(self, offset: int):
        if not 0 <= offset <= len(self.data):
            raise ValueError(f"Texture metadata offset {offset} is outside {len(self.data)} bytes")
        self.offset = offset


def _bounded_count(value: int, maximum: int, label: str) -> int:
    if value > maximum:
        raise ValueError(f"Sprite sheet declares {value} {label}; maximum supported is {maximum}")
    return value


def parse_sprite_sheet(data: bytes) -> SpriteSheetMetadata:
    reader = _BlobReader(data)
    sequences_offset = reader.int32()
    sequence_count = _bounded_count(reader.uint32(), MAX_SHEET_SEQUENCES, "sequences")
    reader.seek(sequences_offset)
    sequences = []

    for _ in range(sequence_count):
        sequence_id = reader.uint32()
        clamp = reader.boolean()
        alpha_crop = reader.boolean()
        no_color = reader.boolean()
        no_alpha = reader.boolean()

        frames_field = reader.offset
        frames_offset = frames_field + reader.int32()
        frame_count = _bounded_count(reader.uint32(), MAX_SHEET_FRAMES, "frames")
        total_time = reader.float32()
        name = reader.offset_string()
        params_field = reader.offset
        params_offset = params_field + reader.int32()
        params_count = _bounded_count(reader.uint32(), MAX_SHEET_PARAMETERS, "parameters")
        next_sequence_offset = reader.offset

        params: dict[str, float] = {}
        if params_count:
            reader.seek(params_offset)
            for _ in range(params_count):
                parameter_name = reader.offset_string()
                params[parameter_name] = reader.float32()

        reader.seek(frames_offset)
        frames = []
        for _ in range(frame_count):
            display_time = reader.float32()
            images_field = reader.offset
            images_offset = images_field + reader.int32()
            image_count = _bounded_count(reader.uint32(), MAX_SHEET_IMAGES, "images")
            next_frame_offset = reader.offset

            reader.seek(images_offset)
            images = tuple(
                SpriteSheetImage(
                    reader.vector2(),
                    reader.vector2(),
                    reader.vector2(),
                    reader.vector2(),
                )
                for _ in range(image_count)
            )
            frames.append(SpriteSheetFrame(display_time, images))
            reader.seek(next_frame_offset)

        sequences.append(SpriteSheetSequence(
            sequence_id,
            clamp,
            alpha_crop,
            no_color,
            no_alpha,
            total_time,
            name,
            params,
            tuple(frames),
        ))
        reader.seek(next_sequence_offset)

    return SpriteSheetMetadata(tuple(sequences), data)


def parse_display_metadata(data: bytes) -> TextureDisplayMetadata:
    if len(data) < 40:
        raise ValueError(f"Texture display metadata requires 40 bytes, got {len(data)}")
    unused, width, height, motion = struct.unpack_from("<HHHh", data)
    if unused != 0:
        raise ValueError(f"Texture display metadata reserved field is {unused}, expected 0")
    range_min = struct.unpack_from("<4f", data, 8)
    range_max = struct.unpack_from("<4f", data, 24)
    return TextureDisplayMetadata(width, height, motion, range_min, range_max, data)
