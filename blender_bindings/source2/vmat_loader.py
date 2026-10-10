import json
import math

from ..utils.bpy_utils import get_or_create_material
from ..material_loader.material_loader import ShaderRegistry, ExtraMaterialParameters
from ...library.shared.content_manager import ContentManager
from ...library.source2 import CompiledMaterialResource
from ...library.source2.blocks.texture_data import TextureImportSettings
from ...library.source2.compiled_shader import CompiledShaderMetadata
from ...library.utils.tiny_path import TinyPath
from ...library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS



def load_material(content_manager: ContentManager, material_resource: CompiledMaterialResource, material_path: TinyPath,
                  tinted: bool = False, texture_settings: TextureImportSettings | None = None,
                  shader_metadata: CompiledShaderMetadata | None = None,
                  import_scale: float = SOURCE2_HAMMER_UNIT_TO_METERS):
    try:
        import_scale = float(import_scale)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Source 2 import scale {import_scale!r}") from exc
    if not math.isfinite(import_scale) or import_scale <= 0:
        raise ValueError(
            f"Source 2 import scale must be finite and greater than zero, got {import_scale!r}")

    texture_settings = texture_settings or TextureImportSettings()
    settings_identity = texture_settings.cache_identity()
    identity_parts = []
    if texture_settings.is_default:
        material_name = material_path.stem
    else:
        digest = texture_settings.cache_digest()[:12]
        material_name = f"{material_path.stem}__{digest}"
        identity_parts.append(f"sourceio-texture={digest}")
    identity_parts.append(f"sourceio-scale={import_scale.hex()}")
    material_identity = f"{material_path.as_posix()}?{'&'.join(identity_parts)}"

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
            {
                ExtraMaterialParameters.USE_OBJECT_TINT: tinted,
                ExtraMaterialParameters.SOURCE2_IMPORT_SCALE: import_scale,
            },
        )
        semantics = material_resource.get_material_semantics()
    finally:
        material_resource.texture_import_settings = previous_settings
        material_resource.shader_metadata = previous_metadata

    material["sourceio_texture_settings"] = settings_identity
    material["sourceio_import_scale"] = import_scale
    material["sourceio_material_semantics"] = json.dumps(semantics.to_dict(), sort_keys=True)
    material["sourceio_material_diagnostics"] = "\n".join(
        f"{diagnostic.code}: {diagnostic.message}" for diagnostic in semantics.diagnostics
    )
    return material
