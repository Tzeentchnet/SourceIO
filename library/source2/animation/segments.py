"""Source 2 compressed animation segment decoders.

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat),
``ResourceTypes/ModelAnimation/SegmentDecoders`` and ``SegmentHelpers.cs``. Unlike VRF, which decodes
one frame at a time, every decoder here returns all frames of a segment at once as NumPy arrays.
"""
from dataclasses import dataclass
from enum import IntEnum

import numpy as np


class ChannelAttribute(IntEnum):
    UNKNOWN = 0
    POSITION = 1
    ANGLE = 2
    SCALE = 3
    DATA = 4
    USER = 5


# decoder name -> (is static, components per element, numpy dtype of one component, element size in bytes)
_DECODERS = {
    "CCompressedStaticFullVector3": (True, 3, "<f4", 12),
    "CCompressedStaticVector3": (True, 3, "<f2", 6),
    "CCompressedStaticQuaternion": (True, 4, None, 6),
    "CCompressedStaticFloat": (True, 1, "<f4", 4),
    "CCompressedStaticBool": (True, 1, "u1", 1),
    "CCompressedFullVector3": (False, 3, "<f4", 12),
    "CCompressedDeltaVector3": (False, 3, "<f2", 6),
    "CCompressedAnimVector3": (False, 3, "<f2", 6),
    "CCompressedAnimQuaternion": (False, 4, None, 6),
    "CCompressedFullQuaternion": (False, 4, "<f4", 16),
    "CCompressedFullFloat": (False, 1, "<f4", 4),
    "CCompressedFullBool": (False, 1, "u1", 1),
}

SUPPORTED_DECODERS = frozenset(_DECODERS)

_QUATERNION_SCALE = np.float32(np.sin(np.float32(np.pi / 4)) / np.float32(16384.0))


def decode_quaternions(raw: np.ndarray) -> np.ndarray:
    """Decode 48-bit compressed quaternions, ``raw`` is ``(..., 6)`` uint8, the result ``(..., 4)`` x, y, z, w."""
    b = raw.astype(np.int32)
    values = []
    for lo, hi in ((0, 1), (2, 3), (4, 5)):
        packed = b[..., lo] + ((b[..., hi] & 63) << 8)
        packed = np.where((b[..., hi] & 64) == 0, packed - 16384, packed)
        values.append(packed.astype(np.float32) * _QUATERNION_SCALE)
    x, y, z = values
    w = np.sqrt(np.maximum(np.float32(0.0), np.float32(1.0) - x * x - y * y - z * z))
    w = np.where((b[..., 5] & 128) != 0, -w, w)

    sign1 = (b[..., 1] & 128) != 0
    sign2 = (b[..., 3] & 128) != 0
    # The two sign bits encode which component was dropped (it is stored as w).
    conditions = [sign1 & sign2, sign1 & ~sign2, ~sign1 & sign2]
    layouts = [(y, z, w, x), (z, w, x, y), (w, x, y, z)]
    default = (x, y, z, w)
    return np.stack([np.select(conditions, [layout[i] for layout in layouts], default[i]) for i in range(4)],
                    axis=-1).astype(np.float32)


@dataclass(slots=True)
class AnimationSegment:
    """One ``m_segmentArray`` entry, bound to the bones (or flex controllers) of a skeleton."""
    decoder: str
    attribute: ChannelAttribute
    element_count: int
    data: bytes
    wanted_elements: np.ndarray  # element index inside the segment, per target
    targets: np.ndarray  # bone (or flex controller) index, per target

    @property
    def is_static(self) -> bool:
        return _DECODERS[self.decoder][0]

    @property
    def components(self) -> int:
        return _DECODERS[self.decoder][1]

    def decode(self, frame_count: int) -> np.ndarray:
        """Decode the targets for ``frame_count`` frames of the block, as ``(frames, targets, components)``.

        Static segments return a single frame. Animated segments return at most ``frame_count``
        frames, fewer when the data is truncated.
        """
        static, components, dtype, element_size = _DECODERS[self.decoder]
        count = self.element_count
        data = self.data
        wanted = self.wanted_elements

        if self.decoder == "CCompressedDeltaVector3":
            base_size = count * 12
            base = np.frombuffer(data, dtype="<f4", count=count * 3).reshape(count, 3)
            deltas = self._frames(data[base_size:], 6, frame_count)
            deltas = np.frombuffer(deltas, dtype="<f2").reshape(-1, count, 3).astype(np.float32)
            return base[wanted][None] + deltas[:, wanted]

        if static:
            raw = np.frombuffer(data, dtype=np.uint8, count=count * element_size).reshape(1, count, element_size)
        else:
            raw = np.frombuffer(self._frames(data, element_size, frame_count), dtype=np.uint8)
            raw = raw.reshape(-1, count, element_size)
        raw = raw[:, wanted]

        if dtype is None:
            return decode_quaternions(raw)
        values = np.ascontiguousarray(raw).view(dtype).astype(np.float32)
        if self.decoder.endswith("Bool"):
            values = (values != 0).astype(np.float32)
        return values.reshape(raw.shape[0], raw.shape[1], components)

    def _frames(self, data: bytes, element_size: int, frame_count: int) -> bytes:
        frame_size = self.element_count * element_size
        if frame_size == 0:
            return b""
        available = min(frame_count, len(data) // frame_size)
        return data[:available * frame_size]


def parse_segment_header(container: bytes) -> tuple[int, int, np.ndarray, bytes]:
    """Split a segment container into (decoder index, element count, element ids, payload)."""
    decoder_index, _cardinality, element_count, _total_length = np.frombuffer(container, dtype="<i2", count=4)
    end = 8 + int(element_count) * 2
    elements = np.frombuffer(container, dtype="<i2", count=int(element_count), offset=8)
    return int(decoder_index), int(element_count), elements, container[end:]
