"""Animation graph 2 clips (``.vnmclip_c``) and NM skeletons (``.vnmskel_c``).

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat):
``ResourceTypes/ModelAnimation2/AnimationClip.cs``, ``ModelAnimation/ClipAnimation.cs``,
``ModelAnimation/SkeletonRetargeter.cs`` and ``Skeleton.FromSkeletonData``.

A clip stores one track per bone of its own NM skeleton, not of the model: every frame holds, per
track, a quantized rotation, translation and scale unless the track's compression settings mark the
channel static. Clips play on a model by bone name, matching world poses (VRF ``SkeletonRetargeter``),
so :meth:`ClipAnimation.decode` returns bone-local transforms in the model skeleton's bone order, the
same as :meth:`SequenceAnimation.decode`.
"""
from dataclasses import dataclass, field

import numpy as np

from .animation import DecodedAnimation, Skeleton, _quat_multiply_xyzw

_QUAT_RANGE_MIN = np.float32(-1.0 / np.sqrt(2.0))
_QUAT_RANGE_LENGTH = np.float32(2.0 / np.sqrt(2.0))


def _transform(values) -> tuple[np.ndarray, float, np.ndarray]:
    """Translation, scale and (x, y, z, w) rotation of a KV3 transform (VRF ``ToTransform``)."""
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if len(values) == 7:
        return values[:3], 1.0, values[3:7]
    return values[:3], float(values[3]), values[4:8]


def nm_skeleton(data) -> Skeleton:
    """A skeleton from ``.vnmskel`` data, with its parent-space reference pose as the bind pose."""
    names = [str(name) for name in data['m_boneIDs']]
    count = len(names)
    parents = np.asarray(data['m_parentIndices'], dtype=np.int32).reshape(count)
    positions = np.zeros((count, 3), dtype=np.float32)
    rotations = np.zeros((count, 4), dtype=np.float32)
    for i, transform in enumerate(data['m_parentSpaceReferencePose']):
        positions[i], _, rotations[i] = _transform(transform)
    return Skeleton(names, parents, positions, rotations, np.zeros(count, dtype=np.int64))


def _range(kv) -> tuple[float, float]:
    return float(kv['m_flRangeStart']), float(kv['m_flRangeLength'])


def _decode_unorm(values: np.ndarray, start, length) -> np.ndarray:
    return values.astype(np.float32) / np.float32(65535.0) * np.float32(length) + np.float32(start)


def _decode_quaternions(data: np.ndarray) -> np.ndarray:
    """(..., 3) uint16 -> (..., 4) x y z w; two bits of the first two words name the dropped largest component."""
    a = (data[..., 0] & 0x7FFF).astype(np.float32)
    b = (data[..., 1] & 0x7FFF).astype(np.float32)
    c = data[..., 2].astype(np.float32)
    scale = _QUAT_RANGE_LENGTH / np.float32(0x7FFF)
    abc = np.stack([a, b, c], axis=-1) * scale + _QUAT_RANGE_MIN
    largest = np.sqrt(np.maximum(0.0, 1.0 - np.sum(abc * abc, axis=-1)))
    index = ((data[..., 0] >> 14) & 2) | (data[..., 1] >> 15)
    result = np.empty(data.shape[:-1] + (4,), dtype=np.float32)
    # The three stored components fill the other slots in order.
    slots = np.array([[1, 2, 3], [0, 2, 3], [0, 1, 3], [0, 1, 2]])[index]
    np.put_along_axis(result, index[..., None], largest[..., None], axis=-1)
    np.put_along_axis(result, slots, abc, axis=-1)
    return result


# The field that names an event of each class; other classes go by their type alone.
_EVENT_LABELS = {
    'Sound': 'm_name',
    'ID': 'm_ID',
    'FloatCurve': 'm_ID',
    'Particle': 'm_hParticleSystem',
    'MaterialAttribute': 'm_attributeName',
    'Legacy': 'm_animEventClassName',
}


def _event_time(value) -> float:
    if hasattr(value, 'get'):  # {'m_flValue': ...}
        value = value.get('m_flValue', 0.0)
    return float(value or 0.0)


@dataclass(slots=True)
class ClipEvent:
    """One entry of a clip's ``m_events``. Times are fractions of the clip, from 0 to 1."""
    kind: str  # the class without ``CNm`` and ``Event``: Sound, ID, Particle, ...
    start: float
    duration: float
    label: str = ''

    @classmethod
    def from_kv(cls, data) -> 'ClipEvent':
        kind = str(data.get('_class', '')).removeprefix('CNm').removesuffix('Event') or 'Event'
        label = str(data.get(_EVENT_LABELS.get(kind, ''), '') or '')
        if kind == 'Particle':
            label = label.replace('\\', '/').rsplit('/', 1)[-1].removesuffix('.vpcf')
        elif kind == 'ID' and (secondary := str(data.get('m_secondaryID', '') or '')):
            label = f'{label}/{secondary}'
        return cls(kind, _event_time(data.get('m_flStartTime')), _event_time(data.get('m_flDuration')), label)

    @property
    def marker_name(self) -> str:
        return f'{self.kind}: {self.label}' if self.label else self.kind

    def frame(self, frame_count: int) -> int:
        """The nearest frame to the event's start."""
        return round(min(max(self.start, 0.0), 1.0) * max(frame_count - 1, 0))


@dataclass(slots=True)
class AnimationClip:
    """One ``CNmClip``: the main clip, or one of its secondary animations for another skeleton."""
    name: str
    skeleton_name: str
    frame_count: int
    duration: float
    additive: bool
    rotation_static: np.ndarray  # (tracks,) bool
    translation_static: np.ndarray
    scale_static: np.ndarray
    constant_rotations: np.ndarray  # (tracks, 4) x y z w
    translation_ranges: np.ndarray  # (tracks, 3, 2) start, length
    scale_ranges: np.ndarray  # (tracks, 2)
    pose_data: np.ndarray  # uint16
    pose_offsets: np.ndarray  # (frames,), in uint16 units
    root_motion: np.ndarray | None = None  # (frames, 4, 4) or None when the clip has none
    secondary: list['AnimationClip'] = field(default_factory=list)
    events: list[ClipEvent] = field(default_factory=list)

    @classmethod
    def from_kv(cls, data, name: str) -> 'AnimationClip':
        settings = data.get('m_trackCompressionSettings', []) or []
        count = len(settings)
        rotation_static = np.array([bool(s['m_bIsRotationStatic']) for s in settings], dtype=bool)
        translation_static = np.array([bool(s['m_bIsTranslationStatic']) for s in settings], dtype=bool)
        scale_static = np.array([bool(s['m_bIsScaleStatic']) for s in settings], dtype=bool)
        constant_rotations = np.array([np.asarray(s['m_constantRotation'], dtype=np.float32).reshape(4)
                                       for s in settings], dtype=np.float32).reshape(count, 4)
        translation_ranges = np.array([[_range(s[f'm_translationRange{axis}']) for axis in 'XYZ'] for s in settings],
                                      dtype=np.float32).reshape(count, 3, 2)
        scale_ranges = np.array([_range(s['m_scaleRange']) for s in settings], dtype=np.float32).reshape(count, 2)

        raw = data.get('m_compressedPoseData')
        raw = bytes(raw) if isinstance(raw, (bytes, bytearray, memoryview)) else \
            np.asarray(raw if raw is not None else [], dtype=np.uint8).tobytes()
        pose_data = np.frombuffer(raw[:len(raw) // 2 * 2], dtype='<u2')
        pose_offsets = np.asarray(data.get('m_compressedPoseOffsets', []), dtype=np.int64).reshape(-1)

        root_motion = None
        transforms = (data.get('m_rootMotion') or {}).get('m_transforms') or []
        # Static clips store a single identity placeholder, which is no root motion.
        if len(transforms) > 1:
            root_motion = np.stack([_matrix(*_transform(t)) for t in transforms])

        clip = cls(name, str(data.get('m_skeleton', '')), int(data.get('m_nNumFrames', 0)),
                   float(data.get('m_flDuration', 0.0)), bool(data.get('m_bIsAdditive', False)),
                   rotation_static, translation_static, scale_static, constant_rotations,
                   translation_ranges, scale_ranges, pose_data, pose_offsets, root_motion)
        clip.secondary = [cls.from_kv(secondary, name) for secondary in data.get('m_secondaryAnimations', []) or []]
        clip.events = [ClipEvent.from_kv(event) for event in data.get('m_events', []) or []]
        return clip

    @property
    def fps(self) -> float:
        # frame_count samples span the duration, so the frame rate counts the intervals between them.
        if self.duration > 0 and self.frame_count > 1:
            return (self.frame_count - 1) / self.duration
        return 1.0

    @property
    def track_count(self) -> int:
        return len(self.rotation_static)

    def decode_tracks(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per-frame, per-track positions (frames, tracks, 3), x y z w rotations and scales (frames, tracks)."""
        frames = min(self.frame_count, len(self.pose_offsets))
        tracks = self.track_count
        positions = np.broadcast_to(self.translation_ranges[None, :, :, 0], (frames, tracks, 3)).copy()
        rotations = np.broadcast_to(self.constant_rotations[None], (frames, tracks, 4)).copy()
        scales = np.broadcast_to(self.scale_ranges[None, :, 0], (frames, tracks)).copy()
        if frames == 0:
            return positions, rotations, scales

        # Every frame has the same layout: per track, the rotation, translation and scale words that aren't static.
        widths = np.stack([np.where(self.rotation_static, 0, 3), np.where(self.translation_static, 0, 3),
                           np.where(self.scale_static, 0, 1)], axis=1)  # (tracks, 3)
        starts = np.cumsum(widths.reshape(-1)).reshape(tracks, 3) - widths
        stride = int(widths.sum())
        if stride == 0:
            return positions, rotations, scales
        offsets = self.pose_offsets[:frames]
        if offsets.max() + stride > len(self.pose_data):
            raise ValueError(f"Clip {self.name}: compressed pose data is too short")
        frame_words = self.pose_data[offsets[:, None] + np.arange(stride)[None]]  # (frames, stride)

        animated = np.nonzero(~self.rotation_static)[0]
        if len(animated):
            words = frame_words[:, starts[animated, 0, None] + np.arange(3)]  # (frames, animated, 3)
            rotations[:, animated] = _decode_quaternions(words)
        animated = np.nonzero(~self.translation_static)[0]
        if len(animated):
            words = frame_words[:, starts[animated, 1, None] + np.arange(3)]
            ranges = self.translation_ranges[animated]
            positions[:, animated] = _decode_unorm(words, ranges[None, :, :, 0], ranges[None, :, :, 1])
        animated = np.nonzero(~self.scale_static)[0]
        if len(animated):
            words = frame_words[:, starts[animated, 2]]
            ranges = self.scale_ranges[animated]
            scales[:, animated] = _decode_unorm(words, ranges[None, :, 0], ranges[None, :, 1])
        return positions, rotations, scales

    def root_motion_movement(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Root motion per frame as (positions (frames, 3), unwrapped yaw in degrees), or None."""
        if self.root_motion is None:
            return None
        positions = self.root_motion[:, :3, 3].astype(np.float32)
        yaw = np.degrees(np.arctan2(self.root_motion[:, 1, 0], self.root_motion[:, 0, 0]))
        previous = 0.0
        for i in range(len(yaw)):  # unwrapped against the previous frame, as VRF does
            yaw[i] += 360.0 * np.round((previous - yaw[i]) / 360.0)
            previous = yaw[i]
        frames = self.frame_count
        if len(positions) < frames:  # hold the last sample
            positions = np.concatenate([positions, np.repeat(positions[-1:], frames - len(positions), axis=0)])
            yaw = np.concatenate([yaw, np.repeat(yaw[-1:], frames - len(yaw))])
        return positions[:frames], yaw[:frames].astype(np.float32)


def _matrix(position, scale, rotation) -> np.ndarray:
    return _matrices(np.asarray(position, dtype=np.float64), np.asarray(rotation, dtype=np.float64),
                     np.asarray(scale, dtype=np.float64))


def _matrices(positions: np.ndarray, rotations: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """(..., 4, 4) from translations, x y z w rotations and uniform scales."""
    x, y, z, w = np.moveaxis(rotations, -1, 0)
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
    matrices[..., :3, :3] *= np.asarray(scales)[..., None, None]
    matrices[..., :3, 3] = positions
    matrices[..., 3, 3] = 1
    return matrices


def _bone_order(parents: np.ndarray) -> list[int]:
    """Bone indices with every parent before its children."""
    order, done = [], np.zeros(len(parents), dtype=bool)

    def visit(index: int):
        if done[index]:
            return
        done[index] = True
        if parents[index] >= 0:
            visit(int(parents[index]))
        order.append(index)

    for i in range(len(parents)):
        visit(i)
    return order


def world_matrices(parents: np.ndarray, local: np.ndarray) -> np.ndarray:
    """(frames, bones, 4, 4) world matrices from local ones."""
    world = np.empty_like(local)
    for index in _bone_order(parents):
        parent = int(parents[index])
        world[:, index] = world[:, parent] @ local[:, index] if parent >= 0 else local[:, index]
    return world


def _decompose(matrices: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Translations, x y z w rotations and uniform scales of (..., 4, 4) matrices."""
    positions = matrices[..., :3, 3]
    basis = matrices[..., :3, :3]
    scales = np.cbrt(np.linalg.det(basis))
    safe = np.where(np.abs(scales) > 1e-12, scales, 1.0)
    m = basis / safe[..., None, None]
    # Shepperd's method, branch-free: pick the largest of w, x, y, z.
    trace = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    candidates = np.stack([1 + trace, 1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2],
                           1 - m[..., 0, 0] + m[..., 1, 1] - m[..., 2, 2],
                           1 - m[..., 0, 0] - m[..., 1, 1] + m[..., 2, 2]], axis=-1)
    best = np.argmax(candidates, axis=-1)
    root = np.sqrt(np.maximum(np.take_along_axis(candidates, best[..., None], axis=-1)[..., 0], 1e-20))
    half = 0.5 / root
    w_case = np.stack([(m[..., 2, 1] - m[..., 1, 2]) * half, (m[..., 0, 2] - m[..., 2, 0]) * half,
                       (m[..., 1, 0] - m[..., 0, 1]) * half, 0.5 * root], axis=-1)
    x_case = np.stack([0.5 * root, (m[..., 0, 1] + m[..., 1, 0]) * half,
                       (m[..., 0, 2] + m[..., 2, 0]) * half, (m[..., 2, 1] - m[..., 1, 2]) * half], axis=-1)
    y_case = np.stack([(m[..., 0, 1] + m[..., 1, 0]) * half, 0.5 * root,
                       (m[..., 1, 2] + m[..., 2, 1]) * half, (m[..., 0, 2] - m[..., 2, 0]) * half], axis=-1)
    z_case = np.stack([(m[..., 0, 2] + m[..., 2, 0]) * half, (m[..., 1, 2] + m[..., 2, 1]) * half,
                       0.5 * root, (m[..., 1, 0] - m[..., 0, 1]) * half], axis=-1)
    rotations = np.choose(best[..., None], [w_case, x_case, y_case, z_case])
    return positions, rotations, scales


def _bind_locals(skeleton: Skeleton) -> np.ndarray:
    return _matrices(skeleton.positions.astype(np.float64), skeleton.rotations.astype(np.float64),
                     np.ones(len(skeleton)))


class ClipAnimation:
    """A clip bound to the skeleton it was authored on, decoded onto a model skeleton by bone name.

    Has the attributes of :class:`SequenceAnimation` that importers use.
    """

    def __init__(self, clip: AnimationClip, source: Skeleton, path: str = '', events: list[ClipEvent] | None = None):
        self.clip = clip
        self.source = source
        self.path = path or clip.name
        self.name = clip.name
        # Only a main clip has events, so a secondary animation is given its main clip's.
        self.events = clip.events if events is None else events
        self.fps = clip.fps
        self.frame_count = clip.frame_count
        self.looping = False
        self.delta = clip.additive
        self.hidden = False

    def mapped_bones(self, skeleton: Skeleton) -> int:
        source_names = {name.casefold() for name in self.source.names}
        return sum(name.casefold() in source_names for name in skeleton.names)

    def source_pose(self) -> np.ndarray:
        """(frames, source bones, 4, 4) local matrices on the clip's own skeleton."""
        positions, rotations, scales = self.clip.decode_tracks()
        tracks = positions.shape[1]
        bones = len(self.source)
        frames = positions.shape[0]
        # Tracks follow the skeleton's bone order; any bone without a track holds its bind pose.
        full_positions = np.broadcast_to(self.source.positions[None], (frames, bones, 3)).astype(np.float64)
        full_rotations = np.broadcast_to(self.source.rotations[None], (frames, bones, 4)).astype(np.float64)
        full_scales = np.ones((frames, bones))
        n = min(tracks, bones)
        if self.clip.additive:
            # A decoded clip bone is already the delta: add the position and the scale, post-multiply the rotation.
            full_positions[:, :n] += positions[:, :n]
            full_rotations[:, :n] = _quat_multiply_xyzw(full_rotations[:, :n].astype(np.float32), rotations[:, :n])
            full_scales[:, :n] = 1 + scales[:, :n]
        else:
            full_positions[:, :n] = positions[:, :n]
            full_rotations[:, :n] = rotations[:, :n]
            full_scales[:, :n] = scales[:, :n]
        full_rotations /= np.linalg.norm(full_rotations, axis=-1, keepdims=True)
        return _matrices(full_positions, full_rotations, full_scales)

    def decode(self, skeleton: Skeleton) -> DecodedAnimation:
        """Bone-local transforms on ``skeleton``: bones the clip names take its world pose, the others
        follow their parent at bind pose (VRF ``SkeletonRetargeter``)."""
        source_world = world_matrices(self.source.parents, self.source_pose())
        frames = source_world.shape[0]
        source_lookup = {}
        for i, name in enumerate(self.source.names):
            source_lookup.setdefault(name.casefold(), i)
        mapping = np.array([source_lookup.get(name.casefold(), -1) for name in skeleton.names], dtype=np.int64)

        bind = _bind_locals(skeleton)
        world = np.empty((frames, len(skeleton), 4, 4))
        for index in _bone_order(skeleton.parents):
            parent = int(skeleton.parents[index])
            if mapping[index] >= 0:
                world[:, index] = source_world[:, mapping[index]]
            elif parent >= 0:
                world[:, index] = world[:, parent] @ bind[index]
            else:
                world[:, index] = bind[index]

        local = world.copy()
        has_parent = skeleton.parents >= 0
        if np.any(has_parent):
            children = np.nonzero(has_parent)[0]
            local[:, children] = np.linalg.inv(world[:, skeleton.parents[children]]) @ world[:, children]
        positions, rotations, scales = _decompose(local)

        mapped = mapping >= 0
        result = DecodedAnimation(positions.astype(np.float32), rotations[..., [3, 0, 1, 2]].astype(np.float32),
                                  scales.astype(np.float32), mapped.copy(), mapped.copy(),
                                  np.any(np.abs(scales - 1) > 1e-4, axis=0))
        movement = self.clip.root_motion_movement()
        if movement is not None and not self.delta:
            result.movement_positions, result.movement_angles = movement
        return result
