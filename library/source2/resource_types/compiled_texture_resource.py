import io
import logging
from dataclasses import dataclass, field
from typing import Optional, Type

import numpy as np
import numpy.typing as npt

from ..blocks.resource_edit_info import ResourceEditInfo, ResourceEditInfo2
from ...utils.perf_sampler import timed
from ...utils.pylib.compression import lz4_decompress
from ...utils.pylib.image import decode_texture
from ..blocks.texture_data import CompressedMip, TextureData, VTexExtraData, \
    VTexFlags, VTexFormat
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


HDR_FORMATS = {VTexFormat.BC6H, VTexFormat.RGBA16161616F, VTexFormat.R16F, VTexFormat.RG1616F,
               VTexFormat.R32F, VTexFormat.RG3232F, VTexFormat.RGB323232F, VTexFormat.RGBA32323232F}


@dataclass(slots=True)
class CompiledTextureResource(CompiledResource):
    _cached_mips: dict[int, tuple[npt.NDArray, bool]] = field(default_factory=dict)

    def get_data_block_type(self):
        return TextureData

    @staticmethod
    def _calculate_buffer_size_for_mip(data_block: TextureData, mip_level):
        texture_info = data_block.texture_info
        bytes_per_pixel = VTexFormat.block_size(texture_info.pixel_format)
        width = texture_info.width >> mip_level
        height = texture_info.height >> mip_level
        depth = texture_info.depth >> mip_level
        if depth < 1:
            depth = 1
        if texture_info.pixel_format in [
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
        ]:
            misalign = width % 4
            if misalign > 0:
                width += 4 - misalign
            misalign = height % 4
            if misalign > 0:
                height += 4 - misalign

            if 4 > width > 0:
                width = 4
            if 4 > height > 0:
                height = 4
            if 4 > depth > 1:
                depth = 4

            num_blocks = (width * height) >> 4
            num_blocks *= depth

            size = num_blocks * bytes_per_pixel
        else:
            size = width * height * depth * bytes_per_pixel
        return size

    def get_texture_format(self) -> VTexFormat:
        data_block = self.get_block(TextureData, block_name='DATA')
        return data_block.texture_info.pixel_format

    def is_hdr(self) -> bool:
        return self.get_texture_format() in HDR_FORMATS

    def get_encoded_image(self) -> tuple[bytes, str] | None:
        """Return ``(file bytes, extension)`` for textures that embed a PNG/JPEG/WEBP file, else None."""
        extension = ENCODED_IMAGE_EXTENSIONS.get(self.get_texture_format())
        if extension is None:
            return None
        info_block = next(block for block in self._header.blocks if block.name == 'DATA')
        buffer = self._buffer
        buffer.seek(info_block.absolute_offset + info_block.size)
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
        data_block = self.get_block(TextureData, block_name='DATA')
        return data_block.texture_info.flags & VTexFlags.CUBE_TEXTURE

    def get_resolution(self, mip_level: int = 0):
        data_block = self.get_block(TextureData, block_name='DATA')
        texture_info = data_block.texture_info
        width = texture_info.width >> mip_level
        height = texture_info.height >> mip_level
        return width, height

    def get_cubemap_face(self, face: int = 0, mip_level: int = 0):
        if not self.is_cubemap():
            return None
        info_block = None
        for block in self._header.blocks:
            if block.name == 'DATA':
                info_block = block
                break
        data_block = self.get_block(TextureData, block_name='DATA')
        buffer = self._buffer
        buffer.seek(info_block.absolute_offset + info_block.size)

        compression_info: Optional[CompressedMip] = data_block.extra_data.get(VTexExtraData.COMPRESSED_MIP_SIZE, None)

        face_size = self._calculate_buffer_size_for_mip(data_block, mip_level)

        if compression_info and compression_info.compressed:
            compressed_size = compression_info.mip_sizes[mip_level]
            total_size = 0
            for size in reversed(compression_info.mip_sizes[mip_level + 1:]):
                total_size += size
            buffer.seek(total_size, io.SEEK_CUR)
            data = buffer.read(compressed_size)
            if compressed_size != face_size * 6:
                data = lz4_decompress(data, face_size * 6)
            assert len(data) == face_size * 6, "Uncompressed data size != expected uncompressed size"
        else:
            total_size = 0
            for i in range(data_block.texture_info.mip_count - 1, mip_level, -1):
                total_size += self._calculate_buffer_size_for_mip(data_block, i) * 6
            buffer.seek(total_size, io.SEEK_CUR)
            data = buffer.read(face_size * 6)

        face_data = data[face_size * face:face_size * face + face_size]

        pixel_format = data_block.texture_info.pixel_format
        width = data_block.texture_info.width >> mip_level
        height = data_block.texture_info.height >> mip_level

        data = self._decompress_texture(face_data, height, pixel_format, width)
        return data, (width, height)


    def get_texture_data(self, mip_level: int = 0) -> tuple[npt.NDArray, tuple[int, int]]:
        logger.info(f'Loading texture {self._filepath.as_posix()!r}')
        info_block = None
        for block in self._header.blocks:
            if block.name == 'DATA':
                info_block = block
                break

        data_block = self.get_block(TextureData, block_name='DATA')
        buffer = self._buffer
        buffer.seek(info_block.absolute_offset + info_block.size)
        compression_info: Optional[CompressedMip] = data_block.extra_data.get(VTexExtraData.COMPRESSED_MIP_SIZE, None)

        desired_mip_size = self._calculate_buffer_size_for_mip(data_block, mip_level)
        if self.is_cubemap():
            desired_mip_size *= 6
        texture_info = data_block.texture_info
        if compression_info and compression_info.compressed:
            compressed_size = compression_info.mip_sizes[mip_level]
            total_size = 0
            for size in reversed(compression_info.mip_sizes[mip_level + 1:]):
                total_size += size
            buffer.seek(total_size, io.SEEK_CUR)
            data = buffer.read(compressed_size)
            if compressed_size < desired_mip_size:
                data = lz4_decompress(data, desired_mip_size)
            assert len(data) == desired_mip_size, "Uncompressed data size != expected uncompressed size"
        else:
            total_size = 0
            for i in range(texture_info.mip_count - 1, mip_level, -1):
                total_size += self._calculate_buffer_size_for_mip(data_block, i)
            if self.is_cubemap():
                total_size *= 6
            buffer.seek(total_size, io.SEEK_CUR)
            data = buffer.read(desired_mip_size)

        pixel_format = texture_info.pixel_format
        width = texture_info.width
        height = texture_info.height
        if self.is_cubemap():
            height *= 6
        if texture_info.depth > 1:
            height *= texture_info.depth
        data = self._decompress_texture(data, height, pixel_format, width)
        return data, (width, height)


    def _decompress_texture(self, data: bytes, height, pixel_format, width)-> npt.NDArray:
        resource_info_block = (self.get_block(ResourceEditInfo, block_name="REDI") or
                               self.get_block(ResourceEditInfo2, block_name="RED2"))

        invert = False
        normalize = False
        hemi_oct_aniso_roughness = False
        y_co_cg = False
        hemi_oct_normal = False
        if resource_info_block:
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

        if pixel_format == VTexFormat.RGBA8888:
            pixel_data = np.frombuffer(data, np.uint8).reshape((width, height, 4)).astype(np.float32) / 255
        elif pixel_format == VTexFormat.BC6H:
            t_data = decode_texture(data, width, height, "BC6H")
            tmp = np.frombuffer(t_data, np.float16, width * height * 3).reshape((width, height, 3))
            pixel_data = np.ones((width, height, 4), dtype=np.float32)
            pixel_data[:, :, :3] = tmp
        elif pixel_format == VTexFormat.BC7:
            pixel_data = decode_texture(data, width, height, "BC7")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((width, height, 4))
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
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((width, height, 1)).astype(np.float32) / 255
            output = np.zeros((width, height, 4), dtype=np.float32)
            output[..., 0] = pixel_data[..., 0]
            output[..., 3] = 1
            pixel_data = output
        elif pixel_format == VTexFormat.ATI2N:
            pixel_data = decode_texture(data, width, height, "ATI2N")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((width, height, 2))
            output = np.zeros((width, height, 4), dtype=np.uint8)
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
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((width, height, 4)).astype(np.float32) / 255
        elif pixel_format == VTexFormat.DXT5:
            pixel_data = decode_texture(data, width, height, "DXT5")
            pixel_data = np.frombuffer(pixel_data, np.uint8).reshape((width, height, 4))
            output = pixel_data.copy()
            if y_co_cg:
                output = self._y_co_cg(output)
            if normalize:
                if hemi_oct_aniso_roughness:
                    output = self._hemi_oct_aniso_roughness(output)
                else:
                    output = self._normalize(output)
            if invert:
                output[:, :, 1] = 1 - output[:, :, 1]

            pixel_data = output
            pixel_data = pixel_data.astype(np.float32) / 255
        elif pixel_format == VTexFormat.RGBA16161616F:
            pixel_data = np.frombuffer(data, np.float16, width * height * 4).astype(np.float32).reshape((width, height, 4))
        elif pixel_format == VTexFormat.I8:
            r = np.frombuffer(data, np.uint8)[:, None]
            pixel_data = np.repeat(r, 4, axis=1).astype(np.float32) / 255
            pixel_data[:, 3] = 1
            pixel_data.reshape((width, height, 4))
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
            pixel_data = np.frombuffer(data, np.uint8, width * height * 4).reshape((width, height, 4))
            pixel_data = pixel_data[..., [2, 1, 0, 3]].astype(np.float32) / 255
        elif pixel_format == VTexFormat.IA88:
            ia = np.frombuffer(data, np.uint8, width * height * 2).reshape((width, height, 2)).astype(np.float32) / 255
            pixel_data = np.empty((width, height, 4), np.float32)
            pixel_data[..., :3] = ia[..., :1]
            pixel_data[..., 3] = ia[..., 1]
        elif pixel_format == VTexFormat.A8:
            pixel_data = np.ones((width, height, 4), np.float32)
            pixel_data[..., 3] = np.frombuffer(data, np.uint8, width * height).reshape((width, height)) / 255
        elif pixel_format == VTexFormat.R32_UINT:
            pixel_data = np.zeros((width, height, 4), np.float32)
            values = np.frombuffer(data, np.uint32, width * height).reshape((width, height))
            pixel_data[..., 0] = np.minimum(values, 255) / 255
            pixel_data[..., 3] = 1
        elif pixel_format in UNCOMPRESSED_FORMATS:
            dtype, channels, divisor = UNCOMPRESSED_FORMATS[pixel_format]
            values = np.frombuffer(data, dtype, width * height * channels).reshape((width, height, channels))
            values = values.astype(np.float32)
            if divisor is not None:
                values /= divisor
            pixel_data = np.zeros((width, height, 4), np.float32)
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
