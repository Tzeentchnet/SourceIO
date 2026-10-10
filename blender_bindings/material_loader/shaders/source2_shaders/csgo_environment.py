import math
from dataclasses import dataclass
from typing import Any, Optional

import bpy
import numpy as np

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath, Scalar, Vector, group_call, node_group
from ..source2_shader_base import Source2ShaderBase

UP = (0.0, 0.0, 1.0)
# GetLuma's weights, which ColorizeTint uses
LUMA = (0.2125, 0.7154, 0.0721)
_LUMINANCE = np.array((0.2126, 0.7152, 0.0722)) / np.linalg.norm((0.2126, 0.7152, 0.0722))


def _axis_angle(axis, angle) -> np.ndarray:
    """System.Numerics' Matrix4x4.CreateFromAxisAngle (row vectors: v' = v·M)."""
    x, y, z = axis
    s, c = math.sin(angle), math.cos(angle)
    return np.array(((x * x + c * (1 - x * x), x * y - c * x * y + s * z, x * z - c * x * z - s * y, 0),
                     (x * y - c * x * y - s * z, y * y + c * (1 - y * y), y * z - c * y * z + s * x, 0),
                     (x * z - c * x * z + s * y, y * z - c * y * z - s * x, z * z + c * (1 - z * z), 0),
                     (0, 0, 0, 1)))


def _scale(s) -> np.ndarray:
    return np.diag((*np.broadcast_to(np.asarray(s, np.float64), 3), 1.0))


def _translation(t) -> np.ndarray:
    matrix = np.identity(4)
    matrix[3, :3] = t
    return matrix


def color_matrix(contrast: float, saturation: float, brightness: float, average, tint=(1.0, 1.0, 1.0)) -> np.ndarray:
    """The CS2 shaders' g_mTextureColorAdjust (g_mTextureAdjust without the tint), as VRF evaluates
    MatrixMultiply(MatrixColorTint2(tint, 1), MatrixColorCorrect2((contrast, saturation, brightness), average)):
    contrast about the texture's average color, brightness and saturation, then a luminance-preserving tint.
    A 4×4 matrix for row vectors, (r, g, b, 1)·M."""
    cross = np.cross(_LUMINANCE, (0.0, 0.0, 1.0))
    rotation = _axis_angle(cross / np.linalg.norm(cross), math.atan2(np.linalg.norm(cross), _LUMINANCE[2]))
    average = np.asarray(average[:3], np.float64)
    correct = (_translation(-average) @ _scale(contrast) @ _translation(average) @ _scale(brightness)
               @ _scale(_LUMINANCE) @ rotation @ _scale((saturation, saturation, 1.0)) @ rotation.T
               @ _scale(1.0 / _LUMINANCE))
    tint = np.asarray(tint[:3], np.float64)
    tint_saturation = 0.0 if tint.max() == 0 else (tint.max() - tint.min()) / tint.max()
    gray = (np.append(tint, 1.0) @ _scale(_LUMINANCE) @ rotation)[:3]
    keep = 1.0 - tint_saturation ** 2
    tint_matrix = (_scale(_LUMINANCE) @ rotation @ _translation((-gray[0], -gray[1], 0.0))
                   @ _scale((1.0 - tint_saturation, 1.0 - tint_saturation, keep))
                   @ _translation((gray[0], gray[1], (1.0 - keep) * gray[2])) @ rotation.T
                   @ _scale(1.0 / _LUMINANCE))
    return correct @ tint_matrix


def srgb_to_linear(color) -> tuple[float, ...]:
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color)


def _colorize_tint_group():
    """VRF's ColorizeTint: keep the color's luminance, take the tint's hue (clamped so a dark tint can't brighten
    it), blended in by Amount; the result is clamped to [0, 1] at any amount."""

    def build(m: NodeMath, i):
        tint_direction = m.normalize(m.vmax(i['Tint'], (0.001, 0.001, 0.001)))
        luma = m.dot(i['Color'], LUMA)
        tinted = m.min(luma / m.dot(tint_direction, LUMA), 3.0 * luma * m.max3(i['Tint']))
        return {'Color': m.vsaturate(m.lerp(i['Color'], tint_direction * tinted, i['Amount']))}

    return node_group('SourceIO Colorize Tint',
                      {'Color': ('color', (1, 1, 1)), 'Tint': ('color', (1, 1, 1)), 'Amount': ('float', 0.0)},
                      {'Color': 'color'}, build)


def _layer_normal_group():
    """A CS2 environment layer's tangent-space normal from its decoded normal map: turned by the layer's UV
    rotation (the image turns with its coordinates' inverse, so in Blender's V-up frame by +rotation), then
    pushed away from flat by Contrast: normalize(lerp(up, n, contrast))."""

    def build(m: NodeMath, i):
        normal = i['Normal'] * 2.0 - 1.0
        angle = i['Rotation'] * (math.pi / 180.0)
        cos, sin = m.cos(angle), m.sin(angle)
        turned = m.combine(cos * normal.x - sin * normal.y, sin * normal.x + cos * normal.y, normal.z)
        return {'Normal': m.normalize(m.lerp(UP, turned, i['Contrast']))}

    return node_group('SourceIO Environment Layer Normal',
                      {'Normal': ('color', (0.5, 0.5, 1.0)), 'Rotation': ('float', 0.0), 'Contrast': ('float', 1.0)},
                      {'Normal': 'vector'}, build)


@dataclass
class Layer:
    color: Vector  # linear, color corrected, model tinted and clamped
    alpha: Optional[Scalar]  # the color texture's alpha (layer 1 with F_ALPHA_TEST)
    height: Scalar  # g_tHeight red
    tint_mask_raw: Scalar  # g_tHeight green
    tint_mask: Scalar  # through g_fTintMaskContrast and g_fTintMaskBrightness
    metalness: Scalar
    roughness: Scalar
    normal: Vector  # unit, tangent space


class CSGOEnvironment(Source2ShaderBase):
    """csgo_environment and, in CSGOEnvironmentBlend, csgo_environment_blend, after VRF's csgo_environment.frag.
    Each layer n packs color (and AO) in g_tColor<n>, height, tint mask and metalness in g_tHeight<n> (red, green,
    alpha) and the normal and roughness in g_tNormal<n> (SourceIO decodes it to a tangent-space normal with
    roughness in alpha). Ambient occlusion is left to the renderer, as for the other CS2 shaders. Not handled:
    biplanar mapping (g_nUVSet 0, read as UV set 1), texture scale by model scale, wetness."""
    SHADER: str = 'csgo_environment.vfx'

    def _float(self, name: str, default: float) -> float:
        return float(self._material_resource.get_float_property(name, default))

    def _int(self, name: str, default: int) -> int:
        return int(self._material_resource.get_int_property(name, default))

    def _vector(self, name: str, default: tuple) -> tuple:
        return tuple(self._material_resource.get_vector_property(name, default))

    def _uv_set(self, uv_set: int):
        """UV set 2 is the secondary set (or the primary one where a mesh has none); 1, and 0 (biplanar, not
        handled), the primary one."""
        if uv_set == 2:
            return self._secondary_uv_or_primary()
        uv_node = self.create_node(Nodes.ShaderNodeUVMap)
        uv_node.uv_map = "TEXCOORD"
        return uv_node.outputs[0]

    def _transformed_uv(self, prefix: str, suffix: str, uv_set: int):
        """The UV set through g_v<prefix>TexCoordScale/Offset/Center<suffix> and g_fl<prefix>TexCoordRotation<suffix>,
        scaled about the origin (the g_v*TexCoordXform expressions of both environment shaders)."""
        return self.create_transform(self._uv_set(uv_set),
                                     self._vector(f"g_v{prefix}TexCoordScale{suffix}", (1.0, 1.0, 0.0)),
                                     self._vector(f"g_v{prefix}TexCoordOffset{suffix}", (0.0, 0.0, 0.0)),
                                     self._vector(f"g_v{prefix}TexCoordCenter{suffix}", (0.5, 0.5, 0.0)),
                                     self._float(f"g_fl{prefix}TexCoordRotation{suffix}", 0.0)).outputs[0]

    @staticmethod
    def _contrast_brightness(m: NodeMath, value, contrast: float, brightness: float) -> Scalar:
        """saturate(((value - 0.5)·contrast + 0.5)·brightness)"""
        return m.math('MULTIPLY_ADD', value, contrast * brightness, 0.5 * (1.0 - contrast) * brightness, clamp=True)

    @staticmethod
    def _affine(m: NodeMath, color, matrix: np.ndarray) -> Vector:
        """(color, 1)·matrix"""
        if np.allclose(matrix, np.identity(4), atol=1e-6):
            return m.vector(color)
        return m.combine(*(m.dot(color, tuple(matrix[:3, column])) + float(matrix[3, column])
                           for column in range(3)))

    def _layer_color(self, m: NodeMath, n: int, color, tint_mask: Scalar) -> Vector:
        """The tint mask blends in the color adjusted by g_mTextureColorAdjust<n> (with g_vTextureColorTint<n>),
        over the color itself or, with g_nColorCorrectionMode<n> 1, the color adjusted without the tint. Then the
        model tint (sRGB-decoded), colorized into the tint mask by g_flModelTintAmount (scaled by how far the tint is
        from white) unless g_bModelTint<n> is 0."""
        contrast = self._float(f"g_fTextureColorContrast{n}", 1.0)
        saturation = self._float(f"g_fTextureColorSaturation{n}", 1.0)
        brightness = self._float(f"g_fTextureColorBrightness{n}", 1.0)
        tint = self._vector(f"g_vTextureColorTint{n}", (1.0, 1.0, 1.0, 0.0))
        average = (1.0, 1.0, 1.0)
        if (contrast, saturation, brightness) != (1.0, 1.0, 1.0):
            average = self._texture_average_color(f"g_tColor{n}")
        adjusted = self._affine(m, color, color_matrix(contrast, saturation, brightness, average, tint))
        base = m.vector(color)
        if self._int(f"g_nColorCorrectionMode{n}", 0) == 1:
            base = self._affine(m, color, color_matrix(contrast, saturation, brightness, average))
        color = m.lerp(base, adjusted, tint_mask)

        amount: Scalar | float = 0.0
        model_tint = (1.0, 1.0, 1.0)
        tint_amount = self._float("g_flModelTintAmount", 1.0)
        if self._int(f"g_bModelTint{n}", 1) and tint_amount:
            model_tint = m.vector(self._model_tint())
            amount = tint_amount * (1.0 - m.min3(model_tint)) * tint_mask
        return group_call(m, _colorize_tint_group(), {'Color': color, 'Tint': model_tint, 'Amount': amount})['Color']

    def _layer_normal(self, m: NodeMath, n: int, normal_texture, uv_set: int) -> Vector:
        """g_tNormal<n> through its contrast and the layer's rotation; with F_DETAIL_NORMAL, g_tNormalDetail<n>
        (on its own transform and UV set, -1 inheriting the layer's) folded in: normalize(n + detail - up)."""
        normal = group_call(m, _layer_normal_group(), {
            'Normal': normal_texture.outputs[0], 'Rotation': self._float(f"g_flTexCoordRotation{n}", 0.0),
            'Contrast': self._float(f"g_fTextureNormalContrast{n}", 1.0)})['Normal']
        slot = f"g_tNormalDetail{n}"
        if not self._int("F_DETAIL_NORMAL", 0) or not self._have_texture(slot):
            self._skip_texture(slot)
            return normal
        detail_set = self._int(f"g_nDetailUVSet{n}", -1)
        detail_texture = self._get_texture(slot, (0.5, 0.5, 1.0, 1.0), True)
        self.connect_nodes(self._transformed_uv("Detail", str(n), uv_set if detail_set == -1 else detail_set),
                           detail_texture.inputs[0])
        detail = group_call(m, _layer_normal_group(), {
            'Normal': detail_texture.outputs[0], 'Rotation': self._float(f"g_flDetailTexCoordRotation{n}", 0.0),
            'Contrast': self._float(f"g_fDetailTextureNormalContrast{n}", 1.0)})['Normal']
        return m.normalize(normal + detail - UP)

    def _layer(self, m: NodeMath, n: int) -> Layer:
        uv_set = self._int(f"g_nUVSet{n}", 1)
        uv = self._transformed_uv("", str(n), uv_set)
        color_texture = self._get_texture(f"g_tColor{n}", (1.0, 1.0, 1.0, 1.0))
        height_texture = self._get_texture(f"g_tHeight{n}", (0.5, 1.0, 1.0, 0.0), True)
        normal_texture = self._get_texture(f"g_tNormal{n}", (0.5, 0.5, 1.0, 0.5), True)
        for texture in (color_texture, height_texture, normal_texture):
            self.connect_nodes(uv, texture.inputs[0])
        height = self.create_node(Nodes.ShaderNodeSeparateColor)
        self.connect_nodes(height_texture.outputs[0], height.inputs[0])

        tint_mask_raw = m.scalar(height.outputs["Green"])
        tint_mask = self._contrast_brightness(m, tint_mask_raw, self._float(f"g_fTintMaskContrast{n}", 1.0),
                                              self._float(f"g_fTintMaskBrightness{n}", 1.0))
        roughness = self._contrast_brightness(m, normal_texture.outputs["Alpha"],
                                              self._float(f"g_fTextureRoughnessContrast{n}", 1.0),
                                              self._float(f"g_fTextureRoughnessBrightness{n}", 1.0))
        metalness = m.scalar(height_texture.outputs["Alpha"] if self._int(f"g_bMetalness{n}", 1) else 0.0)
        alpha = m.scalar(color_texture.outputs["Alpha"]) if n == 1 and self._int("F_ALPHA_TEST", 0) else None
        return Layer(self._layer_color(m, n, color_texture.outputs[0], tint_mask), alpha,
                     m.scalar(height.outputs["Red"]), tint_mask_raw, tint_mask, metalness, roughness,
                     self._layer_normal(m, n, normal_texture, uv_set))

    def _vertex_paint(self, m: NodeMath) -> Vector:
        """g_vColorTint (sRGB) times the painted COLOR faded by its alpha."""
        vertex_color = self._vertex_color()
        paint = m.lerp((1.0, 1.0, 1.0), m.vector(vertex_color.outputs["Color"]), vertex_color.outputs["Alpha"])
        tint = srgb_to_linear(self._vector("g_vColorTint", (1.0, 1.0, 1.0, 0.0))[:3])
        return paint if tint == (1.0, 1.0, 1.0) else paint * tint

    def _surface(self, m: NodeMath, color, metalness, roughness, normal, alpha: Optional[Scalar]):
        """A Principled BSDF (reflectance 0.04, Blender's default) with an alpha clip under F_ALPHA_TEST."""
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        bsdf = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(bsdf.outputs['BSDF'], material_output.inputs['Surface'])
        m.set(bsdf.inputs['Base Color'], color)
        m.set(bsdf.inputs['Metallic'], metalness)
        m.set(bsdf.inputs['Roughness'], roughness)
        normal_map = self.create_node(Nodes.ShaderNodeNormalMap)
        m.set(normal_map.inputs['Color'], normal * 0.5 + 0.5)
        self.connect_nodes(normal_map.outputs[0], bsdf.inputs['Normal'])
        if alpha is not None:
            reference = self._float("g_flAlphaTestReference", 0.5)
            self.connect_nodes(self.insert_alpha_clip(alpha.socket, reference), bsdf.inputs['Alpha'])
            self.set_blend_mode('CLIP')
        return bsdf

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        m = NodeMath(material.node_tree, self.create_node)
        layer = self._layer(m, 1)
        # The painted vertex color, masked by the raw tint mask (g_nVertexColorMode1 is ignored here).
        color = layer.color * m.lerp((1.0, 1.0, 1.0), self._vertex_paint(m), layer.tint_mask_raw)
        self._surface(m, color, layer.metalness, layer.roughness, layer.normal, layer.alpha)
