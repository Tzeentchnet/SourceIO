from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT, Nodes
from ..source2_shader_base import ADDITIVE, ALPHA_TEST, OPAQUE, TRANSLUCENT, Source2ShaderBase


class Hero(Source2ShaderBase):
    SHADER: str = "hero.vfx"

    def _split_color(self, texture):
        split = self.create_node(Nodes.ShaderNodeSeparateColor)
        split.mode = "RGB"
        self.connect_nodes(texture.outputs["Color"], split.inputs["Color"])
        return split

    def _blend_to_full(self, value_output, parameter: str):
        minimum = self._material_resource.get_float_property(parameter, 0.0)
        if minimum == 0.0:
            return value_output
        maximum = self.create_node(Nodes.ShaderNodeMath)
        maximum.operation = "MAXIMUM"
        self.connect_nodes(value_output, maximum.inputs[0])
        maximum.inputs[1].default_value = minimum
        return maximum.outputs[0]

    def _scale_value(self, value_output, scale: float):
        return value_output if scale == 1.0 else self._multiply_value(value_output, scale)

    def _scale_unit(self, value_output, scale: float):
        """value * scale, clamped to 0..1. Dota's scales are Blinn-Phong intensities (g_flSpecularScale is often
        tens), while Blender's Specular IOR Level and Sheen Weight treat 0.5 and 1 as the normal maximum."""
        scaled = self.create_node(Nodes.ShaderNodeMath)
        scaled.operation = "MULTIPLY"
        scaled.use_clamp = True
        self.connect_nodes(value_output, scaled.inputs[0])
        scaled.inputs[1].default_value = scale
        return scaled.outputs[0]

    def _scale_color(self, color_output, scale):
        scaled = self.create_node(Nodes.ShaderNodeVectorMath)
        scaled.operation = "SCALE"
        self.connect_nodes(color_output, scaled.inputs[0])
        if isinstance(scale, bpy.types.NodeSocket):
            self.connect_nodes(scale, scaled.inputs["Scale"])
        else:
            scaled.inputs["Scale"].default_value = scale
        return scaled.outputs[0]

    def _add_colors(self, first, second):
        if first is None:
            return second
        added = self.create_node(Nodes.ShaderNodeVectorMath)
        added.operation = "ADD"
        self.connect_nodes(first, added.inputs[0])
        self.connect_nodes(second, added.inputs[1])
        return added.outputs[0]

    def _set_factor(self, factor_input, factor):
        if isinstance(factor, bpy.types.NodeSocket):
            self.connect_nodes(factor, factor_input)
        else:
            factor_input.default_value = factor

    def _detail_factor(self, mask_output):
        blend = self._material_resource.get_float_property("g_flDetailBlendFactor", 1.0)
        if mask_output is None:
            return blend
        mask_output = self._blend_to_full(mask_output, "g_flDetailBlendToFull")
        return self._scale_value(mask_output, blend)

    def _tinted_detail(self, texture_output, parameter: str):
        tint = self._material_resource.get_vector_property(parameter, (1.0, 1.0, 1.0, 0.0))
        if any(channel != 1.0 for channel in tint[:3]):
            return self._multiply(texture_output, (*tint[:3], 1.0))
        return texture_output

    def _apply_detail(self, color_output, detail_mask):
        mode = self._material_resource.get_int_property("F_DETAIL", 0)
        if mode not in (1, 2, 3) or not self._have_texture("g_tDetail"):
            self._skip_texture("g_tDetail")
            return color_output, None

        detail = self._get_texture("g_tDetail", (1.0, 1.0, 1.0, 1.0))
        self.connect_nodes(self._texcoord_transform("Detail").outputs[0], detail.inputs["Vector"])
        detail_output = self._tinted_detail(detail.outputs["Color"], "g_vDetail1ColorTint")
        factor = self._detail_factor(detail_mask)

        blend = self.create_mix_color("MULTIPLY" if mode == 3 else "ADD")
        self._set_factor(blend.inputs[MIX_FACTOR], factor)
        self.connect_nodes(color_output, blend.inputs[MIX_A])
        if mode == 3:
            detail_output = self._multiply(detail_output, (2.0, 2.0, 2.0, 1.0))
        self.connect_nodes(detail_output, blend.inputs[MIX_B])

        emission = self._scale_color(detail_output, factor) if mode == 2 else None
        return blend.outputs[MIX_RESULT], emission

    def _apply_detail2(self, color_output):
        mode = self._material_resource.get_int_property("F_DETAIL_2", 0)
        if mode not in (1, 2) or not self._have_texture("g_tDetail2"):
            self._skip_texture("g_tDetail2")
            return color_output, None

        detail = self._get_texture("g_tDetail2", (1.0, 1.0, 1.0, 1.0))
        self.connect_nodes(self._texcoord_transform("Detail2").outputs[0], detail.inputs["Vector"])
        detail_output = self._tinted_detail(detail.outputs["Color"], "g_vDetail2ColorTint")
        factor = self._material_resource.get_float_property("g_flDetail2BlendFactor", 0.0)

        blend = self.create_mix_color("ADD")
        blend.inputs[MIX_FACTOR].default_value = factor
        self.connect_nodes(color_output, blend.inputs[MIX_A])
        self.connect_nodes(detail_output, blend.inputs[MIX_B])

        emission = self._scale_color(detail_output, factor) if mode == 2 else None
        return blend.outputs[MIX_RESULT], emission

    def _alpha_output(self, color_texture, primary_uv):
        if not self._check_flag("F_SEPARATE_ALPHA_TRANSFORM"):
            return color_texture.outputs["Alpha"]
        alpha_texture = self.create_node(Nodes.ShaderNodeTexImage, "g_tColor Alpha")
        alpha_texture.image = color_texture.image
        self.connect_nodes(self._texcoord_transform("Alpha").outputs[0], alpha_texture.inputs["Vector"])
        return alpha_texture.outputs["Alpha"]

    def _normal(self, shader, primary_uv):
        if not self._have_texture("g_tNormal"):
            self._skip_texture("g_tNormal")
            return
        normal = self._get_texture("g_tNormal", (0.5, 0.5, 1.0, 1.0), True, True)
        normal_uv = (self._texcoord_transform("Normal").outputs[0]
                     if self._check_flag("F_SEPARATE_NORMAL_TRANSFORM") else primary_uv)
        self.connect_nodes(normal_uv, normal.inputs["Vector"])
        normal_map = self.create_node(Nodes.ShaderNodeNormalMap)
        self.connect_nodes(normal.outputs["Color"], normal_map.inputs["Color"])
        self.connect_nodes(normal_map.outputs["Normal"], shader.inputs["Normal"])

    def _specular_exponent(self, exponent_mask):
        exponent = self._scale_value(
            self._blend_to_full(exponent_mask, "g_flSpecularExponentBlendToFull"),
            self._material_resource.get_float_property("g_flSpecularExponent", 32.0),
        )
        exponent_plus_two = self.create_node(Nodes.ShaderNodeMath)
        exponent_plus_two.operation = "ADD"
        self.connect_nodes(exponent, exponent_plus_two.inputs[0])
        exponent_plus_two.inputs[1].default_value = 2.0

        # Blinn-Phong exponent n to Beckmann alpha = sqrt(2 / (n + 2)); Blender squares its roughness into
        # alpha, so roughness = (2 / (n + 2)) ** 0.25.
        alpha_squared = self.create_node(Nodes.ShaderNodeMath)
        alpha_squared.operation = "DIVIDE"
        alpha_squared.inputs[0].default_value = 2.0
        self.connect_nodes(exponent_plus_two.outputs[0], alpha_squared.inputs[1])

        roughness = self.create_node(Nodes.ShaderNodeMath)
        roughness.operation = "POWER"
        self.connect_nodes(alpha_squared.outputs[0], roughness.inputs[0])
        roughness.inputs[1].default_value = 0.25
        return roughness.outputs[0]

    def _render_mode(self):
        if self._check_flag("F_ALPHA_TEST") or self._check_flag("F_ALPHA_STIPPLE"):
            return ALPHA_TEST
        if self._check_flag("F_ADDITIVE_BLEND"):
            return ADDITIVE
        if self._check_flag("F_TRANSLUCENT"):
            return TRANSLUCENT
        return OPAQUE

    def create_nodes(self, material: bpy.types.Material,
                     extra_parameters: dict[ExtraMaterialParameters, Any]):
        for slot_name in (
                "g_tColorWarp3D",
                "g_tCubeMap",
                "g_tCubeMapSeparateMask",
                "g_tDiffuseWarp",
                "g_tFresnelColorWarp3D",
                "g_tFresnelWarp",
                "g_tSpecularWarp",
        ):
            self._skip_texture(slot_name)
        self._skip_textures_with_prefix("g_tWind")

        material.use_backface_culling = not self._check_flag("F_RENDER_BACKFACES")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        shader.inputs["Specular IOR Level"].default_value = 0.0

        primary_uv = self._texcoord_transform().outputs[0]
        color_texture = self._get_texture("g_tColor", (1.0, 1.0, 1.0, 1.0))
        self.connect_nodes(primary_uv, color_texture.inputs["Vector"])
        alpha_output = self._alpha_output(color_texture, primary_uv)

        masks1 = masks2 = None
        masks1_alpha = masks2_alpha = None
        if self._check_flag("F_MASKS_1") and self._have_texture("g_tMasks1"):
            masks1_texture = self._get_texture("g_tMasks1", (1.0, 1.0, 1.0, 1.0), True)
            self.connect_nodes(primary_uv, masks1_texture.inputs["Vector"])
            masks1 = self._split_color(masks1_texture)
            masks1_alpha = masks1_texture.outputs["Alpha"]
        else:
            self._skip_texture("g_tMasks1")

        if self._check_flag("F_MASKS_2") and self._have_texture("g_tMasks2"):
            masks2_texture = self._get_texture("g_tMasks2", (1.0, 1.0, 1.0, 1.0), True)
            self.connect_nodes(primary_uv, masks2_texture.inputs["Vector"])
            masks2 = self._split_color(masks2_texture)
            masks2_alpha = masks2_texture.outputs["Alpha"]
        else:
            self._skip_texture("g_tMasks2")

        color_output = color_texture.outputs["Color"]
        tint = self._material_resource.get_vector_property("g_vColorTint", (1.0, 1.0, 1.0, 0.0))
        if any(channel != 1.0 for channel in tint[:3]):
            color_output = self._multiply(color_output, (*tint[:3], 1.0))

        detail_mask = masks1.outputs["Red"] if masks1 is not None else None
        color_output, emission_output = self._apply_detail(color_output, detail_mask)
        color_output, detail2_emission = self._apply_detail2(color_output)
        if detail2_emission is not None:
            emission_output = self._add_colors(emission_output, detail2_emission)
        self.connect_nodes(color_output, shader.inputs["Base Color"])

        self._normal(shader, primary_uv)

        if masks1 is not None:
            if self._check_flag("F_DIFFUSE_WARP"):
                # Blender has no diffuse-warp lookup; diffuse roughness is the closest lobe-level equivalent.
                diffuse_roughness = self._blend_to_full(
                    masks1.outputs["Green"], "g_flDiffuseWarpBlendToFull")
                self.connect_nodes(diffuse_roughness, shader.inputs["Diffuse Roughness"])

            metalness = self._blend_to_full(masks1.outputs["Blue"], "g_flMetalnessBlendToFull")
            self.connect_nodes(metalness, shader.inputs["Metallic"])

            self_illum = self._blend_to_full(masks1_alpha, "g_flSelfIllumBlendToFull")
            emission_output = self._add_colors(emission_output, self._scale_color(color_output, self_illum))

        if masks2 is not None:
            specular = self._scale_unit(
                self._blend_to_full(masks2.outputs["Red"], "g_flSpecularBlendToFull"),
                self._material_resource.get_float_property("g_flSpecularScale", 1.0),
            )
            self.connect_nodes(specular, shader.inputs["Specular IOR Level"])

            rim = self._scale_unit(
                masks2.outputs["Green"],
                self._material_resource.get_float_property("g_flRimLightScale", 1.0),
            )
            # Sheen is Blender's grazing-angle lobe, the closest match to a rim light; its tint colors only
            # the sheen (Coat Tint would tint the base layer under the coat instead).
            self.connect_nodes(rim, shader.inputs["Sheen Weight"])
            rim_color = self._material_resource.get_vector_property(
                "g_vRimLightColor", (1.0, 1.0, 1.0, 0.0))
            shader.inputs["Sheen Tint"].default_value = (*rim_color[:3], 1.0)

            tint_by_base = masks2.outputs["Blue"]
            tint_to_none = self._material_resource.get_float_property(
                "g_flReflectionsTintByBaseBlendToNone", 0.0)
            if tint_to_none != 0.0:
                tint_by_base = self._scale_value(tint_by_base, max(0.0, 1.0 - tint_to_none))
            specular_color = self._material_resource.get_vector_property(
                "g_vSpecularColor", (1.0, 1.0, 1.0, 0.0))
            tinted_specular = self._multiply(color_output, (*specular_color[:3], 1.0))
            specular_tint = self.create_mix_color("MIX")
            self.connect_nodes(tint_by_base, specular_tint.inputs[MIX_FACTOR])
            specular_tint.inputs[MIX_A].default_value = (*specular_color[:3], 1.0)
            self.connect_nodes(tinted_specular, specular_tint.inputs[MIX_B])
            self.connect_nodes(specular_tint.outputs[MIX_RESULT], shader.inputs["Specular Tint"])

            self.connect_nodes(self._specular_exponent(masks2_alpha), shader.inputs["Roughness"])

        if emission_output is not None:
            self.connect_nodes(emission_output, shader.inputs["Emission Color"])
            shader.inputs["Emission Strength"].default_value = 1.0

        surface = self._blended_surface(
            color_output,
            alpha_output,
            self._render_mode(),
            shader.outputs["BSDF"],
            self._material_resource.get_float_property("g_flAlphaTestReference", 0.5),
        )
        self.connect_nodes(surface, material_output.inputs["Surface"])
