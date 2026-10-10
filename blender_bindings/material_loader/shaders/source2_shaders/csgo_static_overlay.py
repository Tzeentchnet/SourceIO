from typing import Any

import bpy
import numpy as np

from ...shader_base import Nodes, ExtraMaterialParameters
from ..node_math import NodeMath, Scalar, Vector
from ..source2_shader_base import Source2ShaderBase, OPAQUE, TRANSLUCENT, ALPHA_TEST, MOD2X
from .csgo_environment import color_matrix


class CSGOStaticOverlay(Source2ShaderBase):
    """Decals and overlays, blended by F_BLEND_MODE (``Source2ShaderBase._blended_surface``) and lit with F_LIT.

    As the shader does (VRF's complex.frag): Mod2x reads the color in gamma space, so a stored 0.5 leaves what is
    behind unchanged; the color goes through g_mTextureColorAdjust (contrast about the texture's average color,
    brightness, saturation); F_PAINT_VERTEX_COLORS multiplies the color and alpha by the vertex color; and
    g_flOpacityScale scales the alpha in every mode but Opaque and Alpha Test. Unlit overlays (every Mod2x, Additive
    and ModThenAdd one in CS2) draw the color as it is, and the modulating modes ignore lighting either way.
    Material-reference overlays use the csgo_environment layer-1 packing: color in g_tColor1, height and tint mask
    and metalness in g_tHeight1, and normal and roughness in g_tNormal1. Texture animation changes the shared UV
    before any texture lookup. Static overlays have no detail-texture combo, so detail ordering does not apply.

    Self-illumination isn't an overlay feature (no F_SELF_ILLUM, and g_flSelfIllumBrightness is 0 on every CS2
    overlay). In material-reference mode only, g_tSelfIllumMask alpha is still read because that packing carries
    TextureTranslucency for the height-based overlay edge.
    """
    SHADER: str = 'csgo_static_overlay.vfx'

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        # Source 2 applies ambient occlusion to indirect light only, which Blender's renderers compute themselves.
        self._skip_texture("g_tAmbientOcclusion")
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        material_data = self._material_resource
        blend_mode = material_data.get_int_property("F_BLEND_MODE", OPAQUE)
        material_reference = self._check_flag("F_MATERIAL_REFERENCE")
        m = NodeMath(material.node_tree, self.create_node)

        uv_output = self._animated_uv(self._texcoord_transform().outputs[0], m)
        reference = None
        if material_reference:
            reference = self._reference_layer(uv_output, m)
            color_texture = reference["color_texture"]
            color_output = reference["color"]
            alpha_output = None
        else:
            self._skip_texture("g_tSelfIllumMask")
            color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
            self.connect_nodes(uv_output, color_texture.inputs[0])
            color_output, alpha_output = color_texture.outputs[0], color_texture.outputs[1]
            if blend_mode == MOD2X:
                color_output = self._linear_to_srgb(color_output)
            color_output = self._main_color_adjusted(color_output, m)

        paint_alpha = None
        if self._check_flag("F_PAINT_VERTEX_COLORS"):
            paint_color, paint_alpha = self._paint_vertex_color(m)
            color_output = self._multiply(color_output, paint_color)

        if material_reference:
            alpha_output = self._reference_opacity(reference, paint_alpha, m)
            if blend_mode == ALPHA_TEST:
                alpha_output = (m.scalar(alpha_output) * color_texture.outputs["Alpha"]).socket
        elif paint_alpha is not None:
            alpha_output = (m.scalar(alpha_output) * paint_alpha).socket

        if blend_mode not in (OPAQUE, ALPHA_TEST):
            alpha_output = self._opacity_scaled(alpha_output)

        if self._check_flag("F_LIT") and blend_mode in (OPAQUE, TRANSLUCENT, ALPHA_TEST):
            surface_output = self._lit_surface(color_output, uv_output, reference)
        else:
            for slot_name in ("g_tNormal", "g_tMetalness", "g_tTintMask"):
                self._skip_texture(slot_name)
            color_output = self._tinted(color_output)
            surface_output = None
        surface = self._blended_surface(color_output, alpha_output, blend_mode, surface_output,
                                        material_data.get_float_property("g_flAlphaTestReference", 0.5))
        self.connect_nodes(surface, material_output.inputs['Surface'])

    @staticmethod
    def _affine(m: NodeMath, color, matrix: np.ndarray) -> Vector:
        """Apply a row-vector affine color matrix: ``(color, 1) * matrix``."""
        if np.allclose(matrix, np.identity(4), atol=1e-6):
            return m.vector(color)
        color = m.vector(color)
        return m.combine(*(m.dot(color, tuple(matrix[:3, column])) + float(matrix[3, column])
                           for column in range(3)))

    @staticmethod
    def _contrast_brightness(m: NodeMath, value, contrast: float, brightness: float) -> Scalar:
        if contrast == brightness == 1.0:
            return m.scalar(value)
        return m.math('MULTIPLY_ADD', value, contrast * brightness,
                      0.5 * (1.0 - contrast) * brightness, clamp=True)

    def _main_color_adjusted(self, color_output, m: NodeMath):
        material_data = self._material_resource
        contrast = material_data.get_float_property("g_fTextureColorContrast", 1.0)
        saturation = material_data.get_float_property("g_fTextureColorSaturation", 1.0)
        brightness = material_data.get_float_property("g_fTextureColorBrightness", 1.0)
        tint = material_data.get_vector_property("g_vTextureColorCorrectionTint", (1.0, 1.0, 1.0, 0.0))
        average = ((1.0, 1.0, 1.0) if (contrast, saturation, brightness) == (1.0, 1.0, 1.0)
                   else self._texture_average_color("g_tColor"))
        adjusted = self._affine(m, color_output,
                                color_matrix(contrast, saturation, brightness, average, tint))
        if adjusted.socket == color_output:
            return color_output
        return m.vmax(adjusted, (0.0, 0.0, 0.0)).socket

    def _reference_layer(self, uv_output, m: NodeMath):
        material_data = self._material_resource
        color_texture = self._get_texture("g_tColor1", (1.0, 1.0, 1.0, 1.0))
        height_texture = self._get_texture("g_tHeight1", (0.5, 1.0, 1.0, 0.0), True)
        normal_texture = self._get_texture("g_tNormal1", (0.5, 0.5, 1.0, 0.5), True, True)
        translucency_texture = self._get_texture("g_tSelfIllumMask", (0.0, 0.0, 0.0, 1.0))
        for texture in (color_texture, height_texture, normal_texture, translucency_texture):
            self.connect_nodes(uv_output, texture.inputs[0])

        height = self.create_node(Nodes.ShaderNodeSeparateColor, "Material Reference Packed Channels")
        self.connect_nodes(height_texture.outputs[0], height.inputs[0])
        tint_mask = self._contrast_brightness(
            m, height.outputs["Green"],
            material_data.get_float_property("g_fTintMaskContrast1", 1.0),
            material_data.get_float_property("g_fTintMaskBrightness1", 1.0))

        contrast = material_data.get_float_property("g_fTextureColorContrast1", 1.0)
        saturation = material_data.get_float_property("g_fTextureColorSaturation1", 1.0)
        brightness = material_data.get_float_property("g_fTextureColorBrightness1", 1.0)
        tint = material_data.get_vector_property("g_vTextureColorTint1", (1.0, 1.0, 1.0, 0.0))
        average = ((1.0, 1.0, 1.0) if (contrast, saturation, brightness) == (1.0, 1.0, 1.0)
                   else self._texture_average_color("g_tColor1"))
        adjusted = self._affine(
            m, color_texture.outputs[0],
            color_matrix(contrast, saturation, brightness, average, tint))
        base = m.vector(color_texture.outputs[0])
        if material_data.get_int_property("g_nColorCorrectionMode1", 0) == 1:
            base = self._affine(
                m, color_texture.outputs[0],
                color_matrix(contrast, saturation, brightness, average))
        color = base if adjusted.socket == base.socket else m.vsaturate(m.lerp(base, adjusted, tint_mask))

        roughness = self._contrast_brightness(
            m, normal_texture.outputs["Alpha"],
            material_data.get_float_property("g_fTextureRoughnessContrast1", 1.0),
            material_data.get_float_property("g_fTextureRoughnessBrightness1", 1.0))
        normal = self._reference_normal(normal_texture.outputs[0], m)
        return {
            "color": color.socket,
            "color_texture": color_texture,
            "height": height,
            "height_texture": height_texture,
            "normal": normal,
            "normal_texture": normal_texture,
            "roughness": roughness,
            "tint_mask": tint_mask,
            "translucency_texture": translucency_texture,
        }

    def _reference_normal(self, normal_output, m: NodeMath):
        material_data = self._material_resource
        contrast = material_data.get_float_property("g_fTextureNormalContrast1", 1.0)
        rotation = material_data.get_float_property("g_flTexCoordRotation1", 0.0)
        if contrast == 1.0 and rotation == 0.0:
            return normal_output

        normal = m.vector(normal_output) * 2.0 - (1.0, 1.0, 1.0)
        if rotation != 0.0:
            rotate = self.create_node(Nodes.ShaderNodeVectorRotate, "Material Reference Normal Rotation")
            rotate.rotation_type = 'Z_AXIS'
            self.connect_nodes(normal.socket, rotate.inputs["Vector"])
            rotate.inputs["Angle"].default_value = np.deg2rad(rotation)
            normal = m.vector(rotate.outputs["Vector"])
        normal = m.normalize(m.lerp((0.0, 0.0, 1.0), normal, contrast))
        return (normal * 0.5 + (0.5, 0.5, 0.5)).socket

    def _reference_opacity(self, reference, paint_alpha: Scalar | None, m: NodeMath):
        """The layer-height/translucency edge at base mip level.

        The native shader additionally widens this edge from texture LOD and coarse derivatives. Blender shader
        nodes expose neither value, so the distance-dependent widening cannot be represented in this material.
        """
        material_data = self._material_resource
        height = m.scalar(reference["height"].outputs["Red"])
        translucency = m.scalar(reference["translucency_texture"].outputs["Alpha"])
        if paint_alpha is not None:
            translucency *= paint_alpha

        scale = material_data.get_float_property("g_flHeightMapScale1", 1.0)
        zero_point = material_data.get_float_property("g_flHeightMapZeroPoint1", 0.5)
        softness = max(material_data.get_float_property("g_flBlendSoftness1", 0.01), 0.001)
        translucency_edge = 1.0 - 2.0 * translucency + softness
        height_edge = ((height - zero_point) * scale - zero_point) * scale - softness
        edge = m.max(translucency_edge, height_edge) - softness
        translucency_weight = m.max(translucency_edge - edge, 0.0) + 0.001
        height_weight = m.max(height_edge - edge, 0.0) + 0.001
        opacity = height_weight / (translucency_weight + height_weight)
        opacity.socket.node.name = opacity.socket.node.label = "Material Reference Opacity"
        return opacity.socket

    def _paint_vertex_color(self, m: NodeMath) -> tuple[object, Scalar]:
        """Vertex paint, treating RGBA zero as an unbound input as the shader's vertex program does."""
        vertex_color = self._vertex_color()
        color = m.vector(vertex_color.outputs["Color"])
        alpha = m.scalar(vertex_color.outputs["Alpha"])
        has_color = m.greater(m.dot(color, color) + alpha * alpha, 0.0)
        effective_color = m.lerp((1.0, 1.0, 1.0), color, has_color)
        effective_alpha = m.lerp(1.0, alpha, has_color)
        effective_color.socket.node.name = effective_color.socket.node.label = "Static Overlay Vertex Paint"
        effective_alpha.socket.node.name = effective_alpha.socket.node.label = "Static Overlay Vertex Paint Alpha"
        return effective_color.socket, effective_alpha

    def _time_seconds(self):
        time = self.get_node("Source 2 Time")
        if time is not None:
            return time.outputs[0]

        time = self.create_node(Nodes.ShaderNodeValue, "Source 2 Time")
        driver = time.outputs[0].driver_add("default_value").driver
        driver.type = 'SCRIPTED'
        driver.expression = "frame * fps_base / fps"
        scene = bpy.context.scene
        for name, data_path in (
                ("fps", "render.fps"),
                ("fps_base", "render.fps_base")):
            variable = driver.variables.new()
            variable.name = name
            variable.type = 'SINGLE_PROP'
            variable.targets[0].id_type = 'SCENE'
            variable.targets[0].id = scene
            variable.targets[0].data_path = data_path
        return time.outputs[0]

    def _animated_uv(self, uv_output, m: NodeMath):
        if not self._check_flag("F_TEXTURE_ANIMATION"):
            return uv_output

        material_data = self._material_resource
        grid = material_data.get_vector_property("g_vAnimationGrid", (1.0, 1.0, 0.0, 0.0))
        grid_x, grid_y = int(grid[0]), int(grid[1])
        cell_count = material_data.get_int_property("g_nNumAnimationCells", 1)
        if grid_x < 1 or grid_y < 1 or cell_count < 1:
            self.logger.warning(
                f"Invalid texture-animation grid {grid_x}x{grid_y} with {cell_count} cells; using minimums")
            grid_x, grid_y, cell_count = max(grid_x, 1), max(grid_y, 1), max(cell_count, 1)

        mode = material_data.get_int_property("F_TEXTURE_ANIMATION_MODE", 0)
        if mode == 0:
            seconds_per_frame = material_data.get_float_property("g_flAnimationTimePerFrame", 0.1)
            if seconds_per_frame <= 0.0:
                self.logger.warning(
                    f"Invalid g_flAnimationTimePerFrame {seconds_per_frame}; texture animation is disabled")
                return uv_output
            frame = m.math(
                'FLOOR',
                (m.scalar(self._time_seconds())
                 + material_data.get_float_property("g_flAnimationTimeOffset", 0.0)) / seconds_per_frame)
            frame = m.math('MODULO', frame, cell_count)
        elif mode == 1:
            seed = m.math('FLOOR', material_data.get_float_property("g_flAnimationFrame", 0.0))
            random_value = m.sin(seed * (12.9898 + 78.233)) * 43758.546875
            frame = m.math('FLOOR', m.math('FRACT', random_value) * cell_count)
        elif mode == 2:
            frame = m.math('FLOOR', material_data.get_float_property("g_flAnimationFrame", 0.0))
        else:
            self.logger.warning(f"Unknown F_TEXTURE_ANIMATION_MODE {mode}; texture animation is disabled")
            return uv_output

        column = m.math('MODULO', frame, grid_x)
        row = m.math('FLOOR', frame / grid_x)
        # Source and Blender both flip the imported image and V coordinate. Reversing the atlas row here preserves
        # Source's top-to-bottom cell numbering in Blender's bottom-to-top UV space.
        offset = m.combine(column, grid_y - 1.0 - row, 0.0)
        animated = (m.vector(uv_output) + offset) * (1.0 / grid_x, 1.0 / grid_y, 1.0)
        animated.socket.node.name = animated.socket.node.label = "Texture Animation UV"
        return animated.socket

    def _lit_surface(self, color_output, uv_output, reference=None):
        material_data = self._material_resource
        shader = self.create_node_group("csgo_complex.vfx", name=self.SHADER)
        self.connect_nodes(color_output, shader.inputs["TextureColor"])

        if reference is not None:
            self.connect_nodes(reference["normal"], shader.inputs["TextureNormal"])
            if reference["roughness"].is_const:
                shader.inputs["TextureRoughness"].default_value = reference["roughness"].const
            else:
                self.connect_nodes(reference["roughness"].socket, shader.inputs["TextureRoughness"])
            if material_data.get_int_property("g_bMetalness1", 1):
                self.connect_nodes(reference["height_texture"].outputs["Alpha"], shader.inputs["TextureMetalness"])
            else:
                shader.inputs["TextureMetalness"].default_value = 0.0
            if reference["tint_mask"].is_const:
                shader.inputs["TextureTintMask"].default_value = reference["tint_mask"].const
            else:
                self.connect_nodes(reference["tint_mask"].socket, shader.inputs["TextureTintMask"])
            for slot_name in ("g_tNormal", "g_tMetalness", "g_tTintMask"):
                self._skip_texture(slot_name)
        elif self._have_texture("g_tNormal"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1, 1), True, True)
            self.connect_nodes(uv_output, normal_texture.inputs[0])
            self.connect_nodes(normal_texture.outputs[0], shader.inputs["TextureNormal"])
            self.connect_nodes(normal_texture.outputs[1], shader.inputs["TextureRoughness"])

        if reference is None:
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
