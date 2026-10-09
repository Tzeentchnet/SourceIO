from ..utils.bpy_utils import get_or_create_material
from ..material_loader.material_loader import ShaderRegistry, ExtraMaterialParameters
from ...library.shared.content_manager import ContentManager
from ...library.source2 import CompiledMaterialResource
from ...library.utils.perf_sampler import timed
from ...library.utils.tiny_path import TinyPath



def load_material(content_manager: ContentManager, material_resource: CompiledMaterialResource, material_path: TinyPath,
                  tinted: bool = False):
    material = get_or_create_material(material_path.stem, material_path.as_posix())
    ShaderRegistry.source2_create_nodes(content_manager,material,material_resource, {ExtraMaterialParameters.USE_OBJECT_TINT:tinted})
