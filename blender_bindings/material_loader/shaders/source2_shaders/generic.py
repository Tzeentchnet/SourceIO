from typing import Any

import bpy
import numpy as np

from ...shader_base import (Nodes, ExtraMaterialParameters, MIX_FACTOR,
                                                                   MIX_A, MIX_B, MIX_RESULT)
from ..source2_shader_base import Source2ShaderBase

# Principled BSDF's Specular IOR Level 0.5 is F0 0.04 and scales it linearly.
F0_PER_SPECULAR_LEVEL = 0.08


class Generic(Source2ShaderBase):
    SHADER: str = 'generic.vfx'

    @property
    def color(self):
        return self._material_resource.get_vector_property('g_vColorTint', np.ones(4, dtype=np.float32))

    @property
    def alpha_test(self):
        return self._material_resource.get_int_property('F_ALPHA_TEST', 0)

    @property
    def metalness(self):
        return self._material_resource.get_int_property('F_METALNESS_TEXTURE', 0)

    @property
    def translucent(self):
        return self._material_resource.get_int_property('F_TRANSLUCENT', 0)

    def _range(self, name: str):
        value = self._material_resource.get_vector_property(name, (0, 1, 0, 0))
        return float(value[0]), float(value[1])

    def _scale_and_bias(self, value_output, scale: float, bias: float):
        """value * scale + bias, how the shader maps its g_v*Range parameters onto a texture."""
        math_node = self.create_node(Nodes.ShaderNodeMath)
        math_node.operation = 'MULTIPLY_ADD'
        self.connect_nodes(value_output, math_node.inputs[0])
        math_node.inputs[1].default_value = scale
        math_node.inputs[2].default_value = bias
        return math_node.outputs[0]

    def _connect_specular(self, shader):
        """CS2's generic.vfx with F_SPECULAR: g_tRoughness holds roughness in blue and alpha (red and green are
        normal variance), mapped into 1 - g_vGlossinessRange; g_tMetalnessReflectanceFresnel holds metalness in
        red and reflectance (F0) in green, mapped into g_vMetalnessRange and g_vReflectanceRange."""
        gloss_min, gloss_max = self._range('g_vGlossinessRange')
        roughness_scale = max(gloss_min, gloss_max) - gloss_min
        roughness_bias = 1 - gloss_max
        if self._have_texture('g_tRoughness'):
            roughness_texture = self._get_texture('g_tRoughness', (0, 0, 0.5, 0.5), True)
            split = self.create_node(Nodes.ShaderNodeSeparateColor)
            self.connect_nodes(roughness_texture.outputs[0], split.inputs[0])
            average = self.create_node(Nodes.ShaderNodeMath)
            average.operation = 'ADD'
            self.connect_nodes(split.outputs['Blue'], average.inputs[0])
            self.connect_nodes(roughness_texture.outputs['Alpha'], average.inputs[1])
            roughness = self._scale_and_bias(average.outputs[0], roughness_scale / 2, roughness_bias)
            self.connect_nodes(roughness, shader.inputs['Roughness'])
        else:
            shader.inputs['Roughness'].default_value = 0.5 * roughness_scale + roughness_bias

        metal_min, metal_max = self._range('g_vMetalnessRange')
        reflectance_min, reflectance_max = self._range('g_vReflectanceRange')
        if self._have_texture('g_tMetalnessReflectanceFresnel'):
            texture = self._get_texture('g_tMetalnessReflectanceFresnel', (0, 0.1, 0, 1), True)
            split = self.create_node(Nodes.ShaderNodeSeparateColor)
            self.connect_nodes(texture.outputs[0], split.inputs[0])
            self.connect_nodes(self._scale_and_bias(split.outputs['Red'], metal_max - metal_min, metal_min),
                               shader.inputs['Metallic'])
            self.connect_nodes(self._scale_and_bias(split.outputs['Green'],
                                                    (reflectance_max - reflectance_min) / F0_PER_SPECULAR_LEVEL,
                                                    reflectance_min / F0_PER_SPECULAR_LEVEL),
                               shader.inputs['Specular IOR Level'])
        else:
            shader.inputs['Metallic'].default_value = metal_min
            shader.inputs['Specular IOR Level'].default_value = (
                    (reflectance_min + 0.1 * (reflectance_max - reflectance_min)) / F0_PER_SPECULAR_LEVEL)

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        if self._check_flag('F_SPECULAR'):
            self._connect_specular(shader)
        else:
            # Without F_SPECULAR the shader is diffuse only, and doesn't sample these.
            self._skip_texture('g_tRoughness')
            self._skip_texture('g_tMetalnessReflectanceFresnel')
            shader.inputs['Roughness'].default_value = 1.0
            shader.inputs['Specular IOR Level'].default_value = 0.0

        albedo_node = self._get_texture('g_tColor', (0.3, 0.3, 0.3, 1.0))
        color_output_socket = albedo_node.outputs['Color']
        if any(channel != 1.0 for channel in self.color[:3]):
            color_mix = self.create_mix_color('MULTIPLY')
            self.connect_nodes(color_output_socket, color_mix.inputs[MIX_A])
            color = self.color
            if sum(color) > 3:
                color = list(np.divide(color, 255))
            color_mix.inputs[MIX_B].default_value = self.ensure_length(list(color), 4, 1.0)
            color_mix.inputs[MIX_FACTOR].default_value = 1.0
            color_output_socket = color_mix.outputs[MIX_RESULT]
        if extra_parameters.get(ExtraMaterialParameters.USE_OBJECT_TINT, False):
            color_output_socket = self.insert_object_tint(color_output_socket)
        self.connect_nodes(color_output_socket, shader.inputs['Base Color'])

        if self.translucent or self.alpha_test:
            self.set_blend_mode('HASHED')
            self.connect_nodes(albedo_node.outputs['Alpha'], shader.inputs['Alpha'])
        elif self.metalness:
            self.connect_nodes(albedo_node.outputs['Alpha'], shader.inputs['Metallic'])

        if self._have_texture('g_tNormal'):
            normal_map_texture = self._get_texture('g_tNormal', (0.5, 0.5, 1.0, 1.0), True)
            normalmap_node = self.create_node(Nodes.ShaderNodeNormalMap)
            self.connect_nodes(normal_map_texture.outputs['Color'], normalmap_node.inputs['Color'])
            self.connect_nodes(normalmap_node.outputs['Normal'], shader.inputs['Normal'])

        if self._check_flag('F_SELF_ILLUM'):
            if self._have_texture('g_tSelfIllumMask'):
                mask_output = self._get_texture('g_tSelfIllumMask', (1, 1, 1, 1), True).outputs[0]
            else:
                mask_output = None
            self._handle_self_illum(color_output_socket, mask_output,
                                    self._material_resource.get_vector_property('g_vSelfIllumTint', None),
                                    1.0, self._material_resource.get_float_property('g_flSelfIllumScale', 1.0),
                                    shader.inputs['Emission Color'], shader.inputs['Emission Strength'])
