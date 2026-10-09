from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOLightmappedGeneric(Source2ShaderBase):
    SHADER: str = 'csgo_lightmappedgeneric.vfx'

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
            self.connect_nodes(self._apply_layer_detail(1, color0_texture.outputs[0]), shader.inputs["TextureColor0"])
            if (material_data.get_int_property("F_ALPHA_TEST", 0) or
                    self._is_translucent() or
                    material_data.get_int_property("F_OVERLAY", 0)):
                self.connect_nodes(color0_texture.outputs[1], shader.inputs["TextureAlpha0"])

        if self._have_texture("g_tLayer2Color"):
            color_texture = self._get_texture("g_tLayer2Color", (1, 1, 1, 1))
            if (material_data.get_int_property("F_ALPHA_TEST", 0) or
                    self._is_translucent() or
                    material_data.get_int_property("F_OVERLAY", 0)):
                self.connect_nodes(color_texture.outputs[1], shader.inputs["TextureAlpha1"])

            self.connect_nodes(self._apply_layer_detail(2, color_texture.outputs[0]), shader.inputs["TextureColor1"])

        if self._have_texture("g_tLayer1NormalRoughness"):
            normal0_texture = self._get_texture("g_tLayer1NormalRoughness", (0.5, 0.5, 1, 1))
            self.connect_nodes(normal0_texture.outputs[0], shader.inputs["TextureNormal0"])
            self.connect_nodes(normal0_texture.outputs[1], shader.inputs["TextureRoughness0"])

        if self._have_texture("g_tLayer2NormalRoughness"):
            normal_texture = self._get_texture("g_tLayer2NormalRoughness", (0.5, 0.5, 1, 1))
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal1"])
            self.connect_nodes(normal_texture.outputs[1], shader.inputs["TextureRoughness1"])

        if self._have_texture("g_tBlendModulation"):
            color_texture = self._get_texture("g_tBlendModulation", (1, 1, 1, 1))

            self.connect_nodes(color_texture.outputs[0], shader.inputs["BlendModulate"])

        # TODO: tinting

        if self.tinted:
            object_color = self.create_node(Nodes.ShaderNodeObjectInfo)
            self.connect_nodes(object_color.outputs["Color"], shader.inputs["ModelTint"])

        shader.inputs["Softness"].default_value = material_data.get_float_property("g_flBlendSoftness", 0.5)
        shader.inputs["Sharpness"].default_value = material_data.get_float_property("g_flBevelBlendSharpness", 4)

        if material_data.get_int_property("F_ALPHA_TEST", 0):
            self.set_blend_mode('CLIP')
        elif self._is_translucent():
            self.set_blend_mode('HASHED')
        elif material_data.get_int_property("F_OVERLAY", 0):
            self.set_blend_mode('HASHED')
