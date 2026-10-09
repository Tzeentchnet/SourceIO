import json

from ..utils.bpy_utils import get_or_create_material
from ..material_loader.material_loader import ShaderRegistry, ExtraMaterialParameters
from ...library.shared.content_manager import ContentManager
from ...library.source2 import CompiledMaterialResource
from ...library.source2.blocks.texture_data import TextureImportSettings
from ...library.source2.compiled_shader import CompiledShaderMetadata
from ...library.utils.tiny_path import TinyPath



def load_material(content_manager: ContentManager, material_resource: CompiledMaterialResource, material_path: TinyPath,
                  tinted: bool = False, texture_settings: TextureImportSettings | None = None,
                  shader_metadata: CompiledShaderMetadata | None = None):
    texture_settings = texture_settings or TextureImportSettings()
    settings_identity = texture_settings.cache_identity()
    if texture_settings.is_default:
        material_name = material_path.stem
        material_identity = material_path.as_posix()
    else:
        digest = texture_settings.cache_digest()[:12]
        material_name = f"{material_path.stem}__{digest}"
        material_identity = f"{material_path.as_posix()}?sourceio-texture={digest}"

    material = get_or_create_material(material_name, material_identity)
    previous_settings = material_resource.texture_import_settings
    previous_metadata = material_resource.shader_metadata
    material_resource.texture_import_settings = texture_settings
    if shader_metadata is not None:
        material_resource.shader_metadata = shader_metadata
    try:
        ShaderRegistry.source2_create_nodes(
            content_manager,
            material,
            material_resource,
            {ExtraMaterialParameters.USE_OBJECT_TINT: tinted},
        )
        semantics = material_resource.get_material_semantics()
    finally:
        material_resource.texture_import_settings = previous_settings
        material_resource.shader_metadata = previous_metadata

    material["sourceio_texture_settings"] = settings_identity
    material["sourceio_material_semantics"] = json.dumps(semantics.to_dict(), sort_keys=True)
    material["sourceio_material_diagnostics"] = "\n".join(
        f"{diagnostic.code}: {diagnostic.message}" for diagnostic in semantics.diagnostics
    )
    return material
