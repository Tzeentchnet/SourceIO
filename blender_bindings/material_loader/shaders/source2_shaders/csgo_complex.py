from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase, SECONDARY_UV
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOComplex(Source2ShaderBase):
    SHADER: str = 'csgo_complex.vfx'

    def _skip_unsupported_textures(self):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")

    def _connect_roughness(self, normal_texture, metalness_split, roughness_input, uv_output):
        if self._check_flag("F_ANISOTROPIC_GLOSS") and self._have_texture("g_tAnisoGloss"):
            # The normal map then has only X and Y; g_tAnisoGloss holds the roughness along the tangent (red)
            # and the bitangent (green). Blender gets their average.
            aniso_texture = self._get_texture("g_tAnisoGloss", (0.5, 0.5, 0, 1), True)
            self.connect_nodes(uv_output, aniso_texture.inputs[0])
            average = self.create_node(Nodes.ShaderNodeVectorMath)
            average.operation = 'DOT_PRODUCT'
            self.connect_nodes(aniso_texture.outputs[0], average.inputs[0])
            average.inputs[1].default_value = (0.5, 0.5, 0.0)
            self.connect_nodes(average.outputs['Value'], roughness_input)
        else:
            self._skip_texture("g_tAnisoGloss")
            self.connect_nodes(normal_texture.outputs[1], roughness_input)

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        self._skip_unsupported_textures()
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))

        transform_node = self._texcoord_transform()
        if self._have_texture("g_tColor"):
            color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self.connect_nodes(transform_node.outputs[0], color_texture.inputs[0])
            color_output = color_texture.outputs[0]
            if extra_parameters.get(ExtraMaterialParameters.USE_OBJECT_TINT, False):
                color_output = self.insert_object_tint(color_texture.outputs[0])
            self.connect_nodes(color_output, shader.inputs["TextureColor"])
            albedo_output = color_texture.outputs[0]
            alpha_output = color_texture.outputs[1]
        else:
            albedo_output = alpha_output = None
        if self._have_texture("g_tDetail"):
            scale = material_data.get_vector_property("g_vDetailTexCoordScale", (1.0, 1.0, 0.0))
            offset = material_data.get_vector_property("g_vDetailTexCoordOffset", (0.0, 0.0, 0.0))
            rotation = material_data.get_float_property("g_flDetailTexCoordRotation", 0.0)

            detail_texture = self._get_texture("g_tDetail", (1, 1, 1, 1))
            detail_mask_texture = self._get_texture("g_tDetailMask", (1, 0, 0, 1))
            use_secondary = (self._check_flag("F_SECONDARY_UV") and
                             material_data.get_int_property("g_bUseSecondaryUvForDetailTexture", 1))
            detail_uv_slot = SECONDARY_UV if use_secondary else "TEXCOORD"
            detail_transform_node = self.create_transform(detail_uv_slot, scale, offset, (0.5, 0.5, 0), rotation)
            self.connect_nodes(detail_transform_node.outputs[0], detail_texture.inputs[0])

            self.connect_nodes(detail_texture.outputs[0], shader.inputs["TextureDetail"])
            self.connect_nodes(detail_mask_texture.outputs[0], shader.inputs["TextureDetailMask"])
            shader.inputs["F_DETAIL_TEXTURE"].default_value = float(
                material_data.get_int_property("F_DETAIL_TEXTURE", 0))
            shader.inputs["g_flDetailBlendFactor"].default_value = material_data.get_float_property(
                "g_flDetailBlendFactor", 0)

        metalness_split = None
        if self._have_texture("g_tMetalness"):
            metalness_split = self._split_metalness_texture(transform_node.outputs[0])

        if self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(transform_node.outputs[0], normal_texture.inputs[0])
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal"])
            self._connect_roughness(normal_texture, metalness_split, shader.inputs["TextureRoughness"],
                                    transform_node.outputs[0])

        if self._have_texture("g_tTintMask") and self._check_flag("F_TINT_MASK", 0):
            tint_texture = self._get_texture("g_tTintMask", (1, 0, 0, 1), True)
            self.connect_nodes(transform_node.outputs[0], tint_texture.inputs[0])
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureTintMask"])

        if self._have_texture("g_tSelfIllumMask") and self._check_flag("F_SELF_ILLUM", 0):
            tint_texture = self._get_texture("g_tSelfIllumMask", (0, 0, 0, 1), True)
            self.connect_nodes(transform_node.outputs[0], tint_texture.inputs[0])
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureSelfIllumMask"])

            shader.inputs["SelfIllumTint"].default_value = material_data.get_vector_property(
                "g_vSelfIllumTint", (1, 1, 1, 1))

            shader.inputs["Emission Strength"].default_value = material_data.get_float_property(
                "g_flSelfIllumScale", 1)

        tint = material_data.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        shader.inputs["g_vColorTint"].default_value = (*tint[:3], 1.0)

        if self.tinted:
            vcolor_node = self.create_node(Nodes.ShaderNodeVertexColor)
            vcolor_node.layer_name = "TINT"
            self.connect_nodes(vcolor_node.outputs[0], shader.inputs["m_vColorTint"])
        else:
            object_info_node = self.create_node(Nodes.ShaderNodeObjectInfo)
            self.connect_nodes(object_info_node.outputs["Color"], shader.inputs["m_vColorTint"])

        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property("g_flModelTintAmount",
                                                                                              0.0)

        if metalness_split is not None:
            self.connect_nodes(metalness_split.outputs[1], shader.inputs["TextureMetalness"])
        elif self._check_flag("F_METALNESS_TEXTURE", 0) and alpha_output is not None:
            # Without a g_tMetalness texture the flag means metalness is in the color alpha.
            self.connect_nodes(alpha_output, shader.inputs["TextureMetalness"])
        else:
            shader.inputs["TextureMetalness"].default_value = material_data.get_float_property(
                "g_flMetalness", 0)

        if alpha_output is None:
            pass
        elif self._check_flag("F_ALPHA_TEST", 0):
            alpha_test_reference = material_data.get_float_property("g_flAlphaTestReference", 0.5)
            self._handle_alpha_modes("TEST", alpha_test_reference,
                                     alpha_output, shader.inputs['Alpha'])
        elif self._is_translucent():
            self._handle_alpha_modes("TRANSLUCENT", 0.5,
                                     alpha_output, shader.inputs['Alpha'])
        elif self._check_flag("F_OVERLAY", 0):
            self._handle_alpha_modes("OVERLAY", 0.5,
                                     alpha_output, shader.inputs['Alpha'])

        self._add_transmission(shader.outputs['BSDF'], albedo_output, shader.inputs['Alpha'],
                               material_output.inputs['Surface'], transform_node.outputs[0])


class CSGOCharacter(CSGOComplex):
    SHADER: str = 'csgo_character.vfx'

    def _skip_unsupported_textures(self):
        super()._skip_unsupported_textures()
        # Blood and inventory patches are applied by the game at runtime; the slots hold placeholders.
        self._skip_textures_with_prefix("g_tBloodMask", "g_tPatch")


class CSGOWeapon(CSGOComplex):
    SHADER: str = 'csgo_weapon.vfx'

    def _skip_unsupported_textures(self):
        super()._skip_unsupported_textures()
        # Stickers are applied by the game from the inventory; the slots hold placeholders.
        self._skip_textures_with_prefix("g_tSticker", "g_tGlitterNormalSticker", "g_tHoloSpectrumSticker",
                                        "g_tNormalRoughnessSticker", "g_tSfxMaskSticker")

    def _connect_roughness(self, normal_texture, metalness_split, roughness_input, uv_output):
        # Weapons keep roughness in the red channel of g_tMetalness, not in the normal map.
        if metalness_split is not None:
            self.connect_nodes(metalness_split.outputs[0], roughness_input)
        else:
            super()._connect_roughness(normal_texture, metalness_split, roughness_input, uv_output)
