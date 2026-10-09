import bpy

from ..utils.texture_utils import (
    check_texture_cache,
    create_and_cache_texture,
    create_texture_from_encoded_image,
)
from ...library.source2.blocks.texture_data import TextureImportSettings
from ...library.utils.tiny_path import TinyPath
from ...library.source2.resource_types import CompiledTextureResource
from ...logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Texture")


def texture_settings_from_scene(scene) -> TextureImportSettings:
    return TextureImportSettings(
        mip_level=max(0, int(getattr(scene, "source2_texture_mip_level", 0))),
        decode_packed_channels=bool(getattr(scene, "source2_decode_packed_channels", True)),
    )


def import_texture(
        resource: CompiledTextureResource,
        texture_path: TinyPath,
        invert_y: bool = False,
        settings: TextureImportSettings | None = None,
):
    settings = settings or TextureImportSettings(invert_y=invert_y)
    if invert_y and not settings.invert_y:
        settings = settings.with_invert_y(True)
    cache_identity = resource.get_cache_identity(settings)
    cached = check_texture_cache(texture_path, cache_identity)
    if cached is not None:
        return cached

    logger.info(f'Loading {texture_path} texture')
    artifact = resource.get_texture_artifact(settings)
    if artifact.encoded_data is not None:
        image = create_texture_from_encoded_image(
            texture_path,
            artifact.encoded_data,
            artifact.encoded_extension,
            cache_identity,
        )
    else:
        pixel_data = artifact.image_pixels()
        if pixel_data is None or pixel_data.shape[0] == 0:
            return None

        image = create_and_cache_texture(
            texture_path,
            pixel_data,
            artifact.is_hdr,
            False,
            cache_identity,
        )
        del pixel_data

    image.alpha_mode = "CHANNEL_PACKED"
    image["sourceio_mip_level"] = artifact.mip_level
    image["sourceio_requested_mip_level"] = artifact.requested_mip_level
    image["sourceio_texture_depth"] = artifact.depth
    image["sourceio_array_layers"] = artifact.array_layers
    image["sourceio_cubemap_faces"] = artifact.face_count
    image["sourceio_decode_semantics"] = ",".join(artifact.decode_semantics)
    image["sourceio_texture_diagnostics"] = "\n".join(
        f"{diagnostic.code}: {diagnostic.message}" for diagnostic in artifact.diagnostics
    )
    return image
