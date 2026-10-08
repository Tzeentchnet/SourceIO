"""
Blender import of Source 2 skeletal animations.

Decoding is a port of ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat),
see ``SourceIO.library.source2.animation``. Each animation becomes its own Action (slotted, one slot named
after the model) keyed on every frame with linear interpolation.

Values are written in the rest-relative bone space of the armature built by ``vmdl_loader.create_armature``.
Delta (additive) animations are composed over the bind pose, and root motion (movement data) is baked
into the root bones, the same way VRF's glTF exporter does.
"""
from __future__ import annotations

import bpy
import numpy as np
from mathutils import Matrix

from SourceIO.blender_bindings.models.import_animations import _make_continuous, _quat_multiply, _write_curves
from SourceIO.blender_bindings.utils.bpy_utils import ActionCurveFactory
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.source2.animation import DecodedAnimation, SequenceAnimation, Skeleton, load_model_animations, \
    model_skeleton
from SourceIO.library.source2.compiled_resource import CompiledResource
from SourceIO.logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('Source2::AnimImport')


def import_animations(content_manager: ContentManager, model_resource: CompiledResource,
                      armature_obj: bpy.types.Object, scale: float,
                      apply_root_motion: bool = True) -> list[tuple[bpy.types.Action, bpy.types.ActionSlot]]:
    """Import every animation of ``model_resource`` onto ``armature_obj``; returns the created (action, slot) pairs."""
    skeleton = model_skeleton(model_resource)
    if skeleton is None:
        return []
    try:
        animations = load_model_animations(model_resource, content_manager, skeleton)
    except Exception as ex:
        logger.exception(f"Failed to load animations of {model_resource.name}", ex)
        return []
    if not animations:
        return []

    binding = _ArmatureBinding(armature_obj, skeleton)
    factory = ActionCurveFactory(model_resource.name, armature_obj, legacy_behavior=True)
    created = []
    for animation in animations:
        if animation.frame_count <= 0:
            continue
        try:
            decoded = animation.decode(skeleton)
            result = _create_action(factory, binding, animation, decoded, scale, apply_root_motion)
        except Exception as ex:
            logger.exception(f"Failed to import animation '{animation.name}'", ex)
            continue
        created.append(result)
    logger.info(f"Imported {len(created)} animation(s) for {model_resource.name}")
    return created


class _ArmatureBinding:
    """How every skeleton bone maps onto the armature's rest pose."""

    def __init__(self, armature_obj: bpy.types.Object, skeleton: Skeleton):
        self.skeleton = skeleton
        bones = armature_obj.data.bones
        # (skeleton index, bone name, rest pose relative to the Blender parent, Blender parent skeleton index)
        self.local_bones: list[tuple[int, str, Matrix]] = []
        self.reparented_bones: list[tuple[int, str, Matrix, int]] = []
        index_by_name = {}
        for i, name in enumerate(skeleton.names):
            index_by_name.setdefault(name, i)
        for i, name in enumerate(skeleton.names):
            bone = bones.get(name)
            if bone is None or index_by_name[name] != i:  # missing, or a duplicate name the armature merged
                continue
            s2_parent = int(skeleton.parents[i])
            parent_index = index_by_name.get(bone.parent.name, -1) if bone.parent else -1
            rest = bone.matrix_local
            rest_local = bone.parent.matrix_local.inverted() @ rest if bone.parent else rest.copy()
            if parent_index == s2_parent:
                self.local_bones.append((i, name, rest_local.inverted()))
            else:
                # Re-parented when the armature was built (cloth controls), so go through armature space.
                self.reparented_bones.append((i, name, rest_local.inverted(), parent_index))


def _apply_root_motion(skeleton: Skeleton, movement_positions: np.ndarray, movement_angles: np.ndarray,
                       positions: np.ndarray, rotations: np.ndarray):
    """Bake planar movement (translation + yaw) into the root bones (VRF glTF exporter behaviour)."""
    roots = np.nonzero(skeleton.parents < 0)[0]
    angles = np.radians(movement_angles.astype(np.float64))
    yaw = np.zeros((len(angles), 4))
    yaw[:, 0] = np.cos(angles / 2)
    yaw[:, 3] = np.sin(angles / 2)
    cos, sin = np.cos(angles)[:, None], np.sin(angles)[:, None]
    offset = movement_positions.astype(np.float64).copy()
    offset[:, 2] = 0
    for root in roots:
        x, y, z = positions[:, root, 0].copy(), positions[:, root, 1].copy(), positions[:, root, 2].copy()
        positions[:, root, 0] = cos[:, 0] * x - sin[:, 0] * y + offset[:, 0]
        positions[:, root, 1] = sin[:, 0] * x + cos[:, 0] * y + offset[:, 1]
        positions[:, root, 2] = z + offset[:, 2]
        rotations[:, root] = _quat_multiply(yaw, rotations[:, root])


def _local_matrices(positions: np.ndarray, rotations: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """(frames, bones, 4, 4) bone-local matrices from translation, (w, x, y, z) rotation and uniform scale."""
    w, x, y, z = np.moveaxis(rotations, -1, 0)
    matrices = np.zeros(positions.shape[:-1] + (4, 4))
    matrices[..., 0, 0] = 1 - 2 * (y * y + z * z)
    matrices[..., 0, 1] = 2 * (x * y - z * w)
    matrices[..., 0, 2] = 2 * (x * z + y * w)
    matrices[..., 1, 0] = 2 * (x * y + z * w)
    matrices[..., 1, 1] = 1 - 2 * (x * x + z * z)
    matrices[..., 1, 2] = 2 * (y * z - x * w)
    matrices[..., 2, 0] = 2 * (x * z - y * w)
    matrices[..., 2, 1] = 2 * (y * z + x * w)
    matrices[..., 2, 2] = 1 - 2 * (x * x + y * y)
    matrices[..., :3, :3] *= scales[..., None, None]
    matrices[..., :3, 3] = positions
    matrices[..., 3, 3] = 1
    return matrices


def _world_matrices(skeleton: Skeleton, local: np.ndarray) -> np.ndarray:
    world = np.empty_like(local)
    done = np.zeros(len(skeleton), dtype=bool)

    def resolve(index: int):
        if done[index]:
            return
        parent = int(skeleton.parents[index])
        if parent >= 0:
            resolve(parent)
            world[:, index] = world[:, parent] @ local[:, index]
        else:
            world[:, index] = local[:, index]
        done[index] = True

    for bone_index in range(len(skeleton)):
        resolve(bone_index)
    return world


def _create_action(factory: ActionCurveFactory, binding: _ArmatureBinding, animation: SequenceAnimation,
                   decoded: DecodedAnimation, scale: float, apply_root_motion: bool):
    skeleton = binding.skeleton
    positions = decoded.positions.astype(np.float64) * scale
    rotations = decoded.rotations.astype(np.float64)
    rotations /= np.linalg.norm(rotations, axis=-1, keepdims=True)
    scales = decoded.scales.astype(np.float64)
    if apply_root_motion and decoded.movement_positions is not None:
        _apply_root_motion(skeleton, decoded.movement_positions * scale, decoded.movement_angles, positions, rotations)

    action, slot = factory.new_action(animation.name)
    action["fps"] = animation.fps
    action["looping"] = animation.looping
    action["delta"] = animation.delta

    for index, name, rest_local_inv in binding.local_bones:
        linear = np.array(rest_local_inv.to_3x3(), dtype=np.float64)
        offset = np.array(rest_local_inv.translation, dtype=np.float64)
        rest_rotation = np.array(rest_local_inv.to_quaternion(), dtype=np.float64)
        location = positions[:, index] @ linear.T + offset
        rotation = _quat_multiply(rest_rotation, rotations[:, index])
        _write_bone(factory, name, location, rotation, scales[:, index] if decoded.animated_scale[index] else None)

    if binding.reparented_bones:
        world = _world_matrices(skeleton, _local_matrices(positions, rotations, scales))
        for index, name, rest_local_inv, parent_index in binding.reparented_bones:
            rest_local_inv = np.array(rest_local_inv, dtype=np.float64)
            if parent_index >= 0:
                basis = rest_local_inv @ np.linalg.inv(world[:, parent_index]) @ world[:, index]
            else:
                basis = rest_local_inv @ world[:, index]
            location = np.empty((len(basis), 3))
            rotation = np.empty((len(basis), 4))
            for frame, matrix in enumerate(basis):
                loc, rot, _ = Matrix(matrix.tolist()).decompose()
                location[frame] = loc
                rotation[frame] = rot
            _write_bone(factory, name, location, rotation, scales[:, index] if decoded.animated_scale[index] else None)
    return action, slot


def _write_bone(factory: ActionCurveFactory, bone_name: str, location: np.ndarray, rotation: np.ndarray,
                scale: np.ndarray | None):
    rotation = rotation / np.linalg.norm(rotation, axis=1, keepdims=True)
    # Prefer the w >= 0 hemisphere on the first frame, then never take the long way between frames.
    if rotation[0, 0] < 0:
        rotation = -rotation
    rotation = _make_continuous(rotation)
    group = factory.new_group(bone_name)
    _write_curves(factory, bone_name, "location", location.astype(np.float32), group)
    _write_curves(factory, bone_name, "rotation_quaternion", rotation.astype(np.float32), group)
    if scale is not None:
        _write_curves(factory, bone_name, "scale", np.repeat(scale[:, None], 3, axis=1).astype(np.float32), group)
