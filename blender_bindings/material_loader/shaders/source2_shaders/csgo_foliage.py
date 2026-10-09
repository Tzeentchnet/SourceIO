from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


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
