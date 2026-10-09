from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..source2_shader_base import Source2ShaderBase, OPAQUE, TRANSLUCENT, ALPHA_TEST, MOD2X


class CSGOStaticOverlay(Source2ShaderBase):
    """Decals and overlays, blended by F_BLEND_MODE (``Source2ShaderBase._blended_surface``) and lit with F_LIT.

    As the shader does (VRF's complex.frag): Mod2x reads the color in gamma space, so a stored 0.5 leaves what is
    behind unchanged; the color goes through g_mTextureColorAdjust (contrast about the texture's average color,
    brightness, saturation); F_PAINT_VERTEX_COLORS multiplies the color and alpha by the vertex color; and
    g_flOpacityScale scales the alpha in every mode but Opaque and Alpha Test. Unlit overlays (every Mod2x, Additive
    and ModThenAdd one in CS2) draw the color as it is, and the modulating modes ignore lighting either way.

    Not applied: g_vTextureColorCorrectionTint (white on every CS2 overlay), the F_MATERIAL_REFERENCE layer
    (g_tColor1, 4 materials) and texture animation. Self-illumination isn't an overlay feature (no F_SELF_ILLUM,
    and g_flSelfIllumBrightness is 0 on every CS2 overlay), so its mask is skipped.
    """
    SHADER: str = 'csgo_static_overlay.vfx'

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")
        self._skip_texture("g_tSelfIllumMask")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        material_data = self._material_resource
        blend_mode = material_data.get_int_property("F_BLEND_MODE", OPAQUE)

        uv_output = self._texcoord_transform().outputs[0]
        color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
        self.connect_nodes(uv_output, color_texture.inputs[0])
        color_output, alpha_output = color_texture.outputs[0], color_texture.outputs[1]
        if blend_mode == MOD2X:
            color_output = self._linear_to_srgb(color_output)
        color_output = self._color_adjusted(color_output,
                                            material_data.get_float_property("g_fTextureColorContrast", 1.0),
                                            material_data.get_float_property("g_fTextureColorSaturation", 1.0),
                                            material_data.get_float_property("g_fTextureColorBrightness", 1.0),
                                            self._texture_average_color("g_tColor"))
        if self._check_flag("F_PAINT_VERTEX_COLORS"):
            vertex_color = self._vertex_color()
            color_output = self._multiply(color_output, vertex_color.outputs["Color"])
            alpha_multiply = self.create_node(Nodes.ShaderNodeMath)
            alpha_multiply.operation = 'MULTIPLY'
            self.connect_nodes(alpha_output, alpha_multiply.inputs[0])
            self.connect_nodes(vertex_color.outputs["Alpha"], alpha_multiply.inputs[1])
            alpha_output = alpha_multiply.outputs[0]
        if blend_mode not in (OPAQUE, ALPHA_TEST):
            alpha_output = self._opacity_scaled(alpha_output)

        if self._check_flag("F_LIT") and blend_mode in (OPAQUE, TRANSLUCENT, ALPHA_TEST):
            surface_output = self._lit_surface(color_output, uv_output)
        else:
            for slot_name in ("g_tNormal", "g_tMetalness", "g_tTintMask"):
                self._skip_texture(slot_name)
            color_output = self._tinted(color_output)
            surface_output = None
        surface = self._blended_surface(color_output, alpha_output, blend_mode, surface_output,
                                        material_data.get_float_property("g_flAlphaTestReference", 0.5))
        self.connect_nodes(surface, material_output.inputs['Surface'])

    def _lit_surface(self, color_output, uv_output):
        material_data = self._material_resource
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(color_output, shader.inputs["TextureColor"])

        if self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(uv_output, normal_texture.inputs[0])
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal"])
            self.connect_nodes(normal_texture.outputs[1], shader.inputs["TextureRoughness"])

        if self._have_texture("g_tMetalness"):
            metalness = self._split_metalness_texture(uv_output)
            self.connect_nodes(metalness.outputs["Green"], shader.inputs["TextureMetalness"])
        else:
            shader.inputs["TextureMetalness"].default_value = 0.0

        if self._have_texture("g_tTintMask") and self._check_flag("F_TINT_MASK"):
            tint_texture = self._get_texture("g_tTintMask", (1, 0, 0, 1), True, True)
            self.connect_nodes(uv_output, tint_texture.inputs[0])
            self.connect_nodes(tint_texture.outputs[0], shader.inputs["TextureTintMask"])
        else:
            self._skip_texture("g_tTintMask")

        tint = material_data.get_vector_property("g_vColorTint", None)
        if tint is not None and any(channel != 1.0 for channel in tint[:3]):
            shader.inputs["g_vColorTint"].default_value = tint
        self.connect_nodes(self._model_tint(), shader.inputs["m_vColorTint"])
        shader.inputs["g_flModelTintAmount"].default_value = material_data.get_float_property(
            "g_flModelTintAmount", 1.0)
        return shader.outputs['BSDF']
