"""Source 2 skeletal animation (``ANIM`` block / ``.vanim_c``) decoding.

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat):
``ResourceTypes/ModelAnimation/SequenceAnimation.cs``, ``AnimationDataChannel.cs``,
``AnimationFrameBlock.cs``, ``AnimationMovement.cs`` and ``Frame.cs``.

Decoded animations are returned as bone-local transforms in Source units for every frame, in the
bone order of the model skeleton they were bound to.
"""
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np

from .segments import (AnimationSegment, ChannelAttribute, OpaqueAnimationSegment,
                       SUPPORTED_DECODERS, parse_segment_header)
from ..interfaces import Diagnostic, DiagnosticSeverity
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
class DecodedDataChannel:
    """A decoded non-skeletal animation channel."""

    attribute: ChannelAttribute
    channel_class: str
    variable_name: str
    names: tuple[str, ...]
    values: np.ndarray  # (frames, elements, components)
    animated: np.ndarray  # (elements,) bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def value(self, name: str) -> np.ndarray | None:
        folded = name.casefold()
        for index, candidate in enumerate(self.names):
            if candidate.casefold() == folded:
                return self.values[:, index]
        return None


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
    data_channels: tuple[DecodedDataChannel, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def channels(self, attribute: ChannelAttribute) -> tuple[DecodedDataChannel, ...]:
        return tuple(channel for channel in self.data_channels if channel.attribute is attribute)


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
    segments: list[AnimationSegment | OpaqueAnimationSegment | None] = field(default_factory=list)
    diagnostics: tuple[Diagnostic, ...] = ()
    morph_set: Any | None = None

    @classmethod
    def from_anim_desc(cls, anim_desc, segments: list[AnimationSegment | OpaqueAnimationSegment | None],
                       name: str | None = None, sequence_flags=None,
                       diagnostics: Iterable[Diagnostic] = (), morph_set=None) -> 'SequenceAnimation':
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
        return cls(
            name if name is not None else str(anim_desc['m_name']),
            float(anim_desc.get('fps', 30.0)),
            int(p_data.get('m_nFrames', 0)),
            looping,
            delta,
            hidden,
            frame_blocks,
            movements,
            segments,
            tuple(diagnostics),
            morph_set,
        )

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
                if (not isinstance(segment, AnimationSegment)
                        or segment.attribute not in targets_by_attribute
                        or not len(segment.targets)):
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

        data_channels, channel_diagnostics = self._decode_data_channels(frames)
        if self.morph_set is not None:
            derived, morph_diagnostics = self._evaluate_morph_targets(data_channels, frames)
            data_channels += derived
            channel_diagnostics += morph_diagnostics

        result = DecodedAnimation(
            positions,
            rotations[..., [3, 0, 1, 2]],
            scales[..., 0],
            animated[ChannelAttribute.POSITION],
            animated[ChannelAttribute.ANGLE],
            animated[ChannelAttribute.SCALE],
            data_channels=data_channels,
            diagnostics=tuple([*self.diagnostics, *channel_diagnostics]),
        )
        if self.has_movement and not self.delta:
            movement = [self.movement_at(frame) for frame in range(frames)]
            result.movement_positions = np.array([m[0] for m in movement], dtype=np.float32).reshape(frames, 3)
            result.movement_positions[:, 2] = 0  # legacy movement is planar
            result.movement_angles = np.array([m[1] for m in movement], dtype=np.float32).reshape(frames)
        return result

    def _decode_data_channels(self, frames: int) -> tuple[tuple[DecodedDataChannel, ...], list[Diagnostic]]:
        bone_attributes = {
            ChannelAttribute.POSITION, ChannelAttribute.ANGLE, ChannelAttribute.SCALE,
        }
        data_segment_indices = {
            index
            for index, segment in enumerate(self.segments)
            if isinstance(segment, AnimationSegment) and segment.attribute not in bone_attributes
        }
        if not data_segment_indices:
            return (), []
        values_by_channel: dict[
            tuple[ChannelAttribute, str, str, tuple[str, ...], int], np.ndarray
        ] = {}
        animated_by_channel: dict[
            tuple[ChannelAttribute, str, str, tuple[str, ...], int], np.ndarray
        ] = {}
        metadata_by_channel: dict[
            tuple[ChannelAttribute, str, str, tuple[str, ...], int], Mapping[str, Any]
        ] = {}
        diagnostics: list[Diagnostic] = []

        for start, end, segment_indices in self.frame_blocks:
            start = max(start, 0)
            end = min(end, frames - 1)
            if end < start:
                continue
            block_frames = end - start + 1
            for segment_index in segment_indices:
                if segment_index not in data_segment_indices:
                    continue
                segment = self.segments[segment_index]
                assert isinstance(segment, AnimationSegment)
                key = segment.channel_key
                target_names = segment.target_names
                if key not in values_by_channel:
                    values_by_channel[key] = np.full(
                        (frames, len(target_names), segment.components), np.nan, dtype=np.float32
                    )
                    animated_by_channel[key] = np.zeros(len(target_names), dtype=bool)
                    metadata_by_channel[key] = segment.metadata
                if not len(segment.targets):
                    continue
                decoded = segment.decode(block_frames)
                if decoded.shape[0] == 0:
                    continue
                target = values_by_channel[key]
                if segment.is_static:
                    target[start:end + 1, segment.targets] = decoded[0][None]
                else:
                    target[start:start + decoded.shape[0], segment.targets] = decoded
                animated_by_channel[key][segment.targets] = True

        channels = []
        for key, values in values_by_channel.items():
            _forward_fill(values)
            values[np.isnan(values)] = 0.0
            attribute, channel_class, variable_name, names, _components = key
            channels.append(DecodedDataChannel(
                attribute,
                channel_class,
                variable_name,
                names,
                values,
                animated_by_channel[key],
                metadata_by_channel[key],
            ))
        return tuple(channels), diagnostics

    def _evaluate_morph_targets(
            self,
            channels: tuple[DecodedDataChannel, ...],
            frames: int,
    ) -> tuple[tuple[DecodedDataChannel, ...], list[Diagnostic]]:
        controller_names = tuple(getattr(self.morph_set, "controller_names", ()))
        if not controller_names:
            return (), []
        values = np.zeros((frames, len(controller_names)), dtype=np.float32)
        name_to_index = {name.casefold(): index for index, name in enumerate(controller_names)}
        any_controller = False
        for channel in channels:
            if channel.attribute is not ChannelAttribute.DATA:
                continue
            for channel_index, name in enumerate(channel.names):
                target = name_to_index.get(name.casefold())
                if target is not None:
                    values[:, target] = channel.values[:, channel_index, 0]
                    any_controller = True
        if not any_controller:
            return (), []
        try:
            names, weights, diagnostics = self.morph_set.evaluate_flex_rules(values)
        except (ValueError, IndexError, TypeError, OverflowError) as ex:
            diagnostic = Diagnostic(
                "animation.morph_rules.failed",
                f"Failed to evaluate legacy morph rules for {self.name}: {ex}",
                DiagnosticSeverity.ERROR,
                details={"animation": self.name, "exception": type(ex).__name__},
            )
            return (), [diagnostic]
        if not names:
            return (), list(diagnostics)
        derived = DecodedDataChannel(
            ChannelAttribute.DATA,
            "MorphTarget",
            "weight",
            tuple(names),
            weights[:, :, None].astype(np.float32, copy=False),
            np.any(weights != 0.0, axis=0),
            {"derived_from": "m_FlexRules"},
        )
        return (derived,), list(diagnostics)


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
    """A ``CAnimDataChannelDesc`` and its target-domain remap."""

    def __init__(self, skeleton: Skeleton, channel, flex_names: Iterable[str] = (),
                 user_names: Iterable[str] = ()):
        self.channel_class = str(channel.get('m_szChannelClass', ''))
        self.variable_name = str(channel.get('m_szVariableName', ''))
        self.metadata = channel
        self.known = False
        bone_attributes = {"Position": ChannelAttribute.POSITION, "Angle": ChannelAttribute.ANGLE,
                           "Scale": ChannelAttribute.SCALE}
        if self.channel_class == "BoneChannel":
            self.attribute = bone_attributes.get(self.variable_name, ChannelAttribute.UNKNOWN)
            self.known = self.attribute is not ChannelAttribute.UNKNOWN
        elif self.channel_class == "MorphChannel":
            self.attribute = ChannelAttribute.DATA
            self.known = True
        elif self.channel_class == "UserChannel":
            self.attribute = ChannelAttribute.USER
            self.known = True
        else:
            self.attribute = bone_attributes.get(
                self.variable_name,
                ChannelAttribute.DATA if self.variable_name == "data" else ChannelAttribute.UNKNOWN,
            )
            self.known = not self.channel_class and self.attribute is not ChannelAttribute.UNKNOWN

        names = [str(name) for name in channel.get('m_szElementNameArray', []) or []]
        indices = np.asarray(channel.get('m_nElementIndexArray', []), dtype=np.int64).reshape(-1)
        def merged_domain(preferred: Iterable[str]) -> tuple[str, ...]:
            result = [str(name) for name in preferred]
            present = {name.casefold() for name in result}
            for name in names:
                if name.casefold() not in present:
                    result.append(name)
                    present.add(name.casefold())
            return tuple(result)

        if self.attribute in (ChannelAttribute.POSITION, ChannelAttribute.ANGLE, ChannelAttribute.SCALE):
            self.target_names = tuple(skeleton.names)
        elif self.attribute is ChannelAttribute.DATA:
            self.target_names = merged_domain(flex_names)
        elif self.attribute is ChannelAttribute.USER:
            self.target_names = merged_domain(user_names)
        else:
            self.target_names = tuple(names)

        self.remap = np.full(len(self.target_names), -1, dtype=np.int64)
        lookup: dict[str, int] = {}
        for target_index, target_name in enumerate(self.target_names):
            lookup.setdefault(target_name.casefold(), target_index)
        for name, element_index in zip(names, indices):
            target = lookup.get(name.casefold(), -1)
            if target != -1:
                self.remap[target] = element_index


def _decode_key_user_names(decode_key) -> tuple[str, ...]:
    names = []
    for user in decode_key.get('m_userArray', []) or []:
        if hasattr(user, 'get'):
            name = user.get('m_name', user.get('name', ''))
        else:
            name = user
        if name is not None and str(name):
            names.append(str(name))
    return tuple(names)


def build_segments(animation_data, decode_key, skeleton: Skeleton,
                   flex_names: Iterable[str] = ()) -> list[AnimationSegment | OpaqueAnimationSegment | None]:
    """Bind every segment of an ``AnimationResourceData_t`` to ``skeleton`` (VRF BuildSegmentArray)."""
    decoder_names = [str(d['m_szName']) for d in animation_data.get('m_decoderArray', []) or []]
    user_names = _decode_key_user_names(decode_key)
    channels = [
        DataChannel(skeleton, channel, flex_names, user_names)
        for channel in decode_key.get('m_dataChannelArray', []) or []
    ]
    segments: list[AnimationSegment | OpaqueAnimationSegment | None] = []
    logged: set[tuple[str, str, str]] = set()
    for segment_index, segment_kv in enumerate(animation_data.get('m_segmentArray', []) or []):
        container = _as_bytes(segment_kv.get('m_container', b''))
        channel_index = int(segment_kv.get('m_nLocalChannel', -1))
        if not 0 <= channel_index < len(channels):
            diagnostic = Diagnostic(
                "animation.segment.channel_index",
                f"Animation segment {segment_index} references invalid channel {channel_index}",
                DiagnosticSeverity.ERROR,
                details={"segment_index": segment_index, "channel_index": channel_index},
            )
            segments.append(OpaqueAnimationSegment(
                "#invalid", ChannelAttribute.UNKNOWN, "", "", 0,
                np.empty(0, dtype=np.int16), container, segment_kv, (diagnostic,),
            ))
            continue
        channel = channels[channel_index]
        if len(container) < 8:
            diagnostic = Diagnostic(
                "animation.segment.header",
                f"Animation segment {segment_index} has a truncated header",
                DiagnosticSeverity.ERROR,
                details={"segment_index": segment_index, "size": len(container)},
            )
            segments.append(OpaqueAnimationSegment(
                "#truncated", channel.attribute, channel.channel_class, channel.variable_name, 0,
                np.empty(0, dtype=np.int16), container, segment_kv, (diagnostic,),
            ))
            continue
        try:
            decoder_index, element_count, elements, payload = parse_segment_header(container)
        except (ValueError, TypeError) as ex:
            diagnostic = Diagnostic(
                "animation.segment.header",
                f"Animation segment {segment_index} header could not be decoded: {ex}",
                DiagnosticSeverity.ERROR,
                details={"segment_index": segment_index, "exception": type(ex).__name__},
            )
            segments.append(OpaqueAnimationSegment(
                "#invalid", channel.attribute, channel.channel_class, channel.variable_name, 0,
                np.empty(0, dtype=np.int16), container, segment_kv, (diagnostic,),
            ))
            continue
        decoder = decoder_names[decoder_index] if 0 <= decoder_index < len(decoder_names) else f"#{decoder_index}"
        if decoder not in SUPPORTED_DECODERS:
            diagnostic = Diagnostic(
                "animation.decoder.unsupported",
                f"Unsupported animation decoder {decoder!r} for {channel.attribute.name} channel",
                DiagnosticSeverity.WARNING,
                details={
                    "segment_index": segment_index,
                    "decoder": decoder,
                    "channel_class": channel.channel_class,
                    "variable_name": channel.variable_name,
                },
            )
            segments.append(OpaqueAnimationSegment(
                decoder, channel.attribute, channel.channel_class, channel.variable_name,
                element_count, elements.copy(), payload, segment_kv, (diagnostic,),
            ))
            key = (decoder, channel.channel_class, channel.variable_name)
            if key not in logged:
                logger.warn(diagnostic.message)
                logged.add(key)
            continue

        element_positions = {int(element): i for i, element in reversed(list(enumerate(elements)))}
        targets = []
        wanted = []
        for target_id, element in enumerate(channel.remap):
            position = element_positions.get(int(element), -1) if element != -1 else -1
            if position != -1:
                targets.append(target_id)
                wanted.append(position)
        diagnostics = ()
        if channel.attribute is ChannelAttribute.UNKNOWN or not channel.known:
            diagnostic = Diagnostic(
                "animation.channel.unknown",
                f"Unknown animation channel {channel.channel_class!r}/{channel.variable_name!r} "
                f"was decoded as inspectable metadata",
                DiagnosticSeverity.WARNING,
                details={
                    "segment_index": segment_index,
                    "decoder": decoder,
                    "channel_class": channel.channel_class,
                    "variable_name": channel.variable_name,
                },
            )
            diagnostics = (diagnostic,)
            key = (decoder, channel.channel_class, channel.variable_name)
            if key not in logged:
                logger.warn(diagnostic.message)
                logged.add(key)
        segments.append(AnimationSegment(
            decoder,
            channel.attribute,
            element_count,
            payload,
            np.asarray(wanted, dtype=np.int64),
            np.asarray(targets, dtype=np.int64),
            channel.target_names,
            channel.channel_class,
            channel.variable_name,
            channel.metadata,
            diagnostics,
        ))
    return segments


def _segment_diagnostics(
        segments: Iterable[AnimationSegment | OpaqueAnimationSegment | None],
) -> tuple[Diagnostic, ...]:
    diagnostics = []
    seen = set()
    for segment in segments:
        for diagnostic in segment.diagnostics if segment is not None else ():
            key = (diagnostic.code, diagnostic.message)
            if key not in seen:
                diagnostics.append(diagnostic)
                seen.add(key)
    return tuple(diagnostics)


def animations_from_data(animation_data, decode_key, skeleton: Skeleton,
                         flex_names: Iterable[str] = (), morph_set=None) -> list[SequenceAnimation]:
    """All animations of an ``AnimationResourceData_t``, named after themselves (VRF FromData)."""
    anim_array = animation_data.get('m_animArray', []) or []
    if not anim_array:
        return []
    segments = build_segments(animation_data, decode_key, skeleton, flex_names)
    diagnostics = _segment_diagnostics(segments)
    return [
        SequenceAnimation.from_anim_desc(
            anim, segments, diagnostics=diagnostics, morph_set=morph_set
        )
        for anim in anim_array
    ]


def animations_from_sequence_data(sequence_data, animation_data, decode_key,
                                  skeleton: Skeleton, flex_names: Iterable[str] = (),
                                  morph_set=None) -> list[SequenceAnimation]:
    """Animations named and flagged by the sequences of an ``ASEQ`` block (VRF FromSequenceData).

    A sequence plays the animation its first local reference names; animations no sequence uses
    are added under their own name.
    """
    anim_array = animation_data.get('m_animArray', []) or []
    if not anim_array:
        return []
    segments = build_segments(animation_data, decode_key, skeleton, flex_names)
    diagnostics = _segment_diagnostics(segments)
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
        animations.append(SequenceAnimation.from_anim_desc(
            anim_desc, segments, name, seq_desc.get('m_flags'), diagnostics, morph_set
        ))

    for anim in anim_array:
        if str(anim['m_name']).casefold() in processed:
            continue
        animations.append(SequenceAnimation.from_anim_desc(
            anim, segments, diagnostics=diagnostics, morph_set=morph_set
        ))
    return animations
