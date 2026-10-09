from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock

# F_BLEND_MODE, as the shader's features file names them
OPAQUE, TRANSLUCENT, ALPHA_TEST, MOD2X, ADDITIVE, MULTIPLY, MOD_THEN_ADD = range(7)


class CSGOUnlitGeneric(Source2ShaderBase):
    """Unlit: the color is drawn as it is (an Emission shader), blended by F_BLEND_MODE.

    The color is g_tColor (times g_tColor2 with F_TWOTEXTURE, alpha too), the color tint and the model tint.
    Additive multiplies the color by its alpha: nuke_clouds_002 (the de_dust2 skybox's cloud cards) is white in
    both textures, with the clouds in alpha. Multiply and Mod2x darken what is behind (a Transparent BSDF), and
    ModThenAdd, which no CS2 material uses, is drawn opaque. Vertex colors (F_VERTEX_COLOR: sprites, particles)
    and texture scrolling and animation aren't applied.
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

        tint = material_data.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        if any(channel != 1.0 for channel in tint[:3]):
            tint_multiply = self.create_mix_color('MULTIPLY')
            tint_multiply.inputs[MIX_FACTOR].default_value = 1.0
            self.connect_nodes(color_output, tint_multiply.inputs[MIX_A])
            tint_multiply.inputs[MIX_B].default_value = (*tint[:3], 1.0)
            color_output = tint_multiply.outputs[MIX_RESULT]

        if self.tinted:
            model_tint = self.create_node(Nodes.ShaderNodeVertexColor)
            model_tint.layer_name = "TINT"
        else:
            model_tint = self.create_node(Nodes.ShaderNodeObjectInfo)
        color_output = self._multiply(color_output, model_tint.outputs["Color"],
                                      material_data.get_float_property("g_flModelTintAmount", 1.0))

        blend_mode = material_data.get_int_property("F_BLEND_MODE", OPAQUE)
        emission = self.create_node(Nodes.ShaderNodeEmission, self.SHADER)
        if blend_mode == ADDITIVE:
            weighted = self.create_node(Nodes.ShaderNodeVectorMath)
            weighted.operation = 'SCALE'
            self.connect_nodes(color_output, weighted.inputs[0])
            self.connect_nodes(alpha_output, weighted.inputs['Scale'])
            self.connect_nodes(weighted.outputs[0], emission.inputs['Color'])
            surface = self._add_transparent(emission.outputs[0], (1, 1, 1, 1))
            self.set_blend_mode('BLEND')
        elif blend_mode in (MULTIPLY, MOD2X):
            self.bpy_material.node_tree.nodes.remove(emission)
            transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent, self.SHADER)
            if blend_mode == MOD2X:
                color_output = self._multiply(color_output, (2, 2, 2, 1))
            self.connect_nodes(color_output, transparent.inputs['Color'])
            surface = transparent.outputs[0]
            self.set_blend_mode('BLEND')
        else:
            self.connect_nodes(color_output, emission.inputs['Color'])
            surface = emission.outputs[0]
            if blend_mode == TRANSLUCENT:
                surface = self._mix_transparent(surface, alpha_output)
                self.set_blend_mode('BLEND')
            elif blend_mode == ALPHA_TEST:
                reference = material_data.get_float_property("g_flAlphaTestReference", 0.5)
                surface = self._mix_transparent(surface, self.insert_alpha_clip(alpha_output, reference))
                self.set_blend_mode('CLIP')
        self.connect_nodes(surface, material_output.inputs['Surface'])

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

    def _add_transparent(self, surface_output, color):
        transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
        transparent.inputs['Color'].default_value = color
        add_shader = self.create_node(Nodes.ShaderNodeAddShader)
        self.connect_nodes(surface_output, add_shader.inputs[0])
        self.connect_nodes(transparent.outputs[0], add_shader.inputs[1])
        return add_shader.outputs[0]

    def _mix_transparent(self, surface_output, alpha_output):
        transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
        mix_shader = self.create_node(Nodes.ShaderNodeMixShader)
        self.connect_nodes(alpha_output, mix_shader.inputs[0])
        self.connect_nodes(transparent.outputs[0], mix_shader.inputs[1])
        self.connect_nodes(surface_output, mix_shader.inputs[2])
        return mix_shader.outputs[0]
