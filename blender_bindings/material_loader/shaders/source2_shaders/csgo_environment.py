import math
from dataclasses import dataclass
from typing import Any, Optional

import bpy
import numpy as np

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath, Scalar, Vector, group_call, node_group
from ..source2_shader_base import Source2ShaderBase
from .....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS

UP = (0.0, 0.0, 1.0)
# GetLuma's weights, which ColorizeTint uses
LUMA = (0.2125, 0.7154, 0.0721)
_LUMINANCE = np.array((0.2126, 0.7152, 0.0722)) / np.linalg.norm((0.2126, 0.7152, 0.0722))
_DEFAULT_BIPLANAR_SCALE = 1.0 / (64.0 * SOURCE2_HAMMER_UNIT_TO_METERS)


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


def _biplanar_frame_group():
    """The CS2 biplanar planes and angular weights from world-space position and the interpolated normal."""

    def build(m: NodeMath, i):
        position = i['Position'] * i['Scale'] * (-1.0, 1.0, -1.0)
        normal = m.normalize(i['Normal'])
        x, y, z = m.abs(normal.x), m.abs(normal.y), m.abs(normal.z)

        # Exclude the least-facing plane. The strict comparisons match the shader away from exact ties.
        minor_x = m.less(x, y) * m.less(x, z)
        minor_y = (1.0 - minor_x) * m.less(y, z)
        minor_z = 1.0 - minor_x - minor_y
        raw_x = m.saturate((x - 0.5773) * 2.365744) * (1.0 - minor_x)
        raw_y = m.saturate((y - 0.5773) * 2.365744) * (1.0 - minor_y)
        raw_z = m.saturate((z - 0.5773) * 2.365744) * (1.0 - minor_z)
        total = m.max(raw_x + raw_y + raw_z, 1e-6)
        return {
            'UV X': m.combine(position.y, position.z, 0.0),
            'UV Y': m.combine(position.x, position.z, 0.0),
            'UV Z': m.combine(position.x, position.y, 0.0),
            'Weight X': raw_x / total,
            'Weight Y': raw_y / total,
            'Weight Z': raw_z / total,
            'Normal': normal,
        }

    return node_group(
        'SourceIO Environment Biplanar Frame',
        {'Position': ('vector', (0.0, 0.0, 0.0)), 'Normal': ('vector', UP),
         'Scale': ('float', _DEFAULT_BIPLANAR_SCALE)},
        {'UV X': 'vector', 'UV Y': 'vector', 'UV Z': 'vector',
         'Weight X': 'float', 'Weight Y': 'float', 'Weight Z': 'float', 'Normal': 'vector'},
        build,
    )


@dataclass
class TextureMapping:
    uv: Optional[Any] = None
    plane_uvs: Optional[tuple[Vector, Vector, Vector]] = None
    weights: Optional[tuple[Scalar, Scalar, Scalar]] = None
    normal: Optional[Vector] = None
    tangent: Optional[Vector] = None
    bitangent: Optional[Vector] = None


@dataclass
class TextureSample:
    color: Vector
    alpha: Scalar
    plane_colors: Optional[tuple[Vector, Vector, Vector]] = None


@dataclass
class Layer:
    color: Vector  # linear, color corrected, model tinted and clamped
    alpha: Optional[Scalar]  # the color texture's alpha (layer 1 with F_ALPHA_TEST)
    height: Scalar  # g_tHeight red
    tint_mask_raw: Scalar  # g_tHeight green
    tint_mask: Scalar  # through g_fTintMaskContrast and g_fTintMaskBrightness
    metalness: Scalar
    roughness: Scalar
    ambient_occlusion: Scalar
    normal: Vector  # unit, tangent space


class CSGOEnvironment(Source2ShaderBase):
    """csgo_environment and, in CSGOEnvironmentBlend, csgo_environment_blend, after VRF's csgo_environment.frag.
    Each layer n packs color (and AO) in g_tColor<n>, height, tint mask and metalness in g_tHeight<n> (red, green,
    alpha) and the normal and roughness in g_tNormal<n> (SourceIO decodes it to a tangent-space normal with
    roughness in alpha). AO is curved per layer, then folded into Base Color because Blender's Principled BSDF has
    no texture-AO input. UV set 0 uses CS2's two-plane world projection at 64 Source units per repeat; the scale is
    derived from the Source 2 import scale. Positive model-scale magnitudes can scale UV1/UV2 before the layer
    transform; the sign of mirrored model axes is unavailable to the material node graph. Not handled: F_WETNESS,
    whose result also requires the scene's live rain, coverage, drying, ripple and dynamic-AO state."""
    SHADER: str = 'csgo_environment.vfx'

    def _float(self, name: str, default: float) -> float:
        return float(self._material_resource.get_float_property(name, default))

    def _int(self, name: str, default: int) -> int:
        return int(self._material_resource.get_int_property(name, default))

    def _vector(self, name: str, default: tuple) -> tuple:
        return tuple(self._material_resource.get_vector_property(name, default))

    def _model_scale_axis(self, m: NodeMath, axis: int) -> Scalar:
        """The positive magnitude of one object-to-world model axis; 0 means no scaling."""
        if axis == 0:
            return m.scalar(1.0)
        if axis not in (1, 2, 3):
            raise ValueError(f"Invalid model-scale axis {axis}")
        cache = getattr(self, "_environment_model_scale_axes", None)
        if cache is None:
            cache = self._environment_model_scale_axes = {}
        if axis not in cache:
            transform = self.create_node(Nodes.ShaderNodeVectorTransform)
            transform.vector_type = 'VECTOR'
            transform.convert_from = 'OBJECT'
            transform.convert_to = 'WORLD'
            transform.inputs['Vector'].default_value = tuple(float(index == axis - 1) for index in range(3))
            cache[axis] = m.vmath('LENGTH', transform.outputs['Vector'])
        return cache[axis]

    def _uv_set(self, m: NodeMath, uv_set: int) -> Vector:
        """UV set 2 is the secondary set (or primary where absent); UV1 and UV2 can follow model-scale axes."""
        if uv_set == 2:
            uv = self._secondary_uv_or_primary()
            affix = "2"
        elif uv_set == 1:
            uv_node = self.create_node(Nodes.ShaderNodeUVMap)
            uv_node.uv_map = "TEXCOORD"
            uv = uv_node.outputs[0]
            affix = ""
        else:
            raise ValueError(f"UV set {uv_set} is not a mesh UV set")
        value = m.vector(uv)
        u_axis = self._int(f"g_nScaleTexCoord{affix}UByModelScaleAxis", 0)
        v_axis = self._int(f"g_nScaleTexCoord{affix}VByModelScaleAxis", 0)
        if not u_axis and not v_axis:
            return value
        return m.combine(value.x * self._model_scale_axis(m, u_axis),
                         value.y * self._model_scale_axis(m, v_axis), value.z)

    def _transformed_uv(self, m: NodeMath, prefix: str, suffix: str, uv_set: int):
        """The UV set through g_v<prefix>TexCoordScale/Offset/Center<suffix> and g_fl<prefix>TexCoordRotation<suffix>,
        scaled about the origin (the g_v*TexCoordXform expressions of both environment shaders)."""
        return self.create_transform(self._uv_set(m, uv_set).socket,
                                     self._vector(f"g_v{prefix}TexCoordScale{suffix}", (1.0, 1.0, 0.0)),
                                     self._vector(f"g_v{prefix}TexCoordOffset{suffix}", (0.0, 0.0, 0.0)),
                                     self._vector(f"g_v{prefix}TexCoordCenter{suffix}", (0.5, 0.5, 0.0)),
                                     self._float(f"g_fl{prefix}TexCoordRotation{suffix}", 0.0)).outputs[0]

    def _biplanar_frame(self, m: NodeMath) -> dict[str, Vector | Scalar]:
        cached = getattr(self, "_environment_biplanar_frame", None)
        if cached is not None:
            return cached
        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        frame = group_call(m, _biplanar_frame_group(), {
            'Position': geometry.outputs['Position'], 'Normal': geometry.outputs['Normal'],
            'Scale': self._environment_biplanar_scale})
        tangent_node = self.create_node(Nodes.ShaderNodeTangent)
        tangent_node.direction_type = 'UV_MAP'
        tangent_node.uv_map = 'TEXCOORD'
        frame['Tangent'] = m.normalize(tangent_node.outputs['Tangent'])
        frame['Bitangent'] = m.normalize(m.vmath('CROSS_PRODUCT', frame['Normal'], frame['Tangent']))
        self._environment_biplanar_frame = frame
        return frame

    def _configure_import_scale(self, extra_parameters: dict[ExtraMaterialParameters, Any]):
        import_scale = extra_parameters.get(
            ExtraMaterialParameters.SOURCE2_IMPORT_SCALE, SOURCE2_HAMMER_UNIT_TO_METERS)
        try:
            import_scale = float(import_scale)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Source 2 import scale {import_scale!r}") from exc
        if not math.isfinite(import_scale) or import_scale <= 0:
            raise ValueError(
                f"Source 2 import scale must be finite and greater than zero, got {import_scale!r}")
        self._environment_biplanar_scale = 1.0 / (64.0 * import_scale)

    def _texture_mapping(self, m: NodeMath, prefix: str, suffix: str, uv_set: int) -> TextureMapping:
        if uv_set != 0:
            return TextureMapping(uv=self._transformed_uv(m, prefix, suffix, uv_set))
        frame = self._biplanar_frame(m)
        scale = self._vector(f"g_v{prefix}TexCoordScale{suffix}", (1.0, 1.0, 0.0))
        scale_vector = (scale[0], scale[1], 1.0)
        return TextureMapping(
            plane_uvs=tuple(frame[f'UV {axis}'] * scale_vector for axis in "XYZ"),
            weights=tuple(frame[f'Weight {axis}'] for axis in "XYZ"),
            normal=frame['Normal'], tangent=frame['Tangent'], bitangent=frame['Bitangent'],
        )

    def _sample_texture(self, m: NodeMath, slot: str, default: tuple[float, float, float, float],
                        is_data: bool, mapping: TextureMapping) -> TextureSample:
        texture = self._get_texture(slot, default, is_data)
        if mapping.uv is not None:
            self.connect_nodes(mapping.uv, texture.inputs[0])
            return TextureSample(m.vector(texture.outputs[0]), m.scalar(texture.outputs['Alpha']))
        if mapping.plane_uvs is None or mapping.weights is None:
            raise ValueError(f"Missing biplanar mapping for {slot}")

        texture.label = "Biplanar X"
        textures = [texture]
        for axis in "YZ":
            projected = self.create_node(Nodes.ShaderNodeTexImage, f"{slot} Biplanar {axis}")
            projected.image = texture.image
            projected.interpolation = texture.interpolation
            projected.extension = texture.extension
            projected.label = f"Biplanar {axis}"
            projected.hide = True
            textures.append(projected)
        for projected, uv in zip(textures, mapping.plane_uvs):
            m.set(projected.inputs['Vector'], uv)
        planes = tuple(m.vector(projected.outputs[0]) for projected in textures)
        alpha_planes = tuple(m.scalar(projected.outputs['Alpha']) for projected in textures)
        color = sum((plane * weight for plane, weight in zip(planes, mapping.weights)), m.vector((0.0, 0.0, 0.0)))
        alpha = sum((plane * weight for plane, weight in zip(alpha_planes, mapping.weights)), m.scalar(0.0))
        return TextureSample(color, alpha, planes)

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

    @staticmethod
    def _projected_normal(m: NodeMath, sample: TextureSample, mapping: TextureMapping,
                          contrast: float) -> Vector:
        if (sample.plane_colors is None or mapping.weights is None or mapping.normal is None
                or mapping.tangent is None or mapping.bitangent is None):
            raise ValueError("Incomplete biplanar normal sample")
        decoded = tuple(plane * 2.0 - 1.0 for plane in sample.plane_colors)
        offsets = (
            m.combine(0.0, decoded[0].x, -decoded[0].y),
            m.combine(-decoded[1].x, 0.0, -decoded[1].y),
            m.combine(-decoded[2].x, decoded[2].y, 0.0),
        )
        offset = sum((value * weight for value, weight in zip(offsets, mapping.weights)),
                     m.vector((0.0, 0.0, 0.0)))
        world = m.normalize(mapping.normal + offset)
        tangent = m.combine(m.dot(world, mapping.tangent), m.dot(world, mapping.bitangent),
                            m.dot(world, mapping.normal))
        return m.normalize(m.lerp(UP, tangent, contrast))

    def _sampled_normal(self, m: NodeMath, sample: TextureSample, mapping: TextureMapping,
                        rotation: float, contrast: float) -> Vector:
        if sample.plane_colors is not None:
            return self._projected_normal(m, sample, mapping, contrast)
        return group_call(m, _layer_normal_group(), {
            'Normal': sample.color, 'Rotation': rotation, 'Contrast': contrast})['Normal']

    def _layer_normal(self, m: NodeMath, n: int, normal_texture: TextureSample,
                      mapping: TextureMapping, uv_set: int) -> Vector:
        """g_tNormal<n> through its contrast and the layer's rotation; with F_DETAIL_NORMAL, g_tNormalDetail<n>
        (on its own transform and UV set, -1 inheriting the layer's) folded in: normalize(n + detail - up)."""
        normal = self._sampled_normal(m, normal_texture, mapping, self._float(f"g_flTexCoordRotation{n}", 0.0),
                                      self._float(f"g_fTextureNormalContrast{n}", 1.0))
        slot = f"g_tNormalDetail{n}"
        if not self._int("F_DETAIL_NORMAL", 0) or not self._have_texture(slot):
            self._skip_texture(slot)
            return normal
        detail_set = self._int(f"g_nDetailUVSet{n}", -1)
        detail_mapping = self._texture_mapping(m, "Detail", str(n), uv_set if detail_set == -1 else detail_set)
        detail_texture = self._sample_texture(m, slot, (0.5, 0.5, 1.0, 1.0), True, detail_mapping)
        detail = self._sampled_normal(
            m, detail_texture, detail_mapping, self._float(f"g_flDetailTexCoordRotation{n}", 0.0),
            self._float(f"g_fDetailTextureNormalContrast{n}", 1.0))
        return m.normalize(normal + detail - UP)

    def _ambient_occlusion(self, m: NodeMath, n: int, sample: Scalar) -> Scalar:
        """The authored AO-level vector through its compiled expression, then the shipped per-layer curve."""
        authored = self._vector(f"g_vAmbientOcclusionLevels{n}", (0.0, 0.5, 1.0, 0.0))
        low = -authored[0]
        power = -1.4427 * math.log(max(0.0001, 1.0 - authored[1]))
        high = 2.0 - authored[2]
        return m.saturate(m.lerp(low, high, m.pow(sample, power)))

    def _layer(self, m: NodeMath, n: int) -> Layer:
        uv_set = self._int(f"g_nUVSet{n}", 1)
        mapping = self._texture_mapping(m, "", str(n), uv_set)
        color_texture = self._sample_texture(m, f"g_tColor{n}", (1.0, 1.0, 1.0, 1.0), False, mapping)
        height_texture = self._sample_texture(m, f"g_tHeight{n}", (0.5, 1.0, 1.0, 0.0), True, mapping)
        normal_texture = self._sample_texture(m, f"g_tNormal{n}", (0.5, 0.5, 1.0, 0.5), True, mapping)

        tint_mask_raw = height_texture.color.y
        tint_mask = self._contrast_brightness(m, tint_mask_raw, self._float(f"g_fTintMaskContrast{n}", 1.0),
                                              self._float(f"g_fTintMaskBrightness{n}", 1.0))
        roughness = self._contrast_brightness(m, normal_texture.alpha,
                                              self._float(f"g_fTextureRoughnessContrast{n}", 1.0),
                                              self._float(f"g_fTextureRoughnessBrightness{n}", 1.0))
        metalness = height_texture.alpha if self._int(f"g_bMetalness{n}", 1) else m.scalar(0.0)
        alpha_test = n == 1 and self._int("F_ALPHA_TEST", 0)
        alpha = color_texture.alpha if alpha_test else None
        ambient_occlusion = self._ambient_occlusion(
            m, n, height_texture.color.z if alpha_test else color_texture.alpha)
        return Layer(self._layer_color(m, n, color_texture.color, tint_mask), alpha,
                     height_texture.color.x, tint_mask_raw, tint_mask, metalness, roughness, ambient_occlusion,
                     self._layer_normal(m, n, normal_texture, mapping, uv_set))

    def _vertex_paint(self, m: NodeMath) -> Vector:
        """g_vColorTint (sRGB) times the painted COLOR faded by its alpha."""
        vertex_color = self._vertex_color()
        paint = m.lerp((1.0, 1.0, 1.0), m.vector(vertex_color.outputs["Color"]), vertex_color.outputs["Alpha"])
        tint = srgb_to_linear(self._vector("g_vColorTint", (1.0, 1.0, 1.0, 0.0))[:3])
        return paint if tint == (1.0, 1.0, 1.0) else paint * tint

    def _surface(self, m: NodeMath, color, metalness, roughness, ambient_occlusion, normal,
                 alpha: Optional[Scalar]):
        """A Principled BSDF with texture AO folded into color and an alpha clip under F_ALPHA_TEST."""
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        bsdf = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(bsdf.outputs['BSDF'], material_output.inputs['Surface'])
        m.set(bsdf.inputs['Base Color'], color * ambient_occlusion)
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
        self._configure_import_scale(extra_parameters)
        m = NodeMath(material.node_tree, self.create_node)
        layer = self._layer(m, 1)
        # The painted vertex color, masked by the raw tint mask (g_nVertexColorMode1 is ignored here).
        color = layer.color * m.lerp((1.0, 1.0, 1.0), self._vertex_paint(m), layer.tint_mask_raw)
        self._surface(m, color, layer.metalness, layer.roughness, layer.ambient_occlusion, layer.normal, layer.alpha)
