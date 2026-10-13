from typing import Any

import bpy
import numpy as np

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath
from ..source2_shader_base import Source2ShaderBase
from .csgo_environment import srgb_to_linear
from .....library.source2.resource_types import CompiledTextureResource
from .....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS

#: Water's index of refraction, as the Source 1 water shader uses (the shader itself has no Fresnel term).
WATER_IOR = 1.333
WATER_F0 = ((WATER_IOR - 1.0) / (WATER_IOR + 1.0)) ** 2


class WaterDota(Source2ShaderBase):
    """Dota 2's water_dota (the river and other water), after the shader's compiled DX11 bytecode (VRF has no
    port). In game the surface shows the refraction buffer, tinted by g_vRefractionTint and fogged towards
    g_vWaterFogColor by a depth factor the scene leaves in the buffer's alpha, plus the planar reflection times
    g_vReflectionTint and g_flReflectance, plus a highlight g_vReflectionColor * saturate(R.g_vReflectionDir) ^
    g_flReflectionPower. Here that is a transmissive Principled BSDF (IOR 1.333) tinted by g_vRefractionTint,
    reflecting the scene with Specular Tint g_vReflectionTint and its reflectance raised towards g_flReflectance
    (Blender caps it at twice the IOR's), and the highlight added as emission.

    The normal is the shader's world-space one: g_tNormal sampled at (x, -y) * g_flWorldPositionScale /
    g_flNormalUvScale in Source units, its xy scaled by (|flow|^2 + 0.1) * g_flBumpStrength and clamped to unit
    length. The flow map (g_tFlow) is placed in the world by runtime constants (DotaHeightFogParams_t), so
    |flow|^2 is its mean where painted (the river's is 0.4) or the material's TextureFlow color's; the flow
    scrolling and its noise (g_tNoise) are not animated. Not handled either: the depth fog, which has no surface
    equivalent, F_FLOW_COLOR, F_FRESNEL and the low-end (cheap water) parameters."""
    SHADER: str = 'water_dota.vfx'

    def _float(self, name: str, default: float) -> float:
        return float(self._material_resource.get_float_property(name, default))

    def _vector(self, name: str, default: tuple) -> tuple:
        return tuple(self._material_resource.get_vector_property(name, default))

    def _flow_speed(self) -> float:
        """|flow|^2: from the material's TextureFlow color, else the mean over g_tFlow's painted (not black)
        texels, else from the shader's default flow color (1, 0.5)."""
        flow = self._material_resource.get_vector_property("TextureFlow", None)
        if flow is None:
            texture_path = self._material_resource.get_texture_property("g_tFlow", None)
            resource = (self._material_resource.get_child_resource(texture_path, self.content_manager,
                                                                   CompiledTextureResource)
                        if texture_path else None)
            if resource is not None:
                pixels, (width, height) = resource.get_texture_data(0)
                rg = np.asarray(pixels).reshape(height, width, -1)[..., :2].reshape(-1, 2)
                rg = rg[rg.any(axis=1)]
                if len(rg):
                    return float(((2.0 * rg - 1.0) ** 2).sum(axis=1).mean())
            flow = (1.0, 0.5)
        return (2.0 * flow[0] - 1.0) ** 2 + (2.0 * flow[1] - 1.0) ** 2

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        for slot in ("g_tColor", "g_tFlow", "g_tNoise"):
            self._skip_texture(slot)
        import_scale = float(extra_parameters.get(ExtraMaterialParameters.SOURCE2_IMPORT_SCALE,
                                                  SOURCE2_HAMMER_UNIT_TO_METERS))
        self.set_blend_mode('OPAQUE')
        material.use_raytrace_refraction = True
        material.use_backface_culling = not self._check_flag("F_RENDER_BACKFACES")
        m = NodeMath(material.node_tree, self.create_node)

        bsdf = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        self.connect_nodes(bsdf.outputs['BSDF'], material_output.inputs['Surface'])
        bsdf.inputs['Transmission Weight'].default_value = 1.0
        bsdf.inputs['IOR'].default_value = WATER_IOR
        bsdf.inputs['Roughness'].default_value = 0.0
        m.set(bsdf.inputs['Base Color'], srgb_to_linear(self._vector("g_vRefractionTint", (1.0, 1.0, 1.0, 0.0))[:3]))
        m.set(bsdf.inputs['Specular Tint'], srgb_to_linear(self._vector("g_vReflectionTint", (1.0, 1.0, 1.0, 0.0))[:3]))
        bsdf.inputs['Specular IOR Level'].default_value = min(0.5 * self._float("g_flReflectance", 0.2) / WATER_F0, 1.0)

        # The normal map in world space: Blender's V runs opposite to the shader's, so (x, -y) samples it at (x, y).
        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        position = m.vector(geometry.outputs['Position'])
        uv_scale = self._float("g_flWorldPositionScale", 1.0) / (self._float("g_flNormalUvScale", 1.0) or 1.0)
        normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1.0, 1.0), True)
        m.set(normal_texture.inputs[0], position * (uv_scale / import_scale))
        strength = (self._flow_speed() + 0.1) * self._float("g_flBumpStrength", 1.0)
        offset = m.vector(normal_texture.outputs['Color']) * 2.0 - (1.0, 1.0, 1.0)
        offset = m.combine(offset.x, offset.y, 0.0) * strength
        length = m.vmath('LENGTH', offset)
        offset = offset / m.max(length, 1.0)
        height = m.math('SQRT', m.max(1.0 - m.dot(offset, offset), 0.0))
        normal = m.combine(offset.x, offset.y, height)
        m.set(bsdf.inputs['Normal'], normal)

        highlight_color = srgb_to_linear(self._vector("g_vReflectionColor", (1.0, 1.0, 1.0, 0.0))[:3])
        if any(highlight_color):
            reflected = m.vmath('REFLECT', -m.vector(geometry.outputs['Incoming']), normal)
            direction = self._vector("g_vReflectionDir", (0.0, 0.0, 1.0, 0.0))[:3]
            highlight = m.pow(m.saturate(m.dot(reflected, direction)), self._float("g_flReflectionPower", 1.0))
            m.set(bsdf.inputs['Emission Color'], m.vector(highlight_color) * highlight)
            bsdf.inputs['Emission Strength'].default_value = 1.0
        # The refraction buffer is the lit scene: the water doesn't shade what lies under it.
        self.disable_shadow_casting()
