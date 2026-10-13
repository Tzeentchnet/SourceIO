from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath, Scalar, Vector
from ..source2_shader_base import Source2ShaderBase
from .csgo_environment import srgb_to_linear


class Crystal(Source2ShaderBase):
    """Dota 2's crystal (crystals, ice, glass and liquids in bottles), after the shader's compiled DX11 bytecode
    (VRF has no port). Each crystal is drawn twice. Back faces are opaque and show the interior cube map
    (g_tCubeMapInterior by g_flCubeMapScalarInterior with F_SEPARATE_INTERIOR_CUBE_MAP, else the exterior one);
    front faces show the exterior cube map and are blended over them by g_tColor's alpha times g_flOpacityScale.
    On both sides the lit albedo is g_tColor times the linear g_vColorTint T, times T and the model tint (with
    F_TINT_MASK, a lerp from g_vColorTint2 to that by g_tTintMask's green), and the cube map, sampled along the
    view reflected about the normal, is added after the Fresnel term g_flFresnelReflectance +
    (1 - g_flFresnelReflectance) * (1 - N.V)^g_flFresnelExponent. Not handled: the sun highlight the shader adds
    with the cube map (g_flSpecularIntensity times the map's global light, which Blender's lights don't
    reproduce) and bloom (g_flBloom)."""
    SHADER: str = 'crystal.vfx'

    def _float(self, name: str, default: float) -> float:
        return float(self._material_resource.get_float_property(name, default))

    def _tint(self, name: str) -> tuple:
        return srgb_to_linear(self._material_resource.get_vector_property(name, (1.0, 1.0, 1.0, 0.0))[:3])

    def _cube_map(self, m: NodeMath, slot: str, reflected: Vector) -> Vector | None:
        texture_path = self._material_resource.get_texture_property(slot, None)
        image = self.load_cubemap_equirect(texture_path) if texture_path else None
        self._skip_texture(slot)
        if image is None:
            return None
        texture = self.create_node(Nodes.ShaderNodeTexEnvironment, slot)
        texture.image = image
        m.set(texture.inputs['Vector'], reflected)
        return m.vector(texture.outputs['Color'])

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        self._skip_textures_with_prefix("g_tCubeMap")
        m = NodeMath(material.node_tree, self.create_node)
        uv = self._texcoord_transform().outputs[0]

        color_texture = self._get_texture("g_tColor", (1.0, 1.0, 1.0, 1.0))
        self.connect_nodes(uv, color_texture.inputs[0])
        tint = self._tint("g_vColorTint")
        model_tint = m.vector(self._model_tint())
        tint_mask = self._check_flag("F_TINT_MASK") and bool(self._have_texture("g_tTintMask"))
        if tint_mask:
            mask_texture = self._get_texture("g_tTintMask", (1.0, 1.0, 1.0, 1.0), True)
            self.connect_nodes(uv, mask_texture.inputs[0])
            split = self.create_node(Nodes.ShaderNodeSeparateColor)
            self.connect_nodes(mask_texture.outputs[0], split.inputs[0])
            second_tint = m.lerp(self._tint("g_vColorTint2"), model_tint * tint, m.scalar(split.outputs['Green']))
        else:
            self._skip_texture("g_tTintMask")
            second_tint = model_tint * tint
        albedo = m.vector(color_texture.outputs['Color']) * tint * second_tint

        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        normal = m.vector(geometry.outputs['Normal'])
        if self._check_flag("F_NORMAL_MAP"):
            normal_texture = self._get_texture("g_tNormal", (0.5, 0.5, 1.0, 1.0), True, True)
            self.connect_nodes(uv, normal_texture.inputs[0])
            normal_map = self.create_node(Nodes.ShaderNodeNormalMap)
            normal_map.inputs['Strength'].default_value = self._float("g_flBumpStrength", 1.0)
            self.connect_nodes(normal_texture.outputs['Color'], normal_map.inputs['Color'])
            normal = m.vector(normal_map.outputs['Normal'])
        else:
            self._skip_texture("g_tNormal")

        bsdf = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        self.connect_nodes(bsdf.outputs['BSDF'], material_output.inputs['Surface'])
        m.set(bsdf.inputs['Base Color'], albedo)
        m.set(bsdf.inputs['Normal'], normal)
        bsdf.inputs['Specular IOR Level'].default_value = 0.0

        # Blender turns back faces' normals toward the viewer, as the shader's back-face pass negates its normal.
        incoming = m.vector(geometry.outputs['Incoming'])
        backfacing = m.scalar(geometry.outputs['Backfacing'])
        if self._check_flag("F_SPECULAR_CUBE_MAP"):
            reflected = m.vmath('REFLECT', -incoming, normal)
            exterior = self._cube_map(m, "g_tCubeMapExterior", reflected)
            exterior_scale = self._float("g_flCubeMapScalarExterior", 1.0)
            reflection = exterior * exterior_scale if exterior is not None else None
            if self._check_flag("F_SEPARATE_INTERIOR_CUBE_MAP"):
                interior = self._cube_map(m, "g_tCubeMapInterior", reflected)
                interior_reflection = (interior * self._float("g_flCubeMapScalarInterior", 1.0)
                                       if interior is not None else m.vector((0.0, 0.0, 0.0)))
                reflection = m.lerp(reflection if reflection is not None else m.vector((0.0, 0.0, 0.0)),
                                    interior_reflection, backfacing)
            if reflection is not None:
                fresnel_reflectance = self._float("g_flFresnelReflectance", 0.0)
                facing = 1.0 - m.saturate(m.dot(normal, incoming))
                fresnel: Scalar = (fresnel_reflectance + (1.0 - fresnel_reflectance) *
                                   m.pow(facing, self._float("g_flFresnelExponent", 5.0)))
                m.set(bsdf.inputs['Emission Color'], reflection * fresnel)
                bsdf.inputs['Emission Strength'].default_value = 1.0

        opacity_scale = self._float("g_flOpacityScale", 1.0)
        alpha = m.scalar(color_texture.outputs['Alpha']) * opacity_scale
        m.set(bsdf.inputs['Alpha'], m.max(alpha, backfacing))
        self.set_blend_mode('BLEND')
        material.use_backface_culling = False
        material.use_transparency_overlap = True
