from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOStaticOverlay(Source2ShaderBase):
    SHADER: str = 'csgo_static_overlay.vfx'

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock,block_name='DATA')
        self.logger.info(pformat(dict(data)))

        if self._have_texture("g_tColor"):
            color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self.connect_nodes(self._texcoord_transform().outputs[0], color_texture.inputs[0])

            self.connect_nodes(color_texture.outputs[0], shader.inputs["TextureColor"])
            alpha_output = color_texture.outputs[1]
        else:
            alpha_output = None
        if self._have_texture("g_tDetail"):
            detail_texture = self._get_texture("g_tDetail", (1, 1, 1, 1))
            detail_mask_texture = self._get_texture("g_tDetailMask", (1, 0, 0, 1))
            detail_transform = self.create_transform(
                "TEXCOORD",
                material_data.get_vector_property("g_vDetailTexCoordScale", (1.0, 1.0, 0.0)),
                material_data.get_vector_property("g_vDetailTexCoordOffset", (0.0, 0.0, 0.0)),
                (0.5, 0.5, 0.0), material_data.get_float_property("g_flDetailTexCoordRotation", 0.0))
            self.connect_nodes(detail_transform.outputs[0], detail_texture.inputs[0])

            self.connect_nodes(detail_texture.outputs[0], shader.inputs["TextureDetail"])
            self.connect_nodes(detail_mask_texture.outputs[0], shader.inputs["TextureDetailMask"])
            shader.inputs["F_DETAIL_TEXTURE"].default_value = float(
                material_data.get_int_property("F_DETAIL_TEXTURE", 0))
            shader.inputs["g_flDetailBlendFactor"].default_value = material_data.get_float_property(
                "g_flDetailBlendFactor", 0)

        if self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal"])
            self.connect_nodes(normal_texture.outputs[1], shader.inputs["TextureRoughness"])

        if self._have_texture("g_tTintMask") and material_data.get_int_property("F_TINT_MASK", 0) == 1:
            tint_texture = self._get_texture("g_tTintMask", (1, 0, 0, 1), True, True)
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureTintMask"])

        if self._have_texture("g_tSelfIllumMask") and material_data.get_int_property("F_SELF_ILLUM", 0) == 1:
            tint_texture = self._get_texture("g_tSelfIllumMask", (0, 0, 0, 1), True, False)
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureSelfIllumMask"])

            shader.inputs["SelfIllumTint"].default_value = material_data.get_vector_property(
                "g_vSelfIllumTint", (1, 1, 1, 1))

            shader.inputs["Emission Strength"].default_value = material_data.get_float_property(
                "g_flSelfIllumScale", 1)

        tint = material_data.get_vector_property("g_vColorTint", None)
        if tint is not None and (tint[0] != 1.0 or tint[1] != 1.0 or tint[2] != 1.0):
            shader.inputs["g_vColorTint"].default_value = tint

        if self.tinted:
            object_color = self.create_node(Nodes.ShaderNodeObjectInfo)
            self.connect_nodes(object_color.outputs["Color"], shader.inputs["m_vColorTint"])
        else:
            object_info_node = self.create_node(Nodes.ShaderNodeObjectInfo)
            self.connect_nodes(object_info_node.outputs["Color"], shader.inputs["m_vColorTint"])

        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property(
            "g_flModelTintAmount", 0.0)

        if material_data.get_int_property("F_METALNESS_TEXTURE", 0) == 1 and alpha_output is not None:
            self.connect_nodes(alpha_output, shader.inputs["TextureMetalness"])
        else:
            shader.inputs["TextureMetalness"].default_value = material_data.get_float_property(
                "g_flMetalness", 0)

        if self._material_resource.get_int_property("F_ALPHA_TEST", 0) and alpha_output is not None:
            self.set_blend_mode('BLEND')
            self.connect_nodes(alpha_output, shader.inputs["Alpha"])
        elif self._material_resource.get_int_property("F_OVERLAY", 0) and alpha_output is not None:
            self.set_blend_mode('BLEND')
            self.connect_nodes(alpha_output, shader.inputs["Alpha"])
        elif self._material_resource.get_int_property("F_BLEND_MODE", 0) and alpha_output is not None:
            self.set_blend_mode('BLEND')
            self.connect_nodes(alpha_output, shader.inputs["Alpha"])
