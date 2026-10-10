import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import (
    ALPHA_CLIP_LABEL,
    MIX_A,
    MIX_B,
    MIX_FACTOR,
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


class Source2FoliageFollowupTests(unittest.TestCase):
    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    @staticmethod
    def linear(color):
        color = np.asarray(color)
        return np.where(color <= 0.04045, color / 12.92, ((color + 0.055) / 1.055) ** 2.4)

    def assert_model_tint_source(self, material, tint_node, tinted):
        combined = linked_node(tint_node.inputs[MIX_B])
        if combined.bl_idname == 'ShaderNodeMix':
            self.assertEqual(combined.blend_type, 'MULTIPLY')
            combined = linked_node(combined.inputs[MIX_A])
        self.assertEqual(combined.bl_idname, 'ShaderNodeGroup')
        self.assertEqual(combined.node_tree.name, 'SourceIO sRGB To Linear')
        source_node = linked_node(combined.inputs['Color'])
        if tinted:
            self.assertEqual(source_node.bl_idname, 'ShaderNodeVertexColor')
            self.assertEqual(source_node.layer_name, 'TINT')
        else:
            self.assertEqual(source_node.bl_idname, 'ShaderNodeObjectInfo')
        decode_nodes = [
            node for node in material.node_tree.nodes
            if node.bl_idname == 'ShaderNodeGroup' and node.node_tree.name == 'SourceIO sRGB To Linear'
        ]
        self.assertEqual(len(decode_nodes), 1)

    def assert_socket_is(self, material, socket, expected, *, tint_color=None, object_color=None):
        nodes, links = material.node_tree.nodes, material.node_tree.links
        emission = nodes.new('ShaderNodeEmission')
        links.new(socket, emission.inputs['Color'])
        links.new(emission.outputs[0], output_node(material).inputs['Surface'])
        rendered = material_tests.Source2MaterialTests.render_over_background(
            material,
            (0.0, 0.0, 0.0),
            tint_color=tint_color,
            object_color=object_color,
        )
        reference = material_tests.Source2MaterialTests.render_constant(expected, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(rendered, reference, atol=2e-3)

    @staticmethod
    def set_color(material, slot, rgba):
        material_tests.Source2MaterialTests.set_color(material, slot, rgba)

    def test_masked_tint_uses_red_channel_and_draw_call_tint(self):
        albedo = np.array((0.6, 0.4, 0.8))
        material_tint = np.array((0.5, 0.75, 1.0))
        draw_tint = np.array((0.25, 0.5, 0.75))
        object_tint = np.array((0.9, 0.2, 0.4))
        mask, amount = 0.25, 0.4
        material = build(
            'csgo_foliage.vfx',
            ('g_tColor', 'g_tNormal', 'g_tTintMask'),
            tinted=True,
            ints={'F_TINT_MASK': 1},
            floats={'g_flModelTintAmount': amount},
            vectors={'g_vColorTint': (*material_tint, 0.0)},
        )
        shader = shader_node(material, 'csgo_foliage.vfx')
        tint = linked_node(shader.inputs['Base Color'])
        self.assertEqual(tint.blend_type, 'MULTIPLY')
        self.assertEqual(source(tint.inputs[MIX_A]), ('g_tColor', 'Color'))
        factor = linked_node(tint.inputs[MIX_FACTOR])
        self.assertEqual(factor.bl_idname, 'ShaderNodeMath')
        self.assertEqual(factor.operation, 'MULTIPLY')
        self.assertEqual(source(factor.inputs[0]), ('g_tTintMask', 'Red'))
        self.assertAlmostEqual(factor.inputs[1].default_value, amount, places=6)
        self.assertTrue(material.node_tree.nodes['g_tTintMask'].image.colorspace_settings.is_data)

        combined = linked_node(tint.inputs[MIX_B])
        self.assertEqual(combined.blend_type, 'MULTIPLY')
        np.testing.assert_allclose(
            combined.inputs[MIX_B].default_value[:3],
            self.linear(material_tint),
            atol=1e-7,
        )
        self.assert_model_tint_source(material, tint, True)

        self.set_color(material, 'g_tColor', (*albedo, 0.7))
        self.set_color(material, 'g_tTintMask', (mask, 0.9, 0.1, 1.0))
        combined_tint = self.linear(draw_tint) * self.linear(material_tint)
        expected = albedo * (1.0 + mask * amount * (combined_tint - 1.0))
        self.assert_socket_is(
            material,
            shader.inputs['Base Color'].links[0].from_socket,
            expected,
            tint_color=(*draw_tint, 1.0),
            object_color=(*object_tint, 1.0),
        )

    def test_unmasked_defaults_use_object_info_and_skip_unused_textures(self):
        albedo = np.array((0.3, 0.6, 0.9))
        object_tint = np.array((0.75, 0.5, 0.25))
        draw_tint = np.array((0.1, 0.9, 0.2))
        material = build(
            'csgo_foliage.vfx',
            ('g_tColor', 'g_tNormal', 'g_tTintMask', 'g_tAmbientOcclusion', 'g_tNoiseMap'),
        )
        shader = shader_node(material, 'csgo_foliage.vfx')
        tint = linked_node(shader.inputs['Base Color'])
        self.assertEqual(tint.blend_type, 'MULTIPLY')
        self.assertFalse(tint.inputs[MIX_FACTOR].is_linked)
        self.assertAlmostEqual(tint.inputs[MIX_FACTOR].default_value, 1.0, places=6)
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal'})
        self.assert_model_tint_source(material, tint, False)

        self.set_color(material, 'g_tColor', (*albedo, 0.65))
        self.assert_socket_is(
            material,
            shader.inputs['Base Color'].links[0].from_socket,
            albedo * self.linear(object_tint),
            tint_color=(*draw_tint, 1.0),
            object_color=(*object_tint, 1.0),
        )

    def test_alpha_normals_roughness_and_albedo_transmission_are_preserved(self):
        albedo = np.array((0.4, 0.7, 0.2))
        draw_tint = np.array((0.5, 0.25, 0.75))
        mask = 0.6
        material = build(
            'csgo_foliage.vfx',
            ('g_tColor', 'g_tNormal', 'g_tTintMask', 'g_tTransmissiveColor',
             'g_tAmbientOcclusion', 'g_tNoiseMap'),
            tinted=True,
            ints={
                'F_ALPHA_TEST': 1,
                'F_TINT_MASK': 1,
                'F_TRANSMISSIVE_BACKFACE_NDOTL': 1,
                'F_USE_ALBEDO_FOR_TRANSMISSIVE': 1,
            },
            floats={'g_flAlphaTestReference': 0.5},
        )
        shader = shader_node(material, 'csgo_foliage.vfx')
        tint = linked_node(shader.inputs['Base Color'])
        alpha_clip = linked_node(shader.inputs['Alpha'])
        self.assertEqual(alpha_clip.label, ALPHA_CLIP_LABEL)
        self.assertAlmostEqual(alpha_clip.inputs[1].default_value, 0.5, places=6)
        normal = linked_node(shader.inputs['Normal'])
        self.assertEqual(normal.bl_idname, 'ShaderNodeNormalMap')
        self.assertEqual(source(normal.inputs['Color']), ('g_tNormal', 'Color'))
        self.assertEqual(source(shader.inputs['Roughness']), ('g_tNormal', 'Alpha'))
        textures = texture_nodes(material)
        self.assertTrue({'g_tColor', 'g_tNormal', 'g_tTintMask'}.issubset(textures))
        self.assertNotIn('g_tTransmissiveColor', textures)
        self.assertNotIn('g_tAmbientOcclusion', textures)
        self.assertNotIn('g_tNoiseMap', textures)

        add_shader = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(add_shader.bl_idname, 'ShaderNodeAddShader')
        translucent = linked_node(add_shader.inputs[1])
        transmission_mask = linked_node(translucent.inputs['Color'])
        self.assertEqual(transmission_mask.blend_type, 'MULTIPLY')
        self.assertEqual(transmission_mask.inputs[MIX_A].links[0].from_node, tint)
        self.assertEqual(
            transmission_mask.inputs[MIX_A].links[0].from_socket.name,
            tint.outputs[2].name,
        )
        transmission_alpha = transmission_mask.inputs[MIX_B].links[0]
        shader_alpha = shader.inputs['Alpha'].links[0]
        self.assertEqual(transmission_alpha.from_node, shader_alpha.from_node)
        self.assertEqual(transmission_alpha.from_socket.name, shader_alpha.from_socket.name)

        self.set_color(material, 'g_tColor', (*albedo, 0.75))
        self.set_color(material, 'g_tTintMask', (mask, 0.0, 1.0, 1.0))
        expected = albedo * (1.0 + mask * (self.linear(draw_tint) - 1.0))
        self.assert_socket_is(
            material,
            translucent.inputs['Color'].links[0].from_socket,
            expected,
            tint_color=(*draw_tint, 1.0),
        )

        self.set_color(material, 'g_tColor', (*albedo, 0.25))
        self.assert_socket_is(
            material,
            translucent.inputs['Color'].links[0].from_socket,
            (0.0, 0.0, 0.0),
            tint_color=(*draw_tint, 1.0),
        )

    def test_transmission_texture_remains_independent_of_tint(self):
        material = build(
            'csgo_foliage.vfx',
            ('g_tColor', 'g_tNormal', 'g_tTransmissiveColor'),
            ints={'F_TRANSMISSIVE_BACKFACE_NDOTL': 1},
        )
        translucent = linked_node(linked_node(output_node(material).inputs['Surface']).inputs[1])
        self.assertEqual(source(translucent.inputs['Color']), ('g_tTransmissiveColor', 'Color'))


if __name__ == '__main__':
    unittest.main()
