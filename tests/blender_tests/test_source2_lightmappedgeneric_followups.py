import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import MIX_B
from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_lightmappedgeneric import (
    BEVEL_BLEND_SHARPNESS_DEFAULT,
    BEVEL_BLEND_WIDTH_DEFAULT,
    LAYER_BORDER_SOFTNESS_DEFAULT,
    LAYER_BORDER_STRENGTH_DEFAULT,
)
from SourceIO.library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from SourceIO.tests.blender_tests import test_source2_materials as material_tests
from SourceIO.tests.blender_tests.test_source2_materials import (
    build,
    linked_node,
    output_node,
    shader_node,
    texture_nodes,
)


class Source2LightmappedGenericFollowupTests(unittest.TestCase):
    def setUp(self):
        self.material_tests = material_tests.Source2MaterialTests()

    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    @staticmethod
    def smoothstep(edge0, edge1, value):
        factor = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
        return factor * factor * (3.0 - 2.0 * factor)

    @staticmethod
    def linear(color):
        color = np.asarray(color)
        return np.where(color <= 0.04045, color / 12.92, ((color + 0.055) / 1.055) ** 2.4)

    def assert_input_is(self, material, socket_name, value, uv_layers=None, object_color=None, tint_color=None):
        shader = shader_node(material, 'csgo_lightmappedgeneric.vfx')
        if object_color is None and tint_color is None:
            rendered = self.material_tests.render_input(
                material, socket_name, 'csgo_lightmappedgeneric.vfx', uv_layers=uv_layers)
        else:
            rendered = self.material_tests.render_link(
                material, shader.inputs[socket_name], uv_layers=uv_layers,
                object_color=object_color, tint_color=tint_color)
        reference = self.material_tests.render_constant(value, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(rendered, reference, atol=2e-3)

    def render_vector(self, material, socket, uv_layers=None):
        nodes, links = material.node_tree.nodes, material.node_tree.links
        encode = nodes.new('ShaderNodeVectorMath')
        encode.operation = 'MULTIPLY_ADD'
        encode.inputs[1].default_value = encode.inputs[2].default_value = (0.5, 0.5, 0.5)
        links.new(socket, encode.inputs[0])
        emission = nodes.new('ShaderNodeEmission')
        links.new(encode.outputs[0], emission.inputs['Color'])
        links.new(emission.outputs[0], output_node(material).inputs['Surface'])
        return self.material_tests.render_over_background(
            material, (0.0, 0.0, 0.0), size=8, uv_layers=uv_layers)

    def test_layer_details_follow_layer1_albedo_transform(self):
        textures = ('g_tColor', 'g_tLayer1Detail', 'g_tLayer2Color', 'g_tLayer2Detail')
        material = build(
            'csgo_lightmappedgeneric.vfx',
            textures,
            ints={'F_DETAILTEXTURE': 2},
            vectors={
                'g_vLayer1TexCoordScale': (1.25, 0.75, 0.0, 0.0),
                'g_vLayer2TexCoordScale': (3.0, 2.0, 0.0, 0.0),
                'g_vLayer1DetailScale': (8.0, 2.0, 0.0, 0.0),
                'g_vLayer2DetailScale': (0.5, 4.0, 0.0, 0.0),
            },
        )
        nodes = material.node_tree.nodes
        layer1_uv = linked_node(nodes['g_tColor'].inputs['Vector'])
        layer2_uv = linked_node(nodes['g_tLayer2Color'].inputs['Vector'])
        detail1_uv = linked_node(nodes['g_tLayer1Detail'].inputs['Vector'])
        detail2_uv = linked_node(nodes['g_tLayer2Detail'].inputs['Vector'])

        self.assertEqual(linked_node(detail1_uv.inputs[0]), layer1_uv)
        self.assertEqual(linked_node(detail2_uv.inputs[0]), layer1_uv)
        self.assertIsNot(linked_node(detail2_uv.inputs[0]), layer2_uv)
        self.assertEqual(tuple(detail1_uv.inputs['g_vTexCoordScale'].default_value), (8.0, 2.0, 0.0))
        self.assertEqual(tuple(detail2_uv.inputs['g_vTexCoordScale'].default_value), (0.5, 4.0, 0.0))

    def test_rotated_normal_rotates_tangent_components(self):
        normal = np.array((0.6, -0.2, np.sqrt(0.6)))
        encoded = normal * 0.5 + 0.5
        for rotation in (90.0, -30.0):
            with self.subTest(rotation=rotation):
                material = build(
                    'csgo_lightmappedgeneric.vfx',
                    ('g_tColor', 'g_tLayer1NormalRoughness'),
                    floats={'g_flLayer1NormalTexCoordRotation': rotation},
                )
                rotation_node = material.node_tree.nodes['Layer 1 Normal Tangent Frame']
                self.assertEqual(rotation_node.node_tree.name, 'SourceIO Lightmapped Normal Tangent Rotation')
                self.assertAlmostEqual(rotation_node.inputs['Rotation'].default_value, rotation, places=6)
                self.material_tests.set_color(
                    material, 'g_tLayer1NormalRoughness', (*encoded, 0.35))

                angle = np.radians(rotation)
                expected = np.array((
                    np.cos(angle) * normal[0] - np.sin(angle) * normal[1],
                    np.sin(angle) * normal[0] + np.cos(angle) * normal[1],
                    normal[2],
                )) * 0.5 + 0.5
                self.assert_input_is(material, 'TextureNormal0', expected)

    def test_layer_border_formula_and_compiled_defaults(self):
        color = np.array((0.2, 0.4, 0.8))
        tint = np.array((0.5, 0.25, 1.0))
        mask, weight, offset = 0.5, 0.4, 0.1
        material = build(
            'csgo_lightmappedgeneric.vfx',
            ('g_tColor', 'g_tLayer2Color', 'g_tBlendModulation'),
            ints={'F_FANCY_BLENDING': 2},
            floats={'g_flLayerBorderOffset': offset},
            vectors={'g_vLayerBorderTint': (*tint, 0.0)},
        )
        self.material_tests.set_color(material, 'g_tColor', (*color, 1.0))
        self.material_tests.set_color(material, 'g_tBlendModulation', (0.0, mask, 0.0, 1.0))

        nodes = material.node_tree.nodes
        self.assertAlmostEqual(
            nodes['g_flLayerBorderStrength'].outputs[0].default_value,
            LAYER_BORDER_STRENGTH_DEFAULT,
            places=6,
        )
        self.assertAlmostEqual(
            nodes['g_flLayerBorderSoftness'].outputs[0].default_value,
            LAYER_BORDER_SOFTNESS_DEFAULT,
            places=6,
        )
        position = self.smoothstep(
            max(0.0, mask - LAYER_BORDER_SOFTNESS_DEFAULT),
            min(1.0, mask + LAYER_BORDER_SOFTNESS_DEFAULT),
            np.clip(weight + offset, 0.0, 1.0),
        )
        band = LAYER_BORDER_STRENGTH_DEFAULT * (1.0 - abs(2.0 * position - 1.0))
        expected = color * (1.0 + band * (self.linear(tint) - 1.0))
        self.assert_input_is(
            material, 'TextureColor0', expected, uv_layers={'TEXCOORD_4': (weight, 0.0)})

    def test_detail_blend_modes(self):
        color = np.array((0.2, 0.4, 0.8))
        detail = np.array((0.25, 0.5, 0.75))
        tint = np.array((0.8, 0.6, 0.4))
        strength = 0.3
        material = build(
            'csgo_lightmappedgeneric.vfx',
            ('g_tColor', 'g_tLayer1Detail'),
            ints={'F_DETAILTEXTURE': 1, 'F_DETAILBLENDMODE': 0},
            vectors={'g_vLayer1DetailTintAndBlend': (*tint, strength)},
        )
        self.material_tests.set_color(material, 'g_tColor', (*color, 1.0))
        self.material_tests.set_color(material, 'g_tLayer1Detail', (*detail, 0.9))
        self.assert_input_is(material, 'TextureColor0', color * (1.0 + strength * (2.0 * detail * tint - 1.0)))

        color_alpha = 0.8
        detail_red, detail_alpha, tint_red = 0.4, 0.9, 0.5
        model_alpha, model_amount = 0.5, 0.5
        strength = 0.25
        material = build(
            'csgo_lightmappedgeneric.vfx',
            ('g_tColor', 'g_tLayer1Detail', 'g_tLayer2Color', 'g_tLayer2Detail'),
            tinted=True,
            ints={'F_DETAILTEXTURE': 2, 'F_DETAILBLENDMODE': 1},
            floats={'g_flModelTintAmount': model_amount},
            vectors={'g_vLayer1DetailTintAndBlend': (tint_red, 1.0, 1.0, strength)},
        )
        self.material_tests.set_color(material, 'g_tColor', (*color, color_alpha))
        self.material_tests.set_color(
            material, 'g_tLayer1Detail', (detail_red, 0.1, 0.2, detail_alpha))
        selector = color_alpha * (1.0 + (model_alpha - 1.0) * model_amount)
        pattern = detail_red * tint_red * (1.0 - selector) + detail_alpha * selector
        expected = color * (1.0 + strength * (2.0 * pattern - 1.0))
        self.assert_input_is(
            material, 'TextureColor0', expected, tint_color=(1.0, 1.0, 1.0, model_alpha))

    def test_bevel_adds_absolute_derivative_normal_at_zero_strength(self):
        textures = (
            'g_tColor',
            'g_tLayer1NormalRoughness',
            'g_tLayer2Color',
            'g_tLayer2NormalRoughness',
            'g_tBlendModulation',
        )
        material = build(
            'csgo_lightmappedgeneric.vfx',
            textures,
            ints={'F_FANCY_BLENDING': 2},
            floats={'g_flBevelBlendStrength': 1.25},
        )
        nodes = material.node_tree.nodes
        surface = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(surface.bl_idname, 'ShaderNodeBsdfPrincipled')
        self.assertEqual(surface.name, 'csgo_lightmappedgeneric.vfx Bevel')
        self.assertAlmostEqual(
            nodes['g_flBevelBlendWidth'].outputs[0].default_value,
            BEVEL_BLEND_WIDTH_DEFAULT,
            places=6,
        )
        self.assertAlmostEqual(
            nodes['g_flBevelBlendSharpness'].outputs[0].default_value,
            BEVEL_BLEND_SHARPNESS_DEFAULT,
            places=6,
        )
        bump = nodes['Layer Bevel Normal']
        distance = linked_node(bump.inputs['Distance'])
        self.assertEqual(distance.operation, 'MULTIPLY')
        self.assertAlmostEqual(distance.inputs[1].default_value, SOURCE2_HAMMER_UNIT_TO_METERS, places=8)
        weighted = nodes['Layer Bevel Weighted Normal']
        self.assertEqual(weighted.operation, 'SCALE')
        self.assertEqual(linked_node(weighted.inputs[0]), bump)
        self.assertEqual(linked_node(weighted.inputs['Scale']), nodes['Layer Bevel Seam Weight'])
        self.assertEqual(nodes['Layer Bevel Blend'].operation, 'NORMALIZE')

        zero_strength = build(
            'csgo_lightmappedgeneric.vfx',
            textures,
            ints={'F_FANCY_BLENDING': 2},
            floats={'g_flBevelBlendStrength': 0.0},
        )
        zero_nodes = zero_strength.node_tree.nodes
        self.assertEqual(
            linked_node(output_node(zero_strength).inputs['Surface']).name,
            'csgo_lightmappedgeneric.vfx Bevel',
        )
        zero_distance = linked_node(zero_nodes['Layer Bevel Normal'].inputs['Distance'])
        self.assertEqual(zero_distance.operation, 'MULTIPLY')
        self.assertEqual(
            linked_node(zero_distance.inputs[0]).outputs[0].default_value,
            0.0,
        )

        neutral = build(
            'csgo_lightmappedgeneric.vfx',
            textures,
            ints={'F_FANCY_BLENDING': 2},
            floats={'g_flBevelBlendStrength': 1.0, 'g_flBevelBlendSharpness': 0.0},
        )
        self.assertEqual(
            linked_node(output_node(neutral).inputs['Surface']),
            shader_node(neutral, 'csgo_lightmappedgeneric.vfx'),
        )
        legacy_blending = build(
            'csgo_lightmappedgeneric.vfx',
            textures,
            ints={'F_FANCY_BLENDING': 1},
            floats={'g_flBevelBlendStrength': 1.0},
        )
        self.assertEqual(
            linked_node(output_node(legacy_blending).inputs['Surface']),
            shader_node(legacy_blending, 'csgo_lightmappedgeneric.vfx'),
        )

        normal = np.array((0.6, 0.0, 0.8))
        encoded = normal * 0.5 + 0.5
        self.material_tests.set_color(zero_strength, 'g_tLayer1NormalRoughness', (*encoded, 0.5))
        self.material_tests.set_color(zero_strength, 'g_tLayer2NormalRoughness', (*encoded, 0.5))
        self.material_tests.set_color(zero_strength, 'g_tBlendModulation', (0.0, 0.5, 0.0, 1.0))
        zero_shader = zero_nodes['csgo_lightmappedgeneric.vfx Bevel']
        zero_actual = zero_shader.inputs['Normal'].links[0].from_socket
        zero_mapped = zero_nodes['Layer Normal Map'].outputs['Normal']
        zero_bump = zero_nodes['Layer Bevel Normal']
        zero_weighted = zero_nodes.new('ShaderNodeVectorMath')
        zero_weighted.operation = 'SCALE'
        zero_strength.node_tree.links.new(zero_bump.outputs['Normal'], zero_weighted.inputs[0])
        zero_strength.node_tree.links.new(
            zero_nodes['Layer Bevel Seam Weight'].outputs[0], zero_weighted.inputs['Scale'])
        zero_add = zero_nodes.new('ShaderNodeVectorMath')
        zero_add.operation = 'ADD'
        zero_strength.node_tree.links.new(zero_mapped, zero_add.inputs[0])
        zero_strength.node_tree.links.new(zero_weighted.outputs[0], zero_add.inputs[1])
        zero_expected = zero_nodes.new('ShaderNodeVectorMath')
        zero_expected.operation = 'NORMALIZE'
        zero_strength.node_tree.links.new(zero_add.outputs[0], zero_expected.inputs[0])

        uv_layers = {'TEXCOORD_4': (0.5, 0.0)}
        np.testing.assert_allclose(
            self.render_vector(zero_strength, zero_actual, uv_layers),
            self.render_vector(zero_strength, zero_expected.outputs[0], uv_layers),
            atol=2e-3,
        )
        self.assertGreater(
            np.linalg.norm(
                self.render_vector(zero_strength, zero_actual, uv_layers) -
                self.render_vector(zero_strength, zero_mapped, uv_layers)
            ),
            1e-2,
        )
        edge_uv = {'TEXCOORD_4': (0.0, 0.0)}
        np.testing.assert_allclose(
            self.render_vector(zero_strength, zero_actual, edge_uv),
            self.render_vector(zero_strength, zero_mapped, edge_uv),
            atol=2e-3,
        )

        position = nodes.new('ShaderNodeSeparateXYZ')
        material.node_tree.links.remove(bump.inputs['Height'].links[0])
        material.node_tree.links.new(
            nodes['Layer Bevel Geometry'].outputs['Position'], position.inputs[0])
        material.node_tree.links.new(position.outputs['X'], bump.inputs['Height'])
        bump.inputs['Distance'].default_value = 0.4

        expected_weighted = nodes.new('ShaderNodeVectorMath')
        expected_weighted.operation = 'SCALE'
        material.node_tree.links.new(bump.outputs['Normal'], expected_weighted.inputs[0])
        material.node_tree.links.new(
            nodes['Layer Bevel Seam Weight'].outputs[0], expected_weighted.inputs['Scale'])
        expected_add = nodes.new('ShaderNodeVectorMath')
        expected_add.operation = 'ADD'
        mapped = nodes['Layer Normal Map'].outputs['Normal']
        material.node_tree.links.new(mapped, expected_add.inputs[0])
        material.node_tree.links.new(expected_weighted.outputs[0], expected_add.inputs[1])
        expected = nodes.new('ShaderNodeVectorMath')
        expected.operation = 'NORMALIZE'
        material.node_tree.links.new(expected_add.outputs[0], expected.inputs[0])

        bevel_shader = material.node_tree.nodes['csgo_lightmappedgeneric.vfx Bevel']
        actual = bevel_shader.inputs['Normal'].links[0].from_socket
        actual_render = self.render_vector(material, actual, uv_layers)
        expected_render = self.render_vector(material, expected.outputs[0], uv_layers)
        mapped_render = self.render_vector(material, mapped, uv_layers)
        np.testing.assert_allclose(actual_render, expected_render, atol=2e-3)
        self.assertGreater(np.linalg.norm(actual_render - mapped_render), 1e-2)

    def test_obsolete_generic_detail_inputs_are_disabled_and_skipped(self):
        material = build(
            'csgo_lightmappedgeneric.vfx',
            ('g_tColor', 'g_tDetail', 'g_tDetailMask', 'g_tNormalDetail'),
            ints={'F_DETAIL_TEXTURE': 4},
        )
        shader = shader_node(material, 'csgo_lightmappedgeneric.vfx')
        self.assertEqual(shader.inputs['F_DETAIL_TEXTURE'].default_value, 0.0)
        self.assertFalse(shader.inputs['F_DETAIL_TEXTURE'].is_linked)
        self.assertFalse(shader.inputs['TextureDetail0'].is_linked)
        self.assertFalse(shader.inputs['TextureDetail1'].is_linked)
        self.assertEqual(texture_nodes(material), {'g_tColor'})

    def test_model_tint_and_layer_blend_regressions(self):
        for tinted in (False, True):
            with self.subTest(tinted=tinted):
                material = build(
                    'csgo_lightmappedgeneric.vfx',
                    ('g_tColor',),
                    tinted=tinted,
                    floats={'g_flModelTintAmount': 0.35},
                )
                tint_mix = linked_node(
                    shader_node(material, 'csgo_lightmappedgeneric.vfx').inputs['ModelTint'])
                self.assertEqual(tint_mix.blend_type, 'MIX')
                self.assertAlmostEqual(tint_mix.inputs[0].default_value, 0.35, places=6)
                decoded = linked_node(tint_mix.inputs[MIX_B])
                self.assertEqual(decoded.node_tree.name, 'SourceIO sRGB To Linear')
                source = linked_node(decoded.inputs['Color'])
                self.assertEqual(source.bl_idname, 'ShaderNodeVertexColor' if tinted else 'ShaderNodeObjectInfo')
                if tinted:
                    self.assertEqual(source.layer_name, 'TINT')

        weight, mask, softness = 0.55, 0.5, 0.15
        factor = self.smoothstep(mask - softness, mask + softness, weight)
        rendered = self.material_tests.lit_layers(
            {'F_FANCY_BLENDING': 2},
            {'g_flBlendSoftness': softness},
            (0.0, mask, 0.0, 1.0),
            weight,
        )
        reference = self.material_tests.lit_layers(grey=factor)
        np.testing.assert_allclose(rendered, reference, atol=5e-3)


if __name__ == '__main__':
    unittest.main()
