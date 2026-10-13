from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, MIX_A, MIX_B, MIX_FACTOR, MIX_RESULT, Nodes
from ..source2_shader_base import (ADDITIVE, ALPHA_TEST, MOD2X, OPAQUE, TRANSLUCENT,
                                   Source2ShaderBase)
from .generic import F0_PER_SPECULAR_LEVEL


class GlobalLitSimple(Source2ShaderBase):
    SHADER: str = "global_lit_simple.vfx"

    def _split_color(self, texture):
        split = self.create_node(Nodes.ShaderNodeSeparateColor)
        split.mode = "RGB"
        self.connect_nodes(texture.outputs["Color"], split.inputs["Color"])
        return split

    def _set_color(self, color_input, color):
        if isinstance(color, bpy.types.NodeSocket):
            self.connect_nodes(color, color_input)
        else:
            color_input.default_value = color

    def _mix_tints(self, first, second, factor, name: str):
        mix = self.create_mix_color("MIX", name)
        self.connect_nodes(factor, mix.inputs[MIX_FACTOR])
        self._set_color(mix.inputs[MIX_A], first)
        self._set_color(mix.inputs[MIX_B], second)
        return mix.outputs[MIX_RESULT]

    def _tint(self, color_output, uv_output):
        material = self._material_resource
        tints = [
            (*material.get_vector_property(name, (1.0, 1.0, 1.0, 0.0))[:3], 1.0)
            for name in ("g_vColorTint", "g_vColorTint2", "g_vColorTint3", "g_vColorTint4")
        ]
        mask1_enabled = self._check_flag("F_TINT_MASK")
        mask2_enabled = self._check_flag("F_TINT_MASK_2")
        if not (mask1_enabled or mask2_enabled) or not self._have_texture("g_tTintMask"):
            self._skip_texture("g_tTintMask")
            return color_output if tints[0] == (1.0, 1.0, 1.0, 1.0) else self._multiply(color_output, tints[0])

        tint_mask = self._get_texture("g_tTintMask", (0.0, 1.0, 0.5, 1.0), True)
        self.connect_nodes(uv_output, tint_mask.inputs["Vector"])
        channels = self._split_color(tint_mask)

        lower = (self._mix_tints(tints[0], tints[1], channels.outputs["Green"], "Tint Mask 1")
                 if mask1_enabled else tints[0])
        if mask2_enabled:
            upper = (self._mix_tints(tints[2], tints[3], channels.outputs["Green"], "Tint Mask 1 Upper")
                     if mask1_enabled else tints[2])
            lower = self._mix_tints(lower, upper, channels.outputs["Blue"], "Tint Mask 2")
        return self._multiply(color_output, lower)

    def _normal(self, shader, uv_output):
        if not self._check_flag("F_NORMAL_MAP") or not self._have_texture("g_tNormal"):
            self._skip_texture("g_tNormal")
            return
        normal = self._get_texture("g_tNormal", (0.5, 0.5, 1.0, 1.0), True, True)
        self.connect_nodes(uv_output, normal.inputs["Vector"])
        normal_map = self.create_node(Nodes.ShaderNodeNormalMap)
        normal_map.inputs["Strength"].default_value = self._material_resource.get_float_property(
            "g_flBumpStrength", 1.0)
        self.connect_nodes(normal.outputs["Color"], normal_map.inputs["Color"])
        self.connect_nodes(normal_map.outputs["Normal"], shader.inputs["Normal"])

    def _specular(self, shader, color_output, uv_output):
        intensity = self._material_resource.get_float_property("g_flSpecularIntensity", 1.0)
        if not self._check_flag("F_SPECULAR"):
            self._skip_texture("g_tSpecular")
            shader.inputs["Specular IOR Level"].default_value = 0.0
            return
        if not self._have_texture("g_tSpecular"):
            self._skip_texture("g_tSpecular")
            shader.inputs["Specular IOR Level"].default_value = 0.1 * intensity / F0_PER_SPECULAR_LEVEL
            return

        specular = self._get_texture("g_tSpecular", (0.1, 0.0, 0.0, 1.0), True)
        self.connect_nodes(uv_output, specular.inputs["Vector"])
        channels = self._split_color(specular)
        reflectance = self._multiply_value(
            channels.outputs["Red"], intensity / F0_PER_SPECULAR_LEVEL)
        self.connect_nodes(reflectance, shader.inputs["Specular IOR Level"])

        self_illum = self.create_node(Nodes.ShaderNodeVectorMath)
        self_illum.operation = "SCALE"
        self.connect_nodes(color_output, self_illum.inputs[0])
        self.connect_nodes(channels.outputs["Green"], self_illum.inputs["Scale"])
        self.connect_nodes(self_illum.outputs[0], shader.inputs["Emission Color"])
        shader.inputs["Emission Strength"].default_value = 1.0

    def _render_mode(self):
        if self._check_flag("F_ALPHA_TEST") or self._check_flag("F_ALPHA_STIPPLE"):
            return ALPHA_TEST
        if self._check_flag("F_MOD_2X"):
            return MOD2X
        if self._check_flag("F_ADDITIVE_BLEND"):
            return ADDITIVE
        if self._check_flag("F_TRANSLUCENT") or self._check_flag("F_OVERLAY"):
            return TRANSLUCENT
        return OPAQUE

    def create_nodes(self, material: bpy.types.Material,
                     extra_parameters: dict[ExtraMaterialParameters, Any]):
        for slot_name in ("g_tAOMap", "g_tParallaxMask", "g_tScrollSpeed"):
            self._skip_texture(slot_name)
        self._skip_textures_with_prefix("g_tWind")

        material.use_backface_culling = not self._check_flag("F_RENDER_BACKFACES")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        shader.inputs["Roughness"].default_value = 0.5

        uv_output = self._texcoord_transform().outputs[0]
        color_texture = self._get_texture("g_tColor", (1.0, 1.0, 1.0, 1.0))
        self.connect_nodes(uv_output, color_texture.inputs["Vector"])
        color_output = self._tint(color_texture.outputs["Color"], uv_output)

        self.connect_nodes(color_output, shader.inputs["Base Color"])
        self._normal(shader, uv_output)
        self._specular(shader, color_output, uv_output)

        blend_mode = self._render_mode()
        alpha_output = color_texture.outputs["Alpha"]
        if blend_mode not in (OPAQUE, ALPHA_TEST):
            alpha_output = self._opacity_scaled(alpha_output)
        blended_color = self._linear_to_srgb(color_output) if blend_mode == MOD2X else color_output
        surface = self._blended_surface(
            blended_color,
            alpha_output,
            blend_mode,
            shader.outputs["BSDF"],
            self._material_resource.get_float_property("g_flAlphaTestReference", 0.7),
        )
        self.connect_nodes(surface, material_output.inputs["Surface"])
