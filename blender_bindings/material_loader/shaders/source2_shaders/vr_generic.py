from typing import Any

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import (Nodes, ExtraMaterialParameters, MIX_FACTOR,
                                                                   MIX_A, MIX_B, MIX_RESULT)
from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase


class VRGeneric(Source2ShaderBase):
    SHADER: str = 'vr_standard.vfx'

    @property
    def color_texture(self):
        texture_path = self._material_resource.get_texture_property('g_tColor', None)
        if texture_path is not None:
            image = self.load_texture_or_default(texture_path, (0.3, 0.3, 0.3, 1.0))
            return image
        return None

    @property
    def ambient_occlusion(self):
        texture_path = self._material_resource.get_texture_property('g_tAmbientOcclusion', None)
        if texture_path is not None:
            image = self.load_texture_or_default(texture_path, (1.0, 1.0, 1.0, 1.0))
            image.colorspace_settings.is_data = True
            image.colorspace_settings.name = 'Non-Color'
            return image
        return None

    @property
    def normal_texture(self):
        texture_path = self._material_resource.get_texture_property('g_tNormal', None)
        if texture_path is not None:
            image = self.load_texture_or_default(texture_path, (0.5, 0.5, 1.0, 1.0))
            image.colorspace_settings.is_data = True
            image.colorspace_settings.name = 'Non-Color'
            return image
        return None

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

    @property
    def specular(self):
        return self._material_resource.get_vector_property('g_vGlossinessRange', [0, 1, 0, 0])[0]

    @property
    def roughness(self):
        return self._material_resource.get_vector_property('g_vGlossinessRange', [0, 1, 0, 0])[1]

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        shader.inputs['Roughness'].default_value = self.roughness
        shader.inputs['Specular IOR Level'].default_value = self.specular
        color_texture = self.color_texture
        normal_texture = self.normal_texture

        albedo_node = self.create_node(Nodes.ShaderNodeTexImage, 'albedo')
        albedo_node.image = color_texture

        color_input_socket = shader.inputs['Base Color']
        color_output_socket = albedo_node.outputs['Color']
        if self.color[0] != 1.0 and self.color[1] != 1.0 and self.color[2] != 1.0:
            color_mix = self.create_mix_color('MULTIPLY')
            self.connect_nodes(color_output_socket, color_mix.inputs[MIX_A])
            color = self.color
            if sum(color) > 3:
                color = list(np.divide(color, 255))
            color_mix.inputs[MIX_B].default_value = color
            color_mix.inputs[MIX_FACTOR].default_value = 1.0
            color_output_socket = color_mix.outputs[MIX_RESULT]
        if extra_parameters.get(ExtraMaterialParameters.USE_OBJECT_TINT, False):
            color_output_socket = self.insert_object_tint(color_output_socket, 1.0)
        self.connect_nodes(color_output_socket, color_input_socket)


        if self.translucent or self.alpha_test:
            self.set_blend_mode('HASHED')
            self.connect_nodes(albedo_node.outputs['Alpha'], shader.inputs['Alpha'])
        elif self.metalness:
            self.connect_nodes(albedo_node.outputs['Alpha'], shader.inputs['Metallic'])

        normal_map_texture = self.create_node(Nodes.ShaderNodeTexImage, 'normal')
        normal_map_texture.image = normal_texture

        normalmap_node = self.create_node(Nodes.ShaderNodeNormalMap)

        self.connect_nodes(normal_map_texture.outputs['Color'], normalmap_node.inputs['Color'])
        self.connect_nodes(normalmap_node.outputs['Normal'], shader.inputs['Normal'])

        # if self.selfillum:
        #     selfillummask = self.selfillummask
        #     albedo_node = self.get_node('$basetexture')
        #     if selfillummask is not None:
        #         selfillummask_node = self.create_node(Nodes.ShaderNodeTexImage, '$selfillummask')
        #         selfillummask_node.image = selfillummask
        #         self.connect_nodes(selfillummask_node.outputs['Color'], shader.inputs['Emission Strength'])
        #
        #     else:
        #         self.connect_nodes(albedo_node.outputs['Alpha'], shader.inputs['Emission Strength'])
        #     self.connect_nodes(albedo_node.outputs['Color'], shader.inputs['Emission'])
