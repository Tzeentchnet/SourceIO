from dataclasses import dataclass
from typing import Any

from ....utils import Buffer, MemoryBuffer

from ..base import BaseBlock
from .enums import VTexExtraData, VTexFlags, VTexFormat, VTexMipCompression
from .metadata import (
    CubemapRadianceMetadata,
    SpriteSheetMetadata,
    TextureArtifact,
    TextureDisplayMetadata,
    TextureImportSettings,
    TextureMetadata,
    TextureMipLayout,
    TextureSubresource,
    parse_display_metadata,
    parse_sprite_sheet,
)


@dataclass(slots=True)
class TextureInfo:
    version: int
    flags: VTexFlags
    reflectivity: tuple[float, float, float, float]
    width: int
    height: int
    depth: int
    pixel_format: VTexFormat
    mip_count: int
    picmip_resolution: int

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        version = buffer.read_uint16()
        if version != 1:
            raise NotImplementedError(f'Unknown texture info version: {version}, expected 1')
        flags = VTexFlags(buffer.read_uint16())
        reflectivity = buffer.read_fmt('4f')
        width, height, depth = buffer.read_fmt('3H')
        pixel_format = VTexFormat(buffer.read_uint8())
        mip, pic = buffer.read_fmt('BI')
        return cls(version, flags, reflectivity, width, height, depth, pixel_format, mip, pic)


@dataclass(slots=True)
class CompressedMip:
    compression_method: VTexMipCompression
    sizes_offset: int
    mip_count: int
    mip_sizes: list[int]
    raw_data: bytes

    @classmethod
    def from_buffer(cls, buffer: Buffer) -> 'CompressedMip':
        compression_method, sizes_offset, mip_count = buffer.read_fmt('3I')
        try:
            method = VTexMipCompression(compression_method)
        except ValueError as exc:
            raise ValueError(f"Unknown VTEX mip compression method {compression_method}") from exc
        return cls(method, sizes_offset, mip_count, [], bytes(buffer.data))

    @property
    def compressed(self) -> bool:
        return self.compression_method is not VTexMipCompression.NONE

    @property
    def unk(self) -> int:
        return self.sizes_offset


@dataclass(slots=True)
class TextureData(BaseBlock):
    texture_info: TextureInfo
    extra_data: dict[VTexExtraData | int, Any]
    raw_extra_data: dict[VTexExtraData | int, bytes]

    @classmethod
    def from_buffer(cls, buffer: Buffer) -> 'BaseBlock':
        texture_info = TextureInfo.from_buffer(buffer)
        extra_data = {}
        raw_extra_data = {}
        extra_data_offset = buffer.read_relative_offset32()
        extra_data_count = buffer.read_uint32()

        if extra_data_count > 0:
            with buffer.read_from_offset(extra_data_offset):
                for _ in range(extra_data_count):
                    raw_extra_type = buffer.read_uint32()
                    try:
                        extra_type: VTexExtraData | int = VTexExtraData(raw_extra_type)
                    except ValueError:
                        extra_type = raw_extra_type
                    offset_field = buffer.tell()
                    payload_offset = offset_field + buffer.read_uint32()
                    size = buffer.read_uint32()
                    if payload_offset < 0 or payload_offset + size > buffer.size():
                        raise ValueError(
                            f"VTEX extra-data type {raw_extra_type} points outside DATA: "
                            f"offset {payload_offset}, size {size}, DATA size {buffer.size()}"
                        )
                    with buffer.read_from_offset(payload_offset):
                        raw_data = buffer.read(size)
                    raw_extra_data[extra_type] = raw_data
                    extra_buffer = MemoryBuffer(raw_data)

                    if extra_type == VTexExtraData.COMPRESSED_MIP_SIZE:
                        compressed_mip = CompressedMip.from_buffer(extra_buffer)
                        mip_sizes_offset = payload_offset + 4 + compressed_mip.sizes_offset
                        with buffer.read_from_offset(mip_sizes_offset):
                            compressed_mip.mip_sizes.extend(
                                buffer.read_uint32() for _ in range(compressed_mip.mip_count)
                            )
                        extra_data[extra_type] = compressed_mip
                    elif extra_type == VTexExtraData.SHEET:
                        extra_data[extra_type] = parse_sprite_sheet(raw_data)
                    elif extra_type == VTexExtraData.METADATA:
                        extra_data[extra_type] = parse_display_metadata(raw_data)
                    elif extra_type == VTexExtraData.CUBEMAP_RADIANCE_SH:
                        coefficients_offset = extra_buffer.read_uint32()
                        coefficient_count = extra_buffer.read_uint32()
                        with buffer.read_from_offset(payload_offset + coefficients_offset):
                            coefficient_data = buffer.read(coefficient_count * 4)
                        coefficient_buffer = MemoryBuffer(coefficient_data)
                        coefficients = tuple(coefficient_buffer.read_float() for _ in range(coefficient_count))
                        extra_data[extra_type] = CubemapRadianceMetadata(
                            coefficients,
                            raw_data,
                            coefficient_data,
                        )
                    else:
                        extra_data[extra_type] = raw_data

        return cls(texture_info, extra_data, raw_extra_data)

    @property
    def metadata(self) -> TextureMetadata:
        sprite_sheet = self.extra_data.get(VTexExtraData.SHEET)
        display = self.extra_data.get(VTexExtraData.METADATA)
        radiance = self.extra_data.get(VTexExtraData.CUBEMAP_RADIANCE_SH)
        return TextureMetadata(
            self.raw_extra_data,
            sprite_sheet if isinstance(sprite_sheet, SpriteSheetMetadata) else None,
            display if isinstance(display, TextureDisplayMetadata) else None,
            radiance if isinstance(radiance, CubemapRadianceMetadata) else None,
        )

    def to_buffer(self, buffer: Buffer) -> None:
        raise NotImplementedError('TextureData.to_buffer is not implemented yet')
        # self.texture_info.to_buffer(buffer)
