import math
from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath, Scalar, Vector
from ..source2_shader_base import Source2ShaderBase
from .csgo_environment import srgb_to_linear
from .generic import F0_PER_SPECULAR_LEVEL

# vmdl_loader's names for Dota's vertex paint streams, each RGBA8 split into xy and zw UV maps:
# VertexPaintBlendParams (layer 1-3 amounts in xyz), VertexPaintBlendParams1 (layer 1-3 blend softness in xyz)
# and VertexPaintTintColor (the painted sRGB tint in rgb).
BLEND_UV = "TEXCOORD_1"
SOFTNESS_UV = "TEXCOORD_2"
TINT_UV = "TEXCOORD_3"


class Multiblend(Source2ShaderBase):
    """Dota 2's multiblend (terrain, cliffs, rocks): up to four layers (two with F_TWO_LAYER_BLEND), after VRF's
    multiblend.frag. Layers 1-3 each show where their painted amount passes their reveal mask (the color
    texture's alpha), softened by the painted softness; layer 0 takes what is left. Colors are tinted per layer
    (g_vColorTintB<n> to g_vColorTint<n> by g_tTintMasks with F_TINT_MASK), then by the painted tint and
    g_vGlobalTint; normals and g_tSpecular<n> (reflectance in red, self-illumination in green) blend by the
    same weights. Not handled: parallax (g_tParallaxMask), scrolling and waving UVs, and F_WORLDSPACE_UVS,
    which VRF ignores too (the meshes carry ordinary UVs)."""
    SHADER: str = 'multiblend.vfx'

    def _float(self, name: str, default: float) -> float:
        return float(self._material_resource.get_float_property(name, default))

    def _vector(self, name: str, default: tuple) -> tuple:
        return tuple(self._material_resource.get_vector_property(name, default))

    def _stream(self, m: NodeMath, name: str) -> Vector:
        """An RGBA8 vertex stream's xyz from its xy and zw UV maps."""
        xy = self.create_node(Nodes.ShaderNodeUVMap)
        xy.uv_map = name
        zw = self.create_node(Nodes.ShaderNodeUVMap)
        zw.uv_map = f"{name}_2"
        xy_vector, zw_vector = m.vector(xy.outputs[0]), m.vector(zw.outputs[0])
        return m.combine(xy_vector.x, xy_vector.y, zw_vector.x)

    def _layer_uv(self, m: NodeMath, n: int) -> Vector:
        """VRF's getTexCoord: rotate(uv, g_flTexCoordRotate<n>) / g_flTexCoordScale<n> + 0.5 +
        g_vTexCoordOffset<n>, in the shader's V-down frame (vmdl_loader stores TEXCOORD as 1 - v)."""
        scale = self._float(f"g_flTexCoordScale{n}", 1.0) or 1.0
        rotation = self._float(f"g_flTexCoordRotate{n}", 0.0)
        offset = self._vector(f"g_vTexCoordOffset{n}", (0.0, 0.0, 0.0, 0.0))
        uv_node = self.create_node(Nodes.ShaderNodeUVMap)
        uv_node.uv_map = "TEXCOORD"
        uv = m.vector(uv_node.outputs[0])
        u, v = uv.x, 1.0 - uv.y
        if rotation:
            angle = math.radians(rotation)
            sin, cos = math.sin(angle), math.cos(angle)
            u, v = u * cos - v * sin, u * sin + v * cos
        u = u / scale + (0.5 + offset[0])
        v = v / scale + (0.5 + offset[1])
        return m.combine(u, 1.0 - v, 0.0)

    def _sample(self, m: NodeMath, slot: str, default: tuple, is_data: bool, uv: Vector,
                invert_y: bool = False) -> tuple[Vector, Scalar]:
        texture = self._get_texture(slot, default, is_data, invert_y)
        m.set(texture.inputs[0], uv)
        return m.vector(texture.outputs[0]), m.scalar(texture.outputs['Alpha'])

    def _tint(self, m: NodeMath, n: int, uv: Vector, tint_mask: bool):
        """Layer n's linear tint: g_vColorTint<n>, or with F_TINT_MASK from g_vColorTintB<n> to it by channel n
        of g_tTintMasks."""
        tint = srgb_to_linear(self._vector(f"g_vColorTint{n}", (1.0, 1.0, 1.0, 0.0))[:3])
        if not tint_mask:
            return tint
        tint_b = srgb_to_linear(self._vector(f"g_vColorTintB{n}", (1.0, 1.0, 1.0, 0.0))[:3])
        mask_color, mask_alpha = self._sample(m, "g_tTintMasks", (1.0, 1.0, 1.0, 1.0), True, uv)
        channel = (mask_color.x, mask_color.y, mask_color.z, mask_alpha)[n]
        return m.lerp(tint_b, tint, channel)

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        self._skip_texture("g_tParallaxMask")
        layer_count = 2 if self._material_resource.get_int_property("F_TWO_LAYER_BLEND", 0) == 1 else 4
        self._skip_textures_with_prefix(*(f"g_t{kind}{n}" for kind in ("Color", "Normal", "Specular")
                                          for n in range(layer_count, 4)))
        material.use_backface_culling = not self._check_flag("F_RENDER_BACKFACES")
        m = NodeMath(material.node_tree, self.create_node)
        normal_map = self._check_flag("F_NORMAL_MAP")
        specular = self._check_flag("F_SPECULAR")
        tint_mask = self._check_flag("F_TINT_MASK") and bool(self._have_texture("g_tTintMasks"))
        if not tint_mask:
            self._skip_texture("g_tTintMasks")
        if not normal_map:
            self._skip_textures_with_prefix("g_tNormal")
        if not specular:
            self._skip_textures_with_prefix("g_tSpecular")

        amounts = self._stream(m, BLEND_UV)
        softness = self._stream(m, SOFTNESS_UV)
        layers = []
        for n in range(layer_count):
            uv = self._layer_uv(m, n)
            color, reveal = self._sample(m, f"g_tColor{n}", (1.0, 1.0, 1.0, 1.0), False, uv)
            normal = self._sample(m, f"g_tNormal{n}", (0.5, 0.5, 1.0, 1.0), True, uv, True)[0] if normal_map else None
            spec = self._sample(m, f"g_tSpecular{n}", (0.1, 0.0, 0.0, 1.0), True, uv)[0] if specular else None
            layers.append((color * self._tint(m, n, uv, tint_mask), reveal, normal, spec))

        # ApplyBlendModulation: smoothstep(max(0, mask - softness), min(1, mask + softness), amount), each layer
        # limited to what the layers above it left.
        weights: list[Scalar] = []
        remaining: Scalar | float = 1.0
        for n, amount, soft in zip(range(1, layer_count), (amounts.x, amounts.y, amounts.z),
                                   (softness.x, softness.y, softness.z)):
            reveal = layers[n][1]
            weight = m.smoothstep(m.max(reveal - soft, 0.0), m.min(reveal + soft, 1.0), amount)
            weight = m.min(weight, remaining)
            weights.append(weight)
            remaining = remaining - weight
        weights.insert(0, remaining)

        def blend(index: int) -> Vector:
            total = None
            for weight, layer in zip(weights, layers):
                term = layer[index] * weight
                total = term if total is None else total + term
            return total

        color = blend(0)
        if not self._check_flag("F_NO_VERTEX_COLOR"):
            tint = self._stream(m, TINT_UV)
            color = color * m.vector(self._srgb_to_linear(tint.socket))
        color = color * m.vector(self._model_tint())
        global_tint = srgb_to_linear(self._vector("g_vGlobalTint", (1.0, 1.0, 1.0, 1.0))[:3])
        if global_tint != (1.0, 1.0, 1.0):
            color = color * global_tint

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        bsdf = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(bsdf.outputs['BSDF'], material_output.inputs['Surface'])
        bsdf.inputs['Roughness'].default_value = 0.5
        m.set(bsdf.inputs['Base Color'], color)
        if normal_map:
            normal_node = self.create_node(Nodes.ShaderNodeNormalMap)
            normal_node.inputs['Strength'].default_value = self._float("g_flBumpStrength", 1.0)
            m.set(normal_node.inputs['Color'], blend(2))
            self.connect_nodes(normal_node.outputs['Normal'], bsdf.inputs['Normal'])
        if specular:
            blended = blend(3)
            intensity = self._float("g_flSpecularIntensity", 1.0)
            m.set(bsdf.inputs['Specular IOR Level'], blended.x * (intensity / F0_PER_SPECULAR_LEVEL))
            m.set(bsdf.inputs['Emission Color'], color * blended.y)
            bsdf.inputs['Emission Strength'].default_value = 1.0
        else:
            bsdf.inputs['Specular IOR Level'].default_value = 0.0
