"""Focused Blender binding tests for Source 2 auxiliary animation channels."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import bpy
import numpy as np

from SourceIO.blender_bindings.operators.source2_animation_operators import (
    AmbiguousAnimationGroupError,
    SOURCEIO_OT_Source2AnimationImport,
    _discover_companion_groups,
    _matching_group,
)
from SourceIO.blender_bindings.source2.animation_loader import import_sequence_animations
from SourceIO.blender_bindings.utils.bpy_utils import edit_armature
from SourceIO.library.source2.animation import (
    AnimationClip,
    ChannelAttribute,
    ClipAnimation,
    SequenceAnimation,
    Skeleton,
)
from SourceIO.library.source2.animation.segments import AnimationSegment
from SourceIO.library.source2.exceptions import Source2Error


class Source2AnimationBindingTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.armature = bpy.data.objects.new(
            'character_ARM', bpy.data.armatures.new('character_ARM_DATA')
        )
        bpy.context.scene.collection.objects.link(self.armature)
        with edit_armature(self.armature) as bones:
            bone = bones.new('root')
            bone.tail = (0, 1, 0)

        mesh = bpy.data.meshes.new('face')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        self.mesh_obj = bpy.data.objects.new('face', mesh)
        bpy.context.scene.collection.objects.link(self.mesh_obj)
        self.mesh_obj.parent = self.armature
        self.mesh_obj.shape_key_add(name='Basis')
        self.mesh_obj.shape_key_add(name='smile')

        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        self.armature.select_set(True)
        bpy.context.view_layer.objects.active = self.armature

    def test_morph_channel_creates_action_curve_and_shape_key_driver(self):
        values = np.array([0.0, 0.5, 1.0], dtype='<f4')
        segment = AnimationSegment(
            'CCompressedFullFloat',
            ChannelAttribute.DATA,
            1,
            values.tobytes(),
            np.array([0], dtype=np.int64),
            np.array([0], dtype=np.int64),
            ('smile',),
            'MorphChannel',
            'data',
        )
        animation = SequenceAnimation(
            'smile_anim',
            30.0,
            3,
            frame_blocks=[(0, 2, [0])],
            segments=[segment],
        )

        created = import_sequence_animations(
            [animation], self.armature, 1.0, source_name='character'
        )
        self.assertEqual(len(created), 1)
        action, _slot = created[0]
        channelbag = action.layers[0].strips[0].channelbags[0]
        curve = channelbag.fcurves.find('["sourceio:flex:smile"]')
        self.assertIsNotNone(curve)
        self.assertEqual(
            [point.co_ui.y for point in curve.keyframe_points],
            [0.0, 0.5, 1.0],
        )

        driver = self.mesh_obj.data.shape_keys.animation_data.drivers.find(
            'key_blocks["smile"].value'
        )
        self.assertIsNotNone(driver)
        self.assertEqual(driver.driver.expression, 'sourceio_value')
        self.assertEqual(driver.driver.variables[0].targets[0].id, self.armature)
        self.assertEqual(
            driver.driver.variables[0].targets[0].data_path,
            '["sourceio:flex:smile"]',
        )

        bpy.context.scene.frame_set(1)
        self.assertAlmostEqual(
            self.mesh_obj.data.shape_keys.key_blocks['smile'].value, 0.5, places=5
        )

    def test_direct_operator_requires_a_selected_armature(self):
        self.assertTrue(SOURCEIO_OT_Source2AnimationImport.poll(bpy.context))
        self.armature.select_set(False)
        self.mesh_obj.select_set(True)
        bpy.context.view_layer.objects.active = self.mesh_obj
        self.assertFalse(SOURCEIO_OT_Source2AnimationImport.poll(bpy.context))

    def test_user_generic_and_unknown_channels_become_inspectable_curves(self):
        values = np.array([0.25, 0.75], dtype='<f4').tobytes()
        segments = [
            AnimationSegment(
                'CCompressedFullFloat',
                attribute,
                1,
                values,
                np.array([0], dtype=np.int64),
                np.array([0], dtype=np.int64),
                (name,),
                channel_class,
                'data',
            )
            for attribute, channel_class, name in (
                (ChannelAttribute.USER, 'UserChannel', 'glow'),
                (ChannelAttribute.DATA, '', 'speed'),
                (ChannelAttribute.UNKNOWN, 'FutureChannel', 'future'),
            )
        ]
        animation = SequenceAnimation(
            'metadata_anim',
            30.0,
            2,
            frame_blocks=[(0, 1, [0, 1, 2])],
            segments=segments,
        )

        action, _slot = import_sequence_animations(
            [animation], self.armature, 1.0, source_name='character'
        )[0]
        channelbag = action.layers[0].strips[0].channelbags[0]
        self.assertIsNotNone(channelbag.fcurves.find('["sourceio:user:glow"]'))
        self.assertIsNotNone(channelbag.fcurves.find('["sourceio:data:speed"]'))
        self.assertIsNotNone(channelbag.fcurves.find('["sourceio:unknown:future"]'))

    def test_nm_curve_and_complete_event_payload_are_bound_to_action(self):
        clip = AnimationClip.from_kv({
            'm_skeleton': 'generated.vnmskel',
            'm_nNumFrames': 2,
            'm_flDuration': 1.0,
            'm_bIsAdditive': False,
            'm_trackCompressionSettings': [],
            'm_compressedPoseData': b'',
            'm_compressedPoseOffsets': [0, 0],
            'm_floatCurveIDs': ['speed'],
            'm_floatCurveDefs': [{
                'm_bIsStatic': False,
                'm_range': {'m_flRangeStart': 0.0, 'm_flRangeLength': 1.0},
            }],
            'm_compressedFloatCurveData': np.array([0, 65535], dtype='<u2'),
            'm_compressedFloatCurveOffsets': [0, 1],
            'm_events': [{
                '_class': 'CNmSoundEvent',
                'm_flStartTime': {'m_flValue': 1.0},
                'm_flDuration': {'m_flValue': 0.25},
                'm_name': 'Weapon.Reload',
                'm_position': 'EntityPos',
                'm_attachmentName': 'weapon_hand',
                'm_futureField': 17,
            }],
        }, 'generated')
        source = Skeleton(
            ['root'],
            np.array([-1], dtype=np.int32),
            np.zeros((1, 3), dtype=np.float32),
            np.array([(0, 0, 0, 1)], dtype=np.float32),
            np.zeros(1, dtype=np.int64),
        )

        action, _slot = import_sequence_animations(
            [ClipAnimation(clip, source, 'clips/generated.vnmclip')],
            self.armature,
            1.0,
            source_name='character',
        )[0]
        channelbag = action.layers[0].strips[0].channelbags[0]
        self.assertIsNotNone(channelbag.fcurves.find('["sourceio:curve:speed"]'))
        events = json.loads(action['sourceio_events'])
        self.assertEqual(events[0]['attachment'], 'weapon_hand')
        self.assertEqual(events[0]['sound_position'], 'EntityPos')
        self.assertEqual(events[0]['duration_seconds'], 0.25)
        self.assertEqual(events[0]['payload']['m_futureField'], 17)
        self.assertEqual(action.pose_markers[0].frame, 1)

    def test_vanim_only_uses_a_group_that_references_it(self):
        group = (Path('animations.vagrp_c'), object())
        vanim = Path('walk.vanim_c')
        with patch(
                'SourceIO.blender_bindings.operators.source2_animation_operators.'
                '_group_references_vanim',
                return_value=False,
        ):
            self.assertIsNone(_matching_group(vanim, [group]))
        with patch(
                'SourceIO.blender_bindings.operators.source2_animation_operators.'
                '_group_references_vanim',
                return_value=True,
        ):
            self.assertEqual(_matching_group(vanim, [group]), group)
            with self.assertRaises(AmbiguousAnimationGroupError):
                _matching_group(vanim, [group, (Path('other.vagrp_c'), object())])

    def test_malformed_companion_group_is_skipped(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.vagrp_c"
            path.write_bytes(b"malformed")
            with patch(
                    "SourceIO.blender_bindings.operators.source2_animation_operators._read_resource",
                    side_effect=Source2Error("malformed animation group"),
            ):
                groups = _discover_companion_groups(
                    Path(directory),
                    [(Path("walk.vanim_c"), object())],
                )

        self.assertEqual(groups, [])


if __name__ == '__main__':
    unittest.main()
