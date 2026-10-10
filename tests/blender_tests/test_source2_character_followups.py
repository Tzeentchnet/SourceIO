"""Run inside Blender with SourceIO registered: unittest ...test_source2_character_followups."""
import unittest
from math import cos, radians, sin

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import MIX_A, MIX_B, MIX_FACTOR
from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_complex import (
    CHARACTER_ANISOTROPIC_ROTATION,
    CHARACTER_ANISOTROPY,
    CHARACTER_LUMA,
    CHARACTER_NODE_GROUP,
    CHARACTER_SSS_WEIGHT,
    CHARACTER_TANGENT,
)
from SourceIO.tests.blender_tests import test_source2_materials as material_tests
from SourceIO.tests.blender_tests.test_source2_materials import (
    build,
    linked_node,
    output_node,
    shader_node,
    source,
    texture_nodes,
)


class Source2CharacterFollowupTests(unittest.TestCase):
    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    @staticmethod
    def vrf_adjust(color, brightness=1.0, contrast=1.0, hue_shift=0.0, saturation=1.0):
        color = np.clip(((np.asarray(color) - 0.5) * contrast + 0.5) * brightness, 0.0, 1.0)
        color_max = color.max()
        color_range = 0.0 if color_max == 0.0 else (color_max - color.min()) / color_max
        axis = np.full(3, 0.57735)
        angle = radians(hue_shift)
        rotated = (color * cos(angle) + np.cross(axis, color) * sin(angle)
                   + axis * np.dot(axis, color) * (1.0 - cos(angle)))
        luma = np.dot(color, CHARACTER_LUMA)
        hue_shifted = luma + (rotated - luma) * color_range ** 0.125
        shifted_luma = np.dot(hue_shifted, CHARACTER_LUMA)
        return np.clip(shifted_luma + (hue_shifted - shifted_luma) * saturation, 0.0, 1.0)

    def assert_socket_is(self, material, socket, expected, *, object_color=None):
        nodes, links = material.node_tree.nodes, material.node_tree.links
        emission = nodes.new('ShaderNodeEmission')
        links.new(socket, emission.inputs['Color'])
        links.new(emission.outputs[0], output_node(material).inputs['Surface'])
        rendered = material_tests.Source2MaterialTests.render_over_background(
            material,
            (0.0, 0.0, 0.0),
            object_color=object_color,
        )
        reference = material_tests.Source2MaterialTests.render_constant(expected, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(rendered, reference, atol=2e-3)

    def assert_input_is(self, material, input_name, expected, *, object_color=None):
        socket = shader_node(material, 'csgo_character.vfx').inputs[input_name].links[0].from_socket
        self.assert_socket_is(material, socket, expected, object_color=object_color)

    @staticmethod
    def set_color(material, slot, rgba):
        material_tests.Source2MaterialTests.set_color(material, slot, rgba)

    def test_runtime_group_exposes_character_inputs_without_mutating_asset_group(self):
        material = build('csgo_character.vfx', ('g_tColor', 'g_tNormal'))
        shader = shader_node(material, 'csgo_character.vfx')
        source_group = bpy.data.node_groups['csgo_complex.vfx']
        source_inputs = {
            item.name for item in source_group.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
        }
        character_inputs = {
            item.name for item in shader.node_tree.interface.items_tree
            if item.item_type == 'SOCKET' and item.in_out == 'INPUT'
        }
        added = {
            CHARACTER_SSS_WEIGHT,
            CHARACTER_ANISOTROPY,
            CHARACTER_ANISOTROPIC_ROTATION,
            CHARACTER_TANGENT,
        }
        self.assertEqual(shader.node_tree.name, CHARACTER_NODE_GROUP)
        self.assertTrue(added.isdisjoint(source_inputs))
        self.assertTrue(added.issubset(character_inputs))

        principled = next(node for node in shader.node_tree.nodes
                          if node.bl_idname == 'ShaderNodeBsdfPrincipled')
        for socket_name in ('Subsurface Weight', 'Anisotropic', 'Anisotropic Rotation', 'Tangent'):
            self.assertTrue(principled.inputs[socket_name].is_linked)

    def test_sss_mask_uses_green_and_shader_defaults(self):
        material = build(
            'csgo_character.vfx',
            ('g_tColor', 'g_tSssMask', 'g_tDiffuseFalloff'),
            ints={'F_SUBSURFACE_SCATTERING': 1},
        )
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertEqual(source(shader.inputs[CHARACTER_SSS_WEIGHT]), ('g_tSssMask', 'Green'))
        self.assertTrue(material.node_tree.nodes['g_tSssMask'].image.colorspace_settings.is_data)
        self.assertNotIn('g_tDiffuseFalloff', texture_nodes(material))

        material = build('csgo_character.vfx', ('g_tColor',), ints={'F_SUBSURFACE_SCATTERING': 1})
        self.assertAlmostEqual(
            shader_node(material, 'csgo_character.vfx').inputs[CHARACTER_SSS_WEIGHT].default_value,
            1.0,
            places=6,
        )

        material = build('csgo_character.vfx', ('g_tColor', 'g_tSssMask'))
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertFalse(shader.inputs[CHARACTER_SSS_WEIGHT].is_linked)
        self.assertAlmostEqual(shader.inputs[CHARACTER_SSS_WEIGHT].default_value, 0.0, places=6)
        self.assertNotIn('g_tSssMask', texture_nodes(material))

    def test_anisotropic_rg_drives_mean_ratio_rotation_and_spherical_tangent(self):
        material = build(
            'csgo_character.vfx',
            ('g_tColor', 'g_tNormal', 'g_tAnisoGloss'),
            ints={
                'F_ANISOTROPIC_GLOSS': 1,
                'F_SPHERICAL_PROJECTED_ANISOTROPIC_TANGENTS': 1,
            },
            vectors={'g_vSphericalAnisotropyPole': (-0.022, 0.0, -0.311, 0.0)},
        )
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertTrue(material.node_tree.nodes['g_tAnisoGloss'].image.colorspace_settings.is_data)
        average = linked_node(shader.inputs['TextureRoughness'])
        self.assertEqual(average.operation, 'DOT_PRODUCT')
        self.assertEqual(source(average.inputs[0]), ('g_tAnisoGloss', 'Color'))
        self.assertEqual(tuple(average.inputs[1].default_value), (0.5, 0.5, 0.0))

        rotation = linked_node(shader.inputs[CHARACTER_ANISOTROPIC_ROTATION])
        self.assertEqual(rotation.operation, 'MULTIPLY')
        self.assertAlmostEqual(rotation.inputs[1].default_value, 0.25, places=6)
        green_is_rougher = linked_node(rotation.inputs[0])
        self.assertEqual(green_is_rougher.operation, 'GREATER_THAN')
        self.assertEqual(source(green_is_rougher.inputs[0]), ('g_tAnisoGloss', 'Green'))
        self.assertEqual(source(green_is_rougher.inputs[1]), ('g_tAnisoGloss', 'Red'))

        normalized_tangent = linked_node(shader.inputs[CHARACTER_TANGENT])
        self.assertEqual(normalized_tangent.operation, 'NORMALIZE')
        self.assertEqual(linked_node(normalized_tangent.inputs[0]).name, 'Character Spherical Tangent')

        self.set_color(material, 'g_tAnisoGloss', (0.4, 0.8, 0.0, 1.0))
        self.assert_input_is(material, 'TextureRoughness', (0.6, 0.6, 0.6))
        self.assert_input_is(material, CHARACTER_ANISOTROPY, (5.0 / 6.0,) * 3)
        self.assert_input_is(material, CHARACTER_ANISOTROPIC_ROTATION, (0.25, 0.25, 0.25))

        material = build(
            'csgo_character.vfx',
            ('g_tColor', 'g_tNormal'),
            ints={'F_ANISOTROPIC_GLOSS': 1},
        )
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertEqual(source(shader.inputs['TextureRoughness']), ('g_tNormal', 'Alpha'))
        self.assertFalse(shader.inputs[CHARACTER_ANISOTROPY].is_linked)
        self.assertAlmostEqual(shader.inputs[CHARACTER_ANISOTROPY].default_value, 0.0, places=6)

    def test_albedo_adjustment_matches_vrf_formula_and_tint_mask(self):
        color = np.array((0.2, 0.6, 0.9))
        mask = 0.25
        values = {'brightness': 0.8, 'contrast': 1.4, 'hue_shift': 35.0, 'saturation': 1.3}
        material = build(
            'csgo_character.vfx',
            ('g_tColor', 'g_tTintMask'),
            ints={'F_ENABLE_ADJUSTMENTS': 1, 'F_TINT_MASK': 1},
            floats={
                'g_fBrightness': values['brightness'],
                'g_fContrast': values['contrast'],
                'g_fHueShift': values['hue_shift'],
                'g_fSaturation': values['saturation'],
            },
        )
        self.set_color(material, 'g_tColor', (*color, 1.0))
        self.set_color(material, 'g_tTintMask', (mask, 0.0, 0.0, 1.0))
        adjusted = self.vrf_adjust(color, **values)
        expected = color + (adjusted - color) * mask
        self.assert_input_is(material, 'TextureColor', expected)
        self.assertAlmostEqual(
            shader_node(material, 'csgo_character.vfx').inputs['g_flModelTintAmount'].default_value,
            0.0,
            places=6,
        )

    def test_replace_detail_is_adjusted_after_character_tint(self):
        color = np.array((0.7, 0.2, 0.4))
        detail = np.array((0.25, 0.8, 0.1))
        object_color = (0.1, 0.7, 0.3, 1.0)
        material = build(
            'csgo_character.vfx',
            ('g_tColor', 'g_tDetail', 'g_tTintMask'),
            ints={
                'F_ENABLE_ADJUSTMENTS': 1,
                'F_DETAIL_TEXTURE': 1,
                'F_TINT_MASK': 1,
            },
        )
        self.set_color(material, 'g_tColor', (*color, 1.0))
        self.set_color(material, 'g_tDetail', (*detail, 1.0))
        self.set_color(material, 'g_tTintMask', (1.0, 1.0, 0.0, 1.0))
        self.assertFalse(material.node_tree.nodes['g_tDetail'].image.colorspace_settings.is_data)
        self.assert_input_is(
            material,
            'TextureColor',
            self.vrf_adjust(detail),
            object_color=object_color,
        )

    def test_unsupported_and_runtime_textures_are_skipped(self):
        textures = (
            'g_tColor',
            'g_tNormal',
            'g_tAnisoGloss',
            'g_tSssMask',
            'g_tDiffuseFalloff',
            'g_tEyeAlbedo1',
            'g_tEyeMask1',
            'g_tIridescentThickness_Mask',
            'g_tAmbientOcclusion',
            'g_tBloodMask',
            'g_tColorBlood',
            'g_tNormalBlood',
            'g_tPatch0',
            'g_tPatch0Backing',
        )
        material = build(
            'csgo_character.vfx',
            textures,
            ints={'F_EYEBALLS': 1, 'F_IRIDESCENCE': 1, 'F_PATCHES': 1, 'F_SUPPORTS_DECALS': 1},
        )
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal'})
        self.assertEqual(source(shader.inputs['TextureColor']), ('g_tColor', 'Color'))
        self.assertEqual(source(shader.inputs['TextureRoughness']), ('g_tNormal', 'Alpha'))
        self.assertAlmostEqual(shader.inputs[CHARACTER_SSS_WEIGHT].default_value, 0.0, places=6)
        self.assertAlmostEqual(shader.inputs[CHARACTER_ANISOTROPY].default_value, 0.0, places=6)


if __name__ == '__main__':
    unittest.main()
