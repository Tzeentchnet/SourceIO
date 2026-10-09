from typing import Union, Optional, Any
import bpy
import numpy as np

from ..shader_base import (ShaderBase, Nodes, ExtraMaterialParameters,
                                                                   MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT)
from ...source2.vtex_loader import import_texture
from ....library.shared.content_manager import ContentManager
from ....library.source2.blocks.texture_data import TextureData
from ....library.source2.keyvalues3.types import NullObject
from ....library.source2.resource_types import CompiledMaterialResource, CompiledTextureResource
from ....library.utils.perf_sampler import timed
from ....library.utils.tiny_path import TinyPath
from ....logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Shader")

# vmdl_loader names UV sets TEXCOORD, TEXCOORD_1, ... and vertex colors COLOR, COLOR_1, ...
SECONDARY_UV = "TEXCOORD_1"
VERTEX_COLOR = "COLOR"
UV_TRANSFORM_GROUP = "SourceIO UV Transform"

# F_BLEND_MODE of csgo_unlitgeneric and csgo_static_overlay, as their features files name the values
OPAQUE, TRANSLUCENT, ALPHA_TEST, MOD2X, ADDITIVE, MULTIPLY, MOD_THEN_ADD = range(7)

# Luminance weights of the saturation in the shaders' color correction matrix (MatrixColorCorrect2 as VRF
# evaluates it): it scales by the normalized Rec. 709 coefficients L, so a color's grey is sum(L_i^2 * c_i).
_LUMINANCE = np.array((0.2126, 0.7152, 0.0722))
SATURATION_WEIGHTS = tuple(float(w) for w in _LUMINANCE ** 2 / np.dot(_LUMINANCE, _LUMINANCE))


def _transfer_group(name: str, threshold: float, low_scale: float, offset: float, power: float, scale: float,
                    post_offset: float) -> bpy.types.ShaderNodeTree:
    """A piecewise transfer function per channel: c * low_scale up to the threshold, else
    (c + offset)^power * scale + post_offset (the sRGB curves, LINEAR_TO_SRGB and SRGB_TO_LINEAR)."""
    group = bpy.data.node_groups.get(name)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    group.interface.new_socket("Color", in_out="INPUT", socket_type="NodeSocketColor")
    nodes, links = group.nodes, group.links
    group_input = nodes.new("NodeGroupInput")
    group_output = nodes.new("NodeGroupOutput")

    def vector_math(operation, *inputs):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        for index, value in enumerate(inputs):
            if isinstance(value, bpy.types.NodeSocket):
                links.new(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node.outputs[0]

    color = vector_math('MAXIMUM', group_input.outputs["Color"], (0.0, 0.0, 0.0))
    low = vector_math('MULTIPLY', color, (low_scale,) * 3)
    high = vector_math('MULTIPLY_ADD', vector_math('POWER', vector_math('ADD', color, (offset,) * 3), (power,) * 3),
                       (scale,) * 3, (post_offset,) * 3)
    # 1 above the threshold, 0 below it (and at it, where both branches agree)
    above = vector_math('MULTIPLY_ADD', vector_math('SIGN', vector_math('SUBTRACT', color, (threshold,) * 3)),
                        (0.5,) * 3, (0.5,) * 3)
    choose = nodes.new("ShaderNodeMix")
    choose.data_type = 'VECTOR'
    choose.factor_mode = 'NON_UNIFORM'
    links.new(above, choose.inputs[1])  # Factor (vector)
    links.new(low, choose.inputs[4])  # A
    links.new(high, choose.inputs[5])  # B
    links.new(choose.outputs[1], group_output.inputs["Color"])
    return group


# _transfer_group's arguments
LINEAR_TO_SRGB = ("SourceIO Linear To sRGB", 0.0031308, 12.92, 0.0, 1 / 2.4, 1.055, -0.055)
SRGB_TO_LINEAR = ("SourceIO sRGB To Linear", 0.04045, 1 / 12.92, 0.055, 2.4, 1 / 1.055 ** 2.4, 0.0)


def _uv_transform_group() -> bpy.types.ShaderNodeTree:
    """The Source 2 texture coordinate transform (the g_v*TexCoordXform0/1 expressions in the shaders), in
    Source's UV space: uv' = R(rotation) * (S * (uv - P) + P - center) + center + offset, where P, the point
    scaling keeps fixed, is the origin in most CS2 shaders and the center in csgo_lightmappedgeneric's layers.
    Rotation is in degrees. Imported UVs (and images) have V flipped, so V is flipped in and out."""
    group = bpy.data.node_groups.get(UV_TRANSFORM_GROUP)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(UV_TRANSFORM_GROUP, "ShaderNodeTree")
    interface = group.interface
    interface.new_socket("UV", in_out="OUTPUT", socket_type="NodeSocketVector")
    interface.new_socket("UV", in_out="INPUT", socket_type="NodeSocketVector")
    interface.new_socket("g_vTexCoordScale", in_out="INPUT", socket_type="NodeSocketVector").default_value = (1, 1, 1)
    interface.new_socket("g_vTexCoordOffset", in_out="INPUT", socket_type="NodeSocketVector")
    interface.new_socket("g_flTexCoordRotation", in_out="INPUT", socket_type="NodeSocketFloat")
    interface.new_socket("g_vTexCoordCenter", in_out="INPUT",
                         socket_type="NodeSocketVector").default_value = (0.5, 0.5, 0)
    interface.new_socket("Scale About Center", in_out="INPUT", socket_type="NodeSocketBool")
    nodes, links = group.nodes, group.links
    group_input = nodes.new("NodeGroupInput")
    group_output = nodes.new("NodeGroupOutput")

    def vector_math(operation, *inputs):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        for index, value in enumerate(inputs):
            if isinstance(value, bpy.types.NodeSocket):
                links.new(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node.outputs[0]

    flip_v = ((1.0, -1.0, 1.0), (0.0, 1.0, 0.0))
    uv = vector_math('MULTIPLY_ADD', group_input.outputs["UV"], *flip_v)
    pivot = nodes.new("ShaderNodeMix")
    pivot.data_type = 'VECTOR'
    links.new(group_input.outputs["Scale About Center"], pivot.inputs["Factor"])
    pivot.inputs[4].default_value = (0, 0, 0)  # A
    links.new(group_input.outputs["g_vTexCoordCenter"], pivot.inputs[5])  # B
    pivot_output = pivot.outputs[1]
    scaled = vector_math('MULTIPLY_ADD', vector_math('SUBTRACT', uv, pivot_output),
                         group_input.outputs["g_vTexCoordScale"], pivot_output)
    radians = nodes.new("ShaderNodeMath")
    radians.operation = 'RADIANS'
    links.new(group_input.outputs["g_flTexCoordRotation"], radians.inputs[0])
    rotate = nodes.new("ShaderNodeVectorRotate")
    rotate.rotation_type = 'Z_AXIS'
    links.new(scaled, rotate.inputs["Vector"])
    links.new(group_input.outputs["g_vTexCoordCenter"], rotate.inputs["Center"])
    links.new(radians.outputs[0], rotate.inputs["Angle"])
    moved = vector_math('ADD', rotate.outputs[0], group_input.outputs["g_vTexCoordOffset"])
    links.new(vector_math('MULTIPLY_ADD', moved, *flip_v), group_output.inputs["UV"])
    return group


class Source2ShaderBase(ShaderBase):
    def __init__(self, content_manager: ContentManager, source2_material: CompiledMaterialResource,
                 tinted: bool = False):
        super().__init__()
        self.content_manager = content_manager
        self.load_source2_nodes()
        self._material_resource = source2_material
        self.texture_import_settings = source2_material.texture_import_settings
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
            settings = self.texture_import_settings.with_invert_y(
                self.texture_import_settings.invert_y or invert_y
            )
            return import_texture(texture_resource, texture_path, settings=settings)
        return None

    def create_transform(self, uv_slot, scale: tuple[float, ...], offset: tuple[float, ...],
                         center: tuple[float, ...], rotation: float = 0.0, scale_about_center: bool = False):
        """A UV map (a UV set name or a UV output socket) through the shaders' texture coordinate transform,
        see ``_uv_transform_group``."""
        if isinstance(uv_slot, str):
            uv_node = self.create_node(Nodes.ShaderNodeUVMap)
            uv_node.uv_map = uv_slot
            uv_slot = uv_node.outputs[0]
        uv_transform = self.create_node(Nodes.ShaderNodeGroup, UV_TRANSFORM_GROUP)
        uv_transform.node_tree = _uv_transform_group()
        uv_transform.inputs["g_vTexCoordScale"].default_value = self.ensure_length(list(scale[:3]), 3, 0.0)
        uv_transform.inputs["g_vTexCoordOffset"].default_value = self.ensure_length(list(offset[:3]), 3, 0.0)
        uv_transform.inputs["g_vTexCoordCenter"].default_value = self.ensure_length(list(center[:3]), 3, 0.0)
        uv_transform.inputs["g_flTexCoordRotation"].default_value = rotation
        uv_transform.inputs["Scale About Center"].default_value = scale_about_center
        self.connect_nodes(uv_slot, uv_transform.inputs[0])

        return uv_transform

    def _secondary_uv_or_primary(self):
        """The secondary UV set, or the primary one where a mesh has none (a missing UV map reads as 0, 0).
        Models carry the set the material asks for; CS2's map meshes don't, and share the materials."""
        secondary = self.create_node(Nodes.ShaderNodeUVMap)
        secondary.uv_map = SECONDARY_UV
        primary = self.create_node(Nodes.ShaderNodeUVMap)
        primary.uv_map = "TEXCOORD"
        length = self.create_node(Nodes.ShaderNodeVectorMath)
        length.operation = 'LENGTH'
        self.connect_nodes(secondary.outputs[0], length.inputs[0])
        has_secondary = self.create_node(Nodes.ShaderNodeMath)
        has_secondary.operation = 'GREATER_THAN'
        has_secondary.inputs[1].default_value = 0.0
        self.connect_nodes(length.outputs['Value'], has_secondary.inputs[0])
        choose = self.create_node(Nodes.ShaderNodeMix)
        choose.data_type = 'VECTOR'
        self.connect_nodes(has_secondary.outputs[0], choose.inputs[0])
        self.connect_nodes(primary.outputs[0], choose.inputs[4])
        self.connect_nodes(secondary.outputs[0], choose.inputs[5])
        return choose.outputs[1]

    def _texcoord_transform(self, prefix: str = "", uv_slot: str = "TEXCOORD", scale_about_center: bool = False):
        """create_transform from the material's g_v<prefix>TexCoordScale/Offset/Center and
        g_fl<prefix>TexCoordRotation, with the shaders' defaults."""
        material = self._material_resource
        return self.create_transform(uv_slot,
                                     material.get_vector_property(f"g_v{prefix}TexCoordScale", (1.0, 1.0, 0.0)),
                                     material.get_vector_property(f"g_v{prefix}TexCoordOffset", (0.0, 0.0, 0.0)),
                                     material.get_vector_property(f"g_v{prefix}TexCoordCenter", (0.5, 0.5, 0.0)),
                                     material.get_float_property(f"g_fl{prefix}TexCoordRotation", 0.0),
                                     scale_about_center)

    def _check_flag(self, name: str, default: int = 0):
        return self._material_resource.get_int_property(name, default) == 1

    def _multiply(self, color_output, other, factor: float = 1.0):
        """color × other (a socket or an RGBA value), blended in by factor."""
        multiply = self.create_mix_color('MULTIPLY')
        multiply.inputs[MIX_FACTOR].default_value = factor
        self.connect_nodes(color_output, multiply.inputs[MIX_A])
        if isinstance(other, tuple):
            multiply.inputs[MIX_B].default_value = other
        else:
            self.connect_nodes(other, multiply.inputs[MIX_B])
        return multiply.outputs[MIX_RESULT]

    def _multiply_value(self, value_output, factor: float):
        multiply = self.create_node(Nodes.ShaderNodeMath)
        multiply.operation = 'MULTIPLY'
        self.connect_nodes(value_output, multiply.inputs[0])
        multiply.inputs[1].default_value = factor
        return multiply.outputs[0]

    def _model_tint(self):
        """The model tint's color: the mesh's TINT colors (draw-call tints) on tinted meshes, else the object color."""
        if self.tinted:
            model_tint = self.create_node(Nodes.ShaderNodeVertexColor)
            model_tint.layer_name = "TINT"
        else:
            model_tint = self.create_node(Nodes.ShaderNodeObjectInfo)
        return model_tint.outputs["Color"]

    def _tinted(self, color_output):
        """The color times g_vColorTint and the model tint (by g_flModelTintAmount, default 1)."""
        material_data = self._material_resource
        tint = material_data.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        if any(channel != 1.0 for channel in tint[:3]):
            color_output = self._multiply(color_output, (*tint[:3], 1.0))
        return self._multiply(color_output, self._model_tint(),
                              material_data.get_float_property("g_flModelTintAmount", 1.0))

    def _texture_average_color(self, slot_name: str) -> tuple[float, float, float]:
        """A texture's mean linear color (the reflectivity in its header); white without the texture."""
        texture_path = self._material_resource.get_texture_property(slot_name, None)
        if texture_path is not None and not isinstance(texture_path, NullObject):
            resource = self._material_resource.get_child_resource(texture_path, self.content_manager,
                                                                  CompiledTextureResource)
            if resource is not None:
                return tuple(resource.get_block(TextureData, block_name='DATA').texture_info.reflectivity[:3])
        return 1.0, 1.0, 1.0

    def _color_adjusted(self, color_output, contrast: float, saturation: float, brightness: float,
                        average: tuple[float, float, float]):
        """The shaders' g_mTextureColorAdjust without its tint: contrast about the texture's average color,
        then brightness, then saturation about the grey of SATURATION_WEIGHTS (MatrixColorCorrect2 as VRF
        evaluates it). Clamped at 0."""
        if contrast == saturation == brightness == 1.0:
            return color_output

        def vector_math(operation, *inputs):
            node = self.create_node(Nodes.ShaderNodeVectorMath)
            node.operation = operation
            for index, value in enumerate(inputs):
                if isinstance(value, bpy.types.NodeSocket):
                    self.connect_nodes(value, node.inputs[index])
                else:
                    node.inputs[index].default_value = value
            return node

        # B·((c − a)·C + a) = c·(C·B) + a·(1 − C)·B
        color_output = vector_math('MULTIPLY_ADD', color_output, (contrast * brightness,) * 3,
                                   tuple(channel * (1 - contrast) * brightness for channel in average)).outputs[0]
        if saturation != 1.0:
            grey = vector_math('DOT_PRODUCT', color_output, SATURATION_WEIGHTS).outputs['Value']
            grey_part = vector_math('SCALE', (1 - saturation,) * 3)
            self.connect_nodes(grey, grey_part.inputs['Scale'])
            color_output = vector_math('MULTIPLY_ADD', color_output, (saturation,) * 3,
                                       grey_part.outputs[0]).outputs[0]
        return vector_math('MAXIMUM', color_output, (0.0, 0.0, 0.0)).outputs[0]

    def _opacity_scaled(self, alpha_output):
        """The alpha times g_flOpacityScale, which the shaders apply in every blend mode but Opaque and Alpha Test."""
        scale = self._material_resource.get_float_property("g_flOpacityScale", 1.0)
        return alpha_output if scale == 1.0 else self._multiply_value(alpha_output, scale)

    def _transfer(self, color_output, curve: tuple):
        node = self.create_node(Nodes.ShaderNodeGroup, curve[0])
        node.node_tree = _transfer_group(*curve)
        self.connect_nodes(color_output, node.inputs[0])
        return node.outputs[0]

    def _linear_to_srgb(self, color_output):
        return self._transfer(color_output, LINEAR_TO_SRGB)

    def _srgb_to_linear(self, color_output):
        return self._transfer(color_output, SRGB_TO_LINEAR)

    def _apply_detail(self, color_output, normal_input, detail_uv, mask_uv):
        """csgo_complex's and csgo_vertexlitgeneric's F_DETAIL_TEXTURE (VRF's applyDetailTexture), ahead of the
        node group: 1 Mod2X multiplies the albedo by lerp(1, 2 × detail, f); 2 Overlay overlays the detail on
        the albedo in gamma space, faded by f; 3 Normals blends g_tNormalDetail into the normal map; 4 does both
        2 and 3. f is g_flDetailBlendFactor × max(mask, g_flDetailBlendToFull). The detail and its mask are
        linear. The shaders apply it after the tint, which differs for Overlay on a tinted material.
        Returns the new color output; the normal map going into normal_input is rerouted in place."""
        material_data = self._material_resource
        mode = material_data.get_int_property("F_DETAIL_TEXTURE", 0)
        color_mode = mode in (1, 2, 4) and self._have_texture("g_tDetail")
        normal_mode = mode in (3, 4) and self._have_texture("g_tNormalDetail") and normal_input.is_linked
        if not color_mode:
            self._skip_texture("g_tDetail")
        if not normal_mode:
            self._skip_texture("g_tNormalDetail")
        if not (color_mode or normal_mode):
            self._skip_texture("g_tDetailMask")
            return color_output

        mask_texture = self._get_texture("g_tDetailMask", (1, 1, 1, 1), True)
        self.connect_nodes(mask_uv, mask_texture.inputs[0])
        mask = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(mask_texture.outputs[0], mask.inputs[0])
        full = self.create_node(Nodes.ShaderNodeMath)
        full.operation = 'MAXIMUM'
        self.connect_nodes(mask.outputs["Red"], full.inputs[0])
        full.inputs[1].default_value = material_data.get_float_property("g_flDetailBlendToFull", 0.0)
        factor = self._multiply_value(full.outputs[0], material_data.get_float_property("g_flDetailBlendFactor", 1.0))

        if color_mode:
            detail_texture = self._get_texture("g_tDetail", (0.5, 0.5, 0.5, 1), True)
            self.connect_nodes(detail_uv, detail_texture.inputs[0])
            if mode == 1:
                detail = self._multiply(detail_texture.outputs[0], (1.9922, 1.9922, 1.9922, 1))
                fade = self.create_mix_color('MIX')
                self.connect_nodes(factor, fade.inputs[MIX_FACTOR])
                fade.inputs[MIX_A].default_value = (1, 1, 1, 1)
                self.connect_nodes(detail, fade.inputs[MIX_B])
                color_output = self._multiply(color_output, fade.outputs[MIX_RESULT])
            else:
                # Blender's Overlay mix at factor 1 is the shader's: 2ab below a = 0.5, else 1 - 2(1 - a)(1 - b).
                overlay = self.create_mix_color('OVERLAY')
                overlay.inputs[MIX_FACTOR].default_value = 1.0
                self.connect_nodes(self._linear_to_srgb(color_output), overlay.inputs[MIX_A])
                self.connect_nodes(self._multiply(detail_texture.outputs[0], (0.9961, 0.9961, 0.9961, 1)),
                                   overlay.inputs[MIX_B])
                fade = self.create_mix_color('MIX')
                self.connect_nodes(factor, fade.inputs[MIX_FACTOR])
                self.connect_nodes(color_output, fade.inputs[MIX_A])
                self.connect_nodes(self._srgb_to_linear(overlay.outputs[MIX_RESULT]), fade.inputs[MIX_B])
                color_output = fade.outputs[MIX_RESULT]

        if normal_mode:
            detail_normal = self._get_texture("g_tNormalDetail", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(detail_uv, detail_normal.inputs[0])
            strength = self._multiply_value(factor, material_data.get_float_property("g_flDetailNormalStrength", 1.0))
            self._blend_detail_normal(normal_input, detail_normal.outputs[0], strength)
        return color_output

    def _blend_detail_normal(self, normal_input, detail_output, strength_output):
        """Reroute the tangent-space normal map going into normal_input through the shaders' detail normal
        blend: D = lerp((0, 0, 1), detail, strength), n' = n·D.z + (n.z·D.z·D.xy, 0) (the Normal Map node
        normalizes it)."""
        base_output = normal_input.links[0].from_socket

        def vector_math(operation, *inputs):
            node = self.create_node(Nodes.ShaderNodeVectorMath)
            node.operation = operation
            for index, value in enumerate(inputs):
                if isinstance(value, bpy.types.NodeSocket):
                    self.connect_nodes(value, node.inputs[index])
                else:
                    node.inputs[index].default_value = value
            return node

        normal = vector_math('MULTIPLY_ADD', base_output, (2.0,) * 3, (-1.0,) * 3).outputs[0]
        detail = vector_math('MULTIPLY_ADD', detail_output, (2.0,) * 3, (-1.0,) * 3).outputs[0]
        faded = self.create_node(Nodes.ShaderNodeMix)
        faded.data_type = 'VECTOR'
        self.connect_nodes(strength_output, faded.inputs[0])
        faded.inputs[4].default_value = (0.0, 0.0, 1.0)  # A
        self.connect_nodes(detail, faded.inputs[5])  # B
        faded_split = self.create_node(Nodes.ShaderNodeSeparateXYZ)
        self.connect_nodes(faded.outputs[1], faded_split.inputs[0])
        normal_split = self.create_node(Nodes.ShaderNodeSeparateXYZ)
        self.connect_nodes(normal, normal_split.inputs[0])
        detail_xy = self.create_node(Nodes.ShaderNodeCombineXYZ)
        self.connect_nodes(faded_split.outputs["X"], detail_xy.inputs["X"])
        self.connect_nodes(faded_split.outputs["Y"], detail_xy.inputs["Y"])
        nz_dz = self.create_node(Nodes.ShaderNodeMath)
        nz_dz.operation = 'MULTIPLY'
        self.connect_nodes(normal_split.outputs["Z"], nz_dz.inputs[0])
        self.connect_nodes(faded_split.outputs["Z"], nz_dz.inputs[1])
        shifted = vector_math('SCALE', detail_xy.outputs[0])
        self.connect_nodes(nz_dz.outputs[0], shifted.inputs['Scale'])
        scaled = vector_math('SCALE', normal)
        self.connect_nodes(faded_split.outputs["Z"], scaled.inputs['Scale'])
        blended = vector_math('ADD', scaled.outputs[0], shifted.outputs[0]).outputs[0]
        self.connect_nodes(vector_math('MULTIPLY_ADD', blended, (0.5,) * 3, (0.5,) * 3).outputs[0], normal_input)

    def _vertex_color(self):
        node = self.create_node(Nodes.ShaderNodeVertexColor, VERTEX_COLOR)
        node.layer_name = VERTEX_COLOR
        return node

    def _blended_surface(self, color_output, alpha_output, blend_mode: int, surface_output=None,
                         alpha_test_reference: float = 0.5):
        """The surface for an F_BLEND_MODE, blended as the shaders' render states do (VRF's RenderMaterial):
        Opaque and Translucent (by alpha) draw the surface, an Emission of the color unless a lit surface_output
        is given; Alpha Test clips it at the reference; Additive adds color × alpha to what is behind; Multiply
        multiplies what is behind by the color; Mod2x by 2 × lerp(0.5, color, alpha); ModThenAdd by
        lerp(1, color, alpha) (the color times alpha, plus what is behind times 1 − alpha). The modulating
        modes are drawn as a tinted Transparent BSDF, so lighting doesn't reach them."""
        if blend_mode in (MULTIPLY, MOD2X, MOD_THEN_ADD):
            if blend_mode != MULTIPLY:
                fade = self.create_mix_color('MIX')
                self.connect_nodes(alpha_output, fade.inputs[MIX_FACTOR])
                fade.inputs[MIX_A].default_value = (0.5, 0.5, 0.5, 1) if blend_mode == MOD2X else (1, 1, 1, 1)
                self.connect_nodes(color_output, fade.inputs[MIX_B])
                color_output = fade.outputs[MIX_RESULT]
            if blend_mode == MOD2X:
                color_output = self._multiply(color_output, (2, 2, 2, 1))
            transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
            self.connect_nodes(color_output, transparent.inputs['Color'])
            self.set_blend_mode('BLEND')
            return transparent.outputs[0]
        if blend_mode == ADDITIVE:
            weighted = self.create_node(Nodes.ShaderNodeVectorMath)
            weighted.operation = 'SCALE'
            self.connect_nodes(color_output, weighted.inputs[0])
            self.connect_nodes(alpha_output, weighted.inputs['Scale'])
            emission = self.create_node(Nodes.ShaderNodeEmission)
            self.connect_nodes(weighted.outputs[0], emission.inputs['Color'])
            transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
            add_shader = self.create_node(Nodes.ShaderNodeAddShader)
            self.connect_nodes(emission.outputs[0], add_shader.inputs[0])
            self.connect_nodes(transparent.outputs[0], add_shader.inputs[1])
            self.set_blend_mode('BLEND')
            return add_shader.outputs[0]
        if surface_output is None:
            emission = self.create_node(Nodes.ShaderNodeEmission)
            self.connect_nodes(color_output, emission.inputs['Color'])
            surface_output = emission.outputs[0]
        if blend_mode == TRANSLUCENT:
            self.set_blend_mode('BLEND')
        elif blend_mode == ALPHA_TEST:
            alpha_output = self.insert_alpha_clip(alpha_output, alpha_test_reference)
            self.set_blend_mode('CLIP')
        else:
            return surface_output
        transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
        mix_shader = self.create_node(Nodes.ShaderNodeMixShader)
        self.connect_nodes(alpha_output, mix_shader.inputs[0])
        self.connect_nodes(transparent.outputs[0], mix_shader.inputs[1])
        self.connect_nodes(surface_output, mix_shader.inputs[2])
        return mix_shader.outputs[0]

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
