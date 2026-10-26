"""Run with a Python environment providing bpy: python -m unittest ...test_flex_controllers.

Covers the object-level Flex controllers panel data that create_flex_drivers fills, non-destructive stereo
adjustments, the operators behind the panel, and the unclamped combo driver shortcut.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

from SourceIO.blender_bindings import bindings
from SourceIO.blender_bindings.models.common import create_flex_drivers
from SourceIO.blender_bindings.operators.flex_operators import (
    _FlexDragSession,
    _flex_drag_terminal_action,
    balance_multipliers,
    SOURCEIO_OT_KeyAllFlexControllers,
    SOURCEIO_OT_FlexSliderHandler,
    SOURCEIO_PT_FlexControlPanel,
    stereo_delta_values,
)
from SourceIO.blender_bindings.operators.flex_resolver import (
    flex_controller_objects,
    resolve_flex_object,
)
from SourceIO.blender_bindings.operators.shared_operators import SOURCEIO_PT_Utils
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

    def test_balance_multipliers_match_sfm(self):
        self.assertEqual(balance_multipliers(-1.0), (1.0, 0.0))
        self.assertEqual(balance_multipliers(-0.5), (1.0, 0.5))
        self.assertEqual(balance_multipliers(0.0), (1.0, 1.0))
        self.assertEqual(balance_multipliers(0.5), (0.5, 1.0))
        self.assertEqual(balance_multipliers(1.0), (0.0, 1.0))

    def test_stereo_drag_preserves_asymmetry_and_uses_snapshot(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6

        session = _FlexDragSession(self.obj, stereo, scene)
        self.assertEqual(session.apply(0.1, 0.0), (stereo.L, stereo.R))
        self.assertAlmostEqual(data[stereo.L], 0.35)
        self.assertAlmostEqual(data[stereo.R], 0.7)

        session.apply(0.2, 0.0)
        self.assertAlmostEqual(data[stereo.L], 0.45)
        self.assertAlmostEqual(data[stereo.R], 0.8)

    def test_stereo_drag_respects_balance_and_cancel(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6

        session = _FlexDragSession(self.obj, stereo, scene)
        self.assertEqual(session.apply(0.2, -1.0), (stereo.L,))
        self.assertAlmostEqual(data[stereo.L], 0.45)
        self.assertAlmostEqual(data[stereo.R], 0.6)
        self.assertTrue(session.cancel())
        self.assertAlmostEqual(data[stereo.L], 0.25)
        self.assertAlmostEqual(data[stereo.R], 0.6)

        session = _FlexDragSession(self.obj, stereo, scene)
        self.assertEqual(session.apply(0.2, -0.5), (stereo.L, stereo.R))
        self.assertAlmostEqual(data[stereo.L], 0.45)
        self.assertAlmostEqual(data[stereo.R], 0.7)

    def test_drag_session_rejects_a_different_active_object(self):
        session = _FlexDragSession(self.obj, self.obj.flex_controllers['cs'], bpy.context.scene)
        self.assertTrue(session.matches_context(SimpleNamespace(object=self.obj)))

        other = bpy.data.objects.new('other', bpy.data.meshes.new('other'))
        bpy.context.scene.collection.objects.link(other)
        self.assertFalse(session.matches_context(SimpleNamespace(object=other)))
        self.assertFalse(session.matches_context(SimpleNamespace(object=None)))
        bpy.data.objects.remove(other, do_unlink=True)

    def test_armature_resolves_its_unique_flex_child(self):
        armature = bpy.data.objects.new('rig', bpy.data.armatures.new('rig'))
        bpy.context.scene.collection.objects.link(armature)
        self.obj.parent = armature

        self.assertEqual([obj.as_pointer() for obj in flex_controller_objects(armature)],
                         [self.obj.as_pointer()])
        self.assertEqual(resolve_flex_object(armature).as_pointer(), self.obj.as_pointer())
        self.assertTrue(SOURCEIO_PT_Utils.poll(SimpleNamespace(active_object=armature)))
        self.assertTrue(SOURCEIO_PT_FlexControlPanel.poll(SimpleNamespace(object=armature)))

        session = _FlexDragSession(self.obj, self.obj.flex_controllers['cs'], bpy.context.scene)
        self.assertTrue(session.matches_context(SimpleNamespace(object=armature)))

    def test_ambiguous_armature_requires_direct_mesh_selection(self):
        armature = bpy.data.objects.new('rig', bpy.data.armatures.new('rig'))
        bpy.context.scene.collection.objects.link(armature)
        self.obj.parent = armature

        mesh = bpy.data.meshes.new('other_face')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        other = bpy.data.objects.new('other_face', mesh)
        bpy.context.scene.collection.objects.link(other)
        other.parent = armature
        other.shape_key_add(name='Basis')
        other.shape_key_add(name='a_b')
        create_flex_drivers(other, fake_mdl())

        self.assertEqual(len(flex_controller_objects(armature)), 2)
        self.assertIsNone(resolve_flex_object(armature))
        self.assertTrue(SOURCEIO_PT_Utils.poll(SimpleNamespace(active_object=armature)))
        self.assertTrue(SOURCEIO_PT_FlexControlPanel.poll(SimpleNamespace(object=armature)))
        self.assertFalse(SOURCEIO_OT_KeyAllFlexControllers.poll(SimpleNamespace(object=armature)))

    def test_armature_active_operators_target_the_flex_child(self):
        armature = bpy.data.objects.new('rig', bpy.data.armatures.new('rig'))
        bpy.context.scene.collection.objects.link(armature)
        self.obj.parent = armature
        bpy.context.view_layer.objects.active = armature

        data = self.obj.data
        data[self.flexmap['ca']] = 0.5
        with bpy.context.temp_override(object=armature):
            self.assertEqual(bpy.ops.sourceio.key_all_flex_controllers(), {'FINISHED'})
            self.assertEqual(bpy.ops.sourceio.reset_flex_controllers(), {'FINISHED'})

        curves = data.animation_data.action.layers[0].strips[0].channelbags[0].fcurves
        expected = {f'["{prop_name}"]'
                    for slider in self.obj.flex_controllers for prop_name in slider.prop_names()}
        self.assertEqual({curve.data_path for curve in curves}, expected)
        self.assertEqual(data[self.flexmap['ca']], 0.0)

    def test_stereo_commit_keys_only_changed_side(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6
        session = _FlexDragSession(self.obj, stereo, scene)
        scene.tool_settings.use_keyframe_insert_auto = True
        try:
            self.assertTrue(session.commit(0.2, -1.0))
        finally:
            scene.tool_settings.use_keyframe_insert_auto = False
        self.assertAlmostEqual(data[stereo.L], 0.45)
        self.assertAlmostEqual(data[stereo.R], 0.6)
        curves = data.animation_data.action.layers[0].strips[0].channelbags[0].fcurves
        self.assertEqual({curve.data_path for curve in curves}, {f'["{stereo.L}"]'})

    def test_stereo_drag_clamps_to_controller_range(self):
        stereo = self.obj.flex_controllers['cs']
        stereo.minimum, stereo.maximum = -1.0, 0.5
        left, right = stereo_delta_values(0.4, -0.8, 0.5, 0.0, stereo.minimum, stereo.maximum)
        self.assertAlmostEqual(left, 0.5)
        self.assertAlmostEqual(right, -0.3)
        left, right = stereo_delta_values(0.4, -0.8, -0.5, 0.0, stereo.minimum, stereo.maximum)
        self.assertAlmostEqual(left, -0.1)
        self.assertAlmostEqual(right, -1.0)

    def test_cancel_and_noop_do_not_key(self):
        data, scene = self.obj.data, bpy.context.scene
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6
        scene.tool_settings.use_keyframe_insert_auto = True
        try:
            session = _FlexDragSession(self.obj, stereo, scene)
            session.apply(0.3, 0.0)
            self.assertTrue(session.cancel())
            self.assertIsNone(data.animation_data)

            session = _FlexDragSession(self.obj, stereo, scene)
            self.assertTrue(session.commit(0.0, 0.0))
            self.assertIsNone(data.animation_data)
        finally:
            scene.tool_settings.use_keyframe_insert_auto = False
        self.assertAlmostEqual(data[stereo.L], 0.25)
        self.assertAlmostEqual(data[stereo.R], 0.6)

    def test_drag_terminal_events(self):
        self.assertEqual(_flex_drag_terminal_action('LEFTMOUSE', 'RELEASE'), 'COMMIT')
        self.assertEqual(_flex_drag_terminal_action('RET', 'PRESS'), 'COMMIT')
        self.assertEqual(_flex_drag_terminal_action('NUMPAD_ENTER', 'PRESS'), 'COMMIT')
        self.assertEqual(_flex_drag_terminal_action('ESC', 'PRESS'), 'CANCEL')
        self.assertEqual(_flex_drag_terminal_action('RIGHTMOUSE', 'PRESS'), 'CANCEL')
        self.assertIsNone(_flex_drag_terminal_action('MOUSEMOVE', 'NOTHING'))
        self.assertIsNone(_flex_drag_terminal_action('A', 'RELEASE'))

    def test_blender_52_timer_without_event_timer_is_accepted(self):
        handler = SimpleNamespace(_timer=object())
        timer_event = SimpleNamespace(type='TIMER', timer=None)
        self.assertTrue(SOURCEIO_OT_FlexSliderHandler._is_own_timer(handler, timer_event))
        self.assertFalse(SOURCEIO_OT_FlexSliderHandler._is_own_timer(
            SimpleNamespace(_timer=None),
            timer_event,
        ))

    def test_background_delta_assignment_commits_and_resets(self):
        if not bpy.app.background:
            self.skipTest('Requires Blender background mode')
        data = self.obj.data
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6

        stereo.value = 0.2

        self.assertAlmostEqual(data[stereo.L], 0.45)
        self.assertAlmostEqual(data[stereo.R], 0.8)
        self.assertEqual(stereo.value, 0.0)

    def test_changing_another_slider_commits_the_active_drag_first(self):
        # A script setting several sliders in a row must apply all of them, not reject all but the first.
        from SourceIO.blender_bindings.operators import flex_operators
        scene, data = bpy.context.scene, self.obj.data
        first = self.obj.flex_controllers['cs']
        data[first.L], data[first.R] = 0.25, 0.6

        mesh = bpy.data.meshes.new('face2')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        other = bpy.data.objects.new('face2', mesh)
        scene.collection.objects.link(other)
        other.shape_key_add(name='Basis')
        other.shape_key_add(name='a_b')
        create_flex_drivers(other, fake_mdl())
        second = other.flex_controllers['cs']
        other.data[second.L], other.data[second.R] = 0.0, 0.0

        session = _FlexDragSession(self.obj, first, scene)
        flex_operators._active_flex_drag = session
        try:
            first.value = 0.2
            second.value = 0.5
        finally:
            flex_operators._active_flex_drag = None

        self.assertTrue(session.finished)
        self.assertAlmostEqual(data[first.L], 0.45)
        self.assertAlmostEqual(data[first.R], 0.8)
        self.assertEqual(first.value, 0.0)
        self.assertAlmostEqual(other.data[second.L], 0.5)
        self.assertAlmostEqual(other.data[second.R], 0.5)
        self.assertEqual(second.value, 0.0)

    def test_undo_handler_zeroes_stale_additive_values(self):
        from SourceIO.blender_bindings.operators import flex_operators
        data = self.obj.data
        stereo = self.obj.flex_controllers['cs']
        data[stereo.L], data[stereo.R] = 0.25, 0.6
        flex_operators._set_flex_delta(stereo, 0.3)

        flex_operators.reset_flex_deltas()

        self.assertEqual(stereo.value, 0.0)
        self.assertAlmostEqual(data[stereo.L], 0.25)
        self.assertAlmostEqual(data[stereo.R], 0.6)
        self.assertIn(flex_operators.reset_flex_deltas, bpy.app.handlers.undo_post)

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
            self.assertEqual(obj.flex_controllers['cs'].value, 0.0)
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
