import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import ExtraMaterialParameters
from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.tests.blender_tests import test_source2_materials as _materials


def build_dynamic(textures=(), ints=None, floats=None, vectors=None, dynamic=()):
    material = bpy.data.materials.new('csgo_unlitgeneric.vfx')
    resource = _materials.FakeMaterial(
        'csgo_unlitgeneric.vfx', textures, ints=ints, floats=floats, vectors=vectors)
    resource._data['m_dynamicParams'] = [
        {'m_name': name, 'm_value': b'\x00'} for name in dynamic
    ]
    ShaderRegistry.source2_create_nodes(
        None, material, resource, {ExtraMaterialParameters.USE_OBJECT_TINT: False})
    return material


class CSGOUnlitGenericFollowupTests(unittest.TestCase):
    render_over_background = staticmethod(_materials.Source2MaterialTests.render_over_background)
    set_color = staticmethod(_materials.Source2MaterialTests.set_color)

    def setUp(self):
        render = bpy.context.scene.render
        self._frame = bpy.context.scene.frame_current
        self._fps = render.fps
        self._fps_base = render.fps_base

    def tearDown(self):
        scene = bpy.context.scene
        scene.render.fps = self._fps
        scene.render.fps_base = self._fps_base
        scene.frame_set(self._frame)
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    def assert_renders_as(self, material, background, value, *, vertex_color=None, uv_layers=None, atol=2e-3):
        actual = self.render_over_background(
            material, background, vertex_color=vertex_color, uv_layers=uv_layers)
        expected = _materials.Source2MaterialTests.render_constant(value, background)
        np.testing.assert_allclose(actual, expected, atol=atol)

    def render_socket(self, material, socket, uv):
        nodes, links = material.node_tree.nodes, material.node_tree.links
        emission = nodes.new('ShaderNodeEmission')
        links.new(socket, emission.inputs['Color'])
        surface = _materials.output_node(material).inputs['Surface']
        for link in list(surface.links):
            links.remove(link)
        links.new(emission.outputs[0], surface)
        return self.render_over_background(
            material, (0.0, 0.0, 0.0), uv_layers={'TEXCOORD': uv})

    def assert_socket_is(self, material, socket, uv, value, atol=2e-3):
        actual = self.render_socket(material, socket, uv)
        expected = _materials.Source2MaterialTests.render_constant(value, (0.0, 0.0, 0.0))
        np.testing.assert_allclose(actual, expected, atol=atol)

    def assert_time_driver(self, material):
        self.assertIn('Source 2 Time', material.node_tree.nodes)
        drivers = material.node_tree.animation_data.drivers
        self.assertEqual(len(drivers), 1)
        fcurve = drivers[0]
        self.assertEqual(fcurve.driver.expression, 'frame * fps_base / fps')
        variables = {
            variable.name: variable.targets[0].data_path
            for variable in fcurve.driver.variables
        }
        self.assertEqual(variables, {
            'fps': 'render.fps',
            'fps_base': 'render.fps_base',
        })

    def test_vertex_color_multiplies_rgb_alpha_and_opacity(self):
        background = np.array((0.2, 0.4, 0.1))
        color = np.array((0.5, 0.25, 0.75))
        texture_alpha = 0.8
        vertex_color = np.array((0.5, 1.0, 0.25, 0.4))
        opacity = 0.75
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_BLEND_MODE': 1, 'F_VERTEX_COLOR': 1},
            floats={'g_flOpacityScale': opacity},
        )
        self.set_color(material, 'g_tColor', (*color, texture_alpha))

        vertex_linear = _materials.Source2MaterialTests.linear(vertex_color)
        alpha = texture_alpha * vertex_linear[3] * opacity
        expected = background * (1.0 - alpha) + color * vertex_linear[:3] * alpha
        self.assert_renders_as(
            material, background, expected, vertex_color=vertex_color)
        raw_alpha = texture_alpha * vertex_color[3] * opacity
        raw = background * (1.0 - raw_alpha) + color * vertex_color[:3] * raw_alpha
        self.assertFalse(np.allclose(expected, raw, atol=2e-3))
        vertex = material.node_tree.nodes['COLOR']
        self.assertEqual(vertex.bl_idname, 'ShaderNodeVertexColor')
        self.assertEqual(vertex.layer_name, 'COLOR')

        without_flag = _materials.build(
            'csgo_unlitgeneric.vfx', ('g_tColor',), ints={'F_BLEND_MODE': 1})
        self.set_color(without_flag, 'g_tColor', (*color, texture_alpha))
        expected = background * (1.0 - texture_alpha) + color * texture_alpha
        self.assert_renders_as(
            without_flag, background, expected, vertex_color=vertex_color)

    def test_missing_color_is_zero_rgb_and_alpha(self):
        background = np.array((0.2, 0.4, 0.1))
        color = np.array((0.5, 0.25, 0.75))
        translucent = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_BLEND_MODE': 1, 'F_VERTEX_COLOR': 1},
        )
        self.set_color(translucent, 'g_tColor', (*color, 0.8))
        self.assert_renders_as(translucent, background, background)

        opaque = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_BLEND_MODE': 0, 'F_VERTEX_COLOR': 1},
        )
        self.set_color(opaque, 'g_tColor', (*color, 0.8))
        self.assert_renders_as(opaque, background, np.zeros(3))

    def test_vertex_alpha_multiplies_before_alpha_test(self):
        background = np.array((0.2, 0.4, 0.1))
        color = np.array((0.5, 0.25, 0.75))
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_BLEND_MODE': 2, 'F_VERTEX_COLOR': 1},
            floats={'g_flAlphaTestReference': 0.5},
        )
        self.set_color(material, 'g_tColor', (*color, 0.8))
        self.assert_renders_as(
            material, background, background, vertex_color=(1.0, 1.0, 1.0, 0.5))
        self.assert_renders_as(
            material, background, color, vertex_color=(1.0, 1.0, 1.0, 0.9))

    def test_scroll_uses_seconds_after_the_primary_transform(self):
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        scene.frame_set(48)
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            vectors={
                'g_vTexCoordScale': (2.0, 0.5, 0.0, 0.0),
                'g_vTexCoordOffset': (0.05, -0.1, 0.0, 0.0),
                'g_vTexCoordScrollSpeed': (0.1, 0.2, 0.0, 0.0),
            },
        )

        nodes = material.node_tree.nodes
        scroll = nodes['Texture Coordinate Scroll']
        self.assertEqual(scroll.operation, 'ADD')
        transform = _materials.linked_node(scroll.inputs[0])
        self.assertEqual(transform.bl_idname, 'ShaderNodeGroup')
        self.assertEqual(transform.node_tree.name, 'SourceIO UV Transform')
        self.assertEqual(_materials.linked_node(nodes['g_tColor'].inputs['Vector']), scroll)
        self.assert_time_driver(material)

        uv = (0.1, 0.2)
        # Source: (0.1, 0.8) -> transform (0.25, 0.3) -> scroll at t=2 (0.45, 0.7).
        # Blender stores the corresponding V as 1 - 0.7.
        socket = nodes['g_tColor'].inputs['Vector'].links[0].from_socket
        self.assert_socket_is(material, socket, uv, (0.45, 0.3, 0.0))

    def test_real_sequential_animation_is_a_row_major_atlas(self):
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        scene.frame_set(3)
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_TEXTURE_ANIMATION': 1, 'g_nNumAnimationCells': 4},
            floats={'g_flAnimationTimePerFrame': 0.05, 'g_flAnimationTimeOffset': 0.0},
            vectors={'g_vAnimationGrid': (2.0, 2.0, 0.0, 0.0)},
        )

        nodes = material.node_tree.nodes
        animation = nodes['Texture Animation UV']
        self.assertEqual(_materials.linked_node(nodes['g_tColor'].inputs['Vector']), animation)
        self.assert_time_driver(material)
        # t = 3 / 24 = 0.125 s, so frame 2 is column 0, Source row 1.
        socket = nodes['g_tColor'].inputs['Vector'].links[0].from_socket
        self.assert_socket_is(material, socket, (0.2, 0.6), (0.1, 0.3, 0.0))

    def test_random_and_scripted_animation_frame_selection(self):
        random_material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={
                'F_TEXTURE_ANIMATION': 1,
                'F_TEXTURE_ANIMATION_MODE': 1,
                'g_nNumAnimationCells': 7,
            },
            floats={'g_flAnimationFrame': 2.0},
            vectors={'g_vAnimationGrid': (4.0, 4.0, 0.0, 0.0)},
        )
        seed = np.float32(2.0)
        random_value = np.sin(seed * np.float32(12.9898 + 78.233)) * np.float32(43758.546875)
        frame = int(np.floor((random_value - np.floor(random_value)) * np.float32(7.0)))
        self.assertEqual(frame, 3)
        socket = random_material.node_tree.nodes['g_tColor'].inputs['Vector'].links[0].from_socket
        column, row = frame % 4, frame // 4
        self.assert_socket_is(
            random_material, socket, (0.2, 0.6),
            ((0.2 + column) / 4.0, (0.6 + 3.0 - row) / 4.0, 0.0))
        self.assertNotIn('Source 2 Time', random_material.node_tree.nodes)

        scripted_material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={
                'F_TEXTURE_ANIMATION': 1,
                'F_TEXTURE_ANIMATION_MODE': 2,
                'g_nNumAnimationCells': 2,
            },
            floats={'g_flAnimationFrame': 3.0},
            vectors={'g_vAnimationGrid': (4.0, 4.0, 0.0, 0.0)},
        )
        socket = scripted_material.node_tree.nodes['g_tColor'].inputs['Vector'].links[0].from_socket
        # Scripted mode uses frame 3 directly; it does not wrap it to cell 1.
        self.assert_socket_is(scripted_material, socket, (0.2, 0.6), (0.8, 0.9, 0.0))
        self.assertNotIn('Source 2 Time', scripted_material.node_tree.nodes)

    def test_gradient_uses_final_source_v_and_preserves_alpha(self):
        background = np.array((0.2, 0.4, 0.1))
        color = np.array((0.8, 0.6, 0.4))
        alpha = 0.4
        positions = np.array((0.1, 0.5, 0.9))
        stop_srgb = (
            np.array((0.1, 0.2, 0.3)),
            np.array((0.5, 0.25, 0.75)),
            np.array((1.0, 0.5, 0.0)),
        )
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor',),
            ints={'F_BLEND_MODE': 1, 'F_GRADIENTMODULATION': 1},
            vectors={
                'g_vGradientColorStop0': (*stop_srgb[0], 0.0),
                'g_vGradientColorStop1': (*stop_srgb[1], 0.0),
                'g_vGradientColorStop2': (*stop_srgb[2], 0.0),
                'g_vGradientModulation_StopPositions': (*positions, 0.0),
            },
        )
        self.set_color(material, 'g_tColor', (*color, alpha))

        source_v = 0.7
        first_weight = self.smoothstep(positions[0], positions[1], source_v)
        second_weight = self.smoothstep(positions[1], positions[2], source_v)
        stops = [_materials.Source2MaterialTests.linear(stop) for stop in stop_srgb]
        first = stops[0] + (stops[1] - stops[0]) * first_weight
        gradient = first + (stops[2] - first) * second_weight ** 2
        drawn = color * gradient
        expected = background * (1.0 - alpha) + drawn * alpha
        self.assert_renders_as(
            material, background, expected, uv_layers={'TEXCOORD': (0.2, 1.0 - source_v)})
        self.assertIn('Gradient Modulation', material.node_tree.nodes)

    def test_dynamic_followup_parameters_are_skipped(self):
        scroll = build_dynamic(
            ('g_tColor',),
            vectors={'g_vTexCoordScrollSpeed': (1.0, 0.0, 0.0, 0.0)},
            dynamic=('g_vTexCoordScrollSpeed',),
        )
        self.assertNotIn('Texture Coordinate Scroll', scroll.node_tree.nodes)
        self.assertNotIn('Source 2 Time', scroll.node_tree.nodes)

        animation = build_dynamic(
            ('g_tColor',),
            ints={'F_TEXTURE_ANIMATION': 1, 'F_TEXTURE_ANIMATION_MODE': 2},
            floats={'g_flAnimationFrame': 2.0},
            vectors={'g_vAnimationGrid': (2.0, 2.0, 0.0, 0.0)},
            dynamic=('g_flAnimationFrame',),
        )
        self.assertNotIn('Texture Animation UV', animation.node_tree.nodes)

        gradient = build_dynamic(
            ('g_tColor',),
            ints={'F_GRADIENTMODULATION': 1},
            dynamic=('g_vGradientColorStop0',),
        )
        self.assertNotIn('Gradient Modulation', gradient.node_tree.nodes)
        self.assertNotIn('g_vGradientColorStop0', gradient.node_tree.nodes)

    def test_two_texture_color_alpha_and_independent_uv_regression(self):
        background = np.array((0.2, 0.4, 0.1))
        material = _materials.build(
            'csgo_unlitgeneric.vfx',
            ('g_tColor', 'g_tColor2'),
            ints={'F_BLEND_MODE': 4, 'F_TWOTEXTURE': 1},
            vectors={
                'g_vTexCoordScrollSpeed': (0.1, 0.0, 0.0, 0.0),
                'g_vTex2CoordScale': (1.5, 1.5, 0.0, 0.0),
                'g_vTex2CoordOffset': (0.25, 0.0, 0.0, 0.0),
            },
        )
        self.set_color(material, 'g_tColor', (1.0, 0.5, 1.0, 0.5))
        self.set_color(material, 'g_tColor2', (0.5, 1.0, 1.0, 0.4))
        self.assert_renders_as(
            material, background, background + np.array((0.5, 0.5, 1.0)) * 0.2)

        nodes = material.node_tree.nodes
        self.assertEqual(
            _materials.linked_node(nodes['g_tColor'].inputs['Vector']).name,
            'Texture Coordinate Scroll',
        )
        second = _materials.linked_node(nodes['g_tColor2'].inputs['Vector'])
        self.assertEqual(second.bl_idname, 'ShaderNodeGroup')
        self.assertEqual(second.node_tree.name, 'SourceIO UV Transform')
        self.assertTrue(second.inputs['Scale About Center'].default_value)
        self.assertEqual(tuple(second.inputs['g_vTexCoordScale'].default_value), (1.5, 1.5, 0.0))

    @staticmethod
    def smoothstep(edge0, edge1, value):
        t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)


if __name__ == '__main__':
    unittest.main()
