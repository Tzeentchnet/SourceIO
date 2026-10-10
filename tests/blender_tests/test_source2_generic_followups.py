import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import MIX_A, MIX_B
from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.generic import F0_PER_SPECULAR_LEVEL
from SourceIO.tests.blender_tests import test_source2_materials as material_tests
from SourceIO.tests.blender_tests.test_source2_materials import (
    build,
    linked_node,
    output_node,
    shader_node,
    source,
    texture_nodes,
)


class Source2GenericFollowupTests(unittest.TestCase):
    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    def test_unlit_uses_emission_and_skips_lit_inputs(self):
        textures = ('g_tColor', 'g_tNormal', 'g_tRoughness', 'g_tMetalnessReflectanceFresnel')
        lit = build('generic.vfx', textures, ints={'F_SPECULAR': 1})
        lit_shader = shader_node(lit, 'generic.vfx')
        self.assertEqual(linked_node(output_node(lit).inputs['Surface']), lit_shader)
        self.assertEqual(linked_node(lit_shader.inputs['Normal']).bl_idname, 'ShaderNodeNormalMap')
        self.assertEqual(texture_nodes(lit), set(textures))

        unlit = build('generic.vfx', textures, ints={'F_UNLIT': 1, 'F_SPECULAR': 1})
        surface = linked_node(output_node(unlit).inputs['Surface'])
        self.assertEqual(surface.bl_idname, 'ShaderNodeEmission')
        self.assertEqual(source(surface.inputs['Color']), ('g_tColor', 'Color'))
        self.assertFalse(any(node.bl_idname == 'ShaderNodeBsdfPrincipled' for node in unlit.node_tree.nodes))
        self.assertFalse(any(node.bl_idname == 'ShaderNodeNormalMap' for node in unlit.node_tree.nodes))
        self.assertEqual(texture_nodes(unlit), {'g_tColor'})

    def test_lit_alpha_modes_remain_on_the_principled_shader(self):
        for flag in ('F_ALPHA_TEST', 'F_TRANSLUCENT'):
            with self.subTest(flag=flag):
                material = build('generic.vfx', ('g_tColor',), ints={flag: 1})
                shader = shader_node(material, 'generic.vfx')
                self.assertEqual(linked_node(output_node(material).inputs['Surface']), shader)
                self.assertEqual(source(shader.inputs['Alpha']), ('g_tColor', 'Alpha'))
                self.assertEqual(material.surface_render_method, 'DITHERED')

    def test_unlit_translucency_uses_emission_alpha_and_opacity_scale(self):
        material = build(
            'generic.vfx',
            ('g_tColor',),
            ints={'F_UNLIT': 1, 'F_TRANSLUCENT': 1},
            floats={'g_flOpacityScale': 0.666},
        )
        surface = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(surface.bl_idname, 'ShaderNodeMixShader')
        self.assertEqual(material.surface_render_method, 'BLENDED')
        opacity = linked_node(surface.inputs[0])
        self.assertEqual(opacity.bl_idname, 'ShaderNodeMath')
        self.assertEqual(opacity.operation, 'MULTIPLY')
        self.assertEqual(source(opacity.inputs[0]), ('g_tColor', 'Alpha'))
        self.assertAlmostEqual(opacity.inputs[1].default_value, 0.666, places=6)
        self.assertEqual(linked_node(surface.inputs[1]).bl_idname, 'ShaderNodeBsdfTransparent')
        emission = linked_node(surface.inputs[2])
        self.assertEqual(emission.bl_idname, 'ShaderNodeEmission')
        self.assertEqual(source(emission.inputs['Color']), ('g_tColor', 'Color'))

    def test_unlit_additive_only_uses_texture_alpha_when_opacity_is_enabled(self):
        opaque_additive = build(
            'generic.vfx',
            ('g_tColor',),
            ints={'F_UNLIT': 1, 'F_ADDITIVE_BLEND': 1},
        )
        surface = linked_node(output_node(opaque_additive).inputs['Surface'])
        weighted = linked_node(linked_node(surface.inputs[0]).inputs['Color'])
        opacity = linked_node(weighted.inputs['Scale'])
        self.assertEqual(opacity.bl_idname, 'ShaderNodeValue')
        self.assertEqual(opacity.outputs[0].default_value, 1.0)

        translucent_additive = build(
            'generic.vfx',
            ('g_tColor',),
            ints={'F_UNLIT': 1, 'F_TRANSLUCENT': 1, 'F_ADDITIVE_BLEND': 1},
            floats={'g_flOpacityScale': 0.5},
        )
        surface = linked_node(output_node(translucent_additive).inputs['Surface'])
        weighted = linked_node(linked_node(surface.inputs[0]).inputs['Color'])
        opacity = linked_node(weighted.inputs['Scale'])
        self.assertEqual(opacity.operation, 'MULTIPLY')
        self.assertEqual(source(opacity.inputs[0]), ('g_tColor', 'Alpha'))
        self.assertAlmostEqual(opacity.inputs[1].default_value, 0.5, places=6)

    def test_unlit_tint_and_self_illum_are_added_before_blending(self):
        albedo = np.array((0.2, 0.3, 0.1))
        color_tint = np.array((0.5, 0.25, 0.75))
        mask = np.array((0.2, 0.5, 0.8))
        self_illum_tint = np.array((1.0, 0.5, 0.25))
        scale = 2.0
        material = build(
            'generic.vfx',
            ('g_tColor', 'g_tSelfIllumMask'),
            ints={'F_UNLIT': 1, 'F_SELF_ILLUM': 1},
            floats={'g_flSelfIllumScale': scale},
            vectors={
                'g_vColorTint': (*color_tint, 0.0),
                'g_vSelfIllumTint': (*self_illum_tint, 0.0),
            },
        )
        emission = linked_node(output_node(material).inputs['Surface'])
        combined = linked_node(emission.inputs['Color'])
        self.assertEqual(combined.blend_type, 'ADD')
        tinted_albedo = linked_node(combined.inputs[MIX_A])
        self.assertEqual(source(tinted_albedo.inputs[MIX_A]), ('g_tColor', 'Color'))
        illumination = linked_node(combined.inputs[MIX_B])
        self.assertEqual(illumination.operation, 'SCALE')
        self.assertAlmostEqual(illumination.inputs['Scale'].default_value, scale, places=6)
        illumination_tint = linked_node(illumination.inputs[0])
        masked_albedo = linked_node(illumination_tint.inputs[MIX_B])
        self.assertEqual(source(masked_albedo.inputs[MIX_A]), ('g_tSelfIllumMask', 'Color'))
        self.assertEqual(linked_node(masked_albedo.inputs[MIX_B]), tinted_albedo)

        material_tests.Source2MaterialTests.set_color(material, 'g_tColor', (*albedo, 1.0))
        material_tests.Source2MaterialTests.set_color(material, 'g_tSelfIllumMask', (*mask, 1.0))
        expected = albedo * color_tint
        expected += expected * mask * self_illum_tint * scale
        rendered = material_tests.Source2MaterialTests.render_over_background(material, (0.0, 0.0, 0.0))
        reference = material_tests.Source2MaterialTests.render_constant(expected, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(rendered, reference, atol=2e-3)

    def test_reflectance_is_f0_with_the_compiled_shader_defaults(self):
        self.assertEqual(F0_PER_SPECULAR_LEVEL, 0.08)
        material = build('generic.vfx', ('g_tColor',), ints={'F_SPECULAR': 1})
        shader = shader_node(material, 'generic.vfx')
        self.assertAlmostEqual(shader.inputs['Roughness'].default_value, 0.5, places=6)
        self.assertAlmostEqual(shader.inputs['Metallic'].default_value, 0.0, places=6)
        self.assertAlmostEqual(shader.inputs['Specular IOR Level'].default_value, 0.1 / 0.08, places=6)

        material = build(
            'generic.vfx',
            ('g_tColor', 'g_tMetalnessReflectanceFresnel'),
            ints={'F_SPECULAR': 1},
            vectors={'g_vReflectanceRange': (0.02, 0.1, 0.0, 0.0)},
        )
        specular = linked_node(shader_node(material, 'generic.vfx').inputs['Specular IOR Level'])
        self.assertEqual(source(specular.inputs[0]), ('g_tMetalnessReflectanceFresnel', 'Green'))
        self.assertAlmostEqual(specular.inputs[1].default_value, (0.1 - 0.02) / 0.08, places=6)
        self.assertAlmostEqual(specular.inputs[2].default_value, 0.02 / 0.08, places=6)


if __name__ == '__main__':
    unittest.main()
