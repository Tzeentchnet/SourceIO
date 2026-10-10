from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT
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

    def _detail_transform(self, uv_slot):
        material_data = self._material_resource
        return self.create_transform(uv_slot,
                                     material_data.get_vector_property("g_vDetailTexCoordScale", (1.0, 1.0, 0.0)),
                                     material_data.get_vector_property("g_vDetailTexCoordOffset", (0.0, 0.0, 0.0)),
                                     (0.5, 0.5, 0), material_data.get_float_property("g_flDetailTexCoordRotation", 0.0))

    def _apply_detail_texture(self, color_output, normal_input, uv_output, tint_mask_output):
        """See Source2ShaderBase._apply_detail. Under F_SECONDARY_UV the detail and its mask read the secondary
        UV set unless g_bUseSecondaryUvForDetailTexture/Mask is 0."""
        if not self._material_resource.get_int_property("F_DETAIL_TEXTURE", 0):
            return self._apply_detail(color_output, normal_input, None, None)  # skips the slots
        secondary = self._check_flag("F_SECONDARY_UV")
        use_secondary = secondary and self._material_resource.get_int_property("g_bUseSecondaryUvForDetailTexture", 1)
        detail_uv = self._detail_transform(SECONDARY_UV if use_secondary else "TEXCOORD").outputs[0]
        if secondary and self._material_resource.get_int_property("g_bUseSecondaryUvForDetailMask", 1):
            mask_uv_node = self.create_node(Nodes.ShaderNodeUVMap)
            mask_uv_node.uv_map = SECONDARY_UV
            mask_uv = mask_uv_node.outputs[0]
        else:
            mask_uv = uv_output
        return self._apply_detail(color_output, normal_input, detail_uv, mask_uv)

    def _apply_adjustments_detail(self, color_output, tint_mask_output, mask_by_tint: int):
        """csgo_character's and csgo_weapon's detail (their "Adjustments", VRF's ApplyCharacterAdjustments):
        F_DETAIL_TEXTURE 0 multiplies the albedo by the sRGB detail, 1 replaces the albedo with it, faded by
        g_fDetailBlendFactor; mask_by_tint 1 also fades it by the tint mask, 2 by its inverse (without a tint
        mask its strength is 1). Not applied: the brightness, contrast, hue and saturation adjustments of the
        detail and the albedo; the shaders blend after the tint, which differs for Replace on a tinted material."""
        if not self._have_texture("g_tDetail"):
            return color_output
        material_data = self._material_resource
        detail_texture = self._get_texture("g_tDetail", (1, 1, 1, 1))
        self.connect_nodes(self._detail_transform("TEXCOORD").outputs[0], detail_texture.inputs[0])
        detail_output = detail_texture.outputs[0]
        if not self._check_flag("F_DETAIL_TEXTURE"):
            detail_output = self._multiply(color_output, detail_output)
        fade = self.create_mix_color('MIX')
        factor = material_data.get_float_property("g_fDetailBlendFactor", 1.0)
        fade.inputs[MIX_FACTOR].default_value = factor
        if mask_by_tint in (1, 2):
            if tint_mask_output is None:
                fade.inputs[MIX_FACTOR].default_value = factor if mask_by_tint == 1 else 0.0
            else:
                tint_strength = self.create_node(Nodes.ShaderNodeSeparateColor)
                self.connect_nodes(tint_mask_output, tint_strength.inputs[0])
                strength_output = tint_strength.outputs["Red"]
                if mask_by_tint == 2:
                    inverse = self.create_node(Nodes.ShaderNodeMath)
                    inverse.operation = 'SUBTRACT'
                    inverse.inputs[0].default_value = 1.0
                    self.connect_nodes(strength_output, inverse.inputs[1])
                    strength_output = inverse.outputs[0]
                self.connect_nodes(self._multiply_value(strength_output, factor), fade.inputs[MIX_FACTOR])
        self.connect_nodes(color_output, fade.inputs[MIX_A])
        self.connect_nodes(detail_output, fade.inputs[MIX_B])
        return fade.outputs[MIX_RESULT]

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        self._skip_unsupported_textures()
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))

        transform_node = self._texcoord_transform()
        metalness_split = None
        if self._have_texture("g_tMetalness"):
            metalness_split = self._split_metalness_texture(transform_node.outputs[0])

        if self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(transform_node.outputs[0], normal_texture.inputs[0])
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal"])
            self._connect_roughness(normal_texture, metalness_split, shader.inputs["TextureRoughness"],
                                    transform_node.outputs[0])

        tint_mask_output = None
        if self._have_texture("g_tTintMask") and self._check_flag("F_TINT_MASK", 0):
            tint_texture = self._get_texture("g_tTintMask", (1, 0, 0, 1), True)
            self.connect_nodes(transform_node.outputs[0], tint_texture.inputs[0])
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureTintMask"])
            tint_mask_output = tint_texture.outputs[0]

        if self._have_texture("g_tColor"):
            color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self.connect_nodes(transform_node.outputs[0], color_texture.inputs[0])
            color_output = self._apply_detail_texture(color_texture.outputs[0], shader.inputs["TextureNormal"],
                                                      transform_node.outputs[0], tint_mask_output)
            if extra_parameters.get(ExtraMaterialParameters.USE_OBJECT_TINT, False):
                color_output = self.insert_object_tint(color_output)
            self.connect_nodes(color_output, shader.inputs["TextureColor"])
            albedo_output = color_texture.outputs[0]
            alpha_output = color_texture.outputs[1]
        else:
            albedo_output = alpha_output = None

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

        self.connect_nodes(self._model_tint(), shader.inputs["m_vColorTint"])

        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property("g_flModelTintAmount",
                                                                                              1.0)

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

    def _apply_detail_texture(self, color_output, normal_input, uv_output, tint_mask_output):
        # g_nMaskDetailTextureByTintMask: 0 None, 1 Tint Mask, 2 Inverse Tint Mask
        return self._apply_adjustments_detail(
            color_output, tint_mask_output,
            self._material_resource.get_int_property("g_nMaskDetailTextureByTintMask", 0))

    def _skip_unsupported_textures(self):
        super()._skip_unsupported_textures()
        # Blood and inventory patches are applied by the game at runtime; the slots hold placeholders.
        self._skip_textures_with_prefix("g_tBloodMask", "g_tPatch")


class CSGOWeapon(CSGOComplex):
    SHADER: str = 'csgo_weapon.vfx'

    def _apply_detail_texture(self, color_output, normal_input, uv_output, tint_mask_output):
        return self._apply_adjustments_detail(
            color_output, tint_mask_output,
            self._material_resource.get_int_property("g_bMaskDetailTextureByTintMask", 0))

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
