from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


# vmdl_loader's name for the VertexPaintBlendParams stream (TEXCOORD4), the painted layer blend in x
BLEND_UV = "TEXCOORD_4"


class CSGOLightmappedGeneric(Source2ShaderBase):
    SHADER: str = 'csgo_lightmappedgeneric.vfx'

    def _connect_layer_uv(self, texture_node, prefix: str):
        """Each layer's albedo, normal and the blend modulation have their own UV transform, scaled about its
        center (g_v<prefix>TexCoordScale and so on)."""
        transform = self._texcoord_transform(prefix, scale_about_center=True)
        self.connect_nodes(transform.outputs[0], texture_node.inputs[0])

    def _apply_layer_detail(self, layer: int, color_output):
        """Source 1's mod2x detail: the albedo times twice the detail texture (0.5 is neutral), faded by the blend
        factor in g_vLayer<n>DetailTintAndBlend.w. F_DETAILTEXTURE 1 details layer 1, 2 both layers."""
        slot = f"g_tLayer{layer}Detail"
        if self._material_resource.get_int_property("F_DETAILTEXTURE", 0) < layer or not self._have_texture(slot):
            self._skip_texture(slot)
            return color_output
        detail_texture = self._get_texture(slot, (0.5, 0.5, 0.5, 1), True)
        scale = self._material_resource.get_vector_property(f"g_vLayer{layer}DetailScale", (4.0, 4.0, 0.0))
        transform_node = self.create_transform("TEXCOORD", scale, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        self.connect_nodes(transform_node.outputs[0], detail_texture.inputs[0])
        tint_and_blend = self._material_resource.get_vector_property(f"g_vLayer{layer}DetailTintAndBlend",
                                                                     (1.0, 1.0, 1.0, 1.0))
        mod2x = self.create_mix_color('MULTIPLY')
        mod2x.inputs[MIX_FACTOR].default_value = 1.0
        self.connect_nodes(detail_texture.outputs[0], mod2x.inputs[MIX_A])
        mod2x.inputs[MIX_B].default_value = (*(2.0 * value for value in tint_and_blend[:3]), 1.0)
        blend = self.create_mix_color('MULTIPLY')
        blend.inputs[MIX_FACTOR].default_value = tint_and_blend[3]
        self.connect_nodes(color_output, blend.inputs[MIX_A])
        self.connect_nodes(mod2x.outputs[MIX_RESULT], blend.inputs[MIX_B])
        return blend.outputs[MIX_RESULT]

    def _layer_blend_factor(self):
        """How much of layer 2 shows (VRF's complex.frag): the painted blend weight w (TEXCOORD_4.x, the
        VertexPaintBlendParams stream), as is with F_FANCY_BLENDING 0 (VertexBlend), else
        smoothstep(max(0, m - s), min(1, m + s), w) for the linear g_tBlendModulation's mask m (1: green, with the
        softness s in red; 2: green, 3: alpha, with s = g_flBlendSoftness)."""
        blend_uv = self.create_node(Nodes.ShaderNodeUVMap)
        blend_uv.uv_map = BLEND_UV
        weight = self.create_node(Nodes.ShaderNodeSeparateXYZ)
        self.connect_nodes(blend_uv.outputs[0], weight.inputs[0])
        mode = self._material_resource.get_int_property("F_FANCY_BLENDING", 0)
        if mode == 0 or not self._have_texture("g_tBlendModulation"):
            self._skip_texture("g_tBlendModulation")
            return weight.outputs["X"]
        modulation = self._get_texture("g_tBlendModulation", (0.5, 0.5, 0.5, 0.5), True)
        self._connect_layer_uv(modulation, "BlendModulate")
        channels = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(modulation.outputs[0], channels.inputs[0])
        mask = modulation.outputs["Alpha"] if mode == 3 else channels.outputs["Green"]
        smoothstep = self.create_node(Nodes.ShaderNodeMapRange)
        smoothstep.interpolation_type = 'SMOOTHSTEP'
        self.connect_nodes(weight.outputs["X"], smoothstep.inputs["Value"])
        for bound, operation, value in (("From Min", 'SUBTRACT', 0.0), ("From Max", 'ADD', 1.0)):
            offset = self.create_node(Nodes.ShaderNodeMath)
            offset.operation = operation
            self.connect_nodes(mask, offset.inputs[0])
            if mode == 1:
                self.connect_nodes(channels.outputs["Red"], offset.inputs[1])
            else:
                offset.inputs[1].default_value = self._material_resource.get_float_property("g_flBlendSoftness", 0.5)
            clamp = self.create_node(Nodes.ShaderNodeMath)
            clamp.operation = 'MAXIMUM' if value == 0.0 else 'MINIMUM'
            self.connect_nodes(offset.outputs[0], clamp.inputs[0])
            clamp.inputs[1].default_value = value
            self.connect_nodes(clamp.outputs[0], smoothstep.inputs[bound])
        return smoothstep.outputs["Result"]

    def _connect_layer_blend(self, shader):
        """Feed _layer_blend_factor through the node group's own blend ramp, a smoothstep of the green of
        BlendModulate from min(V1 + Softness, 1) to max(V0 - Softness, 0), sharpened by a sigmoid of exponent
        Sharpness. With Softness 0, V0 1, V1 0 and Sharpness 1 that is smoothstep(0, 1, green), so the green is
        the factor's inverse smoothstep, 0.5 - sin(asin(1 - 2f) / 3). Without a second layer the factor is 0."""
        if self._have_texture("g_tLayer2Color"):
            def math(operation, *operands):
                node = self.create_node(Nodes.ShaderNodeMath)
                node.operation = operation
                for index, operand in enumerate(operands):
                    if isinstance(operand, bpy.types.NodeSocket):
                        self.connect_nodes(operand, node.inputs[index])
                    else:
                        node.inputs[index].default_value = operand
                return node.outputs[0]

            factor = self._layer_blend_factor()
            angle = math('DIVIDE', math('ARCSINE', math('MULTIPLY_ADD', factor, -2.0, 1.0)), 3.0)
            inverse = math('SUBTRACT', 0.5, math('SINE', angle))
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

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tLayer1AmbientOcclusion")
        self._skip_texture("g_tLayer2AmbientOcclusion")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_lightmappedgeneric.vfx", name=self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))

        if self._have_texture("g_tColor"):
            color0_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self._connect_layer_uv(color0_texture, "Layer1")
            self.connect_nodes(self._apply_layer_detail(1, color0_texture.outputs[0]), shader.inputs["TextureColor0"])
            if (material_data.get_int_property("F_ALPHA_TEST", 0) or
                    self._is_translucent() or
                    material_data.get_int_property("F_OVERLAY", 0)):
                self.connect_nodes(color0_texture.outputs[1], shader.inputs["TextureAlpha0"])

        if self._have_texture("g_tLayer2Color"):
            color_texture = self._get_texture("g_tLayer2Color", (1, 1, 1, 1))
            self._connect_layer_uv(color_texture, "Layer2")
            if (material_data.get_int_property("F_ALPHA_TEST", 0) or
                    self._is_translucent() or
                    material_data.get_int_property("F_OVERLAY", 0)):
                self.connect_nodes(color_texture.outputs[1], shader.inputs["TextureAlpha1"])

            self.connect_nodes(self._apply_layer_detail(2, color_texture.outputs[0]), shader.inputs["TextureColor1"])

        if self._have_texture("g_tLayer1NormalRoughness"):
            normal0_texture = self._get_texture("g_tLayer1NormalRoughness", (0.5, 0.5, 1, 1))
            self._connect_layer_uv(normal0_texture, "Layer1Normal")
            self.connect_nodes(normal0_texture.outputs[0], shader.inputs["TextureNormal0"])
            self.connect_nodes(normal0_texture.outputs[1], shader.inputs["TextureRoughness0"])

        if self._have_texture("g_tLayer2NormalRoughness"):
            normal_texture = self._get_texture("g_tLayer2NormalRoughness", (0.5, 0.5, 1, 1))
            self._connect_layer_uv(normal_texture, "Layer2Normal")
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal1"])
            self.connect_nodes(normal_texture.outputs[1], shader.inputs["TextureRoughness1"])

        self._connect_layer_blend(shader)

        model_tint = self.create_mix_color('MIX')
        model_tint.inputs[MIX_FACTOR].default_value = material_data.get_float_property("g_flModelTintAmount", 1.0)
        model_tint.inputs[MIX_A].default_value = (1.0, 1.0, 1.0, 1.0)
        self.connect_nodes(self._model_tint(), model_tint.inputs[MIX_B])
        self.connect_nodes(model_tint.outputs[MIX_RESULT], shader.inputs["ModelTint"])

        if material_data.get_int_property("F_ALPHA_TEST", 0):
            self.set_blend_mode('CLIP')
        elif self._is_translucent():
            self.set_blend_mode('HASHED')
        elif material_data.get_int_property("F_OVERLAY", 0):
            self.set_blend_mode('HASHED')
