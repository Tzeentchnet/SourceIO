"""Run with a Python environment providing bpy: python -m unittest ...test_flex_controllers.

Covers the object-level Flex controllers panel data that create_flex_drivers fills, the operators behind it,
and the combo driver shortcut (a product of component flexes, not clamped).
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

from SourceIO.blender_bindings import bindings
from SourceIO.blender_bindings.models.common import create_flex_drivers
from SourceIO.library.models.mdl.flex_expressions import FetchController, Mul

_registered_here = False


def setUpModule():
    global _registered_here
    if 'flex_controllers' not in bpy.types.Object.bl_rna.properties:
        bindings.register()
        _registered_here = True


def tearDownModule():
    if _registered_here:
        bindings.unregister()


def controller(name, minimum=0.0, maximum=1.0):
    return SimpleNamespace(name=name, min=minimum, max=maximum)


def ui(name, controller=None, left=None, right=None, nway=None):
    return SimpleNamespace(name=name, controller=controller, left_controller=left, right_controller=right,
                           nway_controller=nway, stereo=left is not None)


def fake_mdl():
    """Flexes a and b follow controllers ca and cb; a_b is their combo, the only one with a shape key."""
    rules = [
        ('a', (FetchController('ca'), [('ca', 'fetch1')])),
        ('b', (FetchController('cb'), [('cb', 'fetch1')])),
        ('a_b', (Mul(FetchController('ca'), FetchController('cb')), [('ca', 'fetch1'), ('cb', 'fetch1')])),
    ]
    return SimpleNamespace(
        flex_controllers=[controller('ca'), controller('cb'), controller('left_cs'), controller('right_cs'),
                          controller('cn', -1.0, 1.0)],
        flex_ui_controllers=[ui('ca', 'ca'), ui('cb', 'cb', nway='cn'), ui('cs', left='left_cs', right='right_cs')],
        rebuild_flex_rules=lambda: list(rules),
    )


class FlexControllerTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        mesh = bpy.data.meshes.new('face')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        self.obj = bpy.data.objects.new('face', mesh)
        bpy.context.scene.collection.objects.link(self.obj)
        bpy.context.view_layer.objects.active = self.obj
        self.obj.shape_key_add(name='Basis')
        self.obj.shape_key_add(name='a_b')
        create_flex_drivers(self.obj, fake_mdl())
        self.flexmap = self.obj.data['flexmap'].to_dict()

    def evaluate(self):
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)

    def test_controller_list(self):
        sliders = self.obj.flex_controllers
        self.assertEqual([s.display_name for s in sliders], ['Flex Scale', 'ca', 'cb', 'cn', 'cs'])
        stereo = sliders['cs']
        self.assertTrue(stereo.split)
        self.assertFalse(stereo.realvalue)
        self.assertEqual((stereo.L, stereo.R), (self.flexmap['left_cs'], self.flexmap['right_cs']))
        nway = sliders[self.flexmap['cn']]
        self.assertEqual((nway.minimum, nway.maximum), (-1.0, 1.0))
        self.assertTrue(all(s.name in self.obj.data for s in sliders if not s.split))

    def test_panel_shows_without_skin_groups(self):
        from SourceIO.blender_bindings.operators.shared_operators import SOURCEIO_PT_Utils
        self.assertNotIn('skin_groups', self.obj)
        self.assertTrue(SOURCEIO_PT_Utils.poll(SimpleNamespace(active_object=self.obj)))
        empty = bpy.data.objects.new('empty', None)
        self.assertFalse(SOURCEIO_PT_Utils.poll(SimpleNamespace(active_object=empty)))
        bpy.data.objects.remove(empty)

    def test_combo_is_not_clamped(self):
        # a = 0.5 * 2 = 1.0 and b = 0.75 * 2 = 1.5 (not shape keys, so not clamped to 0..1);
        # a_b = a * b / FS = 0.75. Clamping the product first gave 1.0 / 2 = 0.5.
        data = self.obj.data
        data[self.flexmap['flex_scale']] = 2.0
        data[self.flexmap['ca']] = 0.5
        data[self.flexmap['cb']] = 0.75
        driver = self.obj.data.shape_keys.animation_data.drivers.find('key_blocks["a_b"].value').driver
        self.assertNotIn('clamp', driver.expression)
        self.assertTrue(driver.is_simple_expression)
        self.evaluate()
        self.assertAlmostEqual(self.obj.data.shape_keys.key_blocks['a_b'].value, 0.75, places=5)

    def test_stereo_slider_sets_both_sides_by_balance(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']

        scene.sourceio_flex_lr_balance = 0.0
        stereo.value = 0.25
        self.assertAlmostEqual(data[stereo.L], 0.25)
        self.assertAlmostEqual(data[stereo.R], 0.25)
        self.assertAlmostEqual(stereo.value, 0.25)

        scene.sourceio_flex_lr_balance = -1.0  # left only: the right side is left alone
        stereo.value = 0.75
        self.assertAlmostEqual(data[stereo.L], 0.75)
        self.assertAlmostEqual(data[stereo.R], 0.25)
        self.assertAlmostEqual(stereo.value, 0.75)  # reads the side the balance gives in full

        scene.sourceio_flex_lr_balance = -0.5  # left in full, right at half
        stereo.value = 0.5
        self.assertAlmostEqual(data[stereo.L], 0.5)
        self.assertAlmostEqual(data[stereo.R], 0.25)
        self.assertAlmostEqual(stereo.value, 0.5)

        scene.sourceio_flex_lr_balance = 0.0
        data[stereo.L], data[stereo.R] = 0.25, 0.6
        self.assertAlmostEqual(stereo.value, 0.6)  # both in full: the larger side

    def test_stereo_slider_is_absolute_and_keys(self):
        # The value stays where it is set (the old additive slider reset to 0 on release and lost the change).
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        scene.sourceio_flex_lr_balance = 0.0
        scene.tool_settings.use_keyframe_insert_auto = True
        try:
            stereo.value = 0.4
        finally:
            scene.tool_settings.use_keyframe_insert_auto = False
        self.evaluate()
        self.assertAlmostEqual(stereo.value, 0.4)
        self.assertAlmostEqual(data[stereo.L], 0.4)
        curves = data.animation_data.action.layers[0].strips[0].channelbags[0].fcurves
        self.assertEqual({curve.data_path for curve in curves}, {f'["{stereo.L}"]', f'["{stereo.R}"]'})

    def test_signed_stereo_slider_and_clamp(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        scene.sourceio_flex_lr_balance = 0.0
        stereo.minimum, stereo.maximum = -1.0, 0.5
        stereo.value_signed = -0.5
        self.assertAlmostEqual(data[stereo.L], -0.5)
        self.assertAlmostEqual(stereo.value_signed, -0.5)
        stereo.value_signed = 1.0  # clamped to the controller's maximum
        self.assertAlmostEqual(data[stereo.R], 0.5)

    def test_key_toggle_and_key_all(self):
        data = self.obj.data
        name = self.flexmap['ca']
        with bpy.context.temp_override(object=self.obj):
            bpy.ops.sourceio.key_flex_controller(flex_controller=name)
            curves = data.animation_data.action.layers[0].strips[0].channelbags[0].fcurves
            self.assertEqual(len(curves.find(f'["{name}"]').keyframe_points), 1)
            bpy.ops.sourceio.key_flex_controller(flex_controller=name)
            self.assertIsNone(curves.find(f'["{name}"]'))

            bpy.ops.sourceio.key_all_flex_controllers()
            keyed = {curve.data_path for curve in curves}
        expected = {f'["{p}"]' for s in self.obj.flex_controllers for p in s.prop_names()}
        self.assertEqual(keyed, expected)

    def test_reset(self):
        data = self.obj.data
        data[self.flexmap['flex_scale']] = 3.0
        data[self.flexmap['ca']] = 0.5
        with bpy.context.temp_override(object=self.obj):
            bpy.ops.sourceio.reset_flex_controllers()
        self.assertEqual(data[self.flexmap['flex_scale']], 1.0)
        self.assertEqual(data[self.flexmap['ca']], 0.0)

    def test_survives_save_and_reload(self):
        count = len(self.obj.flex_controllers)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'flex.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            obj = bpy.data.objects['face']
            self.assertEqual(len(obj.flex_controllers), count)
            self.assertTrue(obj.flex_controllers['cs'].split)
            bpy.ops.wm.read_homefile(use_empty=True)


class FlexRegistrationTests(unittest.TestCase):
    def test_unregister_removes_properties(self):
        bindings.unregister()
        try:
            self.assertNotIn('flex_controllers', bpy.types.Object.bl_rna.properties)
            self.assertNotIn('flex_controller_index', bpy.types.Object.bl_rna.properties)
            self.assertNotIn('sourceio_flex_lr_balance', bpy.types.Scene.bl_rna.properties)
            self.assertNotIn('flex_controllers', bpy.types.Mesh.bl_rna.properties)
        finally:
            bindings.register()
        self.assertIn('flex_controllers', bpy.types.Object.bl_rna.properties)


if __name__ == '__main__':
    unittest.main()
