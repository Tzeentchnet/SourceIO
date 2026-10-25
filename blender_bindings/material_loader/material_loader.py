import traceback
from enum import Enum
from typing import Type, Any

import bpy

from ...library.models.mdl.v10.structs.texture import StudioTexture
from ...library.shared.content_manager import ContentManager
from ...library.source1.vmt import VMT
from ...library.source2 import CompiledMaterialResource
from ...logger import SourceLogMan
from .node_arranger import nodes_iterate
from .shader_base import ShaderBase, ExtraMaterialParameters, unfilter_alpha_clips, apply_vtf_sampler_flags
from .shaders.goldsrc_shader_base import GoldSrcShaderBase
from .shaders.source1_shader_base import Source1ShaderBase
from .shaders.source2_shader_base import Source2ShaderBase
from .shaders.source2_shaders.dummy import DummyShader

# noinspection PyUnresolvedReferences
from .shaders import source1_shaders, source2_shaders, goldsrc_shaders
from ...library.source2.blocks.kv3_block import KVBlock
from ...library.utils.tiny_path import TinyPath
from ...library.utils.perf_sampler import timed
from ..preferences import principled_backface_culling_enabled
from ..utils.bpy_utils import get_or_create_material

log_manager = SourceLogMan()
logger = log_manager.get_logger('MaterialLoader')


class MaterialLoaderBase:
    def __init__(self, material_name):
        self.material_name: str = material_name

    def create_material(self, material: bpy.types.Material):
        pass


def source1_material_parameters(use_bvlg: bool = False) -> dict[ExtraMaterialParameters, Any]:
    return {ExtraMaterialParameters.USE_BVLG: bool(use_bvlg)}


def source1_material_identity(material_path: str | TinyPath, use_bvlg: bool = False) -> str:
    path = TinyPath(material_path).as_posix()
    return f"{path}?sourceio-shader=bvlg" if use_bvlg else path


def get_or_create_source1_material(name: str, material_path: str | TinyPath, use_bvlg: bool = False):
    material_name = f"{name} [BVLG]" if use_bvlg else name
    return get_or_create_material(material_name, source1_material_identity(material_path, use_bvlg))


@timed
def _load_unused_source2_textures(handler: Source2ShaderBase, material_resource: CompiledMaterialResource):
    for unused_texture in handler.unused_textures.copy():
        texture_path = material_resource.get_texture_property(unused_texture, None)
        if texture_path is not None and texture_path not in handler.loaded_textures:
            handler.logger.warn(f"Unused texture {unused_texture} {texture_path}")
            handler._get_texture(unused_texture, (0, 0, 0, 0), False)


def _collect_handlers(*bases) -> dict[str, Type[ShaderBase]]:
    """Map ``SHADER`` name -> handler class for every concrete shader subclass.

    Classes that never override ``SHADER`` (mixins and intermediate bases) are
    skipped, and duplicate shader names are reported instead of silently
    overwriting each other.
    """
    handlers: dict[str, Type[ShaderBase]] = {}
    for base in bases:
        engine = base.__name__
        for sub in base.all_subclasses():
            shader = sub.SHADER
            if shader == ShaderBase.SHADER:  # mixin / abstract helper, not a real shader
                logger.debug(f'Skipping {sub.__name__}: no SHADER name declared')
                continue
            if shader in handlers:
                logger.warning(f'Shader name {shader!r} is claimed by both '
                               f'{handlers[shader].__name__} and {sub.__name__}; keeping the former')
                continue
            logger.info(f'Registered {engine} material handler for {shader} shader')
            handlers[shader] = sub
    return handlers


class ShaderRegistry:
    _handlers: dict[str, Type[ShaderBase]] = _collect_handlers(
        Source1ShaderBase, GoldSrcShaderBase, Source2ShaderBase
    )

    @classmethod
    @timed
    def source1_create_nodes(cls, content_manager: ContentManager,
                             material: bpy.types.Material,
                             vmt: VMT,
                             extra_parameters: dict[ExtraMaterialParameters, Any]):
        if not cls._initial_setup(material):
            return material

        shader = vmt.shader
        if shader not in cls._handlers:
            logger.error(f'Shader "{shader}" not currently supported by SourceIO')
            return material

        handler: Source1ShaderBase = cls._handlers.get(shader, Source1ShaderBase)(content_manager, vmt)
        handler.bpy_material = material
        parameters = dict(extra_parameters)
        parameters.setdefault(
            ExtraMaterialParameters.PRINCIPLED_BACKFACE_CULLING,
            principled_backface_culling_enabled(),
        )
        try:
            new_material = handler.create_nodes(material, parameters) or material  # support material templates
            if not isinstance(new_material, bpy.types.Material):
                new_material = material
            material = new_material

        except Exception as e:
            logger.error(f'Failed to load material, due to {e} error')
            traceback.print_exc()
            logger.debug(f'Failed material: {material.name}')
        # params = handler._vmt.data.to_dict()
        material['vmt_parameters'] = vmt.data.to_dict()
        if material.node_tree is not None:
            apply_vtf_sampler_flags(material)
            unfilter_alpha_clips(material)
        if handler.do_arrange:
            handler.align_nodes()
        return material

    @classmethod
    @timed
    def source2_create_nodes(cls, content_manager: ContentManager,
                             material: bpy.types.Material,
                             material_resource: CompiledMaterialResource,
                             extra_parameters: dict[ExtraMaterialParameters, Any]):
        if not cls._initial_setup(material):
            return
        data = material_resource.get_block(KVBlock, block_name="DATA")
        if not data:
            return
        shader = data['m_shaderName']
        handler_class = cls._handlers.get(shader, DummyShader)
        material['source2_shader'] = str(shader)
        material['source2_shader_supported'] = handler_class is not DummyShader
        use_tint = extra_parameters.get(ExtraMaterialParameters.USE_OBJECT_TINT, False)
        handler: Source2ShaderBase = handler_class(content_manager, material_resource, use_tint)
        handler.bpy_material = material
        try:
            handler.create_nodes(material, extra_parameters)
        except Exception as e:
            logger.error(f'Failed to load material, due to {e} error')
            traceback.print_exc()
            logger.debug(f'Failed material: {material.name}')
        unfilter_alpha_clips(material)
        cls.align_nodes(material)
        _load_unused_source2_textures(handler, material_resource)

    @classmethod
    def _initial_setup(cls, material: bpy.types.Material):
        if material.get('source_loaded', False):
            return False

        material['source_loaded'] = True
        cls._clean_nodes(material)
        material.surface_render_method = 'DITHERED'
        material.use_raytrace_refraction = False
        material.refraction_depth = 0.2
        return True

    @staticmethod
    def _clean_nodes(material: bpy.types.Material):
        for node in material.node_tree.nodes:
            material.node_tree.nodes.remove(node)

    @staticmethod
    def align_nodes(material):
        nodes_iterate(material.node_tree)
        material.node_tree.nodes.update()
