"""Run inside Blender with SourceIO registered: unittest ...test_source2_lights.

The CS2 sky and lights: ``env_sky`` becomes the scene's world, turned by its yaw, and a disabled lighting-only
sky doesn't replace the visible one; ``light_environment`` shines along the entity's forward axis (pitch is the
sun's elevation) at pi x brightness, the point lights' convention; ``light_omni2``, ``light_rect`` and
``light_barn`` give their brightness in stops.
"""
import math
import os
import tempfile
import unittest
from unittest import mock

import bpy
import numpy as np
from mathutils import Vector

from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.sky import Skybox
from SourceIO.blender_bindings.source2.vwrld.entities import base_entity_handlers
from SourceIO.blender_bindings.source2.vwrld.entities.cs2_entity_handlers import CS2EntityHandler

from SourceIO.tests.blender_tests.test_source2_materials import FakeMaterial

SCALE = 0.0254
POINT_LIGHT_SCALE = 10000 * SCALE  # watts for brightness 1
WHITE = np.array((255, 255, 255), dtype=np.uint32)  # as KV3 lumps hold colors


def load(*entities):
    collection = bpy.data.collections.new('map')
    bpy.context.scene.collection.children.link(collection)
    CS2EntityHandler(list(entities), collection, None, SCALE).load_entities()
    return collection


def light(collection, classname):
    return next(obj for obj in collection.all_objects if obj.get('entity_data', {}).get('entity', {}).get(
        'classname') == classname)


def shines_along(obj):
    """Unit direction the light travels."""
    return (obj.matrix_basis.to_3x3().normalized() @ Vector((0, 0, -1))).normalized()


def source_forward(pitch, yaw):
    p, y = math.radians(pitch), math.radians(yaw)
    return Vector((math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)))


def assert_vectors_equal(test, a, b, places=4):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


class LightTests(unittest.TestCase):
    def test_sun_shines_along_the_entity_forward_axis(self):
        collection = load({'classname': 'light_environment', 'origin': '0 0 0', 'angles': '50 43 0',
                           'brightness': 2.5, 'brightnessscale': 2.0, 'angulardiameter': 0.5,
                           'color': np.array((255, 128, 0), dtype=np.uint32)})
        sun = light(collection, 'light_environment')
        assert_vectors_equal(self, shines_along(sun), source_forward(50, 43))
        # de_dust2's sun: 50° up, at azimuth 43 + 180, matching its sky's glow (the old lamp pointed 40° up
        # toward azimuth 43).
        toward_sun = -shines_along(sun)
        self.assertAlmostEqual(math.degrees(math.asin(toward_sun.z)), 50, places=3)
        self.assertAlmostEqual(math.degrees(math.atan2(toward_sun.y, toward_sun.x)) % 360, 223, places=3)
        self.assertAlmostEqual(sun.data.energy, math.pi * 2.5 * 2.0, places=4)
        self.assertAlmostEqual(math.degrees(sun.data.angle), 0.5, places=4)
        assert_vectors_equal(self, sun.data.color, (1.0, 128 / 255, 0.0))

    def test_light2_brightness_is_in_stops(self):
        collection = load(
            {'classname': 'light_omni2', 'origin': '0 0 0', 'scales': '1 1 1', 'angles': '0 0 0', 'brightness': -1.0,
             'brightness_legacy': 0.5, 'brightness_lumens': 500.0, 'outer_angle': 180.0, 'color': WHITE},
            {'classname': 'light_rect', 'origin': '0 0 0', 'scales': '1 1 1', 'angles': '0 0 0', 'brightness': 2.0,
             'brightness_lumens': 10.0, 'brightnessscale': 1.5, 'color': WHITE},
            {'classname': 'light_barn', 'origin': '0 0 0', 'scales': '1 1 1', 'angles': '0 0 0', 'brightness': 3.46094,
             'brightness_lumens': 4500.0, 'color': WHITE})
        self.assertAlmostEqual(light(collection, 'light_omni2').data.energy, 0.5 * POINT_LIGHT_SCALE, places=3)
        self.assertAlmostEqual(light(collection, 'light_rect').data.energy, 6.0 * POINT_LIGHT_SCALE, places=3)
        # de_dust2's light_barn: brightness_legacy 11 (lumens / 256 gave 17.6).
        self.assertAlmostEqual(light(collection, 'light_barn').data.energy / POINT_LIGHT_SCALE, 11.0, delta=0.02)

    def test_omni2_spot_shines_along_the_entity_forward_axis(self):
        collection = load({'classname': 'light_omni2', 'origin': '0 0 0', 'angles': '30 120 45',
                           'brightness': 0.0, 'outer_angle': 45.0, 'color': WHITE})
        spot = light(collection, 'light_omni2')
        self.assertEqual(spot.data.type, 'SPOT')
        assert_vectors_equal(self, shines_along(spot), source_forward(30, 120))


def equirect_image(name, columns):
    """An equirectangular image with one color per column (left to right), the same in every row."""
    image = bpy.data.images.new(name, len(columns), 2, float_buffer=True)
    image.pixels = [c for _row in range(2) for color in columns for c in (*color, 1.0)]
    image.alpha_mode = 'CHANNEL_PACKED'
    return image


class SkyTests(unittest.TestCase):
    def setUp(self):
        self.scene_world = bpy.context.scene.world
        self.image = equirect_image('sky_test', [(0, 0, 1)] * 4)
        patcher = mock.patch.object(Skybox, 'sky_texture', new_callable=mock.PropertyMock, return_value=self.image)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        bpy.context.scene.world = self.scene_world

    def load_skies(self, *skies, floats=None):
        content_manager = mock.Mock()
        material = FakeMaterial('sky.vfx', ('g_tSkyTexture',), floats=floats)
        with mock.patch.object(base_entity_handlers.CompiledMaterialResource, 'from_buffer', return_value=material):
            collection = bpy.data.collections.new('map')
            base_entity_handlers.BaseEntityHandler(list(skies), collection, content_manager, SCALE).load_entities()

    @staticmethod
    def sky(name, yaw=0.0, disabled=0, **values):
        return {'classname': 'env_sky', 'skyname': f'materials/skybox/{name}.vmat', 'origin': '0 0 0',
                'angles': f'0 {yaw} 0', 'StartDisabled': disabled, **values}

    def test_visible_sky_becomes_the_world(self):
        # de_inferno: the visible sky, then a disabled one that only lights the map.
        self.load_skies(self.sky('s2_de_inferno_sky01', 245, brightnessscale=0.5, tint_color='255 128 0'),
                        self.sky('skymodel_hosekwilkie', 40, disabled=1, brightnessscale=2.0),
                        floats={'g_flBrightnessExposureBias': 1.0})
        world = bpy.context.scene.world
        self.assertEqual(world.name, 's2_de_inferno_sky01')
        nodes = world.node_tree.nodes
        background = next(n for n in nodes if n.bl_idname == 'ShaderNodeBackground')
        self.assertAlmostEqual(background.inputs['Strength'].default_value, 0.5 * 2.0, places=5)
        mapping = next(n for n in nodes if n.bl_idname == 'ShaderNodeMapping')
        self.assertAlmostEqual(mapping.inputs['Rotation'].default_value[2], math.radians(-245), places=5)
        tint = next(n for n in nodes if n.bl_idname == 'ShaderNodeMix')
        assert_vectors_equal(self, tint.inputs[7].default_value[:3], (1.0, 128 / 255, 0.0))
        self.assertIn('skymodel_hosekwilkie', bpy.data.worlds)

    def test_disabled_sky_is_used_without_a_visible_one(self):
        # de_ancient has only its lighting sky; the factory scene's world isn't an imported one.
        self.load_skies(self.sky('sky_hr_aztec_02_lighting', disabled=1))
        self.assertEqual(bpy.context.scene.world.name, 'sky_hr_aztec_02_lighting')
        self.load_skies(self.sky('sky_de_dust2'), self.sky('sky_other_lighting', disabled=1))
        self.assertEqual(bpy.context.scene.world.name, 'sky_de_dust2')

    def test_yaw_turns_the_sky_counterclockwise(self):
        # Eight columns centered on azimuths 157.5, 112.5, ... -157.5 (u = 0.5 is +X, azimuth grows leftward):
        # +X red, +Y green, -Y blue, -X yellow.
        image = equirect_image('sky_compass', [(1, 1, 0), (0, 1, 0), (0, 1, 0), (1, 0, 0), (1, 0, 0),
                                               (0, 0, 1), (0, 0, 1), (1, 1, 0)])
        with mock.patch.object(Skybox, 'sky_texture', new_callable=mock.PropertyMock, return_value=image):
            self.load_skies(self.sky('compass_turned', 90))
        turned = bpy.context.scene.world
        with mock.patch.object(Skybox, 'sky_texture', new_callable=mock.PropertyMock, return_value=image):
            self.load_skies(self.sky('compass'))
        plain = bpy.context.scene.world
        for world in (turned, plain):
            next(n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeTexEnvironment').interpolation = \
                'Closest'
        # Turned by 90°, what was at +X is at +Y.
        self.assertEqual(self.dominant(plain, 0), 'R')
        self.assertEqual(self.dominant(plain, 90), 'G')
        self.assertEqual(self.dominant(plain, -90), 'B')
        self.assertEqual(self.dominant(turned, 90), 'R')
        self.assertEqual(self.dominant(turned, 180), 'G')
        self.assertEqual(self.dominant(turned, 0), 'B')

    @staticmethod
    def dominant(world, azimuth):
        """The strongest channel of the sky seen level toward an azimuth (degrees, counterclockwise from +X)."""
        scene = bpy.context.scene
        camera = bpy.data.objects.new('sky_camera', bpy.data.cameras.new('sky_camera'))
        camera.data.angle = math.radians(5)
        camera.rotation_euler = (math.radians(90), 0, math.radians(azimuth - 90))
        scene.collection.objects.link(camera)
        hidden = [other for other in scene.objects if not other.hide_render and other != camera]
        for other in hidden:
            other.hide_render = True
        scene.camera = camera
        scene.world = world
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 1
        scene.cycles.device = 'CPU'
        scene.view_settings.view_transform = 'Standard'
        scene.render.resolution_x = scene.render.resolution_y = 4
        scene.render.image_settings.file_format = 'OPEN_EXR'
        with tempfile.TemporaryDirectory() as directory:
            scene.render.filepath = os.path.join(directory, 'sky.exr')
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath)
            pixels = np.array(image.pixels[:], dtype=np.float32).reshape(-1, 4)
            bpy.data.images.remove(image)
        for other in hidden:
            other.hide_render = False
        bpy.data.objects.remove(camera)
        return 'RGB'[int(np.argmax(pixels[:, :3].mean(0)))]


if __name__ == '__main__':
    unittest.main()
