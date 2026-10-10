import unittest
from unittest import mock

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase
from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_environment import color_matrix
from SourceIO.tests.blender_tests import test_source2_materials as materials


class StaticOverlayFollowupTests(unittest.TestCase):
    def tearDown(self):
        materials.Source2MaterialTests.tearDown(self)

    @staticmethod
    def render_over_background(material, background, **kwargs):
        return materials.Source2MaterialTests.render_over_background(material, background, **kwargs)

    @staticmethod
    def render_constant(value, background):
        return materials.Source2MaterialTests.render_constant(value, background)

    def assert_renders_as(self, material, background, value, **kwargs):
        np.testing.assert_allclose(
            self.render_over_background(material, background, **kwargs),
            self.render_constant(value, background),
            atol=2e-3,
        )

    @staticmethod
    def set_color(material, slot, rgba):
        materials.Source2MaterialTests.set_color(material, slot, rgba)

    def render_input(self, material, input_socket):
        source_socket = input_socket.links[0].from_socket
        emission = material.node_tree.nodes.new("ShaderNodeEmission")
        material.node_tree.links.new(source_socket, emission.inputs["Color"])
        material.node_tree.links.new(
            emission.outputs[0],
            materials.output_node(material).inputs["Surface"],
        )
        return self.render_over_background(material, (0.0, 0.0, 0.0))

    def render_socket(self, material, socket, uv):
        emission = material.node_tree.nodes.new("ShaderNodeEmission")
        material.node_tree.links.new(socket, emission.inputs["Color"])
        surface = materials.output_node(material).inputs["Surface"]
        for link in list(surface.links):
            material.node_tree.links.remove(link)
        material.node_tree.links.new(emission.outputs[0], surface)
        return self.render_over_background(
            material,
            (0.0, 0.0, 0.0),
            uv_layers={"TEXCOORD": uv},
        )

    def assert_socket_is(self, material, socket, uv, value):
        np.testing.assert_allclose(
            self.render_socket(material, socket, uv),
            self.render_constant(value, (0.0, 0.0, 0.0)),
            atol=2e-3,
        )

    def assert_time_driver(self, material):
        self.assertIn("Source 2 Time", material.node_tree.nodes)
        drivers = material.node_tree.animation_data.drivers
        self.assertEqual(len(drivers), 1)
        driver = drivers[0].driver
        self.assertEqual(driver.expression, "frame * fps_base / fps")
        variables = {
            variable.name: variable.targets[0].data_path
            for variable in driver.variables
        }
        self.assertEqual(variables, {
            "fps": "render.fps",
            "fps_base": "render.fps_base",
        })

    def test_material_reference_uses_layer_textures_and_height_opacity(self):
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor1", "g_tHeight1", "g_tNormal1", "g_tSelfIllumMask"),
            ints={"F_MATERIAL_REFERENCE": 1, "F_LIT": 1, "F_BLEND_MODE": 1},
        )
        shader = materials.shader_node(material, "csgo_static_overlay.vfx")

        self.assertEqual(materials.source(shader.inputs["TextureColor"]), ("g_tColor1", "Color"))
        self.assertEqual(materials.source(shader.inputs["TextureNormal"]), ("g_tNormal1", "Color"))
        self.assertEqual(materials.source(shader.inputs["TextureRoughness"]), ("g_tNormal1", "Alpha"))
        self.assertEqual(materials.source(shader.inputs["TextureMetalness"]), ("g_tHeight1", "Alpha"))
        self.assertEqual(materials.source(shader.inputs["TextureTintMask"]), ("g_tHeight1", "Green"))
        self.assertEqual(
            materials.texture_nodes(material),
            {"g_tColor1", "g_tHeight1", "g_tNormal1", "g_tSelfIllumMask"},
        )
        self.assertTrue(material.node_tree.nodes["g_tHeight1"].image.colorspace_settings.is_data)
        self.assertTrue(material.node_tree.nodes["g_tNormal1"].image.colorspace_settings.is_data)

        height, translucency, softness = 0.8, 1.0, 0.01
        self.set_color(material, "g_tHeight1", (height, 1.0, 0.0, 0.0))
        self.set_color(material, "g_tSelfIllumMask", (0.0, 0.0, 0.0, translucency))
        blend = materials.linked_node(materials.output_node(material).inputs["Surface"])
        self.assertEqual(blend.bl_idname, "ShaderNodeMixShader")
        self.assertEqual(blend.inputs[0].links[0].from_node.name, "Material Reference Opacity")

        translucency_edge = 1.0 - 2.0 * translucency + softness
        height_edge = ((height - 0.5) - 0.5) - softness
        edge = max(translucency_edge, height_edge) - softness
        translucency_weight = max(translucency_edge - edge, 0.0) + 0.001
        height_weight = max(height_edge - edge, 0.0) + 0.001
        expected = height_weight / (translucency_weight + height_weight)
        np.testing.assert_allclose(
            self.render_input(material, blend.inputs[0]),
            self.render_constant((expected,) * 3, (0.0, 0.0, 0.0)),
            atol=2e-3,
        )

    def test_material_reference_applies_layer_color_tint_through_height_green(self):
        color = np.array((0.2, 0.5, 0.8))
        tint = (0.1, 0.8, 0.3, 0.0)
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor1", "g_tHeight1", "g_tNormal1", "g_tSelfIllumMask"),
            ints={"F_MATERIAL_REFERENCE": 1, "F_LIT": 1, "F_BLEND_MODE": 1},
            vectors={"g_vTextureColorTint1": tint},
        )
        self.set_color(material, "g_tColor1", (*color, 1.0))
        self.set_color(material, "g_tHeight1", (0.5, 1.0, 0.0, 0.0))
        shader = materials.shader_node(material, "csgo_static_overlay.vfx")
        expected = np.append(color, 1.0) @ color_matrix(1.0, 1.0, 1.0, (1.0, 1.0, 1.0), tint)
        expected = np.clip(expected[:3], 0.0, 1.0)
        np.testing.assert_allclose(
            self.render_input(material, shader.inputs["TextureColor"]),
            self.render_constant(expected, (0.0, 0.0, 0.0)),
            atol=2e-3,
        )

    def test_texture_color_correction_tint_formula_and_neutral_defaults(self):
        background = np.array((0.1, 0.2, 0.3))
        color = np.array((0.25, 0.5, 0.75))
        average = (0.4, 0.3, 0.2)
        contrast, saturation, brightness = 1.3, 0.65, 1.1
        tint = (0.15, 0.75, 0.35, 0.0)
        with mock.patch.object(Source2ShaderBase, "_texture_average_color", return_value=average):
            material = materials.build(
                "csgo_static_overlay.vfx",
                ("g_tColor",),
                floats={
                    "g_fTextureColorContrast": contrast,
                    "g_fTextureColorSaturation": saturation,
                    "g_fTextureColorBrightness": brightness,
                },
                vectors={"g_vTextureColorCorrectionTint": tint},
            )
        self.set_color(material, "g_tColor", (*color, 1.0))
        expected = np.append(color, 1.0) @ color_matrix(
            contrast, saturation, brightness, average, tint)
        self.assert_renders_as(material, background, np.maximum(expected[:3], 0.0))

        for neutral in ((1.0, 1.0, 1.0, 0.0), (0.35, 0.35, 0.35, 0.0)):
            material = materials.build(
                "csgo_static_overlay.vfx",
                ("g_tColor",),
                vectors={"g_vTextureColorCorrectionTint": neutral},
            )
            self.set_color(material, "g_tColor", (*color, 1.0))
            self.assert_renders_as(material, background, color)

    def test_vertex_paint_uses_zero_rgba_as_the_unbound_fallback(self):
        background = np.array((0.2, 0.3, 0.4))
        color, alpha = np.array((0.8, 0.6, 0.4)), 0.75
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor",),
            ints={"F_BLEND_MODE": 1, "F_PAINT_VERTEX_COLORS": 1},
        )
        self.set_color(material, "g_tColor", (*color, alpha))

        unchanged = background * (1.0 - alpha) + color * alpha
        self.assert_renders_as(material, background, unchanged)
        self.assert_renders_as(material, background, unchanged, vertex_color=(0.0, 0.0, 0.0, 0.0))

        paint = np.array((0.5, 1.0, 0.25, 0.5))
        painted_alpha = alpha * paint[3]
        painted = background * (1.0 - painted_alpha) + color * paint[:3] * painted_alpha
        self.assert_renders_as(material, background, painted, vertex_color=paint)

        black = background * (1.0 - alpha)
        self.assert_renders_as(material, background, black, vertex_color=(0.0, 0.0, 0.0, 1.0))

    def test_sequential_animation_matches_the_shipped_8_by_8_60_cell_case(self):
        scene = bpy.context.scene
        old_frame, old_fps, old_fps_base = scene.frame_current, scene.render.fps, scene.render.fps_base
        scene.render.fps = 20
        scene.render.fps_base = 1.0
        scene.frame_set(0)
        try:
            material = materials.build(
                "csgo_static_overlay.vfx",
                ("g_tColor",),
                ints={
                    "F_BLEND_MODE": 0,
                    "F_TEXTURE_ANIMATION": 1,
                    "F_TEXTURE_ANIMATION_MODE": 0,
                    "g_nNumAnimationCells": 60,
                },
                floats={"g_flAnimationTimePerFrame": 0.05, "g_flAnimationTimeOffset": 0.0},
                vectors={"g_vAnimationGrid": (8.0, 8.0, 0.0, 0.0)},
            )
            colors = {
                0: (1.0, 0.0, 0.0),
                1: (0.0, 1.0, 0.0),
                8: (0.0, 0.0, 1.0),
                59: (1.0, 0.0, 1.0),
            }
            atlas = np.zeros((8, 8, 4), np.float32)
            atlas[:, :, 3] = 1.0
            for cell, color in colors.items():
                source_row, column = divmod(cell, 8)
                atlas[7 - source_row, column, :3] = color
            image = bpy.data.images.new("static_overlay_animation", 8, 8, alpha=True, float_buffer=True)
            image.colorspace_settings.is_data = True
            image.pixels.foreach_set(atlas.ravel())
            texture = material.node_tree.nodes["g_tColor"]
            texture.image = image
            texture.interpolation = "Closest"
            for node in material.node_tree.nodes:
                if node.bl_idname == "ShaderNodeUVMap":
                    node.uv_map = "ANIMATION_UV"

            self.assert_time_driver(material)
            self.assertFalse(bpy.app.autoexec_fail)

            for frame, cell in ((0, 0), (1, 1), (8, 8), (59, 59), (60, 0)):
                scene.frame_set(frame)
                self.assert_renders_as(
                    material,
                    (0.0, 0.0, 0.0),
                    colors[cell],
                    uv_layers={"ANIMATION_UV": (0.5, 0.5)},
                )
        finally:
            scene.render.fps = old_fps
            scene.render.fps_base = old_fps_base
            scene.frame_set(old_frame)

    def test_random_animation_hashes_the_integer_frame(self):
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor",),
            ints={
                "F_TEXTURE_ANIMATION": 1,
                "F_TEXTURE_ANIMATION_MODE": 1,
                "g_nNumAnimationCells": 7,
            },
            floats={"g_flAnimationFrame": 2.75},
            vectors={"g_vAnimationGrid": (4.0, 4.0, 0.0, 0.0)},
        )
        seed = np.floor(np.float32(2.75))
        random_value = np.sin(seed * np.float32(12.9898 + 78.233)) * np.float32(43758.546875)
        frame = int(np.floor(
            (random_value - np.floor(random_value)) * np.float32(7.0)))
        self.assertEqual(frame, 3)

        socket = material.node_tree.nodes["g_tColor"].inputs["Vector"].links[0].from_socket
        column, row = frame % 4, frame // 4
        self.assert_socket_is(
            material,
            socket,
            (0.2, 0.6),
            ((0.2 + column) / 4.0, (0.6 + 3.0 - row) / 4.0, 0.0),
        )
        self.assertNotIn("Source 2 Time", material.node_tree.nodes)

    def test_scripted_animation_does_not_wrap_by_cell_count(self):
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor",),
            ints={
                "F_TEXTURE_ANIMATION": 1,
                "F_TEXTURE_ANIMATION_MODE": 2,
                "g_nNumAnimationCells": 2,
            },
            floats={"g_flAnimationFrame": 3.0},
            vectors={"g_vAnimationGrid": (4.0, 4.0, 0.0, 0.0)},
        )

        socket = material.node_tree.nodes["g_tColor"].inputs["Vector"].links[0].from_socket
        self.assert_socket_is(material, socket, (0.2, 0.6), (0.8, 0.9, 0.0))
        self.assertNotIn("Source 2 Time", material.node_tree.nodes)

    def test_sequential_animation_tracks_scene_fps_changes(self):
        scene = bpy.context.scene
        old_frame, old_fps, old_fps_base = scene.frame_current, scene.render.fps, scene.render.fps_base
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        scene.frame_set(24)
        try:
            material = materials.build(
                "csgo_static_overlay.vfx",
                ("g_tColor",),
                ints={
                    "F_TEXTURE_ANIMATION": 1,
                    "F_TEXTURE_ANIMATION_MODE": 0,
                    "g_nNumAnimationCells": 4,
                },
                floats={"g_flAnimationTimePerFrame": 1.0},
                vectors={"g_vAnimationGrid": (4.0, 1.0, 0.0, 0.0)},
            )
            self.assert_time_driver(material)

            scene.render.fps = 12
            scene.frame_set(25)
            scene.frame_set(24)
            socket = material.node_tree.nodes["g_tColor"].inputs["Vector"].links[0].from_socket
            self.assert_socket_is(material, socket, (0.2, 0.6), (0.55, 0.6, 0.0))
        finally:
            scene.render.fps = old_fps
            scene.render.fps_base = old_fps_base
            scene.frame_set(old_frame)

    def test_primary_lit_blend_and_model_tint_wiring_is_preserved(self):
        material = materials.build(
            "csgo_static_overlay.vfx",
            ("g_tColor", "g_tNormal", "g_tMetalness"),
            ints={"F_BLEND_MODE": 1, "F_LIT": 1},
            floats={"g_flOpacityScale": 0.6, "g_flModelTintAmount": 0.35},
            vectors={"g_vColorTint": (0.8, 0.6, 0.4, 0.0)},
        )
        shader = materials.shader_node(material, "csgo_static_overlay.vfx")
        self.assertEqual(materials.source(shader.inputs["TextureColor"]), ("g_tColor", "Color"))
        self.assertEqual(materials.source(shader.inputs["TextureNormal"]), ("g_tNormal", "Color"))
        self.assertEqual(materials.source(shader.inputs["TextureMetalness"]), ("g_tMetalness", "Green"))
        np.testing.assert_allclose(
            shader.inputs["g_vColorTint"].default_value,
            (0.8, 0.6, 0.4, 0.0),
            atol=1e-6,
        )
        self.assertAlmostEqual(shader.inputs["g_flModelTintAmount"].default_value, 0.35, places=6)
        model_tint = materials.linked_node(shader.inputs["m_vColorTint"])
        self.assertEqual(model_tint.node_tree.name, "SourceIO sRGB To Linear")
        self.assertEqual(materials.linked_node(model_tint.inputs["Color"]).bl_idname, "ShaderNodeObjectInfo")

        blend = materials.linked_node(materials.output_node(material).inputs["Surface"])
        self.assertEqual(blend.bl_idname, "ShaderNodeMixShader")
        opacity = materials.linked_node(blend.inputs[0])
        self.assertEqual(opacity.operation, "MULTIPLY")
        self.assertAlmostEqual(opacity.inputs[1].default_value, 0.6, places=6)
        self.assertEqual(materials.source(opacity.inputs[0]), ("g_tColor", "Alpha"))


if __name__ == "__main__":
    unittest.main()
