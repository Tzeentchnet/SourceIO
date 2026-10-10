from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOVertexLitGeneric(Source2ShaderBase):
    SHADER: str = 'csgo_vertexlitgeneric.vfx'

    def _detail_uv(self, flag: str):
        if self._material_resource.get_int_property(flag, 1):
            return self._secondary_uv_or_primary()
        uv_node = self.create_node(Nodes.ShaderNodeUVMap)
        uv_node.uv_map = "TEXCOORD"
        return uv_node.outputs[0]

    def _apply_detail_texture(self, color_output, normal_input):
        """The Mod2X detail (Source2ShaderBase._apply_detail); the detail and its mask read the secondary UV set
        unless g_bUseSecondaryUvForDetailTexture/Mask is 0."""
        if not self._material_resource.get_int_property("F_DETAIL_TEXTURE", 0):
            return self._apply_detail(color_output, normal_input, None, None)  # skips the slots
        material_data = self._material_resource
        detail_transform = self.create_transform(
            self._detail_uv("g_bUseSecondaryUvForDetailTexture"),
            material_data.get_vector_property("g_vDetailTexCoordScale", (1.0, 1.0, 0.0)),
            material_data.get_vector_property("g_vDetailTexCoordOffset", (0.0, 0.0, 0.0)),
            (0.5, 0.5, 0.0), material_data.get_float_property("g_flDetailTexCoordRotation", 0.0))
        return self._apply_detail(color_output, normal_input, detail_transform.outputs[0],
                                  self._detail_uv("g_bUseSecondaryUvForDetailMask"))

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock,block_name='DATA')
        self.logger.info(pformat(dict(data)))

        uv_output = self._texcoord_transform().outputs[0]
        if self._have_texture("g_tColor"):
            color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self.connect_nodes(uv_output, color_texture.inputs[0])
            color_output = self._apply_detail_texture(color_texture.outputs[0], shader.inputs["TextureNormal"])
            if self._check_flag("F_DECAL_TEXTURE") and self._have_texture("g_tDecal"):
                color_output = self._apply_decal(color_output)
            self.connect_nodes(color_output, shader.inputs["TextureColor"])
            alpha_output = color_texture.outputs[1]
        else:
            alpha_output = None
        if self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            normal_transform = self._texcoord_transform("Normal", scale_about_center=True)
            self.connect_nodes(normal_transform.outputs[0], normal_texture.inputs[0])
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

        self.connect_nodes(self._model_tint(), shader.inputs["m_vColorTint"])

        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property(
            "g_flModelTintAmount", 1.0)

        if self._have_texture("g_tMetalness"):
            metalness_split = self._split_metalness_texture(uv_output)
            self.connect_nodes(metalness_split.outputs[1], shader.inputs["TextureMetalness"])
        elif material_data.get_int_property("F_METALNESS_TEXTURE", 0) == 1 and alpha_output is not None:
            self.connect_nodes(alpha_output, shader.inputs["TextureMetalness"])
        else:
            shader.inputs["TextureMetalness"].default_value = material_data.get_float_property(
                "g_flMetalness", 0)

        if material_data.get_int_property("F_ALPHA_TEST", 0) and alpha_output is not None:
            self.set_blend_mode('CLIP')
            alpha_test_ref = material_data.get_float_property("g_flAlphaTestReference", 0.5)
            self.connect_nodes(self.insert_alpha_clip(alpha_output, alpha_test_ref), shader.inputs["Alpha"])

        elif self._is_translucent() and alpha_output is not None:
            self.set_blend_mode('HASHED')
            self.connect_nodes(alpha_output, shader.inputs["Alpha"])
