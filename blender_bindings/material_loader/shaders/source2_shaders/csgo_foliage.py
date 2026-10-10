from pprint import pformat
from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT, Nodes
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


def _srgb_to_linear(color):
    return tuple(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                 for channel in color)


class CSGOFoliage(Source2ShaderBase):
    SHADER: str = 'csgo_foliage.vfx'

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves;
        # the noise map drives the wind animation.
        self._skip_texture("g_tAmbientOcclusion")
        self._skip_texture("g_tNoiseMap")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        data_block = self._material_resource.get_block(KVBlock,block_name='DATA')
        self.logger.info(pformat(dict(data_block)))

        color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))

        color_output = color_texture.outputs[0]
        alpha_output = color_texture.outputs[1]

        model_tint = self._model_tint()
        material_tint = _srgb_to_linear(
            self._material_resource.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))[:3])
        if material_tint != (1.0, 1.0, 1.0):
            model_tint = self._multiply(model_tint, (*material_tint, 1.0))

        tint_amount = self._material_resource.get_float_property("g_flModelTintAmount", 1.0)
        tint_factor = tint_amount
        if self._check_flag("F_TINT_MASK"):
            tint_mask = self._get_texture("g_tTintMask", (1.0, 1.0, 1.0, 1.0), True)
            split_tint_mask = self.create_node(Nodes.ShaderNodeSeparateColor)
            split_tint_mask.mode = "RGB"
            self.connect_nodes(tint_mask.outputs["Color"], split_tint_mask.inputs["Color"])
            tint_factor = split_tint_mask.outputs["Red"]
            if tint_amount != 1.0:
                tint_factor = self._multiply_value(tint_factor, tint_amount)
        else:
            self._skip_texture("g_tTintMask")

        tinted_color = self.create_mix_color("MULTIPLY")
        if isinstance(tint_factor, bpy.types.NodeSocket):
            self.connect_nodes(tint_factor, tinted_color.inputs[MIX_FACTOR])
        else:
            tinted_color.inputs[MIX_FACTOR].default_value = tint_factor
        self.connect_nodes(color_output, tinted_color.inputs[MIX_A])
        self.connect_nodes(model_tint, tinted_color.inputs[MIX_B])
        color_output = tinted_color.outputs[MIX_RESULT]

        self.connect_nodes(color_output, shader.inputs["Base Color"])

        normal_texture = self._get_texture("g_tNormal", (1, 1, 1, 1), True, True)
        normal_conv = self.create_node(Nodes.ShaderNodeNormalMap)
        self.connect_nodes(normal_texture.outputs[0], normal_conv.inputs[1])
        self.connect_nodes(normal_conv.outputs[0], shader.inputs["Normal"])
        self.connect_nodes(normal_texture.outputs[1], shader.inputs["Roughness"])

        if self._material_resource.get_int_property("F_ALPHA_TEST", 0):
            self.set_blend_mode('CLIP')
            alpha_test_ref = self._material_resource.get_float_property("g_flAlphaTestReference", 0.5)
            self.connect_nodes(self.insert_alpha_clip(alpha_output, alpha_test_ref), shader.inputs["Alpha"])

        elif self._is_translucent():
            self.set_blend_mode('HASHED')
            self.connect_nodes(color_texture.outputs[1], shader.inputs["Alpha"])

        self._add_transmission(shader.outputs['BSDF'], color_output, shader.inputs["Alpha"],
                               material_output.inputs['Surface'])
