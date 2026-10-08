"""Run with a Python environment providing bpy: python -m unittest ...test_armatures."""
import unittest
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

from SourceIO.blender_bindings.models.import_animations import set_pose, _ROOT_CORRECTION
from SourceIO.blender_bindings.models.mdl4 import import_mdl as mdl4
from SourceIO.blender_bindings.models.mdl6 import import_mdl as mdl6
from SourceIO.blender_bindings.models.mdl9 import import_mdl as mdl9
from SourceIO.blender_bindings.models.mdl10 import import_mdl as mdl10
from SourceIO.blender_bindings.models.mdl36 import import_mdl as mdl36
from SourceIO.blender_bindings.models.mdl2531 import import_mdl as mdl2531
from SourceIO.blender_bindings.utils.bpy_utils import edit_armature

# parent index per bone; parents come first, as in every MDL
PARENTS = [-1, 0, 1, 1, 0, 4, 5, 2]
POSITIONS = [(1, 2, 3), (0, 5, -1), (2, 0, 7), (-3, 1, 0), (0, -4, 2), (1, 1, 1), (6, 0, -2), (0, 0, 4)]
EULERS = [(0.3, -1.2, 2.0), (1.5, 0.2, -0.4), (-2.8, 0.9, 0.1), (0.0, 0.0, 1.0),
          (0.7, 0.7, -0.7), (-1.1, 2.2, 0.5), (3.0, -0.3, 1.9), (0.2, -2.5, 0.0)]
SCALE = 0.5


def accumulate(locals_):
    result = []
    for parent, local in zip(PARENTS, locals_):
        result.append(result[parent] @ local if parent != -1 else local)
    return result


def local_matrix(position, rotation=None):
    matrix = Matrix.Translation(Vector(position) * SCALE)
    return matrix @ rotation.to_4x4() if rotation is not None else matrix


def assert_matrices(test, actual, expected, places=4):
    for row_a, row_e in zip(actual, expected):
        for a, e in zip(row_a, row_e):
            test.assertAlmostEqual(a, e, places=places)


class EditArmatureTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        self.other = bpy.data.objects.new("other", bpy.data.armatures.new("other"))
        bpy.context.scene.collection.objects.link(self.other)
        self.other.select_set(True)
        bpy.context.view_layer.objects.active = self.other

    def assert_scene_untouched(self, armature_obj):
        self.assertEqual(armature_obj.mode, 'OBJECT')
        self.assertEqual(armature_obj.users_collection, ())
        self.assertEqual(self.other.mode, 'OBJECT')
        self.assertTrue(self.other.select_get())
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)

    def test_creates_bones_and_restores_selection(self):
        armature_obj = bpy.data.objects.new("new", bpy.data.armatures.new("new"))
        with edit_armature(armature_obj) as edit_bones:
            self.assertEqual(self.other.mode, 'OBJECT')
            bone = edit_bones.new("bone")
            bone.tail = (0, 1, 0)
        self.assert_scene_untouched(armature_obj)
        self.assertEqual([bone.name for bone in armature_obj.data.bones], ["bone"])
        self.assertEqual(len(armature_obj.pose.bones), 1)

    def test_leaves_edit_mode_when_the_block_raises(self):
        armature_obj = bpy.data.objects.new("new", bpy.data.armatures.new("new"))
        with self.assertRaises(ValueError):
            with edit_armature(armature_obj) as edit_bones:
                edit_bones.new("bone").tail = (0, 1, 0)
                raise ValueError
        self.assert_scene_untouched(armature_obj)

    def test_armature_already_in_the_scene_stays_there(self):
        armature_obj = bpy.data.objects.new("new", bpy.data.armatures.new("new"))
        bpy.context.scene.collection.objects.link(armature_obj)
        with edit_armature(armature_obj) as edit_bones:
            edit_bones.new("bone").tail = (0, 1, 0)
        self.assertEqual(armature_obj.users_collection, (bpy.context.scene.collection,))
        self.assertEqual(armature_obj.mode, 'OBJECT')


class BuilderTests(unittest.TestCase):
    """Each builder's rest pose must be the bones' accumulated MDL transforms."""

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def check(self, armature_obj, names, expected, length):
        bones = armature_obj.data.bones
        self.assertEqual(sorted(bone.name for bone in bones), sorted(names))
        for name, parent, matrix in zip(names, PARENTS, expected):
            bone = bones[name]
            self.assertEqual(bone.parent.name if bone.parent else None, names[parent] if parent != -1 else None)
            assert_matrices(self, bone.matrix_local, matrix)
            self.assertAlmostEqual(bone.length, length, places=5)
        self.assertEqual(armature_obj.mode, 'OBJECT')
        self.assertEqual(armature_obj.users_collection, ())

    @staticmethod
    def header():
        return SimpleNamespace(name="models/test.mdl", flags=0)

    def test_goldsrc_v4(self):
        mdl = SimpleNamespace(bones=[SimpleNamespace(pos=p, parent=parent) for p, parent in zip(POSITIONS, PARENTS)])
        armature_obj, transforms = mdl4.create_armature("test", mdl, SCALE)
        expected = accumulate([local_matrix(p) for p in POSITIONS])
        self.check(armature_obj, [f"Bone_{i}" for i in range(len(PARENTS))], expected, 0.25 * SCALE)
        for matrix, transform in zip(expected, transforms):
            assert_matrices(self, transform, matrix)

    def goldsrc_euler(self, module):
        bones = [SimpleNamespace(name=f"b{i}" if i else "", pos=p, rot=r, parent=parent)
                 for i, (p, r, parent) in enumerate(zip(POSITIONS, EULERS, PARENTS))]
        armature_obj, transforms = module.create_armature(SimpleNamespace(header=self.header(), bones=bones), SCALE)
        expected = accumulate([local_matrix(p, Euler(r).to_matrix()) for p, r in zip(POSITIONS, EULERS)])
        self.check(armature_obj, ["Bone_0"] + [f"b{i}" for i in range(1, len(PARENTS))], expected, 0.25 * SCALE)
        for matrix, transform in zip(expected, transforms):
            assert_matrices(self, transform, matrix)
        return armature_obj

    def test_goldsrc_v6(self):
        armature_obj = self.goldsrc_euler(mdl6)
        self.assertEqual({bone.rotation_mode for bone in armature_obj.pose.bones}, {'XYZ'})

    def test_goldsrc_v9(self):
        self.goldsrc_euler(mdl9)

    def test_goldsrc_v10(self):
        self.goldsrc_euler(mdl10)

    def test_source1_v36(self):
        bones = [SimpleNamespace(name=f"b{i}", parent_id=parent, position=p, rotation=r)
                 for i, (p, r, parent) in enumerate(zip(POSITIONS, EULERS, PARENTS))]
        armature_obj = mdl36.create_armature(SimpleNamespace(header=self.header(), bones=bones), SCALE)
        expected = accumulate([local_matrix(p, Euler(r).to_matrix()) for p, r in zip(POSITIONS, EULERS)])
        self.check(armature_obj, [f"b{i}" for i in range(len(PARENTS))], expected, SCALE)

    def test_source1_v2531(self):
        quats = [Euler(r).to_quaternion() for r in EULERS]
        bones = [SimpleNamespace(name=f"b{i}", parent_bone_id=parent, position=p, quat=(q.x, q.y, q.z, q.w))
                 for i, (p, q, parent) in enumerate(zip(POSITIONS, quats, PARENTS))]
        armature_obj = mdl2531.create_armature(SimpleNamespace(header=self.header(), bones=bones), SCALE)
        expected = accumulate([local_matrix(p, q.to_matrix()) for p, q in zip(POSITIONS, quats)])
        self.check(armature_obj, [f"b{i}" for i in range(len(PARENTS))], expected, SCALE)


class SetPoseTests(unittest.TestCase):
    def test_pose_matches_the_animation_transforms(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bones = [SimpleNamespace(name=f"b{i}", parent_id=parent, position=p, rotation=r)
                 for i, (p, r, parent) in enumerate(zip(POSITIONS, EULERS, PARENTS))]
        armature_obj = mdl36.create_armature(SimpleNamespace(header=SimpleNamespace(name="t.mdl"), bones=bones), SCALE)
        bpy.context.scene.collection.objects.link(armature_obj)

        dtype = np.dtype([("pos", np.float32, 3), ("rot", np.float32, 4)])
        frame, locals_ = {}, []
        for i, (position, rotation) in enumerate(zip(POSITIONS, EULERS)):
            position = [v + i * 0.5 for v in position]
            q = Euler([v * 0.5 + 0.1 for v in rotation]).to_quaternion()
            frame[f"b{i}"] = np.array([(position, (q.x, q.y, q.z, q.w))], dtype)[0]
            locals_.append(local_matrix(position, q.to_matrix()))
        set_pose(armature_obj, frame, SCALE)
        bpy.context.view_layer.update()

        expected = accumulate(locals_)
        for i, matrix in enumerate(expected):
            assert_matrices(self, armature_obj.pose.bones[f"b{i}"].matrix, _ROOT_CORRECTION @ matrix)


if __name__ == "__main__":
    unittest.main()
