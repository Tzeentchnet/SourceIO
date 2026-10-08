"""v49+ STUDIO_FRAMEANIM decoding: constant and per-frame data mixed in one section.

No TF2 model uses frame animations (TF2 ships v44-v48), so the blocks are built here the way
studiomdl writes them: a flag byte per bone, a constants block for bones that hold still over
the section, and a fixed-stride block per frame for the rest. Within a bone, rotation comes
before position. Sections other than the last carry one extra frame (the next one's first).
"""
import os
import struct
from types import SimpleNamespace

os.environ['NO_BPY'] = '1'

import numpy as np
import pytest

from SourceIO.library.models.mdl.structs.compressed_vectors import decode_quat48, decode_quat48s, Quat48S
from SourceIO.library.models.mdl.structs.local_animation import AniBoneFlags, AnimDescFlags, StudioAnimDesc
from SourceIO.library.utils import MemoryBuffer

F = AniBoneFlags
CONST_ROT2, CONST_ROT, CONST_POS, CONST_POS2 = F.CONST_ROT2, F.RAW_ROT, F.RAW_POS, F.CONST_POS2
ANIM_ROT2, ANIM_ROT, ANIM_POS, ANIM_POS2 = F.ANIM_ROT2, F.ANIM_ROT, F.ANIM_POS, F.FULL_ANIM_POS


def quat(axis, angle):
    axis = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    return (*(axis * np.sin(angle / 2)), np.cos(angle / 2))


def encode_quat48s(q):
    # As studiomdl: drop the largest component, store the three after it.
    p = np.asarray(q, np.float64)
    i = int(np.argmax(np.abs(p)))
    offset = (i + 1) % 4
    a, b, c = (min(max(int(p[(offset + k) % 4] * Quat48S.SCALE48S) + Quat48S.SHIFT48S, 0), 46336) for k in range(3))
    dneg = int(p[(offset + 3) % 4] < 0)
    return struct.pack("<3H", (a & 0x7FFF) | ((offset > 1) << 15), (b & 0x7FFF) | ((offset & 1) << 15),
                       (c & 0x7FFF) | (dneg << 15))


def encode_quat48(q):
    x, y, z, w = q
    return struct.pack("<3H", int(x * 32768) + 32768, int(y * 32768) + 32768,
                       ((int(z * 16384) + 16384) & 0x7FFF) | ((w < 0) << 15))


def encode_rot(flag, q):
    return encode_quat48s(q) if flag & (CONST_ROT2 | ANIM_ROT2) else encode_quat48(q)


def encode_pos(flag, v):
    return struct.pack("<3f", *v) if flag & (CONST_POS2 | ANIM_POS2) else struct.pack("<3e", *v)


def align4(data: bytearray):
    data.extend(b"\0" * (-len(data) % 4))


def frame_anim_block(flags, frames):
    """One mstudio_frame_anim_t. `frames[f][bone]` is (pos, quat); constants come from frame 0."""
    data = bytearray(24)
    data.extend(bytes(flags))
    align4(data)
    constants_offset = len(data)
    for bone, flag in enumerate(flags):
        pos, rot = frames[0][bone]
        if flag & (CONST_ROT2 | CONST_ROT):
            data.extend(encode_rot(flag, rot))
        if flag & (CONST_POS | CONST_POS2):
            data.extend(encode_pos(flag, pos))
    align4(data)
    frame_offset = len(data)
    frame_length = 0
    for flag in flags:
        frame_length += 6 if flag & (ANIM_ROT2 | ANIM_ROT) else 0
        frame_length += 12 if flag & ANIM_POS2 else 6 if flag & ANIM_POS else 0
    for frame in frames:
        for bone, flag in enumerate(flags):
            pos, rot = frame[bone]
            if flag & (ANIM_ROT2 | ANIM_ROT):
                data.extend(encode_rot(flag, rot))
            if flag & (ANIM_POS | ANIM_POS2):
                data.extend(encode_pos(flag, pos))
    align4(data)
    data[0:12] = struct.pack("<3i", constants_offset, frame_offset, frame_length)
    return bytes(data)


BONES = [
    SimpleNamespace(name="root", bone_id=0, position=(0.0, 0.0, 40.0), quat=quat((0, 0, 1), 0.3)),
    SimpleNamespace(name="spine", bone_id=1, position=(5.0, 0.0, 0.0), quat=quat((0, 1, 0), -0.2)),
    SimpleNamespace(name="arm", bone_id=2, position=(0.0, 12.0, 0.0), quat=quat((1, 0, 0), 0.5)),
    SimpleNamespace(name="still", bone_id=3, position=(1.0, 2.0, 3.0), quat=quat((1, 1, 0), 0.4)),
    SimpleNamespace(name="legacy", bone_id=4, position=(-3.0, 0.5, 2.0), quat=quat((0, 1, 1), 0.7)),
]


def pose(frame):
    """Every bone's (pos, quat) at a frame; which channels move is up to the flags."""
    t = frame * 0.1
    return [
        ((0.0, 2.0 * t, 40.0), quat((0, 0, 1), 0.3 + t)),
        ((5.0, t, -t), quat((0, 1, 0), -0.2 + 2 * t)),
        ((0.0, 12.0, 0.0), quat((1, 0.2, 0), 0.5 - t)),
        ((1.0, 2.0, 3.0), quat((1, 1, 0), 0.4)),
        ((-3.0, 0.5 + t, 2.0), quat((0.3, 1, 1), 0.7 + t)),
    ]


def anim_desc(frame_count, flags=AnimDescFlags.FRAMEANIM, section_offset=0, section_frame_count=0,
              animblock_offset=0):
    return StudioAnimDesc(_entry_offset=0, base_prt=0, name="test", fps=30.0, flags=flags,
                          frame_count=frame_count, movement_count=0, movement_offset=0, ikrule_zero_frame_offset=0,
                          animblock_id=0, animblock_offset=animblock_offset, ikrule_count=0, ikrule_offset=0,
                          animblock_ikrule_offset=0, local_hierarchy_count=0, local_hierarchy_offset=0,
                          section_offset=section_offset, section_frame_count=section_frame_count,
                          zero_frame_span=0, zero_frame_count=0, zero_frame_offset=0, stall_time=0)


def assert_rot(actual, expected, atol):
    actual, expected = np.asarray(actual), np.asarray(expected)
    # q and -q are the same rotation
    sign = np.where((actual * expected).sum(axis=-1, keepdims=True) < 0, -1, 1)
    np.testing.assert_allclose(actual * sign, expected, atol=atol)


@pytest.mark.parametrize("q", [quat((1, 2, 3), 0.4), quat((0, 0, 1), 3.0), quat((-1, 0.5, 0.2), -2.5),
                               (0.0, 0.0, 0.0, 1.0), (0.5, -0.5, 0.5, -0.5)])
def test_vectorized_decoders(q):
    q48s = np.frombuffer(encode_quat48s(q), "<u2")
    assert_rot(decode_quat48s(q48s[None])[0], q, 1e-4)
    assert_rot(decode_quat48s(q48s), Quat48S.read(MemoryBuffer(encode_quat48s(q))), 1e-6)
    if abs(q[3]) > 0.3:  # Quat48 rebuilds w, so it is coarse when w is small
        assert_rot(decode_quat48(np.frombuffer(encode_quat48(q), "<u2")), q, 2e-3)


def test_mixed_constant_and_frame_data():
    flags = [
        CONST_ROT2 | ANIM_POS,     # root: constant rotation, moving position
        ANIM_ROT2 | ANIM_POS2,     # spine: everything per frame, full-precision position
        ANIM_ROT2,                 # arm: rotation only; position stays at rest
        F(0),                      # still: holds its rest pose
        ANIM_ROT | CONST_POS2,     # legacy Quaternion48 per frame, constant position
    ]
    frame_count = 6
    frames = [pose(f) for f in range(frame_count)]
    block = frame_anim_block(flags, frames)
    desc = anim_desc(frame_count)

    result = desc.read_animations(MemoryBuffer(block), BONES)

    assert set(result) == {"root", "spine", "arm", "legacy"}
    for name in result:
        assert result[name].shape == (frame_count,)
    expected = {bone.name: ([frames[f][bone.bone_id][0] for f in range(frame_count)],
                            [frames[f][bone.bone_id][1] for f in range(frame_count)]) for bone in BONES}

    np.testing.assert_allclose(result["root"]["pos"], expected["root"][0], rtol=1e-3, atol=1e-3)
    assert_rot(result["root"]["rot"], [frames[0][0][1]] * frame_count, 1e-4)

    np.testing.assert_allclose(result["spine"]["pos"], expected["spine"][0], atol=1e-6)
    assert_rot(result["spine"]["rot"], expected["spine"][1], 1e-4)

    np.testing.assert_allclose(result["arm"]["pos"], [BONES[2].position] * frame_count, atol=1e-6)
    assert_rot(result["arm"]["rot"], expected["arm"][1], 1e-4)

    np.testing.assert_allclose(result["legacy"]["pos"], [frames[0][4][0]] * frame_count, atol=1e-6)
    assert_rot(result["legacy"]["rot"], expected["legacy"][1], 2e-3)


def test_delta_unflagged_channels_are_identity():
    flags = [ANIM_ROT2, CONST_POS, F(0), F(0), F(0)]
    frames = [pose(f) for f in range(3)]
    desc = anim_desc(3, flags=AnimDescFlags.FRAMEANIM | AnimDescFlags.DELTA)

    result = desc.read_animations(MemoryBuffer(frame_anim_block(flags, frames)), BONES)

    np.testing.assert_allclose(result["root"]["pos"], 0)
    np.testing.assert_allclose(result["spine"]["rot"], [(0, 0, 0, 1)] * 3)
    np.testing.assert_allclose(result["spine"]["pos"], [frames[0][1][0]] * 3, rtol=1e-3)


def test_sections_with_different_flags():
    # 10 frames in sections of 4: studiomdl writes 10 // 4 + 2 = 4 sections covering frames
    # 0-4, 4-8, 8-9 and 9-9 (each but the last repeats the next one's first frame).
    frame_count, per_section = 10, 4
    all_frames = [pose(f) for f in range(frame_count)]
    section_flags = [
        [ANIM_ROT2 | ANIM_POS, ANIM_ROT2 | ANIM_POS2, ANIM_ROT2, F(0), ANIM_ROT | ANIM_POS],
        # root stops listing its position: it holds its rest pose there
        [ANIM_ROT2, CONST_ROT2 | CONST_POS2, ANIM_ROT2, F(0), ANIM_ROT2 | ANIM_POS],
        [ANIM_ROT2 | ANIM_POS, ANIM_ROT2 | ANIM_POS2, F(0), F(0), ANIM_ROT | ANIM_POS],
        [ANIM_ROT2 | ANIM_POS, ANIM_ROT2 | ANIM_POS2, F(0), F(0), ANIM_ROT | ANIM_POS],
    ]
    section_count = frame_count // per_section + 2
    blocks = []
    for w in range(section_count):
        start = min(w * per_section, frame_count - 1)
        end = min((w + 1) * per_section, frame_count - 1)
        blocks.append(frame_anim_block(section_flags[w], all_frames[start:end + 1]))

    section_offset = 8  # an offset of 0 means "no sections"
    data = bytearray(section_offset + section_count * 8)
    offsets = []
    for block in blocks:
        offsets.append(len(data))
        data.extend(block)
    data[section_offset:section_offset + section_count * 8] = b"".join(
        struct.pack("<2I", 0, offset) for offset in offsets)
    desc = anim_desc(frame_count, section_offset=section_offset, section_frame_count=per_section,
                     animblock_offset=offsets[0])

    result = desc.read_animations(MemoryBuffer(bytes(data)), BONES)

    root, spine, arm = result["root"], result["spine"], result["arm"]
    expected_root_pos = [all_frames[f][0][0] for f in range(frame_count)]
    for f in range(4, 8):
        expected_root_pos[f] = BONES[0].position
    np.testing.assert_allclose(root["pos"], expected_root_pos, rtol=1e-3, atol=1e-3)
    assert_rot(root["rot"], [all_frames[f][0][1] for f in range(frame_count)], 1e-4)

    # spine is constant over section 1, at that section's first frame
    expected_spine = [all_frames[4 if 4 <= f < 8 else f][1] for f in range(frame_count)]
    np.testing.assert_allclose(spine["pos"], [p for p, _ in expected_spine], atol=1e-6)
    assert_rot(spine["rot"], [q for _, q in expected_spine], 1e-4)

    # arm is only animated in the first two sections
    assert_rot(arm["rot"][:8], [all_frames[f][2][1] for f in range(8)], 1e-4)
    assert_rot(arm["rot"][8:], [BONES[2].quat] * 2, 1e-6)
    np.testing.assert_allclose(arm["pos"], [BONES[2].position] * frame_count)

    assert "still" not in result or np.allclose(result["still"]["pos"], BONES[3].position)


def test_frame_length_too_short():
    flags = [ANIM_ROT2 | ANIM_POS2, F(0), F(0), F(0), F(0)]
    block = bytearray(frame_anim_block(flags, [pose(0), pose(1)]))
    block[8:12] = struct.pack("<i", 6)  # needs 18
    with pytest.raises(ValueError, match="18 bytes per frame"):
        anim_desc(2).read_animations(MemoryBuffer(bytes(block)), BONES)
