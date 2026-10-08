"""
Blender animation import from Source 1 MDL animation data.

Supports:
  - Inline animations (from main MDL)
  - External animations via .ani files (from include_model MDLs)
  - Per-animation Action creation
  - Name-based bone matching (handles differing bone counts across include models)
  - Delta (additive) animations, optionally placed on NLA tracks set to Combine
"""
from __future__ import annotations

from math import radians

import bpy
import numpy as np
from mathutils import Matrix, Euler, Quaternion, Vector

from SourceIO.blender_bindings.utils.bpy_utils import ActionCurveFactory
from SourceIO.library.models.mdl.load_animations import AnimationData
from SourceIO.logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('BlenderAnimImport')

_ROOT_CORRECTION = Euler((0, 0, radians(-90))).to_matrix().to_4x4()
_INTERPOLATION_LINEAR = 1  # bpy.types.Keyframe.interpolation enum value


def import_animations_to_armature(
        armature_obj: bpy.types.Object,
        mdl_name: str,
        animations: list[AnimationData],
        scale: float,
        compact_animations: bool,
        delta_animations_to_nla: bool = False,
) -> list[tuple[bpy.types.Action, bpy.types.ActionSlot]]:
    """Import ``animations`` and return the ``(action, slot)`` pair created for each one."""
    if not animations:
        return []

    rest_matrices = {bone.name: bone.matrix_local.copy() for bone in armature_obj.data.bones}
    action_factory = ActionCurveFactory(mdl_name, armature_obj, not compact_animations)

    created = []
    deltas = []
    for anim_data in animations:
        try:
            result = _create_action(armature_obj, action_factory, anim_data, scale, rest_matrices)
        except Exception as ex:
            logger.error(f"Failed to import animation '{anim_data.name}': {ex}")
            continue
        if result is None:
            continue
        created.append(result)
        if anim_data.is_delta:
            deltas.append(result)

    if delta_animations_to_nla and deltas:
        push_to_nla(armature_obj, deltas, blend_type='COMBINE')
    return created


def set_pose(armature_obj: bpy.types.Object, frame: dict[str, np.void], scale: float):
    """Pose the armature at one frame of a non-delta animation (parent-relative ``pos`` and xyzw
    ``rot`` per bone name), with the bone spaces ``_create_action`` keys animations in."""
    bones = armature_obj.data.bones
    for bone_name, data in frame.items():
        bone = bones.get(bone_name[:63])
        if bone is None:
            continue
        parent_space = bone.parent.matrix_local if bone.parent else _ROOT_CORRECTION
        x, y, z, w = (float(value) for value in data["rot"])
        local = Matrix.Translation(Vector(data["pos"].tolist()) * scale) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
        armature_obj.pose.bones[bone.name].matrix_basis = bone.matrix_local.inverted() @ parent_space @ local


def push_to_nla(armature_obj: bpy.types.Object,
                actions: list[tuple[bpy.types.Action, bpy.types.ActionSlot]],
                blend_type: str = 'COMBINE'):
    """Put each ``(action, slot)`` on its own muted NLA track, ready to be enabled and layered."""
    adt = armature_obj.animation_data or armature_obj.animation_data_create()
    for action, slot in actions:
        track = adt.nla_tracks.new()
        track.name = slot.name_display
        strip = track.strips.new(slot.name_display, 0, action)
        strip.action_slot = slot
        strip.blend_type = blend_type
        track.mute = True


def _quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product of (w, x, y, z) quaternions, broadcasting over leading axes."""
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], axis=-1)


def _make_continuous(quats: np.ndarray) -> np.ndarray:
    """Flip quaternion signs so consecutive frames never take the long way around."""
    dots = np.einsum('ij,ij->i', quats[1:], quats[:-1])
    signs = np.concatenate([[1.0], np.cumprod(np.where(dots < 0.0, -1.0, 1.0))])
    return quats * signs[:, None]


def _write_curves(factory: ActionCurveFactory, bone_name: str, data_path: str, values: np.ndarray, group):
    frame_count = values.shape[0]
    frames = np.arange(frame_count, dtype=np.float32)
    keys = np.empty((frame_count, 2), dtype=np.float32)
    keys[:, 0] = frames
    interpolation = np.full(frame_count, _INTERPOLATION_LINEAR, dtype=np.int32)
    for channel in range(values.shape[1]):
        curve = factory.new_fcurve(data_path=f'pose.bones["{bone_name}"].{data_path}', index=channel, group=group)
        curve.auto_smoothing = "NONE"
        curve.keyframe_points.add(count=frame_count)
        keys[:, 1] = values[:, channel]
        curve.keyframe_points.foreach_set("co_ui", keys.ravel())
        curve.keyframe_points.foreach_set("interpolation", interpolation)
        curve.update()


def _create_action(
        armature_obj: bpy.types.Object,
        factory: ActionCurveFactory,
        anim_data: AnimationData,
        scale: float,
        rest_matrices: dict[str, Matrix],
) -> tuple[bpy.types.Action, bpy.types.ActionSlot] | None:
    if anim_data.frame_count == 0:
        return None

    result = factory.new_action(anim_data.name)

    for bone_name, bone_anim_data in anim_data.frames.items():
        bpy_bone: bpy.types.Bone = armature_obj.data.bones[bone_name]
        frame_count = min(anim_data.frame_count, len(bone_anim_data))
        if frame_count == 0:
            continue
        bone_anim_data = bone_anim_data[:frame_count]

        positions = np.asarray(bone_anim_data["pos"], dtype=np.float64).reshape(-1, 3) * scale
        xyzw = np.asarray(bone_anim_data["rot"], dtype=np.float64).reshape(-1, 4)
        quats = xyzw[:, [3, 0, 1, 2]]

        if not anim_data.is_delta:
            parent_space = rest_matrices[bpy_bone.parent.name] if bpy_bone.parent else _ROOT_CORRECTION
            transform = rest_matrices[bone_name].inverted() @ parent_space
            linear = np.array(transform.to_3x3(), dtype=np.float64)
            offset = np.array(transform.translation, dtype=np.float64)
            positions = positions @ linear.T + offset
            # Keep the sign Matrix.decompose() picks for the first frame.
            reference = (transform @ Quaternion(quats[0]).to_matrix().to_4x4()).decompose()[1]
            quats = _quat_multiply(np.array(transform.to_quaternion(), dtype=np.float64), quats)
            quats /= np.linalg.norm(quats, axis=1, keepdims=True)
            if np.dot(quats[0], np.array(reference)) < 0.0:
                quats = -quats
        else:
            if not bpy_bone.parent:
                positions = positions @ np.array(_ROOT_CORRECTION.to_3x3(), dtype=np.float64).T
            positions = positions @ np.array(bpy_bone.matrix.inverted(), dtype=np.float64).T

        quats = _make_continuous(quats)

        group = factory.new_group(bone_name)
        _write_curves(factory, bone_name, "location", positions.astype(np.float32), group)
        _write_curves(factory, bone_name, "rotation_quaternion", quats.astype(np.float32), group)

    return result
