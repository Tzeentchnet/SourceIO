import math
from pprint import pformat
from typing import Any, NamedTuple

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT
from ..node_math import NodeMath, Scalar, Vector, node_group
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock
from .....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS


# vmdl_loader's name for the VertexPaintBlendParams stream (TEXCOORD4), the painted layer blend in x
BLEND_UV = "TEXCOORD_4"

LAYER_BORDER_STRENGTH_DEFAULT = 0.5
LAYER_BORDER_SOFTNESS_DEFAULT = 0.5
BEVEL_BLEND_WIDTH_DEFAULT = 4.0
BEVEL_BLEND_SHARPNESS_DEFAULT = 2.0


class LayerBlend(NamedTuple):
    factor: bpy.types.NodeSocket
    weight: bpy.types.NodeSocket
    mask: bpy.types.NodeSocket | None
    mode: int


def _srgb_to_linear(color: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                 for channel in color)


def _normal_tangent_rotation_group():
    """Rotate a decoded tangent normal with the image in Blender's V-up frame, then encode it again."""

    def build(m: NodeMath, inputs):
        normal = inputs["Normal"] * 2.0 - 1.0
        angle = inputs["Rotation"] * (math.pi / 180.0)
        cosine, sine = m.cos(angle), m.sin(angle)
        rotated = m.combine(cosine * normal.x - sine * normal.y,
                            sine * normal.x + cosine * normal.y,
                            normal.z)
        return {"Normal": rotated * 0.5 + 0.5}

    return node_group(
        "SourceIO Lightmapped Normal Tangent Rotation",
        {"Normal": ("color", (0.5, 0.5, 1.0)), "Rotation": ("float", 0.0)},
        {"Normal": "color"},
        build,
    )


class CSGOLightmappedGeneric(Source2ShaderBase):
    SHADER: str = 'csgo_lightmappedgeneric.vfx'

    def _value(self, name: str, value: float):
        node = self.create_node(Nodes.ShaderNodeValue, name)
        node.outputs[0].default_value = value
        return node.outputs[0]

    def _connect_layer_uv(self, texture_node, prefix: str):
        """Each layer's albedo, normal and the blend modulation have their own UV transform, scaled about its
        center (g_v<prefix>TexCoordScale and so on)."""
        transform = self._texcoord_transform(prefix, scale_about_center=True)
        self.connect_nodes(transform.outputs[0], texture_node.inputs[0])
        return transform.outputs[0]

    def _rotate_tangent_normal(self, normal_output, prefix: str, layer: int):
        rotation = self._material_resource.get_float_property(f"g_fl{prefix}TexCoordRotation", 0.0)
        if rotation == 0.0:
            return normal_output
        rotate = self.create_node(Nodes.ShaderNodeGroup, f"Layer {layer} Normal Tangent Frame")
        rotate.node_tree = _normal_tangent_rotation_group()
        rotate.inputs["Rotation"].default_value = rotation
        self.connect_nodes(normal_output, rotate.inputs["Normal"])
        return rotate.outputs["Normal"]

    def _model_tint_opacity(self, m: NodeMath):
        """The opacity paired with _model_tint, used only by detail mode 1's pattern selector."""
        if self.tinted:
            source = self.create_node(Nodes.ShaderNodeVertexColor, "Detail Model Tint")
            source.layer_name = "TINT"
            alpha = source.outputs["Alpha"]
        else:
            return m.scalar(1.0)
        amount = self._material_resource.get_float_property("g_flModelTintAmount", 1.0)
        return m.lerp(1.0, alpha, amount)

    def _apply_layer_detail(self, m: NodeMath, layer: int, color_output, layer1_uv, selector):
        """Apply the shader's layer detail mode. Both detail textures use the transformed layer-1 albedo UV,
        followed by their own scale."""
        slot = f"g_tLayer{layer}Detail"
        detail_count = self._material_resource.get_int_property("F_DETAILTEXTURE", 0)
        if detail_count < layer or not self._have_texture(slot):
            self._skip_texture(slot)
            return m.vector(color_output)

        mode = self._material_resource.get_int_property("F_DETAILBLENDMODE", 0)
        if mode not in (0, 1):
            self.logger.warning(f"Unsupported F_DETAILBLENDMODE {mode}; skipping {slot}")
            self._skip_texture(slot)
            return m.vector(color_output)

        detail_texture = self._get_texture(slot, (0.5, 0.5, 0.5, 1), True)
        scale = self._material_resource.get_vector_property(f"g_vLayer{layer}DetailScale", (4.0, 4.0, 0.0))
        transform_node = self.create_transform(layer1_uv, scale, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        transform_node.name = f"Layer {layer} Detail UV"
        self.connect_nodes(transform_node.outputs[0], detail_texture.inputs[0])
        tint_and_blend = self._material_resource.get_vector_property(f"g_vLayer{layer}DetailTintAndBlend",
                                                                     (1.0, 1.0, 1.0, 1.0))
        add_bump_maps = self._material_resource.get_int_property("F_ADDBUMPMAPS", 0) == 1

        mod2x = self.create_mix_color('MULTIPLY')
        mod2x.name = f"Layer {layer} Detail Mod2x"
        mod2x.inputs[MIX_FACTOR].default_value = 1.0
        if mode == 0:
            self.connect_nodes(detail_texture.outputs[0], mod2x.inputs[MIX_A])
            tint = (1.0, 1.0, 1.0) if add_bump_maps else tint_and_blend[:3]
            mod2x.inputs[MIX_B].default_value = (*(2.0 * value for value in tint), 1.0)
        else:
            channels = self.create_node(Nodes.ShaderNodeSeparateColor, f"Layer {layer} Detail Patterns")
            self.connect_nodes(detail_texture.outputs[0], channels.inputs[0])
            red = m.scalar(channels.outputs["Red"])
            if not add_bump_maps and tint_and_blend[0] != 1.0:
                red *= tint_and_blend[0]
            pattern = m.lerp(red, detail_texture.outputs["Alpha"], selector)
            selected = m.combine(pattern, pattern, pattern)
            m.set(mod2x.inputs[MIX_A], selected)
            mod2x.inputs[MIX_B].default_value = (2.0, 2.0, 2.0, 1.0)

        blend = self.create_mix_color('MULTIPLY')
        blend.name = f"Layer {layer} Detail"
        blend.inputs[MIX_FACTOR].default_value = tint_and_blend[3]
        m.set(blend.inputs[MIX_A], color_output)
        self.connect_nodes(mod2x.outputs[MIX_RESULT], blend.inputs[MIX_B])
        return m.vector(blend.outputs[MIX_RESULT])

    def _layer_blend_factor(self):
        """How much of layer 2 shows (VRF's complex.frag): the painted blend weight w (TEXCOORD_4.x, the
        VertexPaintBlendParams stream), as is with F_FANCY_BLENDING 0 (VertexBlend), else
        smoothstep(max(0, m - s), min(1, m + s), w) for the linear g_tBlendModulation's mask m (1: green, with the
        softness s in red; 2: green, 3: alpha, with s = g_flBlendSoftness)."""
        blend_uv = self.create_node(Nodes.ShaderNodeUVMap)
        blend_uv.uv_map = BLEND_UV
        weight = self.create_node(Nodes.ShaderNodeSeparateXYZ)
        self.connect_nodes(blend_uv.outputs[0], weight.inputs[0])
        weight_output = weight.outputs["X"]
        mode = self._material_resource.get_int_property("F_FANCY_BLENDING", 0)
        if mode == 0 or not self._have_texture("g_tBlendModulation"):
            self._skip_texture("g_tBlendModulation")
            return LayerBlend(weight_output, weight_output, None, mode)
        modulation = self._get_texture("g_tBlendModulation", (0.5, 0.5, 0.5, 0.5), True)
        self._connect_layer_uv(modulation, "BlendModulate")
        channels = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(modulation.outputs[0], channels.inputs[0])
        mask = modulation.outputs["Alpha"] if mode == 3 else channels.outputs["Green"]
        smoothstep = self.create_node(Nodes.ShaderNodeMapRange)
        smoothstep.interpolation_type = 'SMOOTHSTEP'
        self.connect_nodes(weight_output, smoothstep.inputs["Value"])
        for bound, operation, value in (("From Min", 'SUBTRACT', 0.0), ("From Max", 'ADD', 1.0)):
            offset = self.create_node(Nodes.ShaderNodeMath)
            offset.operation = operation
            self.connect_nodes(mask, offset.inputs[0])
            if mode == 1:
                self.connect_nodes(channels.outputs["Red"], offset.inputs[1])
            else:
                offset.inputs[1].default_value = self._material_resource.get_float_property(
                    "g_flBlendSoftness", 0.5)
            clamp = self.create_node(Nodes.ShaderNodeMath)
            clamp.operation = 'MAXIMUM' if value == 0.0 else 'MINIMUM'
            self.connect_nodes(offset.outputs[0], clamp.inputs[0])
            clamp.inputs[1].default_value = value
            self.connect_nodes(clamp.outputs[0], smoothstep.inputs[bound])
        return LayerBlend(smoothstep.outputs["Result"], weight_output, mask, mode)

    def _apply_layer_border(self, m: NodeMath, color: Vector, blend: LayerBlend | None):
        if blend is None or blend.mode not in (2, 3) or blend.mask is None:
            return color
        strength = self._material_resource.get_float_property(
            "g_flLayerBorderStrength", LAYER_BORDER_STRENGTH_DEFAULT)
        tint = self._material_resource.get_vector_property("g_vLayerBorderTint", (1.0, 1.0, 1.0, 0.0))
        linear_tint = _srgb_to_linear(tuple(tint[:3]))
        if strength == 0.0 or linear_tint == (1.0, 1.0, 1.0):
            return color

        strength_value = self._value("g_flLayerBorderStrength", strength)
        softness = self._value(
            "g_flLayerBorderSoftness",
            self._material_resource.get_float_property(
                "g_flLayerBorderSoftness", LAYER_BORDER_SOFTNESS_DEFAULT),
        )
        authored_offset = self._value(
            "g_flLayerBorderOffset (Authored)",
            self._material_resource.get_float_property("g_flLayerBorderOffset", 0.0),
        )
        tint_node = self.create_node(Nodes.ShaderNodeRGB, "g_vLayerBorderTint (Linear)")
        tint_node.outputs[0].default_value = (*linear_tint, 1.0)

        mask = m.scalar(blend.mask)
        shifted_weight = m.saturate(m.scalar(blend.weight) + authored_offset)
        border_position = m.smoothstep(m.max(0.0, mask - softness),
                                       m.min(1.0, mask + softness),
                                       shifted_weight)
        band = m.scalar(strength_value) * (1.0 - m.abs(2.0 * border_position - 1.0))
        bordered = color * (1.0 + (m.vector(tint_node.outputs[0]) - 1.0) * band)
        if not bordered.is_const:
            bordered.socket.node.name = "Layer Border Tint"
        return bordered

    def _connect_layer_blend(self, shader, blend: LayerBlend | None):
        """Feed the layer factor through the locked group's inverse smoothstep encoding. With its neutral ramp
        settings the group then recovers the exact factor for color, normal, roughness and alpha."""
        if blend is not None:
            def math_node(operation, *operands):
                node = self.create_node(Nodes.ShaderNodeMath)
                node.operation = operation
                for index, operand in enumerate(operands):
                    if isinstance(operand, bpy.types.NodeSocket):
                        self.connect_nodes(operand, node.inputs[index])
                    else:
                        node.inputs[index].default_value = operand
                return node.outputs[0]

            angle = math_node('DIVIDE',
                              math_node('ARCSINE', math_node('MULTIPLY_ADD', blend.factor, -2.0, 1.0)),
                              3.0)
            inverse = math_node('SUBTRACT', 0.5, math_node('SINE', angle))
            green = self.create_node(Nodes.ShaderNodeCombineColor)
            self.connect_nodes(inverse, green.inputs["Green"])
            self.connect_nodes(green.outputs[0], shader.inputs["BlendModulate"])
        else:
            self._skip_texture("g_tBlendModulation")
            shader.inputs["BlendModulate"].default_value = (0.0, 0.0, 0.0, 1.0)
        shader.inputs["Softness"].default_value = 0.0
        shader.inputs["V0"].default_value = 1.0
        shader.inputs["V1"].default_value = 0.0
        shader.inputs["Sharpness"].default_value = 1.0

    def _bevel_normal(self, m: NodeMath, blend: LayerBlend | None, encoded_normal: Vector):
        if blend is None or blend.mode not in (2, 3) or blend.mask is None:
            return None
        strength = self._material_resource.get_float_property("g_flBevelBlendStrength", 0.0)
        sharpness = self._material_resource.get_float_property(
            "g_flBevelBlendSharpness", BEVEL_BLEND_SHARPNESS_DEFAULT)
        if sharpness == 0.0:
            return None

        width = self._value(
            "g_flBevelBlendWidth",
            self._material_resource.get_float_property("g_flBevelBlendWidth", BEVEL_BLEND_WIDTH_DEFAULT),
        )
        sharpness_value = self._value("g_flBevelBlendSharpness", sharpness)
        strength_value = self._value("g_flBevelBlendStrength", strength)
        softness = self._material_resource.get_float_property("g_flBlendSoftness", 0.5)
        mask = m.scalar(blend.mask)
        bevel_extent = m.scalar(width) * softness
        bevel_position = m.smoothstep(m.max(0.0, mask - bevel_extent),
                                      m.min(1.0, mask + bevel_extent),
                                      blend.weight)
        weight = m.saturate((1.0 - m.abs(2.0 * bevel_position - 1.0)) * sharpness_value)
        weight.socket.node.name = "Layer Bevel Seam Weight"

        geometry = self.create_node(Nodes.ShaderNodeNewGeometry, "Layer Bevel Geometry")
        bump = self.create_node(Nodes.ShaderNodeBump, "Layer Bevel Normal")
        bump.inputs["Strength"].default_value = 1.0
        m.set(bump.inputs["Distance"], m.scalar(strength_value) * SOURCE2_HAMMER_UNIT_TO_METERS)
        m.set(bump.inputs["Height"], bevel_position)
        self.connect_nodes(geometry.outputs["Normal"], bump.inputs["Normal"])

        normal_map = self.create_node(Nodes.ShaderNodeNormalMap, "Layer Normal Map")
        m.set(normal_map.inputs["Color"], encoded_normal)
        # Strength displaces the virtual surface; the independently weighted result is an absolute derivative normal.
        weighted = m.vector(bump.outputs["Normal"]) * weight
        weighted.socket.node.name = "Layer Bevel Weighted Normal"
        combined = m.normalize(m.vector(normal_map.outputs["Normal"]) + weighted)
        if not combined.is_const:
            combined.socket.node.name = "Layer Bevel Blend"
        return combined

    def _bevel_shader(self, m: NodeMath, blend: LayerBlend | None, color: Vector, model_tint,
                      encoded_normal: Vector, roughness: Scalar, alpha: Scalar):
        normal = self._bevel_normal(m, blend, encoded_normal)
        if normal is None:
            return None
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, f"{self.SHADER} Bevel")
        template = bpy.data.node_groups["csgo_complex.vfx"].nodes["csgo_complex.vfx"]
        for source_input in template.inputs:
            target_input = shader.inputs.get(source_input.name)
            if target_input is None:
                continue
            value = source_input.default_value
            target_input.default_value = tuple(value) if hasattr(value, "__len__") else value
        shader.distribution = template.distribution
        shader.subsurface_method = template.subsurface_method
        m.set(shader.inputs["Base Color"], color * m.vector(model_tint))
        m.set(shader.inputs["Roughness"], roughness)
        m.set(shader.inputs["Alpha"], alpha)
        m.set(shader.inputs["Normal"], normal)
        return shader

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tLayer1AmbientOcclusion")
        self._skip_texture("g_tLayer2AmbientOcclusion")
        # These belong to csgo_complex's generic detail path, not lightmappedgeneric's layer details.
        for slot in ("g_tDetail", "g_tDetailMask", "g_tNormalDetail"):
            self._skip_texture(slot)

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_lightmappedgeneric.vfx", name=self.SHADER)
        shader.inputs["F_DETAIL_TEXTURE"].default_value = 0.0
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))
        m = NodeMath(material.node_tree, self.create_node)

        model_tint = self.create_mix_color('MIX')
        model_tint.inputs[MIX_FACTOR].default_value = material_data.get_float_property("g_flModelTintAmount", 1.0)
        model_tint.inputs[MIX_A].default_value = (1.0, 1.0, 1.0, 1.0)
        self.connect_nodes(self._model_tint(), model_tint.inputs[MIX_B])
        self.connect_nodes(model_tint.outputs[MIX_RESULT], shader.inputs["ModelTint"])

        uses_alpha = (material_data.get_int_property("F_ALPHA_TEST", 0) or
                      self._is_translucent() or
                      material_data.get_int_property("F_OVERLAY", 0))
        color0 = m.vector((1.0, 1.0, 1.0))
        color1 = m.vector((1.0, 1.0, 1.0))
        texture_alpha0 = m.scalar(1.0)
        surface_alpha0 = m.scalar(1.0)
        surface_alpha1 = m.scalar(1.0)
        layer1_uv = None

        if self._have_texture("g_tColor"):
            color0_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            layer1_uv = self._connect_layer_uv(color0_texture, "Layer1")
            color0 = m.vector(color0_texture.outputs[0])
            texture_alpha0 = m.scalar(color0_texture.outputs[1])
            if uses_alpha:
                surface_alpha0 = texture_alpha0
                m.set(shader.inputs["TextureAlpha0"], surface_alpha0)

        have_layer2 = self._have_texture("g_tLayer2Color")
        if have_layer2:
            color1_texture = self._get_texture("g_tLayer2Color", (1, 1, 1, 1))
            self._connect_layer_uv(color1_texture, "Layer2")
            color1 = m.vector(color1_texture.outputs[0])
            if uses_alpha:
                surface_alpha1 = m.scalar(color1_texture.outputs[1])
                m.set(shader.inputs["TextureAlpha1"], surface_alpha1)

        normal0 = m.vector((0.5, 0.5, 1.0))
        normal1 = m.vector((0.5, 0.5, 1.0))
        roughness0 = m.scalar(0.5)
        roughness1 = m.scalar(0.5)
        if self._have_texture("g_tLayer1NormalRoughness"):
            normal0_texture = self._get_texture("g_tLayer1NormalRoughness", (0.5, 0.5, 1, 1))
            self._connect_layer_uv(normal0_texture, "Layer1Normal")
            normal0 = m.vector(self._rotate_tangent_normal(normal0_texture.outputs[0], "Layer1Normal", 1))
            roughness0 = m.scalar(normal0_texture.outputs[1])
            m.set(shader.inputs["TextureNormal0"], normal0)
            m.set(shader.inputs["TextureRoughness0"], roughness0)

        if self._have_texture("g_tLayer2NormalRoughness"):
            normal1_texture = self._get_texture("g_tLayer2NormalRoughness", (0.5, 0.5, 1, 1))
            self._connect_layer_uv(normal1_texture, "Layer2Normal")
            normal1 = m.vector(self._rotate_tangent_normal(normal1_texture.outputs[0], "Layer2Normal", 2))
            roughness1 = m.scalar(normal1_texture.outputs[1])
            m.set(shader.inputs["TextureNormal1"], normal1)
            m.set(shader.inputs["TextureRoughness1"], roughness1)

        blend = self._layer_blend_factor() if have_layer2 else None
        self._connect_layer_blend(shader, blend)

        detail_count = material_data.get_int_property("F_DETAILTEXTURE", 0)
        if detail_count and layer1_uv is None:
            layer1_uv = self._texcoord_transform("Layer1", scale_about_center=True).outputs[0]
        selector = None
        if material_data.get_int_property("F_DETAILBLENDMODE", 0) == 1:
            selector = texture_alpha0 * self._model_tint_opacity(m)
        if detail_count:
            color0 = self._apply_layer_detail(m, 1, color0, layer1_uv, selector)
            color1 = self._apply_layer_detail(m, 2, color1, layer1_uv, selector)
        else:
            self._skip_texture("g_tLayer1Detail")
            self._skip_texture("g_tLayer2Detail")
        if material_data.get_int_property("F_ADDBUMPMAPS", 0):
            self.logger.warning("F_ADDBUMPMAPS detail-normal blending is not exposed by the locked node group; "
                                "only its confirmed color-detail tint bypass is applied")

        color0 = self._apply_layer_border(m, color0, blend)
        m.set(shader.inputs["TextureColor0"], color0)
        if have_layer2:
            m.set(shader.inputs["TextureColor1"], color1)

        if blend is None:
            blended_color = color0
            blended_normal = normal0
            blended_roughness = roughness0
            blended_alpha = surface_alpha0
        else:
            blended_color = m.lerp(color0, color1, blend.factor)
            blended_normal = m.lerp(normal0, normal1, blend.factor)
            blended_roughness = m.lerp(roughness0, roughness1, blend.factor)
            blended_alpha = m.lerp(surface_alpha0, surface_alpha1, blend.factor)
        bevel_shader = self._bevel_shader(
            m, blend, blended_color, model_tint.outputs[MIX_RESULT],
            blended_normal, blended_roughness, blended_alpha,
        )
        surface = bevel_shader.outputs["BSDF"] if bevel_shader is not None else shader.outputs["BSDF"]
        self.connect_nodes(surface, material_output.inputs["Surface"])

        if material_data.get_int_property("F_ALPHA_TEST", 0):
            self.set_blend_mode('CLIP')
        elif self._is_translucent():
            self.set_blend_mode('HASHED')
        elif material_data.get_int_property("F_OVERLAY", 0):
            self.set_blend_mode('HASHED')
