"""
Blender import of Source 2 skeletal animations.

Decoding is a port of ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat),
see ``SourceIO.library.source2.animation``. Each animation becomes its own Action (slotted, one slot named
after the model) keyed on every frame with linear interpolation.

Values are written in the rest-relative bone space of the armature built by ``vmdl_loader.create_armature``.
Delta (additive) animations are composed over the bind pose, and root motion (movement data) is baked
into the root bones, the same way VRF's glTF exporter does.

Animation graph 2 clips (``.vnmclip_c``) are authored on their own NM skeleton and play on the model by
bone name: :func:`import_animations` takes the model's graph clips that match a name filter, and
:func:`import_clips` puts clip files on any armature whose bone names match. A clip's events become pose
markers on its action.
"""
from __future__ import annotations

import json

import bpy
import numpy as np
from mathutils import Matrix

from ..models.import_animations import (_INTERPOLATION_LINEAR, _make_continuous,
                                        _quat_multiply, _write_curves)
from ..utils.bpy_utils import ActionCurveFactory
from ...library.shared.content_manager import ContentManager
from ...library.source2.animation import (AnimationClip, ChannelAttribute, ClipAnimation,
                                         ClipLoader, DecodedAnimation, DecodedDataChannel,
                                         SequenceAnimation, Skeleton, clip_names,
                                         load_graph_clips, load_model_animations,
                                         model_skeleton)
from ...library.source2.compiled_resource import CompiledResource
from ...logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('Source2::AnimImport')


def import_animations(content_manager: ContentManager, model_resource: CompiledResource,
                      armature_obj: bpy.types.Object, scale: float, apply_root_motion: bool = True,
                      clip_patterns: list[str] | None = None) -> list[tuple[bpy.types.Action, bpy.types.ActionSlot]]:
    """Import every animation of ``model_resource`` onto ``armature_obj``, plus the clips of its animation
    graphs whose path or name matches one of ``clip_patterns`` (``fnmatch``-style); returns the created
    (action, slot) pairs."""
    skeleton = model_skeleton(model_resource)
    if skeleton is None:
        return []
    try:
        animations = load_model_animations(model_resource, content_manager, skeleton)
    except Exception as ex:
        logger.exception(f"Failed to load animations of {model_resource.name}", ex)
        animations = []
    clips = []
    if clip_patterns:
        try:
            clips = load_graph_clips(model_resource, content_manager, clip_patterns, skeleton)
        except Exception as ex:
            logger.exception(f"Failed to load animation graph clips of {model_resource.name}", ex)
    if not animations and not clips:
        return []

    binding = _ArmatureBinding(armature_obj, skeleton)
    factory = ActionCurveFactory(model_resource.name, armature_obj, legacy_behavior=True)
    created = _create_actions(factory, binding, animations, scale, apply_root_motion)
    created += _create_actions(factory, binding, clips, scale, apply_root_motion)
    logger.info(f"Imported {len(created)} animation(s) for {model_resource.name}"
                + (f", {len(clips)} of them graph clips" if clips else ""))
    return created


def import_clips(content_manager: ContentManager, clips: list[tuple[str, AnimationClip]],
                 armature_obj: bpy.types.Object, scale: float,
                 apply_root_motion: bool = True) -> list[tuple[bpy.types.Action, bpy.types.ActionSlot]]:
    """Import (path, clip) pairs onto any armature, matching bones by name; the clips' skeletons are
    looked up in ``content_manager``.

    The armature's rest pose stands in for the model skeleton, so it should be in Source units times ``scale``.
    """
    skeleton = armature_skeleton(armature_obj, scale)
    loader = ClipLoader(content_manager)
    animations = []
    for path, clip in clips:
        animation = loader.bind(clip, skeleton, path)
        if animation is None:
            missing = [candidate.skeleton_name for candidate in [clip, *clip.secondary]
                       if loader.skeleton(candidate.skeleton_name) is None]
            reason = f" (skeleton {', '.join(missing)} not found)" if missing else ""
            logger.error(f"Clip {path} drives no bone of {armature_obj.name}{reason}")
            continue
        animations.append(animation)
    if not animations:
        return []
    binding = _ArmatureBinding(armature_obj, skeleton)
    # Share the slot name of the armature's current action (the model name after a VMDL import), so
    # swapping actions keeps the binding.
    adt = armature_obj.animation_data
    slot_name = adt.action_slot.name_display if adt and adt.action_slot else armature_obj.name
    factory = ActionCurveFactory(slot_name, armature_obj, legacy_behavior=True)
    return _create_actions(factory, binding, animations, scale, apply_root_motion)


def import_sequence_animations(
        animations: list[SequenceAnimation],
        armature_obj: bpy.types.Object,
        scale: float,
        apply_root_motion: bool = True,
        source_name: str | None = None,
) -> list[tuple[bpy.types.Action, bpy.types.ActionSlot]]:
    """Import already-bound VANIM/VAGRP sequences onto an armature."""
    if not animations:
        return []
    skeleton = armature_skeleton(armature_obj, scale)
    binding = _ArmatureBinding(armature_obj, skeleton)
    adt = armature_obj.animation_data
    slot_name = (
        adt.action_slot.name_display
        if adt and adt.action_slot
        else source_name or armature_obj.name
    )
    factory = ActionCurveFactory(slot_name, armature_obj, legacy_behavior=True)
    return _create_actions(factory, binding, animations, scale, apply_root_motion)


def armature_skeleton(armature_obj: bpy.types.Object, scale: float) -> Skeleton:
    """The armature's rest pose as a skeleton in Source units, bones in the armature's order."""
    bones = armature_obj.data.bones
    names = [bone.name for bone in bones]
    index = {name: i for i, name in enumerate(names)}
    parents = np.array([index[bone.parent.name] if bone.parent else -1 for bone in bones], dtype=np.int32)
    positions = np.zeros((len(bones), 3), dtype=np.float32)
    rotations = np.zeros((len(bones), 4), dtype=np.float32)
    for i, bone in enumerate(bones):
        local = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
        translation, rotation, _ = local.decompose()
        positions[i] = translation / scale
        rotations[i] = (rotation.x, rotation.y, rotation.z, rotation.w)
    return Skeleton(names, parents, positions, rotations, np.zeros(len(bones), dtype=np.int64))


def _create_actions(factory: ActionCurveFactory, binding: '_ArmatureBinding',
                    animations: list[SequenceAnimation | ClipAnimation], scale: float, apply_root_motion: bool):
    names = clip_names([animation.path for animation in animations if isinstance(animation, ClipAnimation)])
    created = []
    for animation in animations:
        if animation.frame_count <= 0:
            continue
        is_clip = isinstance(animation, ClipAnimation)
        name = names[animation.path] if is_clip else animation.name
        try:
            decoded = animation.decode(binding.skeleton)
            action, slot = _create_action(factory, binding, animation, decoded, scale, apply_root_motion, name)
        except (ValueError, IndexError, KeyError, RuntimeError) as ex:
            logger.exception(f"Failed to import animation '{name}'", ex)
            continue
        if is_clip:
            action["clip"] = animation.path
            action["clip_skeleton"] = animation.clip.skeleton_name
            action["sourceio_events"] = json.dumps(
                [event.as_dict() for event in animation.events],
                default=_json_default,
                separators=(',', ':'),
            )
            for event in animation.events:
                action.pose_markers.new(event.marker_name).frame = event.frame(animation.frame_count)
        created.append((action, slot))
    return created


class _ArmatureBinding:
    """How every skeleton bone maps onto the armature's rest pose."""

    def __init__(self, armature_obj: bpy.types.Object, skeleton: Skeleton):
        self.armature_obj = armature_obj
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
    """Bake movement (translation + yaw) into the root bones (VRF glTF exporter behaviour)."""
    roots = np.nonzero(skeleton.parents < 0)[0]
    angles = np.radians(movement_angles.astype(np.float64))
    yaw = np.zeros((len(angles), 4))
    yaw[:, 0] = np.cos(angles / 2)
    yaw[:, 3] = np.sin(angles / 2)
    cos, sin = np.cos(angles)[:, None], np.sin(angles)[:, None]
    offset = movement_positions.astype(np.float64)
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


def _create_action(factory: ActionCurveFactory, binding: _ArmatureBinding,
                   animation: SequenceAnimation | ClipAnimation, decoded: DecodedAnimation, scale: float,
                   apply_root_motion: bool, name: str | None = None):
    skeleton = binding.skeleton
    positions = decoded.positions.astype(np.float64) * scale
    rotations = decoded.rotations.astype(np.float64)
    rotations /= np.linalg.norm(rotations, axis=-1, keepdims=True)
    scales = decoded.scales.astype(np.float64)
    if apply_root_motion and decoded.movement_positions is not None:
        _apply_root_motion(skeleton, decoded.movement_positions * scale, decoded.movement_angles, positions, rotations)

    action, slot = factory.new_action(name or animation.name)
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
    _write_data_channels(factory, binding.armature_obj, action, decoded.data_channels)
    if decoded.diagnostics:
        action["sourceio_animation_diagnostics"] = json.dumps(
            [
                {
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "severity": diagnostic.severity.value,
                    "details": diagnostic.details,
                }
                for diagnostic in decoded.diagnostics
            ],
            default=_json_default,
            separators=(',', ':'),
        )
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


def _write_data_channels(factory: ActionCurveFactory, armature_obj: bpy.types.Object,
                         action: bpy.types.Action, channels: tuple[DecodedDataChannel, ...]):
    if not channels:
        return
    metadata = []
    shape_keys_by_name = None
    derived_morphs = any(channel.channel_class == "MorphTarget" for channel in channels)
    for channel in channels:
        if not len(channel.names) or channel.values.shape[0] == 0:
            continue
        category = _channel_category(channel)
        group = factory.new_group({
            "morph": "Morph targets",
            "flex": "Flex controllers",
            "curve": "Float curves",
            "user": "User channels",
            "data": "Data channels",
        }.get(category, "Animation metadata"))
        for target_index, name in enumerate(channel.names):
            components = channel.values.shape[2]
            property_name = f"sourceio:{category}:{name}"
            initial = channel.values[0, target_index]
            armature_obj[property_name] = float(initial[0]) if components == 1 else initial.tolist()
            data_path = f'[{json.dumps(property_name)}]'
            for component in range(components):
                _write_property_curve(
                    factory, data_path, component, channel.values[:, target_index, component], group
                )
            if (category == "morph"
                    or category == "flex" and not derived_morphs):
                if shape_keys_by_name is None:
                    shape_keys_by_name = _shape_keys_for_armature(armature_obj)
                _bind_shape_key_drivers(
                    armature_obj, name, property_name, shape_keys_by_name
                )
        metadata.append({
            "attribute": channel.attribute.name,
            "channel_class": channel.channel_class,
            "variable_name": channel.variable_name,
            "names": channel.names,
            "components": channel.values.shape[2],
            "metadata": channel.metadata,
        })
    action["sourceio_animation_channels"] = json.dumps(
        metadata, default=_json_default, separators=(',', ':')
    )


def _channel_category(channel: DecodedDataChannel) -> str:
    if channel.channel_class == "MorphTarget":
        return "morph"
    if channel.channel_class == "MorphChannel":
        return "flex"
    if channel.channel_class == "NmFloatCurve":
        return "curve"
    if channel.attribute is ChannelAttribute.USER:
        return "user"
    if channel.attribute is ChannelAttribute.DATA:
        return "data" if not channel.channel_class else "unknown"
    return "unknown"


def _write_property_curve(factory: ActionCurveFactory, data_path: str, component: int,
                          values: np.ndarray, group):
    frame_count = len(values)
    curve = factory.new_fcurve(data_path=data_path, index=component, group=group)
    curve.auto_smoothing = "NONE"
    curve.keyframe_points.add(count=frame_count)
    coordinates = np.empty((frame_count, 2), dtype=np.float32)
    coordinates[:, 0] = np.arange(frame_count, dtype=np.float32)
    coordinates[:, 1] = values
    curve.keyframe_points.foreach_set("co_ui", coordinates.ravel())
    curve.keyframe_points.foreach_set(
        "interpolation", np.full(frame_count, _INTERPOLATION_LINEAR, dtype=np.int32)
    )
    curve.update()


def _shape_keys_for_armature(armature_obj: bpy.types.Object):
    result = {}
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.data.shape_keys is None:
            continue
        uses_armature = obj.parent == armature_obj or any(
            modifier.type == 'ARMATURE' and modifier.object == armature_obj
            for modifier in obj.modifiers
        )
        if not uses_armature:
            continue
        for key in obj.data.shape_keys.key_blocks:
            result.setdefault(key.name.casefold(), []).append((obj, key))
    return result


def _bind_shape_key_drivers(
        armature_obj: bpy.types.Object,
        shape_name: str,
        property_name: str,
        shape_keys_by_name,
):
    for obj, key in shape_keys_by_name.get(shape_name.casefold(), ()):
        shape_keys = obj.data.shape_keys
        data_path = key.path_from_id('value')
        animation_data = shape_keys.animation_data_create()
        curve = animation_data.drivers.find(data_path)
        if curve is not None and curve.driver.expression not in ('', 'sourceio_value'):
            logger.warn(
                f"Shape key {obj.name}:{shape_name} already has a non-SourceIO driver; "
                "the animation channel was not bound"
            )
            continue
        curve = curve or shape_keys.driver_add(data_path)
        driver = curve.driver
        driver.type = 'SCRIPTED'
        driver.expression = 'sourceio_value'
        while driver.variables:
            driver.variables.remove(driver.variables[0])
        variable = driver.variables.new()
        variable.name = 'sourceio_value'
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = armature_obj
        variable.targets[0].data_path = f'[{json.dumps(property_name)}]'


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes_hex": bytes(value).hex()}
    if isinstance(value, tuple):
        return list(value)
    return repr(value)
