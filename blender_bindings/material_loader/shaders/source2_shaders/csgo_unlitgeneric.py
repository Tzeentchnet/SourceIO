from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase, OPAQUE, ALPHA_TEST
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOUnlitGeneric(Source2ShaderBase):
    """Unlit: the color is drawn as it is (an Emission shader), blended by F_BLEND_MODE.

    The color is g_tColor (times g_tColor2 with F_TWOTEXTURE, alpha too), the color tint and the model tint.
    The blend modes are ``Source2ShaderBase._blended_surface``'s. Additive multiplies the color by its alpha:
    nuke_clouds_002 (the de_dust2 skybox's cloud cards) is white in both textures, with the clouds in alpha.
    Vertex colors (F_VERTEX_COLOR: sprites, particles) and texture scrolling and animation aren't applied.
    """
    SHADER: str = 'csgo_unlitgeneric.vfx'

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))

        color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
        self.connect_nodes(self._texcoord_transform().outputs[0], color_texture.inputs[0])
        color_output, alpha_output = color_texture.outputs[0], color_texture.outputs[1]

        if self._check_flag("F_TWOTEXTURE"):
            color2_texture = self._get_texture("g_tColor2", (1, 1, 1, 1))
            # Unlike the first texture's, this transform scales about the center.
            transform = self.create_transform("TEXCOORD",
                                              material_data.get_vector_property("g_vTex2CoordScale", (1.0, 1.0, 0.0)),
                                              material_data.get_vector_property("g_vTex2CoordOffset", (0.0, 0.0, 0.0)),
                                              material_data.get_vector_property("g_vTex2CoordCenter", (0.5, 0.5, 0.0)),
                                              material_data.get_float_property("g_flTex2CoordRotation", 0.0),
                                              scale_about_center=True)
            self.connect_nodes(transform.outputs[0], color2_texture.inputs[0])
            color_output = self._multiply(color_output, color2_texture.outputs[0])
            alpha_multiply = self.create_node(Nodes.ShaderNodeMath)
            alpha_multiply.operation = 'MULTIPLY'
            self.connect_nodes(alpha_output, alpha_multiply.inputs[0])
            self.connect_nodes(color2_texture.outputs[1], alpha_multiply.inputs[1])
            alpha_output = alpha_multiply.outputs[0]
        else:
            self._skip_texture("g_tColor2")

        color_output = self._tinted(color_output)
        blend_mode = material_data.get_int_property("F_BLEND_MODE", OPAQUE)
        if blend_mode not in (OPAQUE, ALPHA_TEST):
            alpha_output = self._opacity_scaled(alpha_output)
        surface = self._blended_surface(color_output, alpha_output, blend_mode, alpha_test_reference=
                                        material_data.get_float_property("g_flAlphaTestReference", 0.5))
        self.connect_nodes(surface, material_output.inputs['Surface'])
