"""Source 2 skeletal animation (``ANIM`` block / ``.vanim_c``) decoding.

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat):
``ResourceTypes/ModelAnimation/SequenceAnimation.cs``, ``AnimationDataChannel.cs``,
``AnimationFrameBlock.cs``, ``AnimationMovement.cs`` and ``Frame.cs``.

Decoded animations are returned as bone-local transforms in Source units for every frame, in the
bone order of the model skeleton they were bound to.
"""
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .segments import (AnimationSegment, ChannelAttribute, SUPPORTED_DECODERS,
                                                         parse_segment_header)
from ....logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Animation")


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return np.asarray(value, dtype=np.uint8).tobytes()


def _flag(flags: Any, name: str) -> bool:
    if not flags:
        return False
    return bool(flags.get(name, False))


@dataclass(slots=True)
class Skeleton:
    """The model skeleton animations are decoded against (``m_modelSkeleton``)."""
    names: list[str]
    parents: np.ndarray  # (bones,) int, -1 for roots
    positions: np.ndarray  # (bones, 3) bind pose, parent space
    rotations: np.ndarray  # (bones, 4) bind pose, parent space, x y z w
    flags: np.ndarray  # (bones,) int

    @classmethod
    def from_model_data(cls, model_data) -> 'Skeleton':
        skeleton = model_data['m_modelSkeleton']
        names = [str(name) for name in skeleton['m_boneName']]
        count = len(names)
        positions = np.asarray(skeleton['m_bonePosParent'], dtype=np.float32).reshape(count, 3)
        rotations = np.asarray(skeleton['m_boneRotParent'], dtype=np.float32).reshape(count, 4)
        parents = np.asarray(skeleton['m_nParent'], dtype=np.int32).reshape(count)
        flags = np.asarray(skeleton.get('m_nFlag', np.zeros(count)), dtype=np.int64).reshape(count)
        return cls(names, parents, positions, rotations, flags)

    def __len__(self):
        return len(self.names)


@dataclass(slots=True)
class AnimationMovement:
    end_frame: int
    angle: float
    position: np.ndarray

    @classmethod
    def from_kv(cls, data) -> 'AnimationMovement':
        return cls(int(data.get('endframe', 0)), float(data.get('angle', 0.0)),
                   np.asarray(data.get('position', (0, 0, 0)), dtype=np.float32).reshape(3))


@dataclass(slots=True)
class DecodedAnimation:
    """Bone-local transforms of every frame, in Source units.

    ``rotations`` are (w, x, y, z). ``animated_*`` flags which bones a channel of this animation
    writes to. Delta animations are already composed over the bind pose.
    """
    positions: np.ndarray  # (frames, bones, 3)
    rotations: np.ndarray  # (frames, bones, 4)
    scales: np.ndarray  # (frames, bones)
    animated_position: np.ndarray  # (bones,) bool
    animated_rotation: np.ndarray  # (bones,) bool
    animated_scale: np.ndarray  # (bones,) bool
    movement_positions: np.ndarray | None = None  # (frames, 3), root motion; planar except for clips
    movement_angles: np.ndarray | None = None  # (frames,), root motion yaw in degrees


@dataclass(slots=True)
class SequenceAnimation:
    name: str
    fps: float
    frame_count: int
    looping: bool = False
    delta: bool = False
    hidden: bool = False
    frame_blocks: list[tuple[int, int, list[int]]] = field(default_factory=list)
    movements: list[AnimationMovement] = field(default_factory=list)
    segments: list[AnimationSegment | None] = field(default_factory=list)

    @classmethod
    def from_anim_desc(cls, anim_desc, segments: list[AnimationSegment | None], name: str | None = None,
                       sequence_flags=None) -> 'SequenceAnimation':
        flags = anim_desc.get('m_flags')
        delta = _flag(flags, 'm_bDelta') or _flag(flags, 'm_bAnimGraphAdditive')
        looping = _flag(flags, 'm_bLooping')
        hidden = _flag(flags, 'm_bHidden')
        if sequence_flags is not None:
            delta = delta or _flag(sequence_flags, 'm_bLegacyDelta')
            looping = _flag(sequence_flags, 'm_bLooping')
            hidden = _flag(sequence_flags, 'm_bHidden')

        p_data = anim_desc.get('m_pData') or {}
        frame_blocks = []
        for block in p_data.get('m_frameblockArray', []) or []:
            indices = [int(i) for i in np.asarray(block['m_segmentIndexArray']).reshape(-1)]
            frame_blocks.append((int(block['m_nStartFrame']), int(block['m_nEndFrame']), indices))
        movements = [AnimationMovement.from_kv(m) for m in anim_desc.get('m_movementArray', []) or []]
        return cls(name if name is not None else str(anim_desc['m_name']),
                   float(anim_desc.get('fps', 30.0)), int(p_data.get('m_nFrames', 0)),
                   looping, delta, hidden, frame_blocks, movements, segments)

    @property
    def has_movement(self) -> bool:
        return any(m.angle != 0 or np.any(m.position != 0) for m in self.movements)

    def movement_at(self, frame: int) -> tuple[np.ndarray, float]:
        """Root motion (position, yaw in degrees) reached at ``frame``.

        Each movement entry holds the accumulated motion at its end frame; frames in between are
        interpolated within their own segment.
        """
        if not self.movements:
            return np.zeros(3, dtype=np.float32), 0.0
        index = len(self.movements) - 1
        for i, movement in enumerate(self.movements):
            if movement.end_frame > frame:
                index = i
                break
        movement = self.movements[index]
        if index == 0:
            start_frame, start_pos, start_angle = 0, np.zeros(3, dtype=np.float32), 0.0
        else:
            previous = self.movements[index - 1]
            start_frame, start_pos, start_angle = previous.end_frame, previous.position, previous.angle
        span = movement.end_frame - start_frame
        t = 1.0 if span <= 0 else min(1.0, max(0.0, (frame - start_frame) / span))
        return start_pos + (movement.position - start_pos) * t, start_angle + (movement.angle - start_angle) * t

    def decode(self, skeleton: Skeleton) -> DecodedAnimation:
        frames = self.frame_count
        bones = len(skeleton)
        # NaN marks "not written yet"; values carry over to later frames like VRF's reused Frame.
        positions = np.full((frames, bones, 3), np.nan, dtype=np.float32)
        rotations = np.full((frames, bones, 4), np.nan, dtype=np.float32)  # x y z w
        scales = np.full((frames, bones, 1), np.nan, dtype=np.float32)
        animated = {ChannelAttribute.POSITION: np.zeros(bones, dtype=bool),
                    ChannelAttribute.ANGLE: np.zeros(bones, dtype=bool),
                    ChannelAttribute.SCALE: np.zeros(bones, dtype=bool)}
        targets_by_attribute = {ChannelAttribute.POSITION: positions,
                                ChannelAttribute.ANGLE: rotations,
                                ChannelAttribute.SCALE: scales}

        for start, end, segment_indices in self.frame_blocks:
            start = max(start, 0)
            end = min(end, frames - 1)
            if end < start:
                continue
            block_frames = end - start + 1
            for segment_index in segment_indices:
                if segment_index < 0 or segment_index >= len(self.segments):
                    continue
                segment = self.segments[segment_index]
                if segment is None or segment.attribute not in targets_by_attribute or not len(segment.targets):
                    continue
                target = targets_by_attribute[segment.attribute]
                values = segment.decode(block_frames)
                if values.shape[0] == 0:
                    continue
                if segment.is_static:
                    target[start:end + 1, segment.targets] = values[0][None]
                else:
                    target[start:start + values.shape[0], segment.targets] = values
                animated[segment.attribute][segment.targets] = True

        _forward_fill(positions)
        _forward_fill(rotations)
        _forward_fill(scales)

        if self.delta:
            # Deltas decode over an identity seed and compose over the bind pose: add the position,
            # post-multiply the rotation (VRF Animation.ComposeAdditiveOverBindPose).
            _fill_nan(positions, np.zeros(3, dtype=np.float32))
            _fill_nan(rotations, np.array((0, 0, 0, 1), dtype=np.float32))
            positions = positions + skeleton.positions[None]
            rotations = _quat_multiply_xyzw(np.broadcast_to(skeleton.rotations[None], rotations.shape), rotations)
        else:
            positions = np.where(np.isnan(positions), skeleton.positions[None], positions)
            rotations = np.where(np.isnan(rotations), skeleton.rotations[None], rotations)
        _fill_nan(scales, np.ones(1, dtype=np.float32))

        result = DecodedAnimation(positions, rotations[..., [3, 0, 1, 2]], scales[..., 0],
                                  animated[ChannelAttribute.POSITION], animated[ChannelAttribute.ANGLE],
                                  animated[ChannelAttribute.SCALE])
        if self.has_movement and not self.delta:
            movement = [self.movement_at(frame) for frame in range(frames)]
            result.movement_positions = np.array([m[0] for m in movement], dtype=np.float32).reshape(frames, 3)
            result.movement_positions[:, 2] = 0  # legacy movement is planar
            result.movement_angles = np.array([m[1] for m in movement], dtype=np.float32).reshape(frames)
        return result


def _forward_fill(values: np.ndarray):
    """Carry the last written value of every bone forward over frames that don't write it."""
    if values.shape[0] < 2:
        return
    written = ~np.isnan(values[..., 0])  # (frames, bones)
    index = np.where(written, np.arange(values.shape[0])[:, None], 0)
    np.maximum.accumulate(index, axis=0, out=index)
    filled = np.take_along_axis(values, index[..., None], axis=0)
    values[...] = filled


def _fill_nan(values: np.ndarray, value: np.ndarray):
    mask = np.isnan(values[..., 0])
    values[mask] = value


def _quat_multiply_xyzw(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], axis=-1).astype(np.float32)


class DataChannel:
    """A ``CAnimDataChannelDesc``: maps skeleton bones to the channel's element indices."""

    def __init__(self, skeleton: Skeleton, channel):
        channel_class = str(channel.get('m_szChannelClass', ''))
        variable = str(channel.get('m_szVariableName', ''))
        bone_attributes = {"Position": ChannelAttribute.POSITION, "Angle": ChannelAttribute.ANGLE,
                           "Scale": ChannelAttribute.SCALE}
        if channel_class == "BoneChannel":
            self.attribute = bone_attributes.get(variable, ChannelAttribute.UNKNOWN)
        elif channel_class == "MorphChannel":
            self.attribute = ChannelAttribute.DATA
        elif channel_class == "UserChannel":
            self.attribute = ChannelAttribute.USER
        else:
            self.attribute = bone_attributes.get(variable, ChannelAttribute.DATA if variable == "data"
                                                 else ChannelAttribute.UNKNOWN)

        names = [str(name) for name in channel.get('m_szElementNameArray', []) or []]
        indices = np.asarray(channel.get('m_nElementIndexArray', []), dtype=np.int64).reshape(-1)
        # bone index -> channel element index. Flex and user channels aren't bound (not imported).
        self.remap = np.full(len(skeleton), -1, dtype=np.int64)
        if self.attribute in (ChannelAttribute.POSITION, ChannelAttribute.ANGLE, ChannelAttribute.SCALE):
            lookup = {}
            for i, bone_name in enumerate(skeleton.names):
                lookup.setdefault(bone_name.casefold(), i)
            for name, element_index in zip(names, indices):
                bone_id = lookup.get(name.casefold(), -1)
                if bone_id != -1:
                    self.remap[bone_id] = element_index


def build_segments(animation_data, decode_key, skeleton: Skeleton) -> list[AnimationSegment | None]:
    """Bind every segment of an ``AnimationResourceData_t`` to ``skeleton`` (VRF BuildSegmentArray)."""
    decoder_names = [str(d['m_szName']) for d in animation_data.get('m_decoderArray', []) or []]
    channels = [DataChannel(skeleton, channel) for channel in decode_key.get('m_dataChannelArray', []) or []]
    segments: list[AnimationSegment | None] = []
    unsupported = set()
    for segment_kv in animation_data.get('m_segmentArray', []) or []:
        container = _as_bytes(segment_kv['m_container'])
        channel_index = int(segment_kv.get('m_nLocalChannel', -1))
        if len(container) < 8 or not 0 <= channel_index < len(channels):
            segments.append(None)
            continue
        channel = channels[channel_index]
        decoder_index, element_count, elements, payload = parse_segment_header(container)
        decoder = decoder_names[decoder_index] if 0 <= decoder_index < len(decoder_names) else f"#{decoder_index}"
        if channel.attribute in (ChannelAttribute.UNKNOWN, ChannelAttribute.DATA, ChannelAttribute.USER):
            segments.append(None)
            continue
        if decoder not in SUPPORTED_DECODERS:
            unsupported.add((decoder, channel.attribute.name))
            segments.append(None)
            continue

        element_positions = {int(element): i for i, element in reversed(list(enumerate(elements)))}
        bones = []
        wanted = []
        for bone_id, element in enumerate(channel.remap):
            position = element_positions.get(int(element), -1) if element != -1 else -1
            if position != -1:
                bones.append(bone_id)
                wanted.append(position)
        segments.append(AnimationSegment(decoder, channel.attribute, element_count, payload,
                                         np.asarray(wanted, dtype=np.int64), np.asarray(bones, dtype=np.int64)))
    for decoder, attribute in sorted(unsupported):
        logger.warn(f"Unsupported animation decoder {decoder!r} for {attribute} channel, skipped")
    return segments


def animations_from_data(animation_data, decode_key, skeleton: Skeleton) -> list[SequenceAnimation]:
    """All animations of an ``AnimationResourceData_t``, named after themselves (VRF FromData)."""
    anim_array = animation_data.get('m_animArray', []) or []
    if not anim_array:
        return []
    segments = build_segments(animation_data, decode_key, skeleton)
    return [SequenceAnimation.from_anim_desc(anim, segments) for anim in anim_array]


def animations_from_sequence_data(sequence_data, animation_data, decode_key,
                                  skeleton: Skeleton) -> list[SequenceAnimation]:
    """Animations named and flagged by the sequences of an ``ASEQ`` block (VRF FromSequenceData).

    A sequence plays the animation its first local reference names; animations no sequence uses
    are added under their own name.
    """
    anim_array = animation_data.get('m_animArray', []) or []
    if not anim_array:
        return []
    segments = build_segments(animation_data, decode_key, skeleton)
    sequence_names = [str(name) for name in sequence_data.get('m_localSequenceNameArray', []) or []]
    anim_lookup = {}
    for anim in anim_array:
        anim_lookup[str(anim['m_name']).casefold()] = anim

    def references(seq_desc) -> list[int]:
        fetch = seq_desc.get('m_fetch') or {}
        return [int(i) for i in np.asarray(fetch.get('m_localReferenceArray', []), dtype=np.int64).reshape(-1)]

    seq_descs = sequence_data.get('m_localS1SeqDescArray', []) or []
    sequence_lookup = {}
    for seq_desc in seq_descs:
        refs = references(seq_desc)
        if refs and 0 <= refs[0] < len(sequence_names):
            referenced = anim_lookup.get(sequence_names[refs[0]].casefold())
            if referenced is not None:
                sequence_lookup[str(seq_desc['m_sName']).casefold()] = referenced

    animations = []
    processed = set()
    for seq_desc in seq_descs:
        refs = references(seq_desc)
        if not refs or not 0 <= refs[0] < len(sequence_names):
            continue
        ref_name = sequence_names[refs[0]].casefold()
        anim_desc = anim_lookup.get(ref_name) or sequence_lookup.get(ref_name)
        name = str(seq_desc['m_sName'])
        if anim_desc is None:
            if len(refs) == 1:
                continue
            processed.add(name.casefold())
            continue  # a blend without data of its own has no frames to import
        processed.add(name.casefold())
        animations.append(SequenceAnimation.from_anim_desc(anim_desc, segments, name, seq_desc.get('m_flags')))

    for anim in anim_array:
        if str(anim['m_name']).casefold() in processed:
            continue
        animations.append(SequenceAnimation.from_anim_desc(anim, segments))
    return animations
