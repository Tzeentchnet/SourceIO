from math import cos, radians, sin
from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT
from ..source2_shader_base import Source2ShaderBase, SECONDARY_UV
from .....library.source2.blocks.kv3_block import KVBlock


CHARACTER_NODE_GROUP = "SourceIO csgo_character.vfx"
CHARACTER_SSS_WEIGHT = "Character SSS Weight"
CHARACTER_ANISOTROPY = "Character Anisotropy"
CHARACTER_ANISOTROPIC_ROTATION = "Character Anisotropic Rotation"
CHARACTER_TANGENT = "Character Tangent"
CHARACTER_LUMA = (0.2125, 0.7154, 0.0721)


def _character_node_group() -> bpy.types.ShaderNodeTree:
    group = bpy.data.node_groups.get(CHARACTER_NODE_GROUP)
    if group is not None:
        return group

    source = bpy.data.node_groups.get("csgo_complex.vfx")
    if source is None:
        raise RuntimeError("csgo_complex.vfx must be loaded before constructing the character shader")
    group = source.copy()
    group.name = CHARACTER_NODE_GROUP

    principled_nodes = [node for node in group.nodes if node.bl_idname == Nodes.ShaderNodeBsdfPrincipled]
    group_inputs = [node for node in group.nodes if node.bl_idname == "NodeGroupInput"]
    if len(principled_nodes) != 1 or len(group_inputs) != 1:
        raise RuntimeError("csgo_complex.vfx must contain one Principled BSDF and one group input")
    principled, group_input = principled_nodes[0], group_inputs[0]

    def expose(name, socket_type, target_names, default, minimum=None, maximum=None):
        target = next((principled.inputs.get(target_name) for target_name in target_names
                       if principled.inputs.get(target_name) is not None), None)
        if target is None:
            raise RuntimeError(f"csgo_complex.vfx's Principled BSDF has no {target_names[0]} input")
        if target.is_linked:
            raise RuntimeError(f"csgo_complex.vfx's Principled BSDF {target.name} input is already linked")
        socket = group.interface.new_socket(name, in_out="INPUT", socket_type=socket_type)
        socket.default_value = default
        if minimum is not None:
            socket.min_value = minimum
        if maximum is not None:
            socket.max_value = maximum
        group.links.new(group_input.outputs[name], target)

    expose(CHARACTER_SSS_WEIGHT, "NodeSocketFloat", ("Subsurface Weight", "Subsurface"), 0.0, 0.0, 1.0)
    expose(CHARACTER_ANISOTROPY, "NodeSocketFloat", ("Anisotropic", "Anisotropic IOR Level"), 0.0, 0.0, 1.0)
    expose(CHARACTER_ANISOTROPIC_ROTATION, "NodeSocketFloat", ("Anisotropic Rotation",), 0.0, 0.0, 1.0)
    expose(CHARACTER_TANGENT, "NodeSocketVector", ("Tangent",), (0.0, 0.0, 0.0))
    return group


class CSGOComplex(Source2ShaderBase):
    SHADER: str = 'csgo_complex.vfx'

    def _math(self, operation, *inputs, name=None):
        node = self.create_node(Nodes.ShaderNodeMath, name)
        node.operation = operation
        for index, value in enumerate(inputs):
            if isinstance(value, bpy.types.NodeSocket):
                self.connect_nodes(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node

    def _vector_math(self, operation, *inputs, name=None):
        node = self.create_node(Nodes.ShaderNodeVectorMath, name)
        node.operation = operation
        for index, value in enumerate(inputs):
            if isinstance(value, bpy.types.NodeSocket):
                self.connect_nodes(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node

    def _create_shader_node(self):
        return self.create_node_group("csgo_complex.vfx", name=self.SHADER)

    def _skip_unsupported_textures(self):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")

    def _uv_anisotropic_tangent(self, mapped_normal):
        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        uv_tangent = self.create_node(Nodes.ShaderNodeTangent)
        uv_tangent.direction_type = 'UV_MAP'
        uv_tangent.uv_map = "TEXCOORD"
        bitangent = self._vector_math('CROSS_PRODUCT', geometry.outputs["Normal"], uv_tangent.outputs["Tangent"])
        tangent = self._vector_math('CROSS_PRODUCT', bitangent.outputs[0], mapped_normal,
                                    name="Character UV Tangent")
        return self._vector_math('NORMALIZE', tangent.outputs[0]).outputs[0]

    def _spherical_anisotropic_tangent(self, mapped_normal):
        angle = self._material_resource.get_float_property("g_vSphericalAnisotropyAngle", 0.0)
        if angle != 0.0:
            self.logger.warn("Nonzero g_vSphericalAnisotropyAngle is unsupported; using the UV tangent")
            return self._uv_anisotropic_tangent(mapped_normal)

        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        object_normal = self.create_node(Nodes.ShaderNodeVectorTransform)
        object_normal.vector_type = 'NORMAL'
        object_normal.convert_from = 'WORLD'
        object_normal.convert_to = 'OBJECT'
        self.connect_nodes(geometry.outputs["Normal"], object_normal.inputs["Vector"])

        pole = self._material_resource.get_vector_property("g_vSphericalAnisotropyPole", (0.0, 0.0, 1.0))
        projected = self._vector_math('CROSS_PRODUCT', pole[:3], object_normal.outputs["Vector"])

        uv_tangent = self.create_node(Nodes.ShaderNodeTangent)
        uv_tangent.direction_type = 'UV_MAP'
        uv_tangent.uv_map = "TEXCOORD"
        object_uv_tangent = self.create_node(Nodes.ShaderNodeVectorTransform)
        object_uv_tangent.vector_type = 'VECTOR'
        object_uv_tangent.convert_from = 'WORLD'
        object_uv_tangent.convert_to = 'OBJECT'
        self.connect_nodes(uv_tangent.outputs["Tangent"], object_uv_tangent.inputs["Vector"])

        projected_length = self._vector_math('LENGTH', projected.outputs[0])
        nonzero = self._math('GREATER_THAN', projected_length.outputs["Value"], 1e-8)
        choose = self.create_node(Nodes.ShaderNodeMix)
        choose.data_type = 'VECTOR'
        self.connect_nodes(nonzero.outputs[0], choose.inputs[0])
        self.connect_nodes(object_uv_tangent.outputs["Vector"], choose.inputs[4])
        self.connect_nodes(projected.outputs[0], choose.inputs[5])

        world_bitangent = self.create_node(Nodes.ShaderNodeVectorTransform)
        world_bitangent.vector_type = 'VECTOR'
        world_bitangent.convert_from = 'OBJECT'
        world_bitangent.convert_to = 'WORLD'
        self.connect_nodes(choose.outputs[1], world_bitangent.inputs["Vector"])
        tangent = self._vector_math('CROSS_PRODUCT', world_bitangent.outputs["Vector"], mapped_normal,
                                    name="Character Spherical Tangent")
        return self._vector_math('NORMALIZE', tangent.outputs[0]).outputs[0]

    def _connect_roughness(self, normal_texture, metalness_split, roughness_input, uv_output):
        if self._check_flag("F_ANISOTROPIC_GLOSS") and self._have_texture("g_tAnisoGloss"):
            # The shader uses the arithmetic mean for its scalar environment BRDF. Blender also needs the
            # axis ratio and tangent to retain the directional lobe.
            aniso_texture = self._get_texture("g_tAnisoGloss", (0.5, 0.5, 0, 1), True)
            self.connect_nodes(uv_output, aniso_texture.inputs[0])
            average = self.create_node(Nodes.ShaderNodeVectorMath)
            average.operation = 'DOT_PRODUCT'
            self.connect_nodes(aniso_texture.outputs[0], average.inputs[0])
            average.inputs[1].default_value = (0.5, 0.5, 0.0)
            self.connect_nodes(average.outputs['Value'], roughness_input)

            shader = roughness_input.node
            if CHARACTER_ANISOTROPY in shader.inputs:
                split = self.create_node(Nodes.ShaderNodeSeparateColor)
                self.connect_nodes(aniso_texture.outputs[0], split.inputs[0])
                minimum = self._math('MINIMUM', split.outputs["Red"], split.outputs["Green"])
                maximum = self._math('MAXIMUM', split.outputs["Red"], split.outputs["Green"])
                ratio = self._math('DIVIDE', minimum.outputs[0], maximum.outputs[0])
                ratio_squared = self._math('MULTIPLY', ratio.outputs[0], ratio.outputs[0])
                one_minus_ratio_squared = self._math('SUBTRACT', 1.0, ratio_squared.outputs[0])
                anisotropy = self._math('DIVIDE', one_minus_ratio_squared.outputs[0], 0.9)
                nonzero = self._math('GREATER_THAN', maximum.outputs[0], 0.0)
                anisotropy = self._math('MULTIPLY', anisotropy.outputs[0], nonzero.outputs[0])
                anisotropy = self._math('MINIMUM', anisotropy.outputs[0], 1.0, name="Character Anisotropy")
                self.connect_nodes(anisotropy.outputs[0], shader.inputs[CHARACTER_ANISOTROPY])

                green_is_rougher = self._math('GREATER_THAN', split.outputs["Green"], split.outputs["Red"])
                rotation = self._math('MULTIPLY', green_is_rougher.outputs[0], 0.25,
                                      name="Character Anisotropic Rotation")
                self.connect_nodes(rotation.outputs[0], shader.inputs[CHARACTER_ANISOTROPIC_ROTATION])

                mapped_normal = self.create_node(Nodes.ShaderNodeNormalMap)
                self.connect_nodes(normal_texture.outputs[0], mapped_normal.inputs["Color"])
                if self._check_flag("F_SPHERICAL_PROJECTED_ANISOTROPIC_TANGENTS"):
                    tangent = self._spherical_anisotropic_tangent(mapped_normal.outputs["Normal"])
                else:
                    tangent = self._uv_anisotropic_tangent(mapped_normal.outputs["Normal"])
                self.connect_nodes(tangent, shader.inputs[CHARACTER_TANGENT])
        else:
            self._skip_texture("g_tAnisoGloss")
            self.connect_nodes(normal_texture.outputs[1], roughness_input)

    def _connect_shader_features(self, shader, uv_output):
        pass

    def _set_shader_tint(self, shader):
        material_data = self._material_resource
        tint = material_data.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        shader.inputs["g_vColorTint"].default_value = (*tint[:3], 1.0)
        self.connect_nodes(self._model_tint(), shader.inputs["m_vColorTint"])
        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property(
            "g_flModelTintAmount", 1.0)

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

    def _apply_adjustments_detail(self, color_output, tint_mask_output, mask_by_tint: int, *, adjust_color=False):
        """csgo_character's and csgo_weapon's detail (their "Adjustments", VRF's ApplyCharacterAdjustments):
        F_DETAIL_TEXTURE 0 multiplies the albedo by the sRGB detail, 1 replaces the albedo with it, faded by
        g_fDetailBlendFactor; mask_by_tint 1 also fades it by the tint mask, 2 by its inverse (without a tint
        mask its strength is 1)."""
        if not self._have_texture("g_tDetail"):
            return color_output
        material_data = self._material_resource
        detail_texture = self._get_texture("g_tDetail", (1, 1, 1, 1))
        self.connect_nodes(self._detail_transform("TEXCOORD").outputs[0], detail_texture.inputs[0])
        detail_output = detail_texture.outputs[0]
        if adjust_color:
            detail_output = self._adjust_character_color(detail_output, "g_fDetail")
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
        shader = self._create_shader_node()
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
        self._connect_shader_features(shader, transform_node.outputs[0])

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

        self._set_shader_tint(shader)

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

    def _create_shader_node(self):
        return self.create_node_group(_character_node_group().name, name=self.SHADER)

    def _apply_character_tint(self, color_output, tint_mask_output):
        source = bpy.data.node_groups["csgo_complex.vfx"]
        tint_groups = [
            node.node_tree for node in source.nodes
            if node.bl_idname == Nodes.ShaderNodeGroup
            and all(name in node.inputs for name in ("Original", "TintMask", "GlobalTint", "ModelTint"))
        ]
        if len(tint_groups) != 1:
            raise RuntimeError("csgo_complex.vfx must contain one compatible tint group")

        tint = self.create_node(Nodes.ShaderNodeGroup, "Character Tint")
        tint.node_tree = tint_groups[0]
        self.connect_nodes(color_output, tint.inputs["Original"])
        if tint_mask_output is None:
            tint.inputs["TintMask"].default_value = (1.0, 1.0, 1.0, 1.0)
        else:
            self.connect_nodes(tint_mask_output, tint.inputs["TintMask"])
        global_tint = self._material_resource.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        tint.inputs["GlobalTint"].default_value = (*global_tint[:3], 1.0)
        self.connect_nodes(self._model_tint(), tint.inputs["ModelTint"])

        amount = self._material_resource.get_float_property("g_flModelTintAmount", 1.0)
        if amount == 0.0:
            return color_output
        if amount == 1.0:
            return tint.outputs["Tinted"]
        faded = self.create_mix_color('MIX')
        faded.inputs[MIX_FACTOR].default_value = amount
        self.connect_nodes(color_output, faded.inputs[MIX_A])
        self.connect_nodes(tint.outputs["Tinted"], faded.inputs[MIX_B])
        return faded.outputs[MIX_RESULT]

    def _adjust_character_color(self, color_output, prefix: str):
        material = self._material_resource
        brightness = material.get_float_property(f"{prefix}Brightness", 1.0)
        contrast = material.get_float_property(f"{prefix}Contrast", 1.0)
        hue_shift = material.get_float_property(f"{prefix}HueShift", 0.0)
        saturation = material.get_float_property(f"{prefix}Saturation", 1.0)

        color = self._vector_math(
            'MULTIPLY_ADD',
            color_output,
            (contrast * brightness,) * 3,
            (0.5 * (1.0 - contrast) * brightness,) * 3,
        ).outputs[0]
        color = self._vector_math('MINIMUM', color, (1.0, 1.0, 1.0)).outputs[0]
        color = self._vector_math('MAXIMUM', color, (0.0, 0.0, 0.0)).outputs[0]

        split = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(color, split.inputs[0])
        color_max = self._math('MAXIMUM', split.outputs["Red"], split.outputs["Green"])
        color_max = self._math('MAXIMUM', color_max.outputs[0], split.outputs["Blue"])
        color_min = self._math('MINIMUM', split.outputs["Red"], split.outputs["Green"])
        color_min = self._math('MINIMUM', color_min.outputs[0], split.outputs["Blue"])
        color_range = self._math('SUBTRACT', color_max.outputs[0], color_min.outputs[0])
        color_range = self._math('DIVIDE', color_range.outputs[0], color_max.outputs[0])
        color_range = self._math('POWER', color_range.outputs[0], 0.125)

        luma = self._vector_math('DOT_PRODUCT', color, CHARACTER_LUMA)
        grey = self._vector_math('SCALE', (1.0, 1.0, 1.0))
        self.connect_nodes(luma.outputs["Value"], grey.inputs["Scale"])

        angle = radians(hue_shift)
        axis = (0.57735, 0.57735, 0.57735)
        cosine, sine = cos(angle), sin(angle)
        rows = tuple(
            tuple(
                (cosine if row == column else 0.0)
                + sine * ((0.0, -axis[2], axis[1]),
                          (axis[2], 0.0, -axis[0]),
                          (-axis[1], axis[0], 0.0))[row][column]
                + (1.0 - cosine) * axis[row] * axis[column]
                for column in range(3)
            )
            for row in range(3)
        )
        rotated = self.create_node(Nodes.ShaderNodeCombineColor)
        for name, row in zip(("Red", "Green", "Blue"), rows):
            channel = self._vector_math('DOT_PRODUCT', color, row)
            self.connect_nodes(channel.outputs["Value"], rotated.inputs[name])

        hue_difference = self._vector_math('SUBTRACT', rotated.outputs["Color"], grey.outputs[0])
        hue_weighted = self._vector_math('SCALE', hue_difference.outputs[0])
        self.connect_nodes(color_range.outputs[0], hue_weighted.inputs["Scale"])
        hue_shifted = self._vector_math('ADD', grey.outputs[0], hue_weighted.outputs[0]).outputs[0]

        shifted_luma = self._vector_math('DOT_PRODUCT', hue_shifted, CHARACTER_LUMA)
        grey_part = self._vector_math('SCALE', ((1.0 - saturation),) * 3)
        self.connect_nodes(shifted_luma.outputs["Value"], grey_part.inputs["Scale"])
        adjusted = self._vector_math('MULTIPLY_ADD', hue_shifted, (saturation,) * 3, grey_part.outputs[0]).outputs[0]
        adjusted = self._vector_math('MINIMUM', adjusted, (1.0, 1.0, 1.0)).outputs[0]
        return self._vector_math('MAXIMUM', adjusted, (0.0, 0.0, 0.0)).outputs[0]

    def _apply_detail_texture(self, color_output, normal_input, uv_output, tint_mask_output):
        if not self._check_flag("F_ENABLE_ADJUSTMENTS"):
            # Older/synthetic resources can carry the pre-adjustments detail path without the newer feature.
            return self._apply_adjustments_detail(
                color_output, tint_mask_output,
                self._material_resource.get_int_property("g_nMaskDetailTextureByTintMask", 0))

        color_output = self._apply_character_tint(color_output, tint_mask_output)
        adjusted = self._adjust_character_color(color_output, "g_f")
        if tint_mask_output is None:
            color_output = adjusted
        else:
            tint_strength = self.create_node(Nodes.ShaderNodeSeparateColor)
            self.connect_nodes(tint_mask_output, tint_strength.inputs[0])
            fade = self.create_mix_color('MIX')
            self.connect_nodes(tint_strength.outputs["Red"], fade.inputs[MIX_FACTOR])
            self.connect_nodes(color_output, fade.inputs[MIX_A])
            self.connect_nodes(adjusted, fade.inputs[MIX_B])
            color_output = fade.outputs[MIX_RESULT]

        # g_nMaskDetailTextureByTintMask: 0 None, 1 Tint Mask, 2 Inverse Tint Mask
        return self._apply_adjustments_detail(
            color_output, tint_mask_output,
            self._material_resource.get_int_property("g_nMaskDetailTextureByTintMask", 0),
            adjust_color=True)

    def _connect_shader_features(self, shader, uv_output):
        sss_input = shader.inputs[CHARACTER_SSS_WEIGHT]
        if not self._check_flag("F_SUBSURFACE_SCATTERING"):
            self._skip_texture("g_tSssMask")
            sss_input.default_value = 0.0
        elif self._have_texture("g_tSssMask"):
            sss_texture = self._get_texture("g_tSssMask", (1.0, 1.0, 1.0, 1.0), True)
            self.connect_nodes(uv_output, sss_texture.inputs[0])
            split = self.create_node(Nodes.ShaderNodeSeparateColor)
            self.connect_nodes(sss_texture.outputs[0], split.inputs[0])
            self.connect_nodes(split.outputs["Green"], sss_input)
        else:
            sss_input.default_value = 1.0

    def _set_shader_tint(self, shader):
        if self._check_flag("F_ENABLE_ADJUSTMENTS"):
            shader.inputs["g_flModelTintAmount"].default_value = 0.0
        else:
            super()._set_shader_tint(shader)

    def _skip_unsupported_textures(self):
        super()._skip_unsupported_textures()
        # Diffuse falloff needs per-light curvature; eyes need rig projection; character iridescence has no
        # verified public formula. Do not substitute unrelated static-UV or thin-film approximations.
        for slot_name in ("g_tDiffuseFalloff", "g_tEyeAlbedo1", "g_tEyeMask1",
                          "g_tIridescentThickness_Mask"):
            self._skip_texture(slot_name)
        # Blood and inventory patches are applied by the game at runtime; the slots hold placeholders.
        self._skip_textures_with_prefix("g_tBlood", "g_tColorBlood", "g_tNormalBlood", "g_tPatch")


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
