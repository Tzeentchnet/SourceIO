import logging
from dataclasses import dataclass, field
from os import PathLike

import numpy as np
import numpy.typing as npt

from ..blocks.resource_edit_info import ResourceEditInfo, ResourceEditInfo2
from ..compiled_file_header import CompiledHeader
from ..exceptions import Source2Error
from ..interfaces import Diagnostic, DiagnosticSeverity, Maturity, ResourceCapabilities, ResourceKind
from ...utils import Buffer, MemoryBuffer, TinyPath
from ...utils.pylib.compression import lz4_decompress, zstd_decompress
from ...utils.pylib.image import decode_texture
from ..blocks.texture_data import CompressedMip, TextureData, VTexExtraData, \
    VTexFlags, VTexFormat, VTexMipCompression, TextureArtifact, TextureImportSettings, \
    TextureMipLayout, TextureSubresource
from ..compiled_resource import CompiledResource

logger = logging.getLogger('CompiledTextureResource')

# Formats that embed a complete image file instead of pixel data.
ENCODED_IMAGE_EXTENSIONS = {
    VTexFormat.PNG_RGBA8888: 'png',
    VTexFormat.PNG_DXT5: 'png',
    VTexFormat.JPEG_RGBA8888: 'jpg',
    VTexFormat.JPEG_DXT5: 'jpg',
    VTexFormat.WEBP_RGBA8888: 'webp',
    VTexFormat.WEBP_DXT5: 'webp',
}

# Uncompressed formats: (dtype, channel count, divisor that maps values to 0..1, or None for float data).
# Channels fill R, G, B, A in order; missing color channels stay 0 and missing alpha is 1, as in VRF.
UNCOMPRESSED_FORMATS = {
    VTexFormat.R8_UNORM: (np.uint8, 1, 255),
    VTexFormat.R16: (np.uint16, 1, 65535),
    VTexFormat.RG1616: (np.uint16, 2, 65535),
    VTexFormat.RGBA16161616: (np.uint16, 4, 65535),
    VTexFormat.R16F: (np.float16, 1, None),
    VTexFormat.RG1616F: (np.float16, 2, None),
    VTexFormat.R32F: (np.float32, 1, None),
    VTexFormat.RG3232F: (np.float32, 2, None),
    VTexFormat.RGB323232F: (np.float32, 3, None),
    VTexFormat.RGBA32323232F: (np.float32, 4, None),
}

# EAC modifier table (Khronos ETC2 spec, same values as VRF's CommonEAC).
_EAC_MODIFIERS = np.array([
    [-3, -6, -9, -15, 2, 5, 8, 14], [-3, -7, -10, -13, 2, 6, 9, 12],
    [-2, -5, -8, -13, 1, 4, 7, 12], [-2, -4, -6, -13, 1, 3, 5, 12],
    [-3, -6, -8, -12, 2, 5, 7, 11], [-3, -7, -9, -11, 2, 6, 8, 10],
    [-4, -7, -8, -11, 3, 6, 7, 10], [-3, -5, -8, -11, 2, 4, 7, 10],
    [-2, -6, -8, -10, 1, 5, 7, 9], [-2, -5, -8, -10, 1, 4, 7, 9],
    [-2, -4, -8, -10, 1, 3, 7, 9], [-2, -5, -7, -10, 1, 4, 6, 9],
    [-3, -4, -7, -10, 2, 3, 6, 9], [-1, -2, -3, -10, 0, 1, 2, 9],
    [-4, -6, -8, -9, 3, 5, 7, 8], [-3, -5, -7, -9, 2, 4, 6, 8],
], np.int32)


def _decode_eac_blocks(blocks: np.ndarray, alpha: bool) -> np.ndarray:
    """Decode (N, 8) EAC blocks into (N, 4, 4) values in 0..1, indexed [block, y, x].

    ``alpha`` selects the 8-bit ETC2 alpha variant, otherwise the 11-bit R11/RG11 variant.
    """
    blocks = blocks.astype(np.int64)
    base = blocks[:, 0:1]
    multiplier = blocks[:, 1:2] >> 4
    table = blocks[:, 1] & 0xF
    bits = np.zeros(len(blocks), np.int64)
    for byte in range(2, 8):
        bits = (bits << 8) | blocks[:, byte]
    shifts = 45 - 3 * np.arange(16)
    indices = (bits[:, None] >> shifts) & 7
    modifiers = _EAC_MODIFIERS[table[:, None], indices]
    if alpha:
        values = np.where(multiplier == 0, base, base + modifiers * multiplier)
        values = np.clip(values, 0, 255) / 255
    else:
        step = np.where(multiplier == 0, 1, multiplier * 8)
        values = np.clip(base * 8 + 4 + modifiers * step, 0, 2047) / 2047
    # Pixels are stored column-major: entry i is at x = i // 4, y = i % 4.
    return values.reshape(-1, 4, 4).transpose(0, 2, 1).astype(np.float32)


def _blocks_to_image(block_values: np.ndarray, width: int, height: int) -> np.ndarray:
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    image = block_values.reshape(blocks_y, blocks_x, 4, 4).transpose(0, 2, 1, 3).reshape(blocks_y * 4, blocks_x * 4)
    return image[:height, :width]


HDR_FORMATS = {
    VTexFormat.BC6H,
    VTexFormat.R16,
    VTexFormat.RG1616,
    VTexFormat.RGBA16161616,
    VTexFormat.R16F,
    VTexFormat.RG1616F,
    VTexFormat.RGBA16161616F,
    VTexFormat.R32F,
    VTexFormat.RG3232F,
    VTexFormat.RGB323232F,
    VTexFormat.RGBA32323232F,
}

BLOCK_COMPRESSED_FORMATS = {
    VTexFormat.DXT1,
    VTexFormat.DXT5,
    VTexFormat.BC6H,
    VTexFormat.BC7,
    VTexFormat.ETC2,
    VTexFormat.ETC2_EAC,
    VTexFormat.R11_EAC,
    VTexFormat.RG11_EAC,
    VTexFormat.ATI1N,
    VTexFormat.ATI2N,
}

POTENTIALLY_PACKED_FORMATS = {VTexFormat.DXT5, VTexFormat.BC7, VTexFormat.ATI2N}
MAX_CACHED_MIPS = 2


@dataclass(slots=True)
class CompiledTextureResource(CompiledResource):
    _cached_mips: dict[tuple[int, bool, tuple[str, ...]], npt.NDArray[np.float32]] = field(default_factory=dict)

    resource_kind = ResourceKind.TEXTURE
    declared_capabilities = ResourceCapabilities(
        read=Maturity.STABLE,
        extract=Maturity.STABLE,
        render=Maturity.STABLE,
    )

    @classmethod
    def from_buffer(
            cls,
            buffer: Buffer | bytes | bytearray | memoryview,
            filename: TinyPath | str | PathLike[str] | None = None,
    ):
        if isinstance(buffer, Buffer):
            raw_data = buffer.read()
        elif isinstance(buffer, (bytes, bytearray, memoryview)):
            raw_data = bytes(buffer)
        else:
            raise TypeError(
                "buffer must be a SourceIO Buffer or bytes-like object, "
                f"got {type(buffer).__name__}"
            )
        path = TinyPath(filename or "<memory>")
        if len(raw_data) < 4:
            return super().from_buffer(raw_data, path)
        declared_resource_size = int.from_bytes(raw_data[:4], "little")
        if not 0 < declared_resource_size <= len(raw_data):
            return super().from_buffer(raw_data, path)

        # VTEX stores its mip payload after the resource section counted by the compiled header.
        try:
            header = CompiledHeader.from_buffer(MemoryBuffer(raw_data[:declared_resource_size]))
        except Source2Error as exc:
            if exc.path is None:
                exc.path = str(path)
            raise
        return cls(
            MemoryBuffer(raw_data),
            path,
            header,
            _capabilities=cls.declared_capabilities,
        )

    def get_data_block_type(self):
        return TextureData

    @staticmethod
    def _resolve_mip_level(texture_info, mip_level: int) -> int:
        if texture_info.mip_count < 1:
            raise ValueError("Texture declares no mip levels")
        return min(max(int(mip_level), 0), texture_info.mip_count - 1)

    @staticmethod
    def _mip_dimensions(data_block: TextureData, mip_level: int) -> tuple[int, int, int, int, int]:
        texture_info = data_block.texture_info
        width = max(texture_info.width >> mip_level, 1)
        height = max(texture_info.height >> mip_level, 1)
        flags = texture_info.flags
        if flags & VTexFlags.VOLUME_TEXTURE:
            depth = max(texture_info.depth >> mip_level, 1)
            array_layers = 1
        else:
            depth = 1
            array_layers = max(texture_info.depth, 1)
        face_count = 6 if flags & VTexFlags.CUBE_TEXTURE else 1
        return width, height, depth, array_layers, face_count

    @staticmethod
    def _calculate_slice_size(pixel_format: VTexFormat, width: int, height: int) -> int:
        block_size = VTexFormat.block_size(pixel_format)
        if pixel_format in BLOCK_COMPRESSED_FORMATS:
            width = max((width + 3) & ~3, 4)
            height = max((height + 3) & ~3, 4)
            return width * height // 16 * block_size
        return width * height * block_size

    @staticmethod
    def _calculate_buffer_size_for_mip(data_block: TextureData, mip_level):
        texture_info = data_block.texture_info
        width, height, depth, array_layers, face_count = CompiledTextureResource._mip_dimensions(
            data_block,
            mip_level,
        )
        subresources = depth * array_layers * face_count
        if texture_info.pixel_format in BLOCK_COMPRESSED_FORMATS and 1 < subresources < 4:
            subresources = 4
        return (
            CompiledTextureResource._calculate_slice_size(texture_info.pixel_format, width, height)
            * subresources
        )

    def _data_block(self) -> TextureData:
        data_block = self.get_block(TextureData, block_name="DATA")
        if data_block is None:
            raise ValueError("Compiled texture has no DATA block")
        return data_block

    def _payload_offset(self) -> int:
        info_block = next((block for block in self._header.blocks if block.name == "DATA"), None)
        if info_block is None:
            raise ValueError("Compiled texture has no DATA block descriptor")
        return info_block.absolute_offset + info_block.size

    def get_mip_layout(self, mip_level: int = 0) -> TextureMipLayout:
        data_block = self._data_block()
        texture_info = data_block.texture_info
        mip_level = self._resolve_mip_level(texture_info, mip_level)
        compression_info: CompressedMip | None = data_block.extra_data.get(VTexExtraData.COMPRESSED_MIP_SIZE)
        if compression_info is not None and len(compression_info.mip_sizes) < texture_info.mip_count:
            raise ValueError(
                f"Texture has {texture_info.mip_count} mips but compression metadata has "
                f"{len(compression_info.mip_sizes)} sizes"
            )

        offset = 0
        for level in range(texture_info.mip_count - 1, mip_level, -1):
            offset += (
                compression_info.mip_sizes[level]
                if compression_info is not None
                else self._calculate_buffer_size_for_mip(data_block, level)
            )

        width, height, depth, array_layers, face_count = self._mip_dimensions(data_block, mip_level)
        decoded_size = self._calculate_buffer_size_for_mip(data_block, mip_level)
        stored_size = compression_info.mip_sizes[mip_level] if compression_info is not None else decoded_size
        return TextureMipLayout(
            mip_level,
            width,
            height,
            depth,
            array_layers,
            face_count,
            offset,
            stored_size,
            decoded_size,
        )

    def get_mip_layouts(self) -> tuple[TextureMipLayout, ...]:
        mip_count = self._data_block().texture_info.mip_count
        return tuple(self.get_mip_layout(level) for level in range(mip_count))

    def get_texture_format(self) -> VTexFormat:
        return self._data_block().texture_info.pixel_format

    def is_hdr(self) -> bool:
        return self.get_texture_format() in HDR_FORMATS

    def get_encoded_image(self) -> tuple[bytes, str] | None:
        """Return ``(file bytes, extension)`` for textures that embed a PNG/JPEG/WEBP file, else None."""
        extension = ENCODED_IMAGE_EXTENSIONS.get(self.get_texture_format())
        if extension is None:
            return None
        buffer = self._buffer
        buffer.seek(self._payload_offset())
        data = buffer.read()
        if extension == 'png':
            size = 8
            while size + 8 <= len(data):
                chunk_length = int.from_bytes(data[size:size + 4], 'big')
                chunk_type = data[size + 4:size + 8]
                size += chunk_length + 12
                if chunk_type == b'IEND':
                    break
            data = data[:size]
        elif extension == 'webp' and data[:4] == b'RIFF':
            data = data[:8 + int.from_bytes(data[4:8], 'little')]
        return data, extension

    def is_cubemap(self) -> bool:
        return bool(self._data_block().texture_info.flags & VTexFlags.CUBE_TEXTURE)

    def is_texture_array(self) -> bool:
        return bool(self._data_block().texture_info.flags & VTexFlags.TEXTURE_ARRAY)

    def is_volume_texture(self) -> bool:
        return bool(self._data_block().texture_info.flags & VTexFlags.VOLUME_TEXTURE)

    def get_resolution(self, mip_level: int = 0):
        data_block = self._data_block()
        texture_info = data_block.texture_info
        mip_level = self._resolve_mip_level(texture_info, mip_level)
        return self._mip_dimensions(data_block, mip_level)[:2]

    def get_cubemap_face(self, face: int = 0, mip_level: int = 0):
        if not self.is_cubemap():
            return None
        if not 0 <= face < 6:
            raise ValueError(f"Cubemap face must be in range 0..5, got {face}")
        artifact = self.get_texture_artifact(TextureImportSettings(mip_level=mip_level, cubemap_face=face))
        return artifact.image_pixels(), (artifact.width, artifact.height)

    def _read_mip_data(self, layout: TextureMipLayout) -> bytes:
        data_block = self._data_block()
        compression_info: CompressedMip | None = data_block.extra_data.get(VTexExtraData.COMPRESSED_MIP_SIZE)
        self._buffer.seek(self._payload_offset() + layout.offset)
        data = self._buffer.read(layout.stored_size)
        if len(data) != layout.stored_size:
            raise ValueError(
                f"Mip {layout.mip_level} is truncated: expected {layout.stored_size} bytes, got {len(data)}"
            )
        if compression_info is None or layout.stored_size >= layout.decoded_size:
            if len(data) != layout.decoded_size:
                raise ValueError(
                    f"Mip {layout.mip_level} has {len(data)} bytes, expected {layout.decoded_size}"
                )
            return data
        if compression_info.compression_method is VTexMipCompression.LZ4:
            data = lz4_decompress(data, layout.decoded_size)
        elif compression_info.compression_method is VTexMipCompression.ZSTD:
            data = zstd_decompress(data, layout.decoded_size)
        else:
            raise ValueError(
                f"Mip {layout.mip_level} is smaller than its decoded size but compression is disabled"
            )
        if len(data) != layout.decoded_size:
            raise ValueError(
                f"Mip {layout.mip_level} decompressed to {len(data)} bytes, expected {layout.decoded_size}"
            )
        return data

    def _decode_semantics(self) -> tuple[tuple[str, ...], tuple[Diagnostic, ...]]:
        resource_info_block = (
            self.get_block(ResourceEditInfo, block_name="REDI")
            or self.get_block(ResourceEditInfo2, block_name="RED2")
        )
        if resource_info_block is None:
            if self.get_texture_format() in POTENTIALLY_PACKED_FORMATS:
                diagnostic = Diagnostic(
                    "source2.texture.edit-info-missing",
                    "Texture edit information is stripped; packed normal, roughness, or color "
                    "transforms cannot be safely identified",
                    DiagnosticSeverity.WARNING,
                    path=self._filepath.as_posix(),
                )
                resource_diagnostics = getattr(self, "_diagnostics", None)
                if resource_diagnostics is not None and diagnostic not in resource_diagnostics:
                    resource_diagnostics.append(diagnostic)
                return ("edit-info-missing",), (diagnostic,)
            return (), ()

        processors = {
            spec.string.removeprefix("Texture Compiler Version ")
            for spec in resource_info_block.special_deps
            if spec.string.startswith("Texture Compiler Version ")
        }
        recognized = tuple(sorted(processors & {
            "Image Inverse",
            "Image NormalizeNormals",
            "Image YCoCg Conversion",
            "LegacySource1InvertNormals",
            "Mip AnisoRoughness_RG",
            "Mip HemiOctAnisoRoughness",
            "Mip HemiOctIsoRoughness_RG_B",
            "Mip HemiOctNormal",
        }))
        return recognized, ()

    def get_cache_identity(self, settings: TextureImportSettings | None = None) -> str:
        settings = settings or TextureImportSettings()
        data_block = self._data_block()
        resolved_mip = self._resolve_mip_level(data_block.texture_info, settings.mip_level)
        decode_semantics, _ = self._decode_semantics()
        return settings.cache_identity(
            resolved_mip=resolved_mip,
            decode_semantics=decode_semantics,
            pixel_format=data_block.texture_info.pixel_format,
        )

    @staticmethod
    def _selected_subresources(
            layout: TextureMipLayout,
            settings: TextureImportSettings,
            flags: VTexFlags,
    ) -> tuple[tuple[int, ...], tuple[TextureSubresource, ...]]:
        indices = []
        descriptions = []
        for array_layer in range(layout.array_layers):
            for volume_slice in range(layout.depth):
                for face in range(layout.face_count):
                    index = (
                        array_layer * layout.depth * layout.face_count
                        + volume_slice * layout.face_count
                        + face
                    )
                    if settings.array_layer is not None and array_layer != min(
                            max(settings.array_layer, 0), layout.array_layers - 1):
                        continue
                    if settings.volume_slice is not None and volume_slice != min(
                            max(settings.volume_slice, 0), layout.depth - 1):
                        continue
                    if settings.cubemap_face is not None and face != min(
                            max(settings.cubemap_face, 0), layout.face_count - 1):
                        continue
                    indices.append(index)
                    descriptions.append(TextureSubresource(
                        array_layer=array_layer,
                        volume_slice=volume_slice,
                        cubemap_face=face if flags & VTexFlags.CUBE_TEXTURE else None,
                    ))
        return tuple(indices), tuple(descriptions)

    def get_texture_artifact(
            self,
            settings: TextureImportSettings | None = None,
    ) -> TextureArtifact:
        settings = settings or TextureImportSettings()
        data_block = self._data_block()
        texture_info = data_block.texture_info
        layout = self.get_mip_layout(settings.mip_level)
        decode_semantics, diagnostics = self._decode_semantics()
        cache_identity = settings.cache_identity(
            resolved_mip=layout.mip_level,
            decode_semantics=decode_semantics,
            pixel_format=texture_info.pixel_format,
        )
        indices, subresources = self._selected_subresources(layout, settings, texture_info.flags)

        encoded = self.get_encoded_image()
        if encoded is not None:
            encoded_data, encoded_extension = encoded
            if settings.invert_y:
                diagnostics += (Diagnostic(
                    "source2.texture.embedded-invert-unsupported",
                    "Green-channel inversion cannot be applied without decoding the embedded image",
                    DiagnosticSeverity.WARNING,
                    path=self._filepath.as_posix(),
                ),)
            for diagnostic in diagnostics:
                logger.warning("%s: %s", diagnostic.code, diagnostic.message)
            return TextureArtifact(
                settings,
                settings.mip_level,
                layout.mip_level,
                layout.width,
                layout.height,
                layout.depth,
                layout.array_layers,
                layout.face_count,
                texture_info.pixel_format,
                self.is_hdr(),
                cache_identity,
                data_block.metadata,
                subresources,
                decode_semantics,
                diagnostics,
                encoded_data=encoded_data,
                encoded_extension=encoded_extension,
            )

        cache_key = (layout.mip_level, settings.decode_packed_channels, decode_semantics)
        pixel_data = self._cached_mips.get(cache_key)
        if pixel_data is None:
            data = self._read_mip_data(layout)
            actual_data_size = (
                self._calculate_slice_size(texture_info.pixel_format, layout.width, layout.height)
                * layout.subresource_count
            )
            data = data[:actual_data_size]
            pixel_data = self._decompress_texture(
                data,
                layout.height * layout.subresource_count,
                texture_info.pixel_format,
                layout.width,
                settings.decode_packed_channels,
            ).reshape(layout.subresource_count, layout.height, layout.width, -1)
            if len(self._cached_mips) >= MAX_CACHED_MIPS:
                self._cached_mips.pop(next(iter(self._cached_mips)))
            self._cached_mips[cache_key] = pixel_data

        selected_pixels = pixel_data[np.asarray(indices, dtype=np.intp)]
        if settings.invert_y and not self.is_hdr():
            selected_pixels = selected_pixels.copy()
            selected_pixels[..., 1] = 1 - selected_pixels[..., 1]

        for diagnostic in diagnostics:
            logger.warning("%s: %s", diagnostic.code, diagnostic.message)
        return TextureArtifact(
            settings,
            settings.mip_level,
            layout.mip_level,
            layout.width,
            layout.height,
            layout.depth,
            layout.array_layers,
            layout.face_count,
            texture_info.pixel_format,
            self.is_hdr(),
            cache_identity,
            data_block.metadata,
            subresources,
            decode_semantics,
            diagnostics,
            selected_pixels,
        )

    def get_texture_data(
            self,
            mip_level: int = 0,
            *,
            settings: TextureImportSettings | None = None,
    ) -> tuple[npt.NDArray, tuple[int, int]]:
        logger.info(f'Loading texture {self._filepath.as_posix()!r}')
        if settings is None:
            settings = TextureImportSettings(mip_level=mip_level)
        artifact = self.get_texture_artifact(settings)
        pixels = artifact.image_pixels()
        if pixels is None:
            raise ValueError(f"{artifact.pixel_format!r} embeds an image file, use get_encoded_image()")
        return pixels, (artifact.width, artifact.height * artifact.subresource_count)


    def _decompress_texture(
            self,
            data: bytes,
            height,
            pixel_format,
            width,
            decode_packed_channels: bool = True,
    ) -> npt.NDArray:
        resource_info_block = (self.get_block(ResourceEditInfo, block_name="REDI") or
                               self.get_block(ResourceEditInfo2, block_name="RED2"))

        invert = False
        normalize = False
        hemi_oct_aniso_roughness = False
        y_co_cg = False
        hemi_oct_normal = False
        if decode_packed_channels and resource_info_block:
            for spec in resource_info_block.special_deps:
                if spec.string == "Texture Compiler Version Mip HemiOctIsoRoughness_RG_B":
                    hemi_oct_aniso_roughness = True
                elif spec.string == "Texture Compiler Version Mip HemiOctAnisoRoughness":
                    hemi_oct_aniso_roughness = True
                elif spec.string == "Texture Compiler Version Mip HemiOctNormal":
                    hemi_oct_normal = True
                elif spec.string == "Texture Compiler Version LegacySource1InvertNormals":
                    invert = True
                elif spec.string == "Texture Compiler Version Image Inverse":
                    invert = True
                elif spec.string == "Texture Compiler Version Image NormalizeNormals":
                    normalize = True
                elif spec.string == "Texture Compiler Version Image YCoCg Conversion":
                    y_co_cg = True
            # CS2 lists every normal-map step on every texture it compiles from a normal map, including the
            # anisotropic gloss (g_tAnisoGloss), whose R and G are roughness values; only it has this step.
            if any(spec.string == "Texture Compiler Version Mip AnisoRoughness_RG"
                   for spec in resource_info_block.special_deps):
                invert = normalize = hemi_oct_aniso_roughness = hemi_oct_normal = False

        if pixel_format == VTexFormat.RGBA8888:
            pixel_data = np.frombuffer(data, np.uint8).reshape((height, width, 4)).astype(np.float32) / 255
        elif pixel_format == VTexFormat.BC6H:
            t_data = decode_texture(data, width, height, "BC6H")
            tmp = np.frombuffer(t_data, np.float16, width * height * 3).reshape((height, width, 3))
            pixel_data = np.ones((height, width, 4), dtype=np.float32)
            pixel_data[:, :, :3] = tmp
        elif pixel_format == VTexFormat.BC7:
            pixel_data = decode_texture(data, width, height, "BC7")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((height, width, 4))
            output = pixel_data.copy()
            del pixel_data
            if hemi_oct_aniso_roughness:
                output = self._hemi_oct_aniso_roughness(output)
            if hemi_oct_normal:
                output = self._hemi_oct_normal(output)
            if invert:
                output[:, :, 1] = np.invert(output[:, :, 1])

            pixel_data = output.astype(np.float32) / 255
        elif pixel_format == VTexFormat.ATI1N:
            pixel_data = decode_texture(data, width, height, "ATI1N")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((height, width, 1)).astype(np.float32) / 255
            output = np.zeros((height, width, 4), dtype=np.float32)
            output[..., 0] = pixel_data[..., 0]
            output[..., 3] = 1
            pixel_data = output
        elif pixel_format == VTexFormat.ATI2N:
            pixel_data = decode_texture(data, width, height, "ATI2N")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((height, width, 2))
            output = np.zeros((height, width, 4), dtype=np.uint8)
            output[..., :2] = pixel_data[..., :2]
            output[..., 3] = 255
            pixel_data = output
            if normalize:
                pixel_data = self._normalize(pixel_data)
            if hemi_oct_aniso_roughness:
                pixel_data = self._hemi_oct_aniso_roughness(pixel_data)
            if invert:
                pixel_data[:, :, 1] = np.invert(pixel_data[:, :, 1])

            pixel_data = pixel_data.astype(np.float32) / 255
        elif pixel_format == VTexFormat.DXT1:
            pixel_data = decode_texture(data, width, height, "DXT1")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((height, width, 4)).astype(np.float32) / 255
        elif pixel_format == VTexFormat.DXT5:
            pixel_data = decode_texture(data, width, height, "DXT5")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((height, width, 4))
            output = pixel_data.copy()
            if y_co_cg:
                output = self._y_co_cg(output)
            if normalize:
                if hemi_oct_aniso_roughness:
                    output = self._hemi_oct_aniso_roughness(output)
                else:
                    output = self._normalize(output)
            if invert:
                output[:, :, 1] = np.invert(output[:, :, 1])

            pixel_data = output
            pixel_data = pixel_data.astype(np.float32) / 255
        elif pixel_format == VTexFormat.RGBA16161616F:
            pixel_data = np.frombuffer(data, np.float16, width * height * 4).astype(np.float32).reshape((height, width, 4))
        elif pixel_format == VTexFormat.I8:
            r = np.frombuffer(data, np.uint8)[:, None]
            pixel_data = np.repeat(r, 4, axis=1).astype(np.float32) / 255
            pixel_data[:, 3] = 1
            pixel_data = pixel_data.reshape((height, width, 4))
        elif pixel_format in (VTexFormat.ETC2, VTexFormat.ETC2_EAC):
            block_size = 8 if pixel_format == VTexFormat.ETC2 else 16
            blocks = np.frombuffer(data, np.uint8).reshape(-1, block_size)
            color = decode_texture(blocks[:, block_size - 8:].tobytes(), width, height, "ETC2")
            pixel_data = np.frombuffer(color, np.uint8).reshape((height, width, 4)).astype(np.float32) / 255
            if pixel_format == VTexFormat.ETC2_EAC:
                pixel_data[..., 3] = _blocks_to_image(_decode_eac_blocks(blocks[:, :8], alpha=True), width, height)
        elif pixel_format in (VTexFormat.R11_EAC, VTexFormat.RG11_EAC):
            channels = 1 if pixel_format == VTexFormat.R11_EAC else 2
            blocks = np.frombuffer(data, np.uint8).reshape(-1, channels, 8)
            pixel_data = np.zeros((height, width, 4), np.float32)
            for channel in range(channels):
                pixel_data[..., channel] = _blocks_to_image(_decode_eac_blocks(blocks[:, channel], alpha=False),
                                                            width, height)
            pixel_data[..., 3] = 1
        elif pixel_format == VTexFormat.BGRA8888:
            pixel_data = np.frombuffer(data, np.uint8, width * height * 4).reshape((height, width, 4))
            pixel_data = pixel_data[..., [2, 1, 0, 3]].astype(np.float32) / 255
        elif pixel_format == VTexFormat.IA88:
            ia = np.frombuffer(data, np.uint8, width * height * 2).reshape((height, width, 2)).astype(np.float32) / 255
            pixel_data = np.empty((height, width, 4), np.float32)
            pixel_data[..., :3] = ia[..., :1]
            pixel_data[..., 3] = ia[..., 1]
        elif pixel_format == VTexFormat.A8:
            pixel_data = np.ones((height, width, 4), np.float32)
            pixel_data[..., 3] = np.frombuffer(data, np.uint8, width * height).reshape((height, width)) / 255
        elif pixel_format == VTexFormat.R32_UINT:
            pixel_data = np.zeros((height, width, 4), np.float32)
            values = np.frombuffer(data, np.uint32, width * height).reshape((height, width))
            pixel_data[..., 0] = np.minimum(values, 255) / 255
            pixel_data[..., 3] = 1
        elif pixel_format in UNCOMPRESSED_FORMATS:
            dtype, channels, divisor = UNCOMPRESSED_FORMATS[pixel_format]
            values = np.frombuffer(data, dtype, width * height * channels).reshape((height, width, channels))
            values = values.astype(np.float32)
            if divisor is not None:
                values /= divisor
            pixel_data = np.zeros((height, width, 4), np.float32)
            pixel_data[..., :channels] = values
            if channels < 4:
                pixel_data[..., 3] = 1
        elif pixel_format in ENCODED_IMAGE_EXTENSIONS:
            raise ValueError(f"{pixel_format!r} embeds an image file, use get_encoded_image()")
        else:
            raise NotImplementedError(f"Unsupported texture format: {pixel_format!r}")
        return pixel_data

    @staticmethod
    def _hemi_oct_normal(output: np.ndarray) -> np.ndarray:
        output = output.astype(np.float32) / 255
        nx = output[..., 3] + output[..., 1] - 1.003922
        ny = output[..., 3] - output[..., 1]
        nz = 1.0 - np.abs(nx) - np.abs(ny)

        l = np.sqrt((nx * nx) + (ny * ny) + (nz * nz))
        output[:, :, 3] = 1
        output[:, :, 0] = ((nx / l * 0.5) + 0.5)
        output[:, :, 1] = 1 - ((ny / l * 0.5) + 0.5)
        output[:, :, 2] = ((nz / l * 0.5) + 0.5)
        return (output * 255).astype(np.uint8)

    @staticmethod
    def _hemi_oct_aniso_roughness(output: np.ndarray) -> np.ndarray:
        output = output.astype(np.float32)
        nx = ((output[:, :, 0] + output[:, :, 1]) / 255) - 1.003922
        ny = ((output[:, :, 0] - output[:, :, 1]) / 255)
        nz = 1 - np.abs(nx) - np.abs(ny)

        l = np.sqrt((nx * nx) + (ny * ny) + (nz * nz))
        output[:, :, 3] = output[:, :, 2]
        output[:, :, 0] = ((nx / l * 0.5) + 0.5) * 255
        output[:, :, 1] = 255 - ((ny / l * 0.5) + 0.5) * 255
        output[:, :, 2] = ((nz / l * 0.5) + 0.5) * 255
        return output.astype(np.uint8)

    @staticmethod
    def _normalize(output: np.ndarray) -> np.ndarray:
        output = output.astype(np.int32)
        swizzle_r = output[:, :, 0] * 2 - 255
        swizzle_g = output[:, :, 1] * 2 - 255
        # R and G can encode a vector longer than 1; Z is then 0, not NaN.
        derive_b = np.sqrt(np.maximum((255 * 255) - (swizzle_r * swizzle_r) - (swizzle_g * swizzle_g), 0))
        output[:, :, 0] = np.clip((swizzle_r / 2) + 128, 0, 255)
        output[:, :, 1] = np.clip((swizzle_g / 2) + 128, 0, 255)
        output[:, :, 2] = np.clip((derive_b / 2) + 128, 0, 255)
        return output.astype(np.uint8)

    @staticmethod
    def _y_co_cg(output: np.ndarray) -> np.ndarray:
        output = output.astype(np.int16)
        s = (output[:, :, 2] >> 3) + 1
        co = (output[:, :, 0] - 128) / s
        cg = (output[:, :, 1] - 128) / s
        output[:, :, 0] = np.clip(output[:, :, 3] + co - cg, 0, 255)
        output[:, :, 1] = np.clip(output[:, :, 3] + cg, 0, 255)
        output[:, :, 2] = np.clip(output[:, :, 3] - co - cg, 0, 255)
        output[:, :, 3] = 255
        return output.astype(np.uint8)
