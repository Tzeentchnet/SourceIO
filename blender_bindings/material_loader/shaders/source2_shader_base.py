from typing import Union, Optional, Any
import bpy
import numpy as np

from ..shader_base import (ShaderBase, Nodes, ExtraMaterialParameters,
                                                                   MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT)
from ...source2.vtex_loader import import_texture
from ...utils.texture_utils import check_texture_cache
from ....library.shared.content_manager import ContentManager
from ....library.source2.keyvalues3.types import NullObject
from ....library.source2.resource_types import CompiledMaterialResource, CompiledTextureResource
from ....library.utils.perf_sampler import timed
from ....library.utils.tiny_path import TinyPath
from ....logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Shader")

# vmdl_loader names UV sets TEXCOORD, TEXCOORD_1, ...
SECONDARY_UV = "TEXCOORD_1"


class Source2ShaderBase(ShaderBase):
    def __init__(self, content_manager: ContentManager, source2_material: CompiledMaterialResource,
                 tinted: bool = False):
        super().__init__()
        self.content_manager = content_manager
        self.load_source2_nodes()
        self._material_resource = source2_material
        self.unused_textures = set(self._material_resource.get_used_textures().keys())
        # Paths loaded by any means; shaders that read texture properties directly never touch unused_textures.
        self.loaded_textures: set[str | int] = set()
        self.tinted = tinted

        self.load_source2_nodes_blender5_0()

    def _have_texture(self, slot_name: str) -> Optional[bpy.types.Node]:
        texture_path = self._material_resource.get_texture_property(slot_name, None)
        if texture_path is not None:
            return self._material_resource.has_child_resource(texture_path, self.content_manager)
        return None


    def _get_texture(self, slot_name: str, default_color: tuple[float, float, float, float],
                     is_data=False,
                     invert_y: bool = False):
        if slot_name in self.unused_textures:
            self.unused_textures.remove(slot_name)
        texture_path = self._material_resource.get_texture_property(slot_name, None)
        if texture_path is not None and not isinstance(texture_path, NullObject):
            image = self.load_texture_or_default(texture_path, default_color, invert_y)
            if is_data:
                image.colorspace_settings.is_data = True
                image.colorspace_settings.name = 'Non-Color'
        else:
            image = self.get_missing_texture(slot_name, default_color)
        texture_node = self.create_node(Nodes.ShaderNodeTexImage, slot_name)
        texture_node.image = image
        return texture_node

    def _skip_texture(self, slot_name: str):
        if slot_name in self.unused_textures:
            self.unused_textures.remove(slot_name)

    def _skip_textures_with_prefix(self, *prefixes: str):
        for slot_name in [name for name in self.unused_textures if name.startswith(prefixes)]:
            self.unused_textures.remove(slot_name)

    def _is_translucent(self) -> bool:
        # CS2 materials set F_TRANSLUCENT; S_TRANSLUCENT is what older content uses.
        return self._check_flag("F_TRANSLUCENT") or self._check_flag("S_TRANSLUCENT")

    def _split_metalness_texture(self, uv_output=None):
        """Load g_tMetalness and return its separated channels. In the CS2 shaders green is metalness;
        red is roughness on weapons, blue the cloth mask on characters."""
        metalness_texture = self._get_texture("g_tMetalness", (0, 0, 0, 1), True)
        if uv_output is not None:
            self.connect_nodes(uv_output, metalness_texture.inputs[0])
        split = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(metalness_texture.outputs[0], split.inputs[0])
        return split

    def _apply_decal(self, color_output):
        """Blend g_tDecal (color, translucency in alpha) into the albedo: blend mode 0 lays it over the albedo,
        mode 1 multiplies. It uses the secondary UV set unless g_bUseSecondaryUvForDecal is 0."""
        decal_texture = self._get_texture("g_tDecal", (1, 1, 1, 0))
        uv_node = self.create_node(Nodes.ShaderNodeUVMap)
        use_secondary = self._material_resource.get_int_property("g_bUseSecondaryUvForDecal", 1)
        uv_node.uv_map = SECONDARY_UV if use_secondary else "TEXCOORD"
        self.connect_nodes(uv_node.outputs[0], decal_texture.inputs[0])
        blend = self.create_mix_color('MULTIPLY' if self._check_flag("F_DECAL_BLEND_MODE") else 'MIX')
        self.connect_nodes(decal_texture.outputs[1], blend.inputs[MIX_FACTOR])
        self.connect_nodes(color_output, blend.inputs[MIX_A])
        self.connect_nodes(decal_texture.outputs[0], blend.inputs[MIX_B])
        return blend.outputs[MIX_RESULT]

    def _add_transmission(self, surface_output, albedo_output, alpha_input, surface_input, uv_output=None):
        """Source 2 adds back-lit diffuse light tinted by the transmissive color (the albedo with
        F_USE_ALBEDO_FOR_TRANSMISSIVE), without a weight; a Translucent BSDF added to the surface does the same."""
        if self._check_flag("F_USE_ALBEDO_FOR_TRANSMISSIVE"):
            self._skip_texture("g_tTransmissiveColor")  # the default texture
            color_output = albedo_output
        elif self._have_texture("g_tTransmissiveColor"):
            transmissive_texture = self._get_texture("g_tTransmissiveColor", (0, 0, 0, 1))
            if uv_output is not None:
                self.connect_nodes(uv_output, transmissive_texture.inputs[0])
            color_output = transmissive_texture.outputs[0]
        else:
            return
        if color_output is None:
            return
        if alpha_input.is_linked:
            # Clipped or translucent parts must not transmit either.
            alpha_mask = self.create_mix_color('MULTIPLY')
            alpha_mask.inputs[MIX_FACTOR].default_value = 1.0
            self.connect_nodes(color_output, alpha_mask.inputs[MIX_A])
            self.connect_nodes(alpha_input.links[0].from_socket, alpha_mask.inputs[MIX_B])
            color_output = alpha_mask.outputs[MIX_RESULT]
        translucent = self.create_node(Nodes.ShaderNodeBsdfTranslucent)
        self.connect_nodes(color_output, translucent.inputs['Color'])
        add_shader = self.create_node(Nodes.ShaderNodeAddShader)
        self.connect_nodes(surface_output, add_shader.inputs[0])
        self.connect_nodes(translucent.outputs[0], add_shader.inputs[1])
        self.connect_nodes(add_shader.outputs[0], surface_input)

    def load_texture_or_default(self, name_or_id: Union[str, int], default_color: tuple = (1.0, 1.0, 1.0, 1.0),
                                invert_y: bool = False):
        self.loaded_textures.add(name_or_id)
        resource = self._material_resource.get_child_resource(name_or_id, self.content_manager,
                                                              CompiledTextureResource)
        texture_name: str
        if isinstance(name_or_id, int):
            texture_name = f"0x{name_or_id:08}"
        elif isinstance(name_or_id, str):
            texture_name = name_or_id
        else:
            raise Exception(f"Invalid name or id: {name_or_id}")

        return self.load_texture(resource, TinyPath(texture_name), invert_y) or self.get_missing_texture(
            f'missing_{texture_name}',
            default_color)

    def split_normal(self, image: bpy.types.Image):
        roughness_name = self.new_texture_name_with_suffix(image.name, 'roughness', 'tga')
        if image.get('normalmap_converted', None):
            return image, bpy.data.images.get(roughness_name, None)

        buffer = np.zeros(image.size[0] * image.size[1] * 4, np.float32)
        image.pixels.foreach_get(buffer)

        mask = buffer[3::4]
        roughness_rgb = np.dstack((mask, mask, mask, np.ones_like(mask)))

        roughness_texture = self.make_texture(roughness_name, image.size, roughness_rgb, True)
        buffer[3::4] = 1.0

        image.pixels.foreach_set(buffer.ravel())

        image.pack()
        image['normalmap_converted'] = True
        return image, roughness_texture

    def load_texture(self, texture_resource: Optional[CompiledTextureResource], texture_path, invert_y: bool = False):
        if texture_resource is not None:
            texture = check_texture_cache(texture_path)
            if texture is not None:
                return texture
            texture = import_texture(texture_resource, texture_path, invert_y)
            return texture
        return None

    def create_transform(self, uv_slot: str, scale: tuple[float, ...], offset: tuple[float, ...],
                         center: tuple[float, ...]):
        uv_node = self.create_node(Nodes.ShaderNodeUVMap)
        uv_node.uv_map = uv_slot  # "TEXCOORD_1" if self._material_resource.get_int_property("F_SECONDARY_UV", 0) else "TEXCOORD"
        uv_transform = self.create_node_group("UVTransform")
        uv_transform.inputs["g_vTexCoordScale"].default_value = scale[:3]
        uv_transform.inputs["g_vTexCoordOffset"].default_value = offset[:3]
        uv_transform.inputs["g_vTexCoordCenter"].default_value = center[:3]
        self.connect_nodes(uv_node.outputs[0], uv_transform.inputs[0])

        return uv_transform

    def _check_flag(self, name: str, default: int = 0):
        return self._material_resource.get_int_property(name, default) == 1

    def _handle_alpha_modes(self,
                            alpha_mode: str,
                            alpha_test_ref: float,
                            alpha_output_socket,
                            alpha_input_socket):
        if alpha_mode == "TEST":
            self.set_blend_mode('CLIP')
            self.connect_nodes(self.insert_alpha_clip(alpha_output_socket, alpha_test_ref), alpha_input_socket)
        elif alpha_mode == "TRANSLUCENT":
            self.set_blend_mode('HASHED')
            self.connect_nodes(alpha_output_socket, alpha_input_socket)
        elif alpha_mode == "OVERLAY":
            self.set_blend_mode('HASHED')
            self.connect_nodes(alpha_output_socket, alpha_input_socket)

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        return super().create_nodes(material, extra_parameters)

    def _handle_self_illum(self, albedo_output, self_illum_mask_output: None | object,
                           self_illum_tint: None | object,
                           albedo_factor: float,
                           emission_strength: float,
                           emission_color_input, emission_strength_input
                           ):
        if self_illum_mask_output is not None:
            color_multiply_node = self.create_mix_color('MULTIPLY')
            color_multiply_node.inputs[MIX_FACTOR].default_value = albedo_factor
            self.connect_nodes(self_illum_mask_output, color_multiply_node.inputs[MIX_A])
            self.connect_nodes(albedo_output, color_multiply_node.inputs[MIX_B])
            emission_color_output = color_multiply_node.outputs[MIX_RESULT]
        else:
            emission_color_output = albedo_output

        if self_illum_tint is not None:
            color_multiply_node = self.create_mix_color('MULTIPLY')
            color_multiply_node.inputs[MIX_FACTOR].default_value = 1.0
            color_multiply_node.inputs[MIX_A].default_value = self.ensure_length(self_illum_tint, 4, 1.0)
            self.connect_nodes(emission_color_output, color_multiply_node.inputs[MIX_B])
            emission_color_output = color_multiply_node.outputs[MIX_RESULT]
        self.connect_nodes(emission_color_output, emission_color_input)
        emission_strength_input.default_value = emission_strength
